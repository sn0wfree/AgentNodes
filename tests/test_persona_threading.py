"""Tests for chat persona (agent selector) threading.

Covers:
- send_message stores persona on the Attempt
- persona survives the store round-trip (get_attempt) so the queue
  consumer can read it back
- /api/chat/personas endpoint lists the curated role set

Note: uses a fully-initialised SQLite schema (unlike test_session_queue.py,
whose fixture predates the attempts table and is a pre-existing failure).
"""
import asyncio
import sqlite3
from pathlib import Path

import pytest


def _setup_full_db(db_path: Path) -> None:
    """Create all tables needed for the service stack, incl. attempts+persona."""
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE sessions (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            title TEXT NOT NULL DEFAULT '',
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            message_count INTEGER NOT NULL DEFAULT 0,
            starred INTEGER NOT NULL DEFAULT 0,
            tags_json TEXT,
            archived INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE messages (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL DEFAULT '',
            created_at REAL NOT NULL,
            metadata_json TEXT,
            message_type TEXT NOT NULL DEFAULT 'assistant',
            seq INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE message_parts (
            id TEXT PRIMARY KEY,
            message_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            type TEXT NOT NULL,
            data_json TEXT NOT NULL,
            seq INTEGER NOT NULL DEFAULT 0,
            time_created REAL NOT NULL
        );
        CREATE TABLE event_log (
            id TEXT PRIMARY KEY,
            aggregate_id TEXT NOT NULL,
            seq INTEGER NOT NULL,
            type TEXT NOT NULL,
            data_json TEXT NOT NULL,
            time_created REAL NOT NULL,
            UNIQUE (aggregate_id, seq)
        );
        CREATE TABLE attempts (
            attempt_id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            parent_attempt_id TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            prompt TEXT,
            run_dir TEXT,
            summary TEXT,
            react_trace_json TEXT,
            metrics_json TEXT,
            created_at TEXT NOT NULL,
            completed_at TEXT,
            error TEXT,
            message_id TEXT,
            persona TEXT,
            mode TEXT NOT NULL DEFAULT 'build',
            model_override TEXT,
            thinking TEXT NOT NULL DEFAULT 'auto'
        );
        """
    )
    conn.commit()
    conn.close()


@pytest.fixture
def service(tmp_path, monkeypatch):
    from tests._fixtures.legacy_events import EventBus
    from strategy_research.interfaces.api.session.service import SessionService
    from strategy_research.interfaces.api.session.store import SessionStore

    monkeypatch.setenv("SR_WORKSPACE_PATH", str(tmp_path))
    db = tmp_path / "test.db"
    _setup_full_db(db)

    bus = EventBus()
    try:
        bus.set_loop(asyncio.get_event_loop())
    except Exception:
        pass
    store = SessionStore(db)
    return SessionService(store=store, event_bus=bus)


def _ensure_session(store, session_id: str) -> None:
    conn = sqlite3.connect(str(store.db_path))
    conn.execute(
        "INSERT OR IGNORE INTO sessions (id, user_id, title, created_at, "
        "updated_at, message_count, starred, tags_json, archived) "
        "VALUES (?, ?, ?, ?, ?, 0, 0, '[]', 0)",
        (session_id, "anonymous", "test", 1000.0, 1000.0),
    )
    conn.commit()
    conn.close()


async def _wait_idle(service, session_id: str, timeout: float = 2.0):
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if session_id not in service._processing_sessions:
            await asyncio.sleep(0.05)
            return
        await asyncio.sleep(0.05)


@pytest.mark.asyncio
async def test_persona_persists_and_reaches_run(service, monkeypatch):
    """Persona survives the store round-trip into _run_attempt."""
    sid = "sess-p"
    _ensure_session(service.store, sid)
    seen: list[tuple[str, str | None]] = []

    async def fake_run(self, *, attempt, **_kwargs):
        seen.append((attempt.attempt_id, attempt.persona))
        await asyncio.sleep(0.02)

    monkeypatch.setattr(
        "strategy_research.interfaces.api.session.service.SessionService._run_attempt",
        fake_run,
    )
    result = await service.send_message(sid, "hello", persona="strategist")
    await _wait_idle(service, sid)
    # Sent attempt carried persona; the consumer re-fetched it from the DB
    # and still saw it.
    assert seen == [(result["attempt_id"], "strategist")]


@pytest.mark.asyncio
async def test_persona_omitted_stays_none(service, monkeypatch):
    sid = "sess-n"
    _ensure_session(service.store, sid)
    seen: list[str] = []

    async def fake_run(self, *, attempt, **_kwargs):
        seen.append(attempt.persona)
        await asyncio.sleep(0.02)

    monkeypatch.setattr(
        "strategy_research.interfaces.api.session.service.SessionService._run_attempt",
        fake_run,
    )
    await service.send_message(sid, "hello")
    await _wait_idle(service, sid)
    assert seen == [None]


def test_personas_endpoint_lists_roles():
    from strategy_research.interfaces.api.routers.chat import list_personas

    resp = asyncio.run(list_personas())
    ids = [p["id"] for p in resp["personas"]]
    assert "chat" in ids
    assert "strategist" in ids
    chat = next(p for p in resp["personas"] if p["id"] == "chat")
    assert chat["name"]
