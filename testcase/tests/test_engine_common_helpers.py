"""Tests for engine_common helpers added during P0-2/P0-3 fixes.

Focus: session row creation for SSE validation, and interrupt lookup
that returns responded interrupts (the lookup the HITL poll loop and
resume path read).
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest

from strategy_research.apps.study.engine_common import (
    SESSION_DB_FILENAME,
    ensure_study_session,
    get_study_session_db_path,
)

# ── Fixtures ────────────────────────────────────────────────


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """Workspace with a session DB already seeded with one chat session.

    The seeded session lets ensure_study_session inherit a real
    user_id (so the chat API's IDOR check will pass for the new
    study session row).
    """
    db_path = tmp_path / SESSION_DB_FILENAME
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """
        CREATE TABLE sessions (
            id TEXT PRIMARY KEY,
            title TEXT,
            user_id TEXT,
            created_at REAL,
            updated_at REAL,
            starred INTEGER DEFAULT 0,
            tags_json TEXT DEFAULT '[]',
            message_count INTEGER DEFAULT 0,
            archived INTEGER DEFAULT 0
        )
        """
    )
    # Seed three sessions owned by 'tester' so it's the most-common user
    for i in range(3):
        conn.execute(
            "INSERT INTO sessions (id, title, user_id, created_at, updated_at) "
            "VALUES (?, ?, 'tester', ?, ?)",
            (f"seed-{i}", f"seed-{i}", time.time(), time.time()),
        )
    # One owned by 'other-user' so 'tester' stays most-common
    conn.execute(
        "INSERT INTO sessions (id, title, user_id, created_at, updated_at) "
        "VALUES ('seed-other', 'other', 'other-user', ?, ?)",
        (time.time(), time.time()),
    )
    conn.commit()
    conn.close()
    return tmp_path


# ── get_study_session_db_path ───────────────────────────────


def test_db_path_resolves_inside_workspace(workspace: Path) -> None:
    db = get_study_session_db_path(workspace)
    assert db == workspace / SESSION_DB_FILENAME
    assert db.name == ".quantnodes_strategy_research_session.db"


# ── ensure_study_session ────────────────────────────────────


def test_ensure_creates_session_row(workspace: Path) -> None:
    db = get_study_session_db_path(workspace)
    sid = "study:abc123:round:1"
    ensure_study_session(db, sid, "Study abc123 Round 1")

    conn = sqlite3.connect(str(db))
    row = conn.execute(
        "SELECT id, title, user_id FROM sessions WHERE id = ?", (sid,)
    ).fetchone()
    conn.close()
    assert row is not None
    assert row[0] == sid
    assert row[1] == "Study abc123 Round 1"
    # owner inherited from most-common non-system user
    assert row[2] == "tester"


def test_ensure_idempotent(workspace: Path) -> None:
    """Calling twice should not error or duplicate the row."""
    db = get_study_session_db_path(workspace)
    sid = "study:abc:round:1"
    ensure_study_session(db, sid, "title1")
    ensure_study_session(db, sid, "title2")

    conn = sqlite3.connect(str(db))
    n = conn.execute("SELECT COUNT(*) FROM sessions WHERE id = ?", (sid,)).fetchone()[0]
    conn.close()
    assert n == 1


def test_ensure_falls_back_to_system_when_no_other_users(workspace: Path) -> None:
    """When the DB has no non-system user, fall back to 'system'."""
    db = get_study_session_db_path(workspace)
    # Wipe the seeded sessions so only 'system' would match
    conn = sqlite3.connect(str(db))
    conn.execute("DELETE FROM sessions")
    conn.execute(
        "INSERT INTO sessions (id, title, user_id, created_at, updated_at) "
        "VALUES ('s', 's', 'system', ?, ?)", (time.time(), time.time())
    )
    conn.commit()
    conn.close()

    ensure_study_session(db, "study:orphan", "orphan")
    conn = sqlite3.connect(str(db))
    row = conn.execute(
        "SELECT user_id FROM sessions WHERE id = ?", ("study:orphan",)
    ).fetchone()
    conn.close()
    assert row[0] == "system"


def test_ensure_swallows_db_errors(workspace: Path) -> None:
    """If the DB doesn't exist, ensure_study_session should not raise."""
    nonexistent = workspace / "does-not-exist" / SESSION_DB_FILENAME
    # Must not raise
    ensure_study_session(nonexistent, "study:x:round:1", "t")


# ── Interrupt lifecycle (P0-3a HITL fixes) ──────────────────


