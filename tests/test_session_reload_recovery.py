"""Tests for reload recovery: GET /api/chat/attempts + list_active_attempts.

Covers:
- running attempt (in-memory _active_loops) reported as "running"
- pending attempt with a live consumer queue reported as "queued"
- zombie rows filtered: running not in _active_loops, pending without a
  live queue (both survive only across a server restart)
- oldest-first ordering
- HTTP endpoint shape + 404 for unowned session

See docs/archive/undated/streaming-reload-recovery.md.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from fastapi.testclient import TestClient

from strategy_research.interfaces.api.app import create_app
from strategy_research.interfaces.api.auth_tokens import create_token
from strategy_research.interfaces.api.session.models import Attempt, AttemptStatus


@pytest.fixture
def temp_db(monkeypatch, tmp_path):
    from strategy_research.kernel.agent.memory_manager import resolve_session_db_path

    workspace = tmp_path
    workspace.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("SR_WORKSPACE_PATH", str(workspace))
    return resolve_session_db_path()


@pytest.fixture
def session_service(temp_db):
    from strategy_research.interfaces.api.routers.web_session import _get_db
    from tests._fixtures.legacy_events import EventBus
    from strategy_research.interfaces.api.session.service import SessionService
    from strategy_research.interfaces.api.session.store import SessionStore
    _get_db().close()  # ensure schema exists on temp_db

    bus = EventBus()
    try:
        bus.set_loop(asyncio.get_event_loop())
    except Exception:
        pass
    return SessionService(store=SessionStore(temp_db), event_bus=bus)


def _insert_attempt(store, session_id: str, status: AttemptStatus, created_at: str, prompt="p"):
    attempt = Attempt(
        session_id=session_id,
        prompt=prompt,
        message_id=str(uuid.uuid4()),
        status=status,
        created_at=created_at,
    )
    store.create_attempt(attempt)
    return attempt


# ── Unit: list_active_attempts ────────────────────────────────────────


def test_running_attempt_reported(session_service):
    sid = "sess-r"
    a = _insert_attempt(session_service.store, sid, AttemptStatus.RUNNING, "2026-01-01T00:00:01")
    session_service._active_loops[a.attempt_id] = asyncio.Task

    out = session_service.list_active_attempts(sid)
    assert len(out) == 1
    assert out[0]["attempt_id"] == a.attempt_id
    assert out[0]["message_id"] == a.message_id
    assert out[0]["status"] == "running"


def test_pending_attempt_queued_when_queue_alive(session_service):
    sid = "sess-q"
    a = _insert_attempt(session_service.store, sid, AttemptStatus.PENDING, "2026-01-01T00:00:01")
    session_service._session_queues[sid] = asyncio.Queue()

    out = session_service.list_active_attempts(sid)
    assert len(out) == 1
    assert out[0]["attempt_id"] == a.attempt_id
    assert out[0]["status"] == "queued"


def test_zombie_running_skipped(session_service):
    """Running row left behind by a restart: not in _active_loops → skip."""
    sid = "sess-zr"
    _insert_attempt(session_service.store, sid, AttemptStatus.RUNNING, "2026-01-01T00:00:01")
    assert session_service.list_active_attempts(sid) == []


def test_zombie_pending_skipped(session_service):
    """Pending row with no live consumer queue (restart) → skip."""
    sid = "sess-zp"
    _insert_attempt(session_service.store, sid, AttemptStatus.PENDING, "2026-01-01T00:00:01")
    assert session_service.list_active_attempts(sid) == []


def test_oldest_first_ordering(session_service):
    sid = "sess-ord"
    a1 = _insert_attempt(session_service.store, sid, AttemptStatus.PENDING, "2026-01-01T00:00:01", prompt="first")
    a2 = _insert_attempt(session_service.store, sid, AttemptStatus.RUNNING, "2026-01-01T00:00:02", prompt="second")
    session_service._active_loops[a2.attempt_id] = asyncio.Task
    session_service._session_queues[sid] = asyncio.Queue()

    out = session_service.list_active_attempts(sid)
    assert [o["attempt_id"] for o in out] == [a1.attempt_id, a2.attempt_id]
    assert out[0]["status"] == "queued"
    assert out[1]["status"] == "running"


def test_terminal_attempts_excluded(session_service):
    """COMPLETED is excluded; FAILED is included (C1: up to 5 with error)."""
    sid = "sess-term"
    _insert_attempt(session_service.store, sid, AttemptStatus.COMPLETED, "2026-01-01T00:00:01")
    _insert_attempt(session_service.store, sid, AttemptStatus.FAILED, "2026-01-01T00:00:02")
    result = session_service.list_active_attempts(sid)
    # COMPLETED excluded; FAILED included with error info
    assert len(result) == 1
    assert result[0]["status"] == "failed"


# ── HTTP endpoint ─────────────────────────────────────────────────────


@pytest.fixture
def client(monkeypatch, session_service):
    from strategy_research.interfaces.api.routers import chat as chat_router

    monkeypatch.setattr(chat_router, "_get_session_service", lambda: session_service)
    app = create_app()
    c = TestClient(app)
    c.headers.update({"Authorization": f"Bearer {create_token('admin')}"})
    return c


def test_endpoint_returns_attempts(client, session_service):
    r = client.post("/api/chat/session", json={"title": "reload-test"})
    sid = r.json()["id"]
    a = _insert_attempt(session_service.store, sid, AttemptStatus.RUNNING, "2026-01-01T00:00:01")
    session_service._active_loops[a.attempt_id] = asyncio.Task

    r2 = client.get(f"/api/chat/attempts?session_id={sid}")
    assert r2.status_code == 200
    body = r2.json()
    assert len(body["attempts"]) == 1
    assert body["attempts"][0]["attempt_id"] == a.attempt_id
    assert body["attempts"][0]["status"] == "running"


def test_endpoint_empty_when_idle(client, session_service):
    r = client.post("/api/chat/session", json={"title": "reload-idle"})
    sid = r.json()["id"]
    r2 = client.get(f"/api/chat/attempts?session_id={sid}")
    assert r2.status_code == 200
    assert r2.json() == {"attempts": []}


def test_endpoint_404_unowned(client, session_service):
    r = client.get("/api/chat/attempts?session_id=no-such-session")
    assert r.status_code == 404
