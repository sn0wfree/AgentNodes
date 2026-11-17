"""B1-f-1: 剩余等价性证据(controller 级)。

**V3-c**:react 节点已删除;原「6. 工具事件顺序」的等价覆盖移居
``tests/test_agent_loop_node.py``(AgentLoop 词表 tool_call→tool_result)。
本文件保留多轮历史顺序与错误路径——两条 controller 契约,与引擎无关。
"""
from __future__ import annotations

from agentnodes.kernel.gateway.session_controller import SessionController
from agentnodes.kernel.orchestration import (
    EntryPoint,
    SourceKind,
    WorkKind,
    WorkRequest,
)


class _Resp:
    def __init__(self, content="", tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls or []
        self.usage = None
        self.finish_reason = "stop"

    def has_tool_calls(self):
        return bool(self.tool_calls)


class _ScriptedClient:
    """Replays a script; records every message list it saw."""

    def __init__(self, script):
        self._script = list(script)
        self.calls: list[list[dict]] = []

    def chat(self, messages, tools=None, **kw):
        self.calls.append([dict(m) for m in messages])
        if self._script:
            return self._script.pop(0)
        return _Resp(content="done")


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


def _registry():
    """**V3-c**:AgentLoop 契约的最小真注册表(空工具集即可——本文件的
    client 全部直接回文本,不发起工具调用)。"""
    from agentnodes.kernel.agent.tools.core import ToolRegistry

    return ToolRegistry()


# ── 7. 多轮历史顺序 ────────────────────────────────────────


def test_history_precedes_current_turn():
    """history 在前、当前输入在后(P5-1 契约,本轮补强)。"""
    seen: dict = {}

    class _Recorder:
        def chat(self, messages, tools=None, **kw):
            seen["messages"] = [dict(m) for m in messages]
            return _Resp(content="ok")

    sink_events: list = []
    ctrl = _controller(_Recorder(), _registry(), lambda k, d: sink_events.append((k, d)))
    ctrl._ep.submit(_chat("current", history=[
        {"role": "user", "content": "older"},
        {"role": "assistant", "content": "reply"},
    ]))
    ctrl.run_pending()

    contents = [m["content"] for m in seen["messages"] if m["role"] != "system"]
    assert contents == ["older", "reply", "current"]


def test_malformed_history_entries_are_filtered():
    """非法 history 项被过滤,不影响本轮。"""
    seen: dict = {}

    class _Recorder:
        def chat(self, messages, tools=None, **kw):
            seen["messages"] = [dict(m) for m in messages]
            return _Resp(content="ok")

    ctrl = _controller(_Recorder(), _registry(), lambda k, d: None)
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
    """LLM 抛错:控制器捕获,run.settled{ok:False},不向上抛。"""
    class _BoomClient:
        def chat(self, messages, tools=None, **kw):
            raise RuntimeError("provider down")

    events: list[tuple[str, dict]] = []
    ctrl = _controller(_BoomClient(), _registry(), lambda k, d: events.append((k, d)))
    ctrl._ep.submit(_chat("hi"))
    results = ctrl.run_pending()

    assert len(results) == 1
    assert results[0].ok is False
    assert "provider down" in (results[0].error or "")
    kinds = [k for k, _ in events]
    assert "run.failed" in kinds
    settled = [d for k, d in events if k == "run.settled"]
    assert settled[-1]["ok"] is False


def test_engine_failure_does_not_stop_later_turns():
    """一个 turn 崩了,后续 turn 照常(控制器不拖垮队列)。"""
    calls = {"n": 0}

    class _Flaky:
        def chat(self, messages, tools=None, **kw):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("first fails")
            return _Resp(content="second ok")

    events: list = []
    ctrl = _controller(_Flaky(), _registry(), lambda k, d: events.append((k, d)))
    ctrl._ep.submit(_chat("one"))
    ctrl._ep.submit(_chat("two"))
    results = ctrl.run_pending()

    assert len(results) == 2
    assert results[0].ok is False
    assert results[1].ok is True
    assert results[1].answer == "second ok"
