# -*- coding: utf-8 -*-
"""缺口①③ 的出口回归（裁决 3：2026-11-18）。

缺口① attempt 级超时（Gateway 等价语义）——三层验证：
1. **执行器检查点**：``AgentLoop._check_attempt_timeout`` 到点抛
   ``AttemptTimeoutError``（真中断，不是等待方超时）；
2. **引擎挂钩**：默认引擎（skeleton）的每轮开头动作会调该检查点
   （``engine_adapters.LoopIterationStart``）；
3. **控制面收尾**：超时异常经 controller 的既有失败路径 →
   ``run.failed`` + ``run.settled{ok:False}`` + Assignment settle +
   fence release（明确失败终态 / 完成事件 / 资源释放）。

缺口③ 历史读取严格失败：读取失败 → 中止提交（无 run 事件）+ 明确错误。
"""
from __future__ import annotations

import time

import pytest

from agentnodes.kernel.agent.loop.errors import AttemptTimeoutError
from agentnodes.kernel.agent.loop.core import AgentLoop
from agentnodes.kernel.agent.tools.core import ToolRegistry
from agentnodes.foundation.llm import LLMConfig
from agentnodes.kernel.gateway.gateway import Gateway

pytestmark = pytest.mark.asyncio


def _make_loop(*, timeout_s, max_iterations=5):
    loop = AgentLoop(
        config=LLMConfig(api_key=""),
        registry=ToolRegistry(),
        workspace=None,
        max_iterations=max_iterations,
    )
    loop._attempt_timeout_s = timeout_s
    return loop


# ── 缺口①-1：执行器检查点 ───────────────────────────────────────────


class TestAttemptTimeoutCheckPoint:
    async def test_budget_read_from_env_default_600(self, monkeypatch):
        """预算来自 AGENTNODES_CHAT_ATTEMPT_TIMEOUT（默认 600；0 = 禁用）。"""
        from agentnodes.kernel.gateway.session_controller import SessionController

        ctrl = SessionController.__new__(SessionController)
        monkeypatch.setenv("AGENTNODES_CHAT_ATTEMPT_TIMEOUT", "600")
        assert ctrl._resolve_attempt_timeout_s() == 600.0

        monkeypatch.setenv("AGENTNODES_CHAT_ATTEMPT_TIMEOUT", "0")
        ctrl2 = SessionController.__new__(SessionController)
        assert ctrl2._resolve_attempt_timeout_s() is None, "0 = 禁用"

        monkeypatch.delenv("AGENTNODES_CHAT_ATTEMPT_TIMEOUT", raising=False)
        ctrl3 = SessionController.__new__(SessionController)
        assert ctrl3._resolve_attempt_timeout_s() == 600.0

    async def test_check_point_raises_when_deadline_passed(self):
        """到点 → 抛 AttemptTimeoutError（执行器内部中断）。"""
        loop = _make_loop(timeout_s=0.05)
        loop._begin_attempt_deadline()
        time.sleep(0.08)
        with pytest.raises(AttemptTimeoutError):
            loop._check_attempt_timeout(1)

    async def test_check_point_passes_before_deadline(self):
        """未到点 → 不抛（正常执行不受影响）。"""
        loop = _make_loop(timeout_s=5.0)
        loop._begin_attempt_deadline()
        loop._check_attempt_timeout(1)  # 不抛

    async def test_timeout_message_states_budget(self):
        """错误信息含预算秒数（与旧路径 "attempt timed out after Ns" 对齐）。"""
        loop = _make_loop(timeout_s=0.05)
        loop._begin_attempt_deadline()
        time.sleep(0.08)
        with pytest.raises(AttemptTimeoutError) as exc:
            loop._check_attempt_timeout(2)
        assert "timed out" in str(exc.value)

    async def test_deadline_starts_at_execution_not_queueing(self):
        """计时从**实际执行**开始——排队时长不计入预算。"""
        loop = _make_loop(timeout_s=5.0)
        # 模拟"排队期已过"：把 deadline 拨到过去，再重新起算 → 不应超时
        loop._attempt_deadline = time.monotonic() - 100
        loop._begin_attempt_deadline()
        loop._check_attempt_timeout(1)  # 不抛

    async def test_zero_budget_disables_check(self):
        """None = 禁用（不因预算缺失误杀正常执行）。"""
        loop = _make_loop(timeout_s=None)
        loop._begin_attempt_deadline()
        assert loop._attempt_deadline is None
        loop._check_attempt_timeout(1)  # 不抛


# ── 缺口①-2：引擎挂钩（默认引擎每轮开头检查）─────────────────────────


