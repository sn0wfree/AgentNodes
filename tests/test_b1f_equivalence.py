"""B1-f-1: remaining equivalence evidence for the react carrier.

B1-a/b/c/d already pin system prompt, context injection, compaction and the
permission gate. This file closes the last three items from the design
checklist:

6. tool events (tool.started -> tool.completed) ordering
7. multi-turn history ordering (history first, current input last)
8. error paths (LLM failure, tool exception, engine failure)
"""
from __future__ import annotations

import json
from types import SimpleNamespace

from strategy_research.core.agent.tools import BaseTool, ToolRegistry
from strategy_research.core.gateway.session_controller import SessionController
from strategy_research.kernel.orchestration import (
    EntryPoint,
    SourceKind,
    WorkKind,
    WorkRequest,
)


class _EchoTool(BaseTool):
    name = "echo"
    description = "echo"
    parameters = {}

    def execute(self, **kwargs):
        return json.dumps({"status": "ok"})


class _RaisingTool(BaseTool):
    name = "boom"
    description = "raises"
    parameters = {}

    def execute(self, **kwargs):
        raise ValueError("tool exploded")


class _Resp:
    def __init__(self, content="", tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls or []
        self.usage = None

    def has_tool_calls(self):
        return bool(self.tool_calls)


class _ScriptedClient:
    """Replays a script; records every message list it saw."""

    def __init__(self, script):
        self._script = list(script)
        self.calls: list[list[dict]] = []

    def chat(self, messages, tools=None):
        self.calls.append([dict(m) for m in messages])
        if self._script:
            return self._script.pop(0)
        return _Resp(content="done")


def _registry(*tools) -> ToolRegistry:
    reg = ToolRegistry()
    for t in tools:
        reg.register(t)
    return reg


def _controller(client, registry, sink) -> SessionController:
    return SessionController(
        EntryPoint(sink=sink), client=client, registry=registry,
        sink=sink, max_iterations=3,
    )


def _chat(text: str, history=None) -> WorkRequest:
    payload = {"text": text}
    if history:
        payload["history"] = history
    return WorkRequest(
        kind=WorkKind.CHAT, source=SourceKind.API_CHAT,
        session_key="chat:s1", payload=payload,
    )


# ── 6. 工具事件顺序 ────────────────────────────────────────


def test_tool_events_emitted_in_order():
    """tool.started 必在 tool.completed 之前，且携带工具名。"""
    from strategy_research.kernel.runtimes.nodes.react import (
        ReactConfig,
        make_react_node,
    )

    events: list[tuple[str, dict]] = []
    tc = SimpleNamespace(id="c1", name="echo", arguments={})
    client = _ScriptedClient([_Resp(tool_calls=[tc]), _Resp(content="fin")])

    node = make_react_node(
        client, _registry(_EchoTool()),
        sink=lambda k, d: events.append((k, d)),
        config=ReactConfig(max_iterations=3),
    )
    node({"messages": [{"role": "user", "content": "go"}]})

    tool_events = [k for k, _ in events if k.startswith("tool.")]
    assert tool_events == ["tool.started", "tool.completed"]
    assert events[0][1]["tool"] == "echo"
    assert events[1][1]["tool"] == "echo"
    assert events[1][1]["result_chars"] > 0


# ── 7. 多轮历史顺序 ────────────────────────────────────────


def test_history_precedes_current_turn():
    """history 在前、当前输入在后（P5-1 契约，本轮补强）。"""
    seen: dict = {}

    class _Recorder:
        def chat(self, messages, tools=None):
            seen["messages"] = [dict(m) for m in messages]
            return _Resp(content="ok")

    app = _Recorder()
    sink_events: list = []
    ctrl = _controller(app, _registry(_EchoTool()), lambda k, d: sink_events.append((k, d)))
    ctrl._ep.submit(_chat("current", history=[
        {"role": "user", "content": "older"},
        {"role": "assistant", "content": "reply"},
    ]))
    ctrl.run_pending()

    contents = [m["content"] for m in seen["messages"] if m["role"] != "system"]
    assert contents == ["older", "reply", "current"]


def test_malformed_history_entries_are_filtered():
    """非法 history 项被过滤，不影响本轮。"""
    seen: dict = {}

    class _Recorder:
        def chat(self, messages, tools=None):
            seen["messages"] = [dict(m) for m in messages]
            return _Resp(content="ok")

    app = _Recorder()
    ctrl = _controller(app, _registry(_EchoTool()), lambda k, d: None)
    ctrl._ep.submit(_chat("now", history=[
        "not-a-dict",
        {"role": "user"},                      # 缺 content
        {"role": "user", "content": "valid"},
    ]))
    ctrl.run_pending()

    contents = [m["content"] for m in seen["messages"] if m["role"] != "system"]
    assert contents == ["valid", "now"]


# ── 8. 错误路径 ────────────────────────────────────────────


def test_llm_failure_settles_with_error_not_crash():
    """LLM 抛错：控制器捕获，run.settled{ok:False}，不向上抛。"""
    class _BoomClient:
        def chat(self, messages, tools=None):
            raise RuntimeError("provider down")

    events: list[tuple[str, dict]] = []
    ctrl = _controller(_BoomClient(), _registry(_EchoTool()), lambda k, d: events.append((k, d)))
    ctrl._ep.submit(_chat("hi"))
    results = ctrl.run_pending()

    assert len(results) == 1
    assert results[0].ok is False
    assert "provider down" in (results[0].error or "")
    kinds = [k for k, _ in events]
    assert "run.failed" in kinds
    settled = [d for k, d in events if k == "run.settled"]
    assert settled[-1]["ok"] is False


def test_tool_exception_becomes_structured_error():
    """工具抛错：结果是结构化 error JSON，循环继续到最终答案。"""
    from strategy_research.kernel.runtimes.nodes.react import (
        ReactConfig,
        make_react_node,
    )

    tc = SimpleNamespace(id="c1", name="boom", arguments={})
    client = _ScriptedClient([_Resp(tool_calls=[tc]), _Resp(content="adapted")])

    node = make_react_node(
        client, _registry(_RaisingTool()), config=ReactConfig(max_iterations=3),
    )
    out = node({"messages": [{"role": "user", "content": "go"}]})

    assert out["answer"] == "adapted"
    tool_msgs = [m for m in out["messages"] if m.get("role") == "tool"]
    assert len(tool_msgs) == 1
    payload = json.loads(tool_msgs[0]["content"])
    assert payload["status"] == "error"
    assert "exploded" in payload["error"]


def test_engine_failure_does_not_stop_later_turns():
    """一个 turn 崩了，后续 turn 照常（控制器不拖垮队列）。"""
    calls = {"n": 0}

    class _Flaky:
        def chat(self, messages, tools=None):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("first fails")
            return _Resp(content="second ok")

    events: list = []
    ctrl = _controller(_Flaky(), _registry(_EchoTool()), lambda k, d: events.append((k, d)))
    ctrl._ep.submit(_chat("one"))
    ctrl._ep.submit(_chat("two"))
    results = ctrl.run_pending()

    assert len(results) == 2
    assert results[0].ok is False
    assert results[1].ok is True
    assert results[1].answer == "second ok"
