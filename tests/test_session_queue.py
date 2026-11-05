"""Tests for per-session FIFO message queue in SessionService.

Covers:
- Basic FIFO processing (single + multiple messages on same session)
- Cross-session isolation (different sessions run independently)
- Hard limit of 10 queued items → returns queue_full error
- Exception in one attempt does not stop subsequent items

Pause/resume is exercised via the /chat/cancel + /chat/queue/resume
HTTP endpoints (see test_session_queue_http.py for end-to-end).
"""
import asyncio

import pytest


@pytest.fixture
def temp_db(monkeypatch, tmp_path):
    """Point SessionStore / persist_message at a tmp SQLite file."""
    workspace = tmp_path
    workspace.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("SR_WORKSPACE_PATH", str(workspace))
    yield workspace / "quantnodes_agentnodes_user.db"


@pytest.fixture
def event_bus():
    from tests._fixtures.legacy_events import EventBus

    bus = EventBus()
    try:
        bus.set_loop(asyncio.get_event_loop())
    except Exception:
        pass
    return bus


@pytest.fixture
def session_service(temp_db, event_bus):
    import sqlite3

    from agentnodes.interfaces.api.routers.web_session import _ensure_schema
    from agentnodes.interfaces.api.session.service import SessionService
    from agentnodes.interfaces.api.session.store import SessionStore
    conn = sqlite3.connect(str(temp_db))
    try:
        _ensure_schema(conn)
        conn.commit()
    finally:
        conn.close()

    store = SessionStore(temp_db)
    return SessionService(store=store, event_bus=event_bus)


@pytest.fixture
def stub_runner(monkeypatch):
    """Replace SessionService._run_attempt with a stub that yields control.

    Records (attempt_id,) per call so tests can verify order.
    """
    calls: list[str] = []

    async def fake_run(self, *, session_id, attempt, **_kwargs):
        calls.append(attempt.attempt_id)
        # Yield so queued items can be enqueued while we run
        await asyncio.sleep(0.02)

    monkeypatch.setattr(
        "agentnodes.interfaces.api.session.service.SessionService._run_attempt",
        fake_run,
    )
    return calls


def _ensure_session(store, session_id: str) -> None:
    """Create the session row if it doesn't already exist."""
    from agentnodes.interfaces.api.routers.web_session import _get_db

    conn = _get_db()
    row = conn.execute(
        "SELECT id FROM sessions WHERE id = ?", (session_id,)
    ).fetchone()
    if not row:
        now = 1000.0
        conn.execute(
            "INSERT INTO sessions (id, user_id, title, created_at, updated_at, "
            "starred, tags_json, message_count, archived) "
            "VALUES (?, ?, ?, ?, ?, 0, '[]', 0, 0)",
            (session_id, "anonymous", "test", now, now),
        )
        conn.commit()


# ── Tests ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_single_message_runs_immediately(session_service, stub_runner):
    """First message starts immediately (status=processing)."""
    sid = "sess-1"
    _ensure_session(session_service.store, sid)
    result = await session_service.send_message(sid, "hello")
    assert "error" not in result
    assert result["status"] == "processing"
    await _wait_for_idle(session_service, sid, timeout=2.0)
    assert len(stub_runner) == 1
    assert stub_runner[0] == result["attempt_id"]


@pytest.mark.asyncio
async def test_sequential_messages_run_fifo(session_service, stub_runner):
    """Multiple messages on same session run in order."""
    sid = "sess-2"
    _ensure_session(session_service.store, sid)
    r1 = await session_service.send_message(sid, "msg-1")
    r2 = await session_service.send_message(sid, "msg-2")
    r3 = await session_service.send_message(sid, "msg-3")
    assert r1["queue_position"] == 1
    assert r2["queue_position"] == 2
    assert r3["queue_position"] == 3
    await _wait_for_idle(session_service, sid, timeout=3.0)
    assert stub_runner == [
        r1["attempt_id"],
        r2["attempt_id"],
        r3["attempt_id"],
    ]


@pytest.mark.asyncio
async def test_cross_session_isolation(session_service, stub_runner):
    """Different sessions process independently."""
    _ensure_session(session_service.store, "s-A")
    _ensure_session(session_service.store, "s-B")
    r_a = await session_service.send_message("s-A", "from A")
    r_b = await session_service.send_message("s-B", "from B")
    await _wait_for_idle(session_service, "s-A", timeout=2.0)
    await _wait_for_idle(session_service, "s-B", timeout=2.0)
    finished_ids = set(stub_runner)
    assert finished_ids == {r_a["attempt_id"], r_b["attempt_id"]}


@pytest.mark.asyncio
async def test_queue_full_returns_error(session_service, stub_runner):
    """Hard limit of 10 queued items — 11th returns queue_full error."""
    sid = "sess-full"
    _ensure_session(session_service.store, sid)
    first = await session_service.send_message(sid, "first")
    assert "error" not in first
    # Fill queue to 10 (the first message already consumed 0 slots, so
    # 10 more puts fill qsize to 10; the 11th attempt would make qsize==10
    # at the moment of the limit check, triggering rejection).
    for i in range(10):
        r = await session_service.send_message(sid, f"q-{i}")
        # While qsize < 10, no error
        if i < 9:
            assert "error" not in r, f"unexpected error at i={i}: {r}"
    # The 11th call must fail (qsize already 10)
    rejected = await session_service.send_message(sid, "over-limit")
    assert rejected["error"] == "queue_full"
    assert rejected["limit"] == 10
    # Drain so the consumer task completes cleanly before the next test
    await _wait_for_idle(session_service, sid, timeout=5.0)


@pytest.mark.asyncio
async def test_attempt_exception_does_not_stop_queue(
    session_service, monkeypatch
):
    """An exception in one attempt should not stop subsequent items."""
    sid = "sess-exc"
    _ensure_session(session_service.store, sid)
    calls: list[str] = []

    async def flaky_run(self, *, session_id, attempt, **_kwargs):
        calls.append(attempt.attempt_id)
        await asyncio.sleep(0.01)
        if len(calls) == 1:
            raise RuntimeError("simulated failure")
        # Subsequent calls succeed silently

    monkeypatch.setattr(
        "agentnodes.interfaces.api.session.service.SessionService._run_attempt",
        flaky_run,
    )

    r1 = await session_service.send_message(sid, "first")
    r2 = await session_service.send_message(sid, "second")
    await _wait_for_idle(session_service, sid, timeout=3.0)
    # Both attempts should have been called (second succeeded despite first
    # raising).
    assert r1["attempt_id"] in calls
    assert r2["attempt_id"] in calls
    assert len(calls) == 2


# ── Helpers ──────────────────────────────────────────────────────────────


async def _wait_for_idle(service, session_id: str, timeout: float):
    """Wait until the session's queue consumer has finished and is removed."""
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if session_id not in service._processing_sessions:
            # One more tick to let in-flight tasks settle
            await asyncio.sleep(0.05)
            return
        await asyncio.sleep(0.05)
    raise AssertionError(
        f"Session {session_id} consumer did not go idle within {timeout}s"
    )
