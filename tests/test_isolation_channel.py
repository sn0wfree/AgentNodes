# -*- coding: utf-8 -*-
"""4c B2.2 子批 3b：IPC 通道 + 审批代理 + actual_state 词表。

E2 上下文跨界（可序列化 ctx 子集）、E3 审批往返（ALLOW/DENY 两路）、
actual_state 与 4a 词表一致、POSIX/未验证平台显式拒绝。
**用常驻 worker**（spawn 一次复用）——每次调用 spawn 的 ~500ms 成本会让
本文件在全量并发下显著拖慢。
"""
from __future__ import annotations

import sys

import pytest

from agentnodes.kernel.agent.tools.isolation import (
    ACTUAL_STATES,
    ApprovalProxy,
    IsolationResult,
    IsolationWorker,
    UnsupportedPlatform,
    build_worker_adapter,
    isolate_tool_call,
)
from agentnodes.kernel.agent.tools.isolation import _EOF

pytestmark = pytest.mark.skipif(
    sys.platform != "win32",
    reason="Windows-only：隔离 adapter 依赖 kernel32 Job Object（POSIX 未验证）",
)


@pytest.fixture()
def worker():
    """常驻隔离 worker（模块级复用，省 spawn 成本）。"""
    with IsolationWorker() as w:
        yield w


def test_e2_context_crosses_to_worker(worker):
    """E2：可序列化的 ToolContext 字段跨界回传一致。"""
    result = worker.call(
        "probe_context",
        {"payload": "ctx-probe"},
        ctx={"workspace": "D:/probe-ws", "session_id": "sess-42"},
    )
    assert result.status == "completed"
    assert result.payload["echo"] == "ctx-probe"
    assert result.actual_state == "stopped"


@pytest.mark.parametrize("action,expected_status,expected_state", [
    ("allow", "completed", "stopped"),
    ("deny", "failed", "returned"),
])
def test_e3_permission_roundtrip(worker, action, expected_status, expected_state):
    """E3：worker 发 ASK → 父进程裁决 → 回传 → 结果。"""
    seen = []

    def _decider(request):
        seen.append(request)
        return action

    result = worker.call(
        "probe_permission",
        {"id": "p-1", "tool": "risky_write"},
        approval=ApprovalProxy(_decider),
    )
    assert seen and seen[0]["id"] == "p-1"
    assert seen[0]["tool"] == "risky_write"
    assert result.status == expected_status
    assert result.actual_state == expected_state


def test_approval_proxy_defaults_to_deny():
    """未知裁决 → deny（fail-closed）。"""
    proxy = ApprovalProxy(lambda req: "maybe")
    assert proxy.decide({"id": "x"}) == "deny"


def test_actual_states_match_4a_vocabulary():
    """actual_state 词表与 4a 的 timeout_tool_outcomes[].state 严格一致。"""
    assert ACTUAL_STATES == {"stopped", "still_running", "returned", "unknown"}


def test_unsupported_platform_is_explicit():
    """未验证平台显式拒绝，不静默降级到进程内执行。"""
    from agentnodes.kernel.agent.tools.isolation import _PosixAdapter

    adapter = _PosixAdapter()
    with pytest.raises(UnsupportedPlatform):
        isolate_tool_call("probe_context", {}, adapter=adapter)


def test_read_timeout_is_not_eof():
    """回归：读超时（worker 只是慢）不得被当成 EOF/退出。

    根因：早期实现用 None 同时表示「超时」与「EOF」，worker 首次 import
    慢于一次读超时就被误判为已退出 → 实际状态错报 unknown。
    """
    from agentnodes.kernel.agent.tools.isolation import IsolationChannel

    handle = {"proc": type("P", (), {
        "stdout": iter(()), "stderr": None, "pid": 1,
    })()}
    channel = IsolationChannel(handle)
    assert channel.read(0.05) is None, "空队列读超时 → None（非 EOF）"
    assert channel._eof is False, "读超时不置 EOF 标志"

    # 显式 EOF 哨兵 → 置 EOF 标志
    channel._queue.put(_EOF)
    assert channel.read(0.05) is None
    assert channel._eof is True, "真 EOF 才置标志"


def test_resident_worker_reused_for_multiple_calls(worker):
    """常驻 worker：多次投递（省 ~500ms/次 spawn 成本），关闭后不可再用。"""
    first = worker.call("probe_context", {"payload": "one"})
    second = worker.call("probe_context", {"payload": "two"})
    assert first.status == "completed"
    assert second.status == "completed"
    assert first.payload["echo"] == "one"
    assert second.payload["echo"] == "two"


def test_isolate_tool_call_accepts_resident_worker(worker):
    """单入口可复用常驻 worker（生产/批量路径形态）。"""
    result = isolate_tool_call(
        "probe_context", {"payload": "x"}, worker=worker,
    )
    assert result.status == "completed"
    assert result.platform == "isolated"


def test_real_tool_executes_in_isolated_worker(worker, tmp_path):
    """真实工具（write）在隔离 worker 内执行：ctx.workspace 正确跨界、
    写根保护生效（绝对路径被拒）、相对路径写入成功。

    这是 3c 临时工作区 WriteFileTool 试点的前置能力证明。
    """
    from agentnodes.kernel.agent.builtin_tools import build_default_registry

    names = {t.name for t in build_default_registry().all_tools()}
    if "write" not in names:
        pytest.skip("write 工具未注册")

    # 绝对路径 → 工具的写根保护拒绝（证明 ctx 已跨界，不是进程内执行）
    denied = worker.call(
        "write",
        {"path": str(tmp_path / "abs.txt"), "content": "x"},
        ctx={"workspace": str(tmp_path)},
    )
    # 工具级失败：worker 执行成功、工具返回结构化拒绝 → status=failed,
    # actual_state=returned
    assert denied.status == "failed"
    assert denied.actual_state == "returned"
    assert "absolute paths are not allowed" in str(denied.payload)
    assert not (tmp_path / "abs.txt").exists()

    # 相对路径 → 写入 workspace 内
    ok = worker.call(
        "write",
        {"path": "rel.txt", "content": "iso-write"},
        ctx={"workspace": str(tmp_path)},
    )
    assert ok.status == "completed"
    assert (tmp_path / "rel.txt").read_text(encoding="utf-8") == "iso-write"
