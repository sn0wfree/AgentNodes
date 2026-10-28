"""B1-d: react path must enforce the permission gate.

Before this, ``react.py`` called ``registry.execute(name, args)`` with no
ToolContext at all — so the evaluator ruleset, the ASK handshake and the
workspace/session plumbing never ran on the react path (in production
since P5-1b). These are the negative-path tests the design requires.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from strategy_research.core.agent.tools import BaseTool, ToolContext, ToolRegistry


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
    from strategy_research.core.permission import (
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


def test_ask_without_gateway_keeps_documented_fail_open(caplog):
    """ASK + 无 gateway：保持既有 fail-open 语义（有 warning）。"""
    reg = _registry()
    ctx = ToolContext(permission_evaluator=_evaluator("ask"), tool_call_id="tc3")

    out = json.loads(reg.execute("danger", {}, ctx=ctx))

    assert out["status"] == "ok"  # fail-open（与 tools.py:688 一致）
    assert len(_RecordingTool.executed) == 1


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


# ── react 节点接线 ────────────────────────────────────────


class _FakeResponse:
    """Minimal LLM response with the has_tool_calls() contract."""

    def __init__(self, content: str = "", tool_calls: list | None = None):
        self.content = content
        self.tool_calls = tool_calls or []
        self.usage = None

    def has_tool_calls(self) -> bool:
        return bool(self.tool_calls)


class _FakeClient:
    """One tool call then a final answer."""

    def __init__(self) -> None:
        self.calls = 0

    def chat(self, messages, tools=None):
        self.calls += 1
        if self.calls == 1:
            tc = SimpleNamespace(
                id="call_1", name="danger", arguments={"a": 1},
            )
            return _FakeResponse(tool_calls=[tc])
        return _FakeResponse(content="done")


def test_react_node_passes_ctx_to_tools():
    """react 节点经 ctx_factory 为每次 tool_call 构造 ctx 并交给 registry。"""
    from strategy_research.core.runtimes.nodes.react import (
        ReactConfig,
        make_react_node,
    )

    seen_ids: list[str] = []

    def factory(tool_call_id: str, params: dict):
        seen_ids.append(tool_call_id)
        return ToolContext(
            permission_evaluator=_evaluator("allow"),
            tool_call_id=tool_call_id,
            session_id="sess-x",
        )

    node = make_react_node(
        _FakeClient(), _registry(),
        config=ReactConfig(ctx_factory=factory),
    )
    out = node({"messages": [{"role": "user", "content": "go"}]})

    assert seen_ids == ["call_1"]
    assert len(_RecordingTool.executed) == 1
    assert _RecordingTool.executed[0]["ctx"].session_id == "sess-x"
    assert out["answer"] == "done"


def test_react_node_denied_tool_does_not_execute():
    """react + DENY：工具不执行，模型看到拒绝原因后继续。"""
    from strategy_research.core.runtimes.nodes.react import (
        ReactConfig,
        make_react_node,
    )

    node = make_react_node(
        _FakeClient(), _registry(),
        config=ReactConfig(
            ctx_factory=lambda tid, p: ToolContext(
                permission_evaluator=_evaluator("deny"), tool_call_id=tid,
            ),
        ),
    )
    node({"messages": [{"role": "user", "content": "go"}]})

    assert _RecordingTool.executed == []


def test_react_node_without_factory_keeps_legacy_behaviour():
    """无 ctx_factory：旧行为（无权限检查）——回归保护。"""
    from strategy_research.core.runtimes.nodes.react import (
        ReactConfig,
        make_react_node,
    )

    node = make_react_node(_FakeClient(), _registry(), config=ReactConfig())
    node({"messages": [{"role": "user", "content": "go"}]})

    assert len(_RecordingTool.executed) == 1
    # 无 factory → ctx 为 None（无权限检查），与迁移前一致
    assert _RecordingTool.executed[0].get("ctx") is None