@pytest.fixture
def study_store(tmp_path: Path, monkeypatch) -> None:
    """StudyStore pointed at a fresh per-test goals DB, with one study seeded."""
    db_path = tmp_path / "goals.db"
    monkeypatch.setenv("QUANTNODES_RESEARCH_GOAL_DB_PATH", str(db_path))
    from strategy_research.apps.study.store import StudyStore
    store = StudyStore(db_path=db_path)
    store.create_study(
        owner_session_id="tester", goal_id=None, objective="o",
        workspace_path=str(tmp_path), strategy_name="demo",
        metric_targets=[{"name": "calmar", "op": ">=", "value": 0.5}],
        cooldown_base=0.01, cooldown_jitter=0.01, min_cooldown=0.01,
    )
    yield store


def _seed_study(store, study_id: str = "s-it") -> None:
    """No-op: the study_store fixture already seeds one study row."""
    pass  # pragma: no cover


def test_create_and_get_pending_interrupt(study_store) -> None:
    """Created interrupts are returned by both lookup methods."""
    sid = study_store._study_from_row(
        study_store._conn.execute(
            "SELECT * FROM studies LIMIT 1"
        ).fetchone()
    ).study_id

    interrupt = study_store.create_interrupt(
        study_id=sid, round_num=1, interrupt_type="novelty_gate",
        payload='{"type": "novelty_gate"}',
    )

    pending = study_store.get_pending_interrupt(sid, 1)
    assert pending is not None
    assert pending.interrupt_id == interrupt.interrupt_id
    assert pending.status == "pending"

    # get_interrupt_for_round must also return it (any-status semantics)
    any_status = study_store.get_interrupt_for_round(sid, 1)
    assert any_status is not None
    assert any_status.interrupt_id == interrupt.interrupt_id


def test_get_interrupt_after_respond(study_store) -> None:
    """P0-3a fix: get_interrupt_for_round returns responded interrupts.

    Pre-fix behaviour: ``get_pending_interrupt`` filters status='pending',
    so once the user responded, the runner poll loop never saw the
    decision and timed out. The new ``get_interrupt_for_round`` returns
    the latest interrupt regardless of status.
    """
    sid = study_store._study_from_row(
        study_store._conn.execute(
            "SELECT * FROM studies LIMIT 1"
        ).fetchone()
    ).study_id

    interrupt = study_store.create_interrupt(
        study_id=sid, round_num=2, interrupt_type="novelty_gate",
        payload='{}',
    )

    # Approve
    updated = study_store.respond_interrupt(
        interrupt.interrupt_id, "approved", response="looks good"
    )
    assert updated is True

    # Old method: returns None (filtered by status='pending')
    pending = study_store.get_pending_interrupt(sid, 2)
    assert pending is None

    # New method: returns the responded interrupt with status='approved'
    any_status = study_store.get_interrupt_for_round(sid, 2)
    assert any_status is not None
    assert any_status.interrupt_id == interrupt.interrupt_id
    assert any_status.status == "approved"
    assert any_status.response == "looks good"
    assert any_status.responded_at is not None


def test_get_interrupt_returns_latest(study_store) -> None:
    """When multiple interrupts exist for the same round, return the
    most recent one (highest created_at)."""
    sid = study_store._study_from_row(
        study_store._conn.execute(
            "SELECT * FROM studies LIMIT 1"
        ).fetchone()
    ).study_id

    study_store.create_interrupt(
        study_id=sid, round_num=3, interrupt_type="first",
    )
    i2 = study_store.create_interrupt(
        study_id=sid, round_num=3, interrupt_type="second",
    )

    latest = study_store.get_interrupt_for_round(sid, 3)
    assert latest is not None
    assert latest.interrupt_id == i2.interrupt_id
    assert latest.interrupt_type == "second"


def test_get_interrupt_round_isolation(study_store) -> None:
    """Different rounds return different interrupts."""
    sid = study_store._study_from_row(
        study_store._conn.execute(
            "SELECT * FROM studies LIMIT 1"
        ).fetchone()
    ).study_id

    r1 = study_store.create_interrupt(
        study_id=sid, round_num=1, interrupt_type="r1",
    )
    r2 = study_store.create_interrupt(
        study_id=sid, round_num=2, interrupt_type="r2",
    )

    assert study_store.get_interrupt_for_round(sid, 1).interrupt_id == r1.interrupt_id
    assert study_store.get_interrupt_for_round(sid, 2).interrupt_id == r2.interrupt_id
    assert study_store.get_interrupt_for_round(sid, 99) is None


def test_respond_already_ended_returns_false(study_store) -> None:
    """Double-respond to the same interrupt is a no-op (idempotent)."""
    sid = study_store._study_from_row(
        study_store._conn.execute(
            "SELECT * FROM studies LIMIT 1"
        ).fetchone()
    ).study_id

    interrupt = study_store.create_interrupt(
        study_id=sid, round_num=4, interrupt_type="x",
    )

    assert study_store.respond_interrupt(interrupt.interrupt_id, "approved") is True
    # Second call: WHERE status='pending' won't match anymore → 0 rows updated
    assert study_store.respond_interrupt(interrupt.interrupt_id, "rejected") is False
