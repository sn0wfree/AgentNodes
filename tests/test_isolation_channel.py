# -*- coding: utf-8 -*-
"""4c B2.2 子批 3b：IPC 通道 + 审批代理（Windows 行为类）。

E2 上下文跨界（可序列化 ctx 子集）与 E3 审批往返（ALLOW/DENY 两路）
的真 worker 行为。平台守卫类（词表/fail-closed/POSIX 显式拒绝/读超时
回归）在 test_isolation_platform_matrix.py——全平台真跑。
**用常驻 worker**（spawn 一次复用）——每次调用 spawn 的 ~500ms 成本会让
本文件在全量并发下显著拖慢。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from agentnodes.kernel.agent.tools.core import ToolContext
from agentnodes.kernel.agent.tools.isolation import (
    ApprovalProxy,
    IsolationWorker,
    isolate_tool_call,
)

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
        ctx=ToolContext(workspace=Path("D:/probe-ws"), session_id="sess-42"),
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
        ctx=ToolContext(workspace=tmp_path),
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
        ctx=ToolContext(workspace=tmp_path),
    )
    assert ok.status == "completed"
    assert (tmp_path / "rel.txt").read_text(encoding="utf-8") == "iso-write"


# ── B2.3 3b：版本 / 解码失败行为（真 worker，win32 gate）──────────


def test_incompatible_version_rejected_by_worker(worker):
    """ctx_v 超版本 → worker 回结构化错误行（failed/returned），不毒化。"""
    result = worker.call(
        "probe_context", {"payload": "x"},
        ctx={"ctx_v": 999, "workspace": "D:/probe"},
    )
    assert result.status == "failed"
    assert result.actual_state == "returned"
    assert "ctx_v" in str(result.payload)

    # 健康 worker 仍可复用（解码失败不毒化——毒化只属 deadline/EOF）
    ok = worker.call("probe_context", {"payload": "after-bad-ctx"})
    assert ok.status == "completed"
    assert ok.actual_state == "stopped"


def test_malformed_wire_key_rejected_and_worker_survives(worker):
    """白名单外键（回调暗道）→ worker 结构化错误行，后续调用正常。"""
    bad = {"ctx_v": 1, "permission_gateway": "leak"}
    result = worker.call("probe_context", {"payload": "x"}, ctx=bad)
    assert result.status == "failed"
    assert "unexpected wire ctx keys" in str(result.payload)

    ok = worker.call("probe_context", {"payload": "after-leak"})
    assert ok.status == "completed"


def test_probe_ctx_echo_reveals_only_whitelisted_keys(worker):
    """3c 契约探针：回调全填的 ToolContext 编码后，worker 实际收到的
    wire 键集合恰为白名单 + ctx_v——无对象/回调键跨界（两端夹击）。"""
    from agentnodes.kernel.agent.tools.core import ToolContext

    ctx = ToolContext(
        workspace=Path("D:/probe-ws"), session_id="sess-echo",
        emit_progress=lambda x: None, emit_event=lambda k, d: None,
        permission_gateway=object(), data_store=object(), sandbox=object(),
    )
    result = worker.call("probe_ctx_echo", {}, ctx=ctx)
    assert result.status == "completed"
    assert result.payload["ctx_v"] == 1
    assert set(result.payload["wire_keys"]) == (
        {"ctx_v", "workspace", "session_id", "strategy_dir", "runs_dir",
         "results_tsv", "write_roots", "read_roots", "message_id", "tool_call_id"}
    )

    # 结构化错误行侧：白名单外键（回调暗道）在 worker 解码即被拒
    bad = worker.call("probe_ctx_echo", {}, ctx={"ctx_v": 1, "sandbox": "leak"})
    assert bad.status == "failed"
    assert "unexpected wire ctx keys" in str(bad.payload)
