"""P5-1b 验收：流式输出 + 工具链（删旧分支的前置）。

依据 docs/design/p5-1b-streaming-tools-design-20261027.md §5。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from agentnodes.kernel.runtimes.nodes.llm import make_llm_node
from agentnodes.kernel.runtimes.nodes.react import ReactConfig, make_react_node

# ── Fakes ────────────────────────────────────────────────


@dataclass
class FakeChunk:
    delta_content: str = ""
    delta_thinking: str = ""
    delta_tool_calls: list[dict] = field(default_factory=list)
    usage: dict | None = None
    finish_reason: str | None = None


@dataclass
class FakeResp:
    content: str = ""
    usage: dict | None = None
    tool_calls: list = field(default_factory=list)

    def has_tool_calls(self) -> bool:
        return bool(self.tool_calls)


@dataclass
class FakeTC:
    id: str
    name: str
    arguments: dict


class StreamingClient:
    """按预设 chunk 序列流式返回；chat() 返回聚合结果。"""

    def __init__(self, chunks: list[FakeChunk]) -> None:
        self.chunks = chunks
        self.stream_calls: list[list[dict]] = []
        self.chat_calls: list[list[dict]] = []

    def stream(self, messages, tools=None, **kwargs):
        self.stream_calls.append([dict(m) for m in messages])
        return iter(self.chunks)

    def chat(self, messages, tools=None, **kwargs):
        self.chat_calls.append([dict(m) for m in messages])
        content = "".join(c.delta_content for c in self.chunks)
        usage = next((c.usage for c in self.chunks if c.usage), None)
        return FakeResp(content=content, usage=usage)


class ToolThenAnswerClient:
    """首轮流式返回工具调用，次轮返回答案。"""

    def __init__(self) -> None:
        self.n = 0

    def stream(self, messages, tools=None, **kwargs):
        self.n += 1
        if self.n == 1:
            # 分三片，arguments 跨 chunk
            return iter([
                FakeChunk(delta_tool_calls=[
                    {"index": 0, "id": "call_1", "type": "function",
                     "function": {"name": "calc", "arguments": ""}}
                ]),
                FakeChunk(delta_tool_calls=[
                    {"index": 0, "function": {"arguments": '{"expr"'}}
                ]),
                FakeChunk(delta_tool_calls=[
                    {"index": 0, "function": {"arguments": ': "2+2"}'}}
                ], finish_reason="tool_calls"),
            ])
        return iter([FakeChunk(delta_content="答案是 4")])

    def chat(self, messages, tools=None, **kwargs):
        return FakeResp(content="unused")


class FakeRegistry:
    def __init__(self) -> None:
        self.executed: list[tuple[str, dict]] = []

    def get_definitions(self):
        return [{"type": "function", "function": {"name": "calc", "parameters": {}}}]

    def execute(self, name: str, params: dict) -> str:
        self.executed.append((name, params))
        return '{"result": 4}'


# ── llm 节点流式 ─────────────────────────────────────────


def test_llm_node_streams_text_deltas():
    events: list[tuple[str, dict]] = []
    client = StreamingClient([
        FakeChunk(delta_content="你"),
        FakeChunk(delta_content="好"),
        FakeChunk(usage={"total_tokens": 5}),
    ])
    node = make_llm_node(client, stream=True, sink=lambda k, d: events.append((k, d)))
    out = node({"messages": [{"role": "user", "content": "hi"}], "run_id": "r1"})

    assert out["answer"] == "你好"
    assert [d["delta"] for k, d in events if k == "text_delta"] == ["你", "好"]
    assert out["usage"]["total_tokens"] == 5
    assert client.chat_calls == []  # 未走非流式


def test_llm_node_stream_shape_matches_non_stream():
    """流式与非流式返回值形状一致（下游无需分支）。"""
    chunks = [FakeChunk(delta_content="abc", usage={"total_tokens": 2})]
    client = StreamingClient(chunks)
    streamed = make_llm_node(client, stream=True)({"messages": []})
    plain = make_llm_node(client, stream=False)({"messages": []})
    assert set(streamed) == set(plain)
    assert streamed["answer"] == plain["answer"] == "abc"


def test_llm_node_thinking_delta_emitted():
    events: list[tuple[str, dict]] = []
    client = StreamingClient([
        FakeChunk(delta_thinking="思考中"),
        FakeChunk(delta_content="答"),
    ])
    node = make_llm_node(client, stream=True, sink=lambda k, d: events.append((k, d)))
    node({"messages": []})
    kinds = [k for k, _ in events]
    assert "thinking_delta" in kinds
    assert "thinking_done" in kinds


def test_llm_node_non_stream_unchanged():
    """stream=False 走 chat()，行为与改造前一致。"""
    client = StreamingClient([FakeChunk(delta_content="x")])
    node = make_llm_node(client, stream=False)
    node({"messages": []})
    assert len(client.chat_calls) == 1
    assert client.stream_calls == []


def test_llm_node_stream_failure_keeps_partial():
    """流中断保留已收内容。"""
    events: list[tuple[str, dict]] = []

    class Broken:
        def stream(self, messages, **kw):
            def gen():
                yield FakeChunk(delta_content="前半")
                raise RuntimeError("connection lost")
            return gen()

    node = make_llm_node(Broken(), stream=True, sink=lambda k, d: events.append((k, d)))
    out = node({"messages": []})
    assert out["answer"] == "前半"
    assert any(k == "stream.failed" for k, _ in events)


# ── react 节点流式 + 工具聚合 ────────────────────────────


def test_react_stream_aggregates_fragmented_tool_calls():
    """arguments 跨 chunk 必须拼接，不能覆盖。"""
    registry = FakeRegistry()
    client = ToolThenAnswerClient()
    node = make_react_node(
        client, registry,
        config=ReactConfig(max_iterations=3, stream=True),
    )
    out = node({"messages": [{"role": "user", "content": "2+2"}]})

    assert registry.executed == [("calc", {"expr": "2+2"})]
    assert out["answer"] == "答案是 4"
    assert out["stop_reason"] == "completed"


def test_react_stream_emits_text_after_tool():
    events: list[tuple[str, dict]] = []
    node = make_react_node(
        ToolThenAnswerClient(), FakeRegistry(),
        sink=lambda k, d: events.append((k, d)),
        config=ReactConfig(max_iterations=3, stream=True),
    )
    node({"messages": []})
    assert [d["delta"] for k, d in events if k == "text_delta"] == ["答案是 4"]
    # 工具事件仍由 react 节点发（平台语义）
    assert any(k == "tool.started" for k, _ in events)


def test_react_stream_no_tools_degrades_to_single_call():
    """无工具调用时退化为单次 LLM（与非流式同形）。"""
    client = StreamingClient([FakeChunk(delta_content="直接回答")])
    node = make_react_node(
        client, FakeRegistry(), config=ReactConfig(stream=True),
    )
    out = node({"messages": []})
    assert out["answer"] == "直接回答"
    assert len(client.stream_calls) == 1


def test_react_non_stream_still_works():
    """stream=False 保持原有行为。"""
    class PlainClient:
        def __init__(self) -> None:
            self.calls = 0

        def chat(self, messages, tools=None, **kw):
            self.calls += 1
            return FakeResp(content="plain")

    c = PlainClient()
    node = make_react_node(c, FakeRegistry(), config=ReactConfig(stream=False))
    out = node({"messages": []})
    assert out["answer"] == "plain"
    assert c.calls == 1


# ── 事件词表映射（chat 层）───────────────────────────────


def test_event_bridge_maps_platform_events_to_ui_vocabulary(monkeypatch):
    """tool.started -> tool_call；text_delta 直通。"""
    import agentnodes.interfaces.api.routers.chat as chat_mod

    pushed: list[tuple[str, dict, str]] = []
    monkeypatch.setattr(
        chat_mod, "_push_sse",
        lambda kind, data, sid: pushed.append((kind, data, sid)),
    )

    class FakeGw:
        def __init__(self) -> None:
            self.bridge = None

        def install_sink(self, sink):
            self.bridge = sink

    gw = FakeGw()
    chat_mod._install_chat_event_bridge(gw, "sess", {"id": "m1"})
    assert gw.bridge is not None

    gw.bridge("tool.started", {"run_id": "r", "tool": "calc"})
    gw.bridge("tool.completed", {"run_id": "r", "tool": "calc", "result_chars": 9})
    gw.bridge("text_delta", {"run_id": "r", "delta": "x"})
    gw.bridge("node.started", {"node": "react"})  # 平台事件，不透传

    kinds = [p[0] for p in pushed]
    assert kinds == ["tool_call", "tool_result", "text_delta"]
    assert all(p[1].get("message_id") == "m1" for p in pushed)


def test_event_bridge_push_failure_does_not_raise(monkeypatch):
    import agentnodes.interfaces.api.routers.chat as chat_mod

    def boom(*a, **k):
        raise RuntimeError("sse down")

    monkeypatch.setattr(chat_mod, "_push_sse", boom)

    class FakeGw:
        def install_sink(self, sink):
            self.sink = sink

    gw = FakeGw()
    chat_mod._install_chat_event_bridge(gw, "s", {})
    gw.sink("text_delta", {"delta": "x"})  # 不应抛出