class TestEngineIterationStartHook:
    async def test_iteration_start_triggers_timeout_check(self):
        """默认引擎的每轮开头动作会调检查点（skeleton 路径覆盖）。"""
        from agentnodes.kernel.agent.loop.engine_adapters import LoopIterationStart

        class _Loop:
            _attempt_timeout_s = 0.05
            _attempt_deadline = None
            _started_at = 0.0
            _wrap_up_nudge = False

            def _check_attempt_timeout(self, iteration, result=None):
                raise AttemptTimeoutError("attempt timed out after 0.05s")

        with pytest.raises(AttemptTimeoutError):
            LoopIterationStart(_Loop())(None, 1)

    async def test_agent_loop_exposes_check_point(self):
        """AgentLoop 提供检查点（供引擎 adapter 调用）。"""
        loop = _make_loop(timeout_s=1.0)
        assert callable(loop._check_attempt_timeout)


# ── 缺口①-3：控制面收尾（明确失败终态 + 事件 + 资源释放）────────────


class _TimeoutEngine:
    """模拟"执行器在超时后中断"——抛 AttemptTimeoutError。"""

    def __init__(self) -> None:
        self.calls = 0

    def run(self, state, **kw):  # noqa: ANN003 — GraphEngine 节点契约
        self.calls += 1
        raise AttemptTimeoutError("attempt timed out after 0.05s")


class TestGatewayTimeoutSettlement:
    async def test_timeout_yields_failed_events_and_release(self):
        """超时 → run.failed + run.settled{ok:False} + fence 释放。"""
        events: list[tuple[str, dict]] = []
        engine = _TimeoutEngine()
        gw = Gateway(
            client=object(),
            sink=lambda k, d: events.append((k, d)),
        )
        gw.controller._engine_factory = lambda: engine  # type: ignore[attr-assign]

        from agentnodes.kernel.gateway.entry_adapters import api_chat_request

        gw.entry.submit(api_chat_request("s1", "hi"))
        results = gw.run_once()

        kinds = [k for k, _ in events]
        assert "run.failed" in kinds, f"超时必须发 run.failed（实际 {kinds}）"
        assert "run.settled" in kinds
        failed = [d for k, d in events if k == "run.failed"][-1]
        assert "timed out" in failed["error"]
        settled = [d for k, d in events if k == "run.settled"][-1]
        assert settled["ok"] is False
        assert results and results[0].ok is False
        assert "timed out" in (results[0].error or "")
        # 资源释放：栅栏不再持有活跃 run（后续 turn 可进）
        assert gw.fence.active_run("s1") is None

    async def test_no_orphan_execution_after_timeout(self):
        """超时后不再执行后续轮次（无孤儿执行）。"""
        engine = _TimeoutEngine()
        gw = Gateway(client=object(), sink=lambda k, d: None)
        gw.controller._engine_factory = lambda: engine  # type: ignore[attr-assign]

        from agentnodes.kernel.gateway.entry_adapters import api_chat_request

        gw.entry.submit(api_chat_request("s1", "hi"))
        gw.run_once()
        calls_after = engine.calls
        time.sleep(0.2)
        assert engine.calls == calls_after, "超时后不得继续执行"


# ── 缺口③：历史读取严格失败 ─────────────────────────────────────────


class _FakeServiceWithBrokenHistory:
    """store.get_messages 抛错（模拟历史读失败）。"""

    class store:  # noqa: N801 — 替身形状
        @staticmethod
        def get_messages(session_id, limit=100):
            raise RuntimeError("event_log replay failed")

    @staticmethod
    def _convert_messages_to_history(stored, keep_all_compactions=False):
        return []


class TestHistoryStrictFailure:
    async def test_history_failure_aborts_submission(self, monkeypatch):
        """历史读取失败 → 中止提交（无 run 事件）+ 明确错误。"""
        from fastapi import HTTPException

        from agentnodes.interfaces.api.routers import chat as chat_mod

        events: list[tuple[str, dict]] = []
        monkeypatch.setattr(
            chat_mod, "_get_session_service",
            lambda: _FakeServiceWithBrokenHistory(),
        )
        monkeypatch.setattr(
            chat_mod, "_get_gateway",
            lambda: Gateway(
                client=object(),
                sink=lambda k, d: events.append((k, d)),
            ),
        )

        with pytest.raises(HTTPException) as exc:
            await chat_mod._send_via_gateway("s1", "hello")

        detail = exc.value.detail
        assert isinstance(detail, dict) and detail["error"] == "history_unavailable"
        assert detail["session_id"] == "s1"
        assert exc.value.status_code == 500
        assert not events, "中止提交不得产生任何 run 事件"
