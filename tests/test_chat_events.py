# -*- coding: utf-8 -*-
"""3a.2：apps/chat 事件词表的 wire 绝对值 + 翻译规则测试。"""
from __future__ import annotations

from agentnodes.apps.chat.events import (
    ChatEvent,
    ENGINE_TO_PRODUCT,
    STREAM_PASSTHROUGH_KINDS,
    translate_engine_event,
)
from agentnodes.kernel.events.event_v2 import EventType


class TestChatEventVocabulary:
    def test_wire_names_match_eventtype_single_source(self):
        """产品事件名从 kernel EventType 取值（无字符串双源）。"""
        assert ChatEvent.MESSAGE_RECEIVED == EventType.MESSAGE_RECEIVED == "message_received"
        assert ChatEvent.AGENT_DONE == EventType.AGENT_DONE == "agent_done"
        assert ChatEvent.AGENT_ERROR == EventType.AGENT_ERROR == "error"
        assert ChatEvent.TEXT_DELTA == EventType.TEXT_DELTA == "text_delta"
        assert ChatEvent.TOOL_CALL == EventType.TOOL_CALL == "tool_call"

    def test_attempt_product_events_absolute_values(self):
        """attempt 生命周期产品事件 wire 名逐字固定（现网 bridge 断言）。"""
        assert ChatEvent.ATTEMPT_STARTED == "attempt.started"
        assert ChatEvent.ATTEMPT_COMPLETED == "attempt.completed"
        assert ChatEvent.ATTEMPT_FAILED == "attempt.failed"

    def test_engine_mapping_wire_parity(self):
        """引擎→前端映射与现网 mapping 逐字一致。"""
        assert ENGINE_TO_PRODUCT == {"tool_call": "tool_call", "tool_result": "tool_result"}
        assert translate_engine_event("tool_call") == "tool_call"
        assert translate_engine_event("tool_result") == "tool_result"

    def test_stream_passthrough_set_parity(self):
        """流式直通 8 个事件与现网集合一致（3a.0 wire 冻结表）。"""
        assert STREAM_PASSTHROUGH_KINDS == frozenset({
            "text_delta", "thinking_delta", "thinking_done", "stream.failed",
            "text.started", "text.ended",
            "thinking_start", "thinking_end",
        })
        for kind in STREAM_PASSTHROUGH_KINDS:
            assert translate_engine_event(kind) == kind

    def test_unmapped_event_returns_none(self):
        """无对应产品语义的引擎事件不转发（现网 bridge return 行为）。"""
        assert translate_engine_event("node.started") is None
        assert translate_engine_event("run.answer") is None
        assert translate_engine_event("steer.applied") is None


def test_bridge_uses_vocabulary_wire_unchanged(monkeypatch):
    """真桥接入验证：tool_call / text_delta 经词表翻译后 SSE 名与
    现网绝对值一致（3a.2 只挪规则、不改 wire 输出）。"""
    from agentnodes.interfaces.api.routers import chat as chat_router

    pushed = []
    monkeypatch.setattr(chat_router, "_push_sse",
                        lambda kind, data, sid: pushed.append((kind, data, sid)))
    sink_holder = {}

    class FakeGW:
        def add_sink(self, sink):
            sink_holder["sink"] = sink
            return lambda: None

    chat_router._install_chat_event_bridge(
        FakeGW(), "s1", {}, service=None, persist=False,
    )
    bridge = sink_holder["sink"]
    bridge("tool_call", {"name": "echo"})
    bridge("text_delta", {"text": "hi"})

    assert [(kind, sid) for kind, _, sid in pushed] == [
        ("tool_call", "s1"), ("text_delta", "s1"),
    ]
    # message_id 只在 holder 有值时 setdefault（现网语义）——此测试未触发
    # run.started，故 payload 不注入 message_id。
    assert pushed[0][1] == {"name": "echo"}
    assert pushed[1][1] == {"text": "hi"}


def test_live_send_turn_bridge_calls_translate_helper(monkeypatch):
    """C1 行为锚：send_turn 活桥的翻译决策真的经过 translate_engine_event。

    spy 拦截 + 驱动真桥（不经 3a.4 deprecated 面）：已知 kind 翻译转发、
    未知 kind 丢弃——helper 从"零生产调用"变为活的 interface。
    """
    from types import SimpleNamespace

    from agentnodes.apps.chat import send_turn as st

    calls: list[str] = []
    real = st.translate_engine_event

    def spy(kind: str):
        calls.append(kind)
        return real(kind)

    monkeypatch.setattr(st, "translate_engine_event", spy)

    pushed = []
    deps = SimpleNamespace(
        publish_sse=lambda kind, data, sid: pushed.append((sid, kind, data)),
        emit=lambda *a, **k: None,
        service=None,
    )

    class FakeGW:
        def __init__(self):
            self.sinks = []

        def add_sink(self, sink):
            self.sinks.append(sink)
            return lambda: None

    gw = FakeGW()
    st._event_bridge(
        gw, deps=deps, session_id="s1", assistant_mid_holder={},
        event_correlation_id="c1", terminal_observer=None, seed_user=None,
    )
    bridge = gw.sinks[0]
    bridge("text_delta", {"event_correlation_id": "c1", "text": "hi"})
    bridge("node.started", {"event_correlation_id": "c1"})

    # spy 证明两个 kind 都经过 helper（行为断言，非源码文本）
    assert calls == ["text_delta", "node.started"]
    # 直通转发、未知丢弃——wire 行为与 helper 契约一致
    assert [(kind, sid) for sid, kind, _ in pushed] == [("text_delta", "s1")]
    assert pushed[0][2]["text"] == "hi"
