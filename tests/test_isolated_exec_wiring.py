# -*- coding: utf-8 -*-
"""B2.4 子批 4c：run_isolated helper + WP-5 六场景契约测试。

用 fake isolate_tool_call 驱动六场景（deadline 前完成/到达/终止成功/
终止失败未知/迟到结果/lease 不误放关注的确认前不返回），不依赖真 Windows
worker；真 worker 时序由 3c S1-S4 已证 + 本批 `_emit_isolation_outcome`
的重注入测试覆盖。生产开关默认关（4a/4d 语义），本文件不改默认行为。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from agentnodes.kernel.agent.loop._isolated_exec import (
    IsolatedOutcome,
    run_isolated,
)


@pytest.fixture()
def exec_host_stub():
    """单一 host 能力面 stub（候选 3：两份 HostStub 合并）。

    4 能力：registry / trace_and_emit / record_timeout_outcome /
    finalize_timeout——执行器不读宿主私有数据字段。
    """
    from agentnodes.kernel.agent.loop._isolated_exec import IsolatedToolExecutor

    class HostStub:
        def __init__(self):
            self.outcomes = []
            self.events = []
            self.finalized = []
            self.registry = SimpleNamespace(
                timeout_capability=lambda name: "isolatable",
            )

        def trace_and_emit(self, kind, payload):
            self.events.append((kind, payload))

        def record_timeout_outcome(self, kind, name, declared, state):
            self.outcomes.append((kind, name, declared, state))

        def finalize_timeout(self):
            self.events.append(("timeout_checked", {"finalize": True}))
            self.finalized.append(1)

    host = HostStub()
    return IsolatedToolExecutor(host), host


class FakeIsolationModule:
    """替换 isolation.isolate_tool_call 的返回值工厂。"""

    def __init__(self, result):
        self.result = result
        self.calls = []

    def __call__(self, tool, args, **kw):
        self.calls.append((tool, args, kw))
        return self.result


def _result(status="completed", state="stopped", *, timed_out=False,
            discarded=0, payload=None):
    return SimpleNamespace(
        status=status,
        payload=payload if payload is not None else {"output": '{"ok": true}'},
        actual_state=state, platform="fake", timed_out=timed_out,
        call_id="write:x", discarded_rows=discarded,
    )


@pytest.fixture()
def patch_isolation(monkeypatch):
    """返回 setter：设置 fake 结果并注入 isolation 模块。"""
    import agentnodes.kernel.agent.tools.isolation as iso

    holder = {"fake": None}

    def fake_isolate(tool, args, **kw):
        holder["calls"].append((tool, args, kw))
        return holder["fake"]

    holder["calls"] = []
    monkeypatch.setattr(iso, "isolate_tool_call", fake_isolate)
    return holder


class TestRunIsolated:
    def test_deadline_before_completion(self, patch_isolation):
        """场景1：deadline 前完成 → completed/stopped，不重注入终态。"""
        patch_isolation["fake"] = _result()
        out = run_isolated(
            "write", {"path": "x"}, remaining_budget_s=9.0,
        )
        assert out.status == "completed"
        assert out.actual_state == "stopped"
        assert out.timed_out is False
        assert out.result_rejected is False
        # 剩余预算透传（不用 seam 默认 30s）
        assert patch_isolation["calls"][0][2]["timeout_s"] == 9.0

    def test_deadline_reached_terminated(self, patch_isolation):
        """场景3：确认成功 → terminated/stopped，结果拒收。"""
        patch_isolation["fake"] = _result(
            status="terminated", state="stopped", timed_out=True,
        )
        out = run_isolated("write", {}, remaining_budget_s=0.0)
        assert out.status == "terminated"
        assert out.actual_state == "stopped"
        assert out.timed_out is True
        assert out.result_rejected is True

    def test_termination_failed_unknown(self, patch_isolation):
        """场景4：未确认 → unknown，结果拒收、不升级 stopped。"""
        patch_isolation["fake"] = _result(
            status="failed", state="unknown", timed_out=True,
        )
        out = run_isolated("write", {}, remaining_budget_s=0.0)
        assert out.actual_state == "unknown"
        assert out.actual_state != "stopped"
        assert out.result_rejected is True

    def test_late_result_discarded(self, patch_isolation):
        """场景5：迟到结果（discarded_rows>0）被丢弃计数，不接纳其 payload。"""
        patch_isolation["fake"] = _result(
            status="completed", state="stopped", discarded=2,
            payload={"output": '{"ok": true}'},
        )
        out = run_isolated("write", {}, remaining_budget_s=5.0)
        assert out.discarded_rows == 2
        assert out.call_id == "write:x"

    def test_no_auto_retry_on_unknown(self, patch_isolation):
        """场景6：unknown 不重试——run_isolated 只调用一次 seam。"""
        patch_isolation["fake"] = _result(
            status="failed", state="unknown", timed_out=True,
        )
        run_isolated("write", {}, remaining_budget_s=0.0)
        assert len(patch_isolation["calls"]) == 1

    def test_transient_worker_per_call(self, patch_isolation):
        """每调用独占 transient worker——不传 worker（复用模式本批不开）。"""
        patch_isolation["fake"] = _result()
        run_isolated("write", {}, remaining_budget_s=3.0)
        kw = patch_isolation["calls"][0][2]
        assert "worker" not in kw or kw["worker"] is None

    def test_ctx_and_approval_passed(self, patch_isolation):
        """ctx 与 approval_decider 透传到 seam（回调不入 wire 由 codec 保证）。"""
        patch_isolation["fake"] = _result()
        ctx = SimpleNamespace(workspace=None)
        decider = lambda req: "deny"
        run_isolated(
            "write", {}, remaining_budget_s=3.0,
            ctx=ctx, approval_decider=decider,
        )
        kw = patch_isolation["calls"][0][2]
        assert kw["ctx"] is ctx
        assert kw["approval"] is not None  # ApprovalProxy 包装

    def test_none_budget_uses_seam_default(self, patch_isolation):
        patch_isolation["fake"] = _result()
        run_isolated("write", {}, remaining_budget_s=None)
        assert patch_isolation["calls"][0][2]["timeout_s"] == 30.0


class TestIsolatedOutcomeShape:
    def test_result_rejected_values(self):
        assert IsolatedOutcome(
            "{}", "stopped", "completed", "x", 0, False,
        ).result_rejected is False
        assert IsolatedOutcome(
            "{}", "returned", "failed", "x", 0, False,
        ).result_rejected is True
        assert IsolatedOutcome(
            "{}", "unknown", "failed", "x", 0, True,
        ).result_rejected is True
        assert IsolatedOutcome(
            "{}", "stopped", "terminated", "x", 0, True,
        ).result_rejected is True


class TestE5OutcomeReinjection:
    """IsolatedToolExecutor._settle：declared 取 registry + 终态重注入 + 事件。"""

    def test_declared_uses_registry_not_raw_getattr(self, exec_host_stub):
        executor, host = exec_host_stub
        outcome = IsolatedOutcome("{}", "returned", "failed", "x", 0, False)
        executor._record_outcome(SimpleNamespace(name="write"), outcome)
        assert host.outcomes == [("tool", "write", "isolatable", "returned")]

    def test_declared_registry_none_falls_back_to_unstoppable(self, exec_host_stub):
        """I1：执行器 registry=None（此前零覆盖）→ unstoppable 常量，非裸字面量。"""
        from agentnodes.kernel.agent.tools.core import (
            TIMEOUT_CAPABILITY_UNSTOPPABLE,
        )

        executor, host = exec_host_stub
        host.registry = None
        outcome = IsolatedOutcome("{}", "returned", "failed", "x", 0, False)
        executor._record_outcome(SimpleNamespace(name="write"), outcome)
        assert host.outcomes == [
            ("tool", "write", TIMEOUT_CAPABILITY_UNSTOPPABLE, "returned"),
        ]

    def test_terminal_reinjects_timeout_chain(self, exec_host_stub):
        executor, host = exec_host_stub
        outcome = IsolatedOutcome("{}", "stopped", "terminated", "x", 0, True)
        decision = SimpleNamespace(
            isolate=True, reason="isolatable", capability="isolatable", tool_name="write",
        )
        executor._settle(SimpleNamespace(name="write"), outcome, decision)
        kinds = [k for k, _ in host.events]
        assert "timeout_checked" in kinds  # 终态重注入（经 finalize_timeout 能力）
        assert "isolation.terminal" in kinds
        assert host.finalized == [1]

    def test_non_terminal_no_reinjection(self, exec_host_stub):
        executor, host = exec_host_stub
        outcome = IsolatedOutcome("{}", "stopped", "completed", "x", 0, False)
        decision = SimpleNamespace(
            isolate=True, reason="isolatable", capability="isolatable", tool_name="write",
        )
        executor._settle(SimpleNamespace(name="write"), outcome, decision)
        kinds = [k for k, _ in host.events]
        assert "timeout_checked" not in kinds
        assert "isolation.terminal" not in kinds

    def test_exec_host_finalize_swallows_timeout_error(self):
        """Q2：ExecHost.finalize_timeout 吞 AttemptTimeoutError（终态重注入只产事件）。"""
        from agentnodes.kernel.agent.loop.errors import AttemptTimeoutError
        from agentnodes.kernel.agent.loop.tools import _ExecHost

        class FakeLoop:
            _current_iter = 0

            def _check_attempt_timeout(self, iteration, result, *, finalize=True):
                assert finalize is True
                raise AttemptTimeoutError("deadline reached")

        adapter = _ExecHost(FakeLoop())
        adapter.finalize_timeout()  # 不抛（刻意吞）


class TestExecutorRunInterface:
    """Q7：IsolatedToolExecutor.run 单一接口——不隔离返回 None，隔离走全流程。"""

    def test_switch_off_returns_none(self, monkeypatch, exec_host_stub):
        monkeypatch.delenv("AGENTNODES_ISOLATION_EXECUTION", raising=False)
        executor, host = exec_host_stub
        tool = SimpleNamespace(
            name="write", timeout_capability="isolatable",
            enforce_permission_sync=lambda args, ctx: None,
        )
        tc = SimpleNamespace(name="write", arguments={})
        out = executor.run(
            tc, tool, tool_ctx=None, remaining_budget_s=5.0,
            platform="win32",
        )
        assert out is None  # 开关关 → core 走 in-process 路径
        assert host.events == [] and host.outcomes == []

    def test_isolated_run_full_flow(self, monkeypatch, patch_isolation, exec_host_stub):
        monkeypatch.setenv("AGENTNODES_ISOLATION_EXECUTION", "1")
        monkeypatch.setenv("AGENTNODES_ISOLATION_TOOLS", "write")
        patch_isolation["fake"] = _result()
        executor, host = exec_host_stub
        tool = SimpleNamespace(
            name="write", timeout_capability="isolatable",
            enforce_permission_sync=lambda args, ctx: None,
        )
        tc = SimpleNamespace(name="write", arguments={"path": "x"})
        out = executor.run(
            tc, tool, tool_ctx=None, remaining_budget_s=5.0,
            platform="win32",
        )
        assert out is not None and out.status == "completed"
        assert host.outcomes == [("tool", "write", "isolatable", "stopped")]
        kinds = [k for k, _ in host.events]
        assert "isolation.shadow_decision" in kinds
        assert "isolation.terminal" not in kinds  # 正常完成非终态
