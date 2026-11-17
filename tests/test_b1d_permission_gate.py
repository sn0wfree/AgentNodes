"""B1-d: ToolRegistry 执行的权限闸门(negative-path 契约)。

**V3-c**:react 节点已删除;其「经 ctx_factory 接线 evaluator」的等价
覆盖移居 ``tests/test_agent_loop_node.py``(controller 级 deny/allow)。
本文件保留 registry 层的 deny/allow/ASK fail-closed 语义——与执行引擎
无关,是工具执行的通用闸门。
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from agentnodes.kernel.agent.tools.core import BaseTool, ToolContext, ToolRegistry


class _RecordingTool(BaseTool):
    name = "danger"
    description = "records execution"
    parameters = {}
    executed: list = []

    def execute(self, **kwargs):
        type(self).executed.append(kwargs)
        return json.dumps({"status": "ok"})


@pytest.fixture(autouse=True)
def _reset_executed():
    _RecordingTool.executed = []
    yield
    _RecordingTool.executed = []


def _registry() -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(_RecordingTool())
    return reg


def _evaluator(action: str):
    """Stub evaluator returning one fixed verdict."""
    from agentnodes.kernel.permission import (
        PermissionAction,
        PermissionDecision,
    )

    # ``pattern`` is a read-only property derived from rule/target.
    decision = PermissionDecision(
        action=PermissionAction(action),
        rule=None,
        target="danger",
    )
    return SimpleNamespace(evaluate=lambda name, kwargs: decision)


# ── registry.execute 同步闸门 ─────────────────────────────


def test_deny_blocks_execution_and_reports():
    """DENY: tool never runs; caller gets a structured refusal."""
    reg = _registry()
    ctx = ToolContext(permission_evaluator=_evaluator("deny"), tool_call_id="tc1")

    out = json.loads(reg.execute("danger", {}, ctx=ctx))

    assert _RecordingTool.executed == []  # 未执行
    assert out["status"] == "error"
    assert out["permission_denied"] is True
    assert "denied" in out["error"].lower()


def test_allow_runs_tool_with_ctx():
    """ALLOW: tool runs and receives the ctx."""
    reg = _registry()
    ctx = ToolContext(
        permission_evaluator=_evaluator("allow"), tool_call_id="tc2",
        session_id="s1",
    )

    out = json.loads(reg.execute("danger", {"x": 1}, ctx=ctx))

    assert out["status"] == "ok"
    assert len(_RecordingTool.executed) == 1
    assert _RecordingTool.executed[0]["ctx"] is ctx


def test_ask_without_gateway_fails_closed():
    """ASK + 无 gateway：**fail-closed**（批次 7 修复）。

    旧行为（当 allow + warning）是一个安全洞：CLI 路径从不接 gateway，
    等于所有 ask 规则实际都在放行。修复后拒绝并给出可操作出路
    （切 L2 模式或接入 gateway），与 sync 路径的
    ``test_ask_with_gateway_denied_on_sync_path`` 同语义。
    """
    reg = _registry()
    ctx = ToolContext(permission_evaluator=_evaluator("ask"), tool_call_id="tc3")

    out = json.loads(reg.execute("danger", {}, ctx=ctx))

    assert _RecordingTool.executed == []  # 未执行
    assert out["status"] == "error"
    assert out["permission_denied"] is True
    assert "AGENTNODES_PERMISSION_MODE" in out["error"]


def test_ask_with_gateway_denied_on_sync_path():
    """ASK + 配置了 gateway：同步路径无法握手 → 拒绝而非静默放行。"""
    reg = _registry()
    ctx = ToolContext(
        permission_evaluator=_evaluator("ask"),
        permission_gateway=SimpleNamespace(),
        tool_call_id="tc4",
    )

    out = json.loads(reg.execute("danger", {}, ctx=ctx))

    assert _RecordingTool.executed == []
    assert out["permission_denied"] is True
    assert "async" in out["error"].lower()


def test_no_ctx_keeps_legacy_unprotected_behaviour():
    """无 ctx：与今天一致（测试/脚本路径不受影响）。"""
    reg = _registry()
    out = json.loads(reg.execute("danger", {}))
    assert out["status"] == "ok"
    assert len(_RecordingTool.executed) == 1
