# -*- coding: utf-8 -*-
"""3a.1 探针：apps/chat.messages 的逐字段断言（四种 message_received 形状）。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from agentnodes.apps.chat.messages import (
    UserMessageRecord,
    build_message_received,
    record_user_message,
)


class TestBuildMessageReceived:
    def test_shape3_gateway_sync_no_assistant_id(self):
        """现网形状 3（_send_via_gateway sync/wait）：message_id /
        user_message_id / role / content / created_at，无 assistant 占位。"""
        sid, payload, meta = build_message_received(
            session_id="s1", message_id="m1", content="hello",
            created_at=123.0,
        )
        assert sid == "s1"
        assert payload == {
            "message_id": "m1",
            "user_message_id": "m1",
            "role": "user",
            "content": "hello",
            "created_at": 123.0,
        }
        assert meta == {"assistant_message_id": ""}

    def test_shape1_send_async_with_queue_fields(self):
        """现网形状 1（send_async）：assistant/attempt/queue 字段经
        **extra 透传，构造器不合并、不删减。"""
        sid, payload, meta = build_message_received(
            session_id="s1", message_id="m1", content="q",
            user_message_id="u1", assistant_message_id="a1",
            attempt_id="at1", status="queued",
            queue_position=2, queue_length=10, created_at=123.0,
        )
        assert payload == {
            "message_id": "m1", "user_message_id": "u1",
            "assistant_message_id": "a1", "role": "user", "content": "q",
            "created_at": 123.0, "attempt_id": "at1", "status": "queued",
            "queue_position": 2, "queue_length": 10,
        }
        assert meta["assistant_message_id"] == "a1"

    def test_shape4_slash_no_created_at(self):
        """现网形状 4（slash_common）：assistant_message_id + status=done，
        **无 created_at**。"""
        sid, payload, meta = build_message_received(
            session_id="s1", message_id="m1", content="/compact",
            user_message_id="u1", assistant_message_id="a1", status="done",
        )
        assert "created_at" not in payload
        assert payload["assistant_message_id"] == "a1"
        assert payload["status"] == "done"

    def test_shape2_sse_seed_processing(self):
        """现网形状 2（桥 SSE seed）：seed_user 展开 + assistant_message_id
        + status=processing；SE E 适配由调用方负责，构造器只保证字段形状。"""
        sid, payload, meta = build_message_received(
            session_id="s1", message_id="m1", content="hi",
            user_message_id="u1", assistant_message_id="a1",
            status="processing",
        )
        assert payload["status"] == "processing"
        assert payload["assistant_message_id"] == "a1"


class TestRecordUserMessage:
    def test_emits_expected_event(self):
        emitted = []
        rec = record_user_message(
            lambda sid, kind, payload: emitted.append((sid, kind, payload)),
            session_id="s1", message_id="m1", content="hello",
            created_at=1.0,
        )
        assert isinstance(rec, UserMessageRecord)
        assert rec.message_id == "m1" and rec.user_message_id == "m1"
        assert emitted == [("s1", "message_received", {
            "message_id": "m1", "user_message_id": "m1", "role": "user",
            "content": "hello", "created_at": 1.0,
        })]

    def test_emit_failure_is_best_effort(self):
        """落库失败不抛（现网所有发射点的 best-effort 语义）。"""
        def boom(*args):
            raise RuntimeError("event store down")

        rec = record_user_message(
            boom, session_id="s1", message_id="m1", content="hello",
        )
        assert rec.message_id == "m1"


@pytest.mark.asyncio
async def test_wired_sync_path_emits_same_payload(monkeypatch):
    """真实接入验证：_send_via_gateway sync 路径经新构造器后，
    message_received payload 与基线形状 3 逐字段一致。"""
    from agentnodes.interfaces.api.routers import chat as chat_router
    from agentnodes.kernel.gateway.gateway import Gateway

    class Engine:
        def run(self, state):
            return {"answer": state["messages"][-1]["content"]}

    gateway = Gateway(client=object(), engine_factory=Engine)
    emitted = []
    service = type("S", (), {})()
    service.store = type("Store", (), {
        "get_messages": lambda *a, **k: [],
        "get_attempt": lambda *a, **k: None,
    })()
    service.load_history = lambda *a, **k: []
    service.update_attempt_status = lambda *a, **k: None
    bus = type("Bus", (), {})()
    # 用 staticmethod 避免 bound-method 把 self 混进 emit 参数
    bus.emit = staticmethod(lambda sid, kind, payload: emitted.append((sid, kind, payload)))
    service.event_bus = bus
    monkeypatch.setattr(chat_router, "_get_gateway", lambda: gateway)
    monkeypatch.setattr(chat_router, "_get_session_service", lambda: service)
    monkeypatch.setattr(chat_router, "_build_llm_config", lambda: None)
    monkeypatch.setattr(chat_router, "_push_sse", lambda *a: None)

    await chat_router._send_via_gateway("s1", "hello")

    received = [payload for sid, kind, payload in emitted
                if kind == "message_received"]
    assert len(received) == 1
    assert set(received[0]) == {
        "message_id", "user_message_id", "role", "content", "created_at",
    }
    assert received[0]["role"] == "user" and received[0]["content"] == "hello"
