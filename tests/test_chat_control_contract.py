"""HTTP contract baselines for chat submission and queue controls."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi.testclient import TestClient
from fastapi import HTTPException

from agentnodes.interfaces.api.app import create_app
from agentnodes.interfaces.api.auth_tokens import create_token
from agentnodes.kernel.orchestration import QueueMode, Receipt, RejectReason
from agentnodes.kernel.session.models import Attempt, AttemptId, AttemptStatus, SessionId


@pytest.fixture
def client(tmp_path, monkeypatch):
    import agentnodes.interfaces.api.routers.web_session as web_session
    import agentnodes.kernel.session.db as session_db

    db_path = str(tmp_path / "chat.db")
    monkeypatch.setattr(web_session, "_get_db_path", lambda: db_path)
    monkeypatch.setattr(session_db, "_get_db_path", lambda: db_path)
    c = TestClient(create_app())
    c.headers.update({"Authorization": f"Bearer {create_token('admin')}"})
    return c


def _new_session(client: TestClient) -> str:
    response = client.post(
        "/api/chat/session",
        json={"user_id": "anonymous", "title": "chat contract"},
    )
    assert response.status_code == 200, response.text
    return response.json()["id"]


def _install_service(monkeypatch, service):
    import agentnodes.interfaces.api.routers.chat as chat_router

    monkeypatch.setattr(chat_router, "_get_session_service", lambda: service)
    return chat_router


def test_send_async_response_and_execution_options(client, monkeypatch):
    session_id = _new_session(client)
    service = SimpleNamespace(
        store=SimpleNamespace(
            create_attempt=Mock(),
            delete_attempt=Mock(),
            get_attempt=Mock(return_value=SimpleNamespace(status="pending")),
        ),
        event_bus=SimpleNamespace(emit=Mock()),
    )
    _install_service(monkeypatch, service)
    import agentnodes.interfaces.api.routers.chat as chat_router
    gateway = SimpleNamespace(
        attempt_state=Mock(return_value="queued"),
        entry=SimpleNamespace(
            mailbox_for=Mock(return_value=SimpleNamespace(peek_depth=lambda: 2)),
        ),
    )
    submit = AsyncMock(return_value={"accepted": True})
    monkeypatch.setattr(chat_router, "_get_gateway", lambda: gateway)
    monkeypatch.setattr(chat_router, "_send_via_gateway", submit)
    monkeypatch.setattr(
        chat_router,
        "_gateway_chat_options",
        lambda body: {
            "mode": "plan",
            "system_prompt": "persona prompt",
            "role": "researcher",
            "allowed_tools": ["read_file"],
            "thinking": "on",
            "model": "model-x",
        },
    )

    response = client.post(
        "/api/chat/send_async",
        json={
            "session_id": session_id,
            "content": "question",
            "agent_id": "researcher",
            "mode": "plan",
            "model": "model-x",
            "thinking": "on",
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["message_id"] == body["user_message_id"]
    assert body["assistant_message_id"]
    assert body["event_id"] == ""
    assert body["status"] == "queued"
    assert body["attempt_id"]
    submit.assert_awaited_once_with(
        session_id,
        "question",
        queue_mode=QueueMode.FOLLOWUP,
        max_pending=10,
        user_message_id=body["user_message_id"],
        attempt_id=body["attempt_id"],
        assistant_message_id=body["assistant_message_id"],
        receipt_only=True,
        mode="plan",
        system_prompt="persona prompt",
        role="researcher",
        allowed_tools=["read_file"],
        thinking="on",
        model="model-x",
    )
    service.store.create_attempt.assert_called_once()
    emitted = [call.args[1] for call in service.event_bus.emit.call_args_list]
    assert emitted == ["attempt.created", "message_received", "queue_state"]
    created_attempt = service.store.create_attempt.call_args.args[0]
    assert created_attempt.attempt_id == body["attempt_id"]
    assert created_attempt.message_id == body["assistant_message_id"]
    assert created_attempt.model_override == "model-x"
    assert created_attempt.thinking == "on"


def test_gateway_chat_options_reuse_persona_plan_and_thinking_policy(monkeypatch):
    import agentnodes.interfaces.api.routers.chat as chat_router
    import agentnodes.kernel.session.loop_events as loop_events
    from agentnodes.interfaces.api.schemas.chat import ChatMessageRequest

    monkeypatch.setattr(
        loop_events,
        "_resolve_loop_prompt",
        lambda _prompt, persona: ("persona system", persona or "chat"),
    )
    monkeypatch.setattr(
        loop_events, "_thinking_instructions", lambda thinking: f"<{thinking}>",
    )
    monkeypatch.setattr(
        loop_events, "_plan_mode_allowed_tools", lambda mode: ["read_file"] if mode == "plan" else None,
    )

    options = chat_router._gateway_chat_options(ChatMessageRequest(
        session_id="s",
        content="q",
        agent_id="researcher",
        mode=" PLAN ",
        model="model-x",
        thinking="on",
    ))

    assert options == {
        "mode": "plan",
        "system_prompt": "persona system<on>",
        "role": "researcher",
        "allowed_tools": ["read_file"],
        "thinking": "on",
        "model": "model-x",
    }


def test_send_async_queue_full_preserves_429_detail(client, monkeypatch):
    session_id = _new_session(client)
    service = SimpleNamespace(
        store=SimpleNamespace(create_attempt=Mock(), delete_attempt=Mock()),
        event_bus=SimpleNamespace(emit=Mock()),
    )
    _install_service(monkeypatch, service)
    import agentnodes.interfaces.api.routers.chat as chat_router
    monkeypatch.setattr(
        chat_router,
        "_send_via_gateway",
        AsyncMock(side_effect=HTTPException(
            status_code=429,
            detail={"error": "queue_full", "limit": 10, "current_size": 10},
        )),
    )

    response = client.post(
        "/api/chat/send_async",
        json={"session_id": session_id, "content": "question"},
    )

    assert response.status_code == 429
    assert response.json()["detail"] == {
        "error": "queue_full",
        "limit": 10,
        "current_size": 10,
    }
    service.store.delete_attempt.assert_called_once()
    assert [
        call.args[1] for call in service.event_bus.emit.call_args_list
    ] == ["attempt.created"]


def test_cancel_and_resume_response_shapes(client, monkeypatch):
    session_id = _new_session(client)
    gateway = SimpleNamespace(
        attempt_state=Mock(return_value="running"),
        cancel_session=Mock(return_value=True),
        resume_session=Mock(return_value=True),
    )
    import agentnodes.interfaces.api.routers.chat as chat_router
    monkeypatch.setattr(chat_router, "_get_gateway", lambda: gateway)

    cancelled = client.post(
        "/api/chat/cancel",
        json={"session_id": session_id, "attempt_id": "attempt-1"},
    )
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json() == {
        "status": "cancelled",
        "session_id": session_id,
        "attempt_id": "attempt-1",
    }

    resumed = client.post(
        "/api/chat/queue/resume",
        json={"session_id": session_id},
    )
    assert resumed.status_code == 200, resumed.text
    assert resumed.json() == {"ok": True, "session_id": session_id}
    gateway.cancel_session.assert_called_once_with(session_id)
    gateway.resume_session.assert_called_once_with(session_id)


@pytest.mark.parametrize(
    ("state", "expected_actions"),
    [
        (
            {"active": False, "pending": False, "paused": False},
            ["send"],
        ),
        (
            {"active": True, "pending": False, "paused": False},
            ["cancel"],
        ),
        (
            {"active": False, "pending": True, "paused": True},
            ["cancel", "resume"],
        ),
    ],
)
def test_available_actions_use_gateway_chat_state(
    client, monkeypatch, state, expected_actions,
):
    session_id = _new_session(client)
    gateway = SimpleNamespace(
        chat_session_state=Mock(return_value=state),
    )
    import agentnodes.interfaces.api.routers.chat as chat_router
    monkeypatch.setattr(chat_router, "_get_gateway", lambda: gateway)

    response = client.get(f"/api/chat/session/{session_id}/available_actions")

    assert response.status_code == 200, response.text
    assert [
        action["name"] for action in response.json()["actions"]
    ] == expected_actions
    gateway.chat_session_state.assert_called_once_with(session_id)


def test_attempt_reload_uses_live_gateway_state(client, monkeypatch):
    session_id = _new_session(client)
    persisted = [
        SimpleNamespace(
            attempt_id="queued-1",
            message_id="message-1",
            prompt="queued prompt",
            created_at="2026-01-01T00:00:00Z",
            error=None,
            status="pending",
        ),
        SimpleNamespace(
            attempt_id="running-1",
            message_id="message-2",
            prompt="running prompt",
            created_at="2026-01-01T00:00:01Z",
            error=None,
            status="running",
        ),
        SimpleNamespace(
            attempt_id="stale-1",
            message_id="message-3",
            prompt="stale prompt",
            created_at="2026-01-01T00:00:02Z",
            error=None,
            status="pending",
        ),
        SimpleNamespace(
            attempt_id="failed-1",
            message_id="message-4",
            prompt="failed prompt",
            created_at="2026-01-01T00:00:03Z",
            error="provider failed",
            status="failed",
        ),
    ]
    store = SimpleNamespace(
        list_attempts_by_status=Mock(return_value=persisted),
    )
    service = SimpleNamespace(store=store)
    _install_service(monkeypatch, service)
    import agentnodes.interfaces.api.routers.chat as chat_router
    states = {
        "queued-1": "queued",
        "running-1": "running",
        "stale-1": None,
    }
    gateway = SimpleNamespace(
        attempt_state=Mock(side_effect=lambda _session, attempt: states[attempt]),
    )
    monkeypatch.setattr(chat_router, "_get_gateway", lambda: gateway)

    response = client.get(
        "/api/chat/attempts",
        params={"session_id": session_id},
    )

    assert response.status_code == 200, response.text
    assert response.json()["attempts"] == [
        {
            "attempt_id": "queued-1",
            "message_id": "message-1",
            "status": "queued",
            "prompt": "queued prompt",
            "created_at": "2026-01-01T00:00:00Z",
            "error": "",
        },
        {
            "attempt_id": "running-1",
            "message_id": "message-2",
            "status": "running",
            "prompt": "running prompt",
            "created_at": "2026-01-01T00:00:01Z",
            "error": "",
        },
        {
            "attempt_id": "failed-1",
            "message_id": "message-4",
            "status": "failed",
            "prompt": "failed prompt",
            "created_at": "2026-01-01T00:00:03Z",
            "error": "provider failed",
        },
    ]


def test_gateway_event_bridge_updates_attempt_and_assistant_message(monkeypatch):
    import agentnodes.interfaces.api.routers.chat as chat_router

    session_id = "session-1"
    attempt = Attempt(
        attempt_id=AttemptId("attempt-1"),
        session_id=SessionId(session_id),
        status=AttemptStatus.PENDING,
    )
    store = SimpleNamespace(
        get_attempt=Mock(return_value=attempt),
        update_attempt=Mock(),
    )
    service = SimpleNamespace(
        store=store,
        event_bus=SimpleNamespace(emit=Mock()),
    )
    sink_holder = {}
    unsubscribed = []
    gateway = SimpleNamespace(
        add_sink=lambda sink: (
            sink_holder.update({"sink": sink})
            or (lambda: unsubscribed.append(True))
        ),
    )
    sse = []
    monkeypatch.setattr(
        chat_router,
        "_push_sse",
        lambda kind, data, _session_id: sse.append((kind, data)),
    )
    message_id_holder = {}
    chat_router._install_chat_event_bridge(
        gateway,
        session_id,
        message_id_holder,
        service=service,
        persist=True,
    )

    sink_holder["sink"]("run.started", {
        "run_id": "run-1",
        "attempt_id": "attempt-1",
        "assistant_message_id": "assistant-1",
    })
    assert attempt.status is AttemptStatus.RUNNING
    sink_holder["sink"]("run.answer", {
        "run_id": "run-1",
        "attempt_id": "attempt-1",
        "assistant_message_id": "assistant-1",
        "content": "answer",
    })

    assert attempt.status is AttemptStatus.COMPLETED
    assert attempt.summary == "answer"
    assert message_id_holder["id"] == "assistant-1"
    assert service.event_bus.emit.call_args.args[1] == "assistant_message"
    assert service.event_bus.emit.call_args.args[2]["message_id"] == "assistant-1"
    assert ("agent_done", {
        "message_id": "assistant-1",
        "status": "completed",
    }) in sse
    assert unsubscribed == [True]


@pytest.mark.asyncio
async def test_gateway_receipt_path_does_not_run_inline_without_consumer(monkeypatch):
    import agentnodes.interfaces.api.routers.chat as chat_router

    entry = SimpleNamespace(submit=Mock(return_value=Receipt.ok("session-1")))
    gateway = SimpleNamespace(
        entry=entry,
        consumer_running=Mock(return_value=False),
        add_sink=Mock(return_value=lambda: None),
        run_once=Mock(side_effect=AssertionError("receipt path must not run inline")),
    )
    service = SimpleNamespace(
        store=SimpleNamespace(get_messages=Mock(return_value=[])),
        _convert_messages_to_history=Mock(return_value=[]),
    )
    monkeypatch.setattr(chat_router, "_get_gateway", lambda: gateway)
    monkeypatch.setattr(chat_router, "_get_session_service", lambda: service)
    monkeypatch.setattr(chat_router, "_build_llm_config", lambda: None)

    result = await chat_router._send_via_gateway(
        "session-1",
        "question",
        queue_mode=QueueMode.FOLLOWUP,
        max_pending=10,
        attempt_id="attempt-1",
        assistant_message_id="assistant-1",
        receipt_only=True,
    )

    assert result["accepted"] is True
    gateway.run_once.assert_not_called()
    args, kwargs = entry.submit.call_args
    request = args[0]
    assert request.queue_mode is QueueMode.FOLLOWUP
    assert request.payload["attempt_id"] == "attempt-1"
    assert kwargs == {"max_pending": 10}


@pytest.mark.asyncio
async def test_gateway_queue_full_maps_to_429_with_current_size(monkeypatch):
    import agentnodes.interfaces.api.routers.chat as chat_router

    entry = SimpleNamespace(submit=Mock(return_value=Receipt.reject(
        RejectReason.QUEUE_FULL,
        current_size=10,
    )))
    gateway = SimpleNamespace(
        entry=entry,
        consumer_running=Mock(return_value=False),
        add_sink=Mock(return_value=lambda: None),
    )
    service = SimpleNamespace(
        store=SimpleNamespace(get_messages=Mock(return_value=[])),
        _convert_messages_to_history=Mock(return_value=[]),
    )
    monkeypatch.setattr(chat_router, "_get_gateway", lambda: gateway)
    monkeypatch.setattr(chat_router, "_get_session_service", lambda: service)
    monkeypatch.setattr(chat_router, "_build_llm_config", lambda: None)

    with pytest.raises(HTTPException) as exc_info:
        await chat_router._send_via_gateway(
            "session-1",
            "question",
            queue_mode=QueueMode.FOLLOWUP,
            max_pending=10,
            receipt_only=True,
        )

    assert exc_info.value.status_code == 429
    assert exc_info.value.detail == {
        "error": "queue_full",
        "limit": 10,
        "current_size": 10,
    }
