"""B4 hotfix: /clear foreign-key cascade + schema dual-master.

Contract: docs/stage-c1-b4-hotfix-d7-schema-20261029.md

Two live bugs fixed here, both user-visible:

D7  ``/clear`` deleted the ``sessions`` row; every later projector flush
    then died on ``messages.session_id`` FK until some other write
    recreated the row.
Dual-master  ``SQLiteStore._init_schema`` carried its own two-table DDL;
    whoever created the DB first decided the schema, and a store created
    by this class lacked ``message_parts`` -- a later projector flush died
    with ``no such table: message_parts``.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from strategy_research.core.agent.memory_manager import (
    MemoryManagerFactory,
    SQLiteStore,
    UnifiedMemoryManager,
)


@pytest.fixture
def unified_db(tmp_path: Path) -> Path:
    return tmp_path / "unified.db"


@pytest.fixture
def mm(unified_db: Path):
    manager = UnifiedMemoryManager(db_path=unified_db)
    yield manager
    manager.close()


# ── D7: /clear must not destroy the session anchor row ────────


def test_delete_session_keeps_session_row(unified_db: Path):
    store = SQLiteStore(unified_db)
    store.insert_message("s1", "user", "hi")
    store.delete_session("s1")
    ids = store.list_recent_sessions(limit=10)
    assert "s1" in ids, "session row must survive clear (FK anchor)"
    assert store.list_messages("s1") == []


def test_flush_after_clear_does_not_hit_fk(unified_db: Path):
    """The actual D7 sequence: clear, then a projector flush.

    Before the fix: sessions row gone -> INSERT messages -> FK error.
    After: flush succeeds because the anchor row survives.
    """
    store = SQLiteStore(unified_db)
    store.insert_message("s1", "user", "hi")
    store.delete_session("s1")
    conn = sqlite3.connect(str(unified_db))
    conn.execute("PRAGMA foreign_keys = ON")
    # The projection inserts a message into the cleared session.
    conn.execute(
        "INSERT INTO messages (id, session_id, role, content, created_at, seq, message_type)"
        " VALUES ('m1', 's1', 'assistant', 'cleared.', 1.0, 1, 'assistant')"
    )
    conn.commit()
    count = conn.execute(
        "SELECT COUNT(*) FROM messages WHERE session_id='s1'"
    ).fetchone()[0]
    conn.close()
    assert count == 1


def test_clear_via_manager_preserves_recent_sessions(mm: UnifiedMemoryManager):
    import asyncio

    mm._backend.insert_message("s1", "user", "hi")
    asyncio.run(mm.clear("s1"))
    assert "s1" in mm._backend.list_recent_sessions(limit=10)
    assert mm._backend.list_messages("s1") == []


# ── Dual-master: SQLiteStore-first must still yield the full schema ──


def test_sqlite_store_first_creates_message_parts(unified_db: Path):
    """The projector writes message_parts; a store created by SQLiteStore
    must have that table or the flush dies with 'no such table'."""
    store = SQLiteStore(unified_db)
    store.insert_message("s1", "assistant", "hello")
    conn = sqlite3.connect(str(unified_db))
    tables = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    conn.close()
    assert {"sessions", "messages", "message_parts"} <= tables


def test_sqlite_store_first_accepts_projector_shape(unified_db: Path):
    """Insert a message_part the way the projector does -- the exact
    statement shape that used to fail with 'no such table: message_parts'."""
    store = SQLiteStore(unified_db)
    store.insert_message("s1", "assistant", "hello")
    conn = sqlite3.connect(str(unified_db))
    conn.execute("PRAGMA foreign_keys = ON")
    row = conn.execute("SELECT id FROM messages WHERE session_id='s1'").fetchone()
    conn.execute(
        "INSERT INTO message_parts (message_id, session_id, type, data_json, seq, time_created)"
        " VALUES (?, 's1', 'text', '{}', 0, 1.0)",
        (row[0],),
    )
    conn.commit()
    conn.close()


# ── InMemoryStore parity ─────────────────────────────────────


def test_in_memory_delete_session_keeps_session_key():
    from strategy_research.core.agent.memory_manager import InMemoryStore

    store = InMemoryStore()
    store.insert_message("s1", "user", "hi")
    store.delete_session("s1")
    # Key stays (anchor parity), messages cleared.
    assert store.list_messages("s1") == []
    assert "s1" in store.list_recent_sessions(limit=10)


# ── factory hygiene (the fixture closes; reset singleton) ────


def test_factory_reset_after_use(unified_db: Path):
    MemoryManagerFactory.reset()
    manager = MemoryManagerFactory.create(db_path=unified_db)
    assert manager is not None
    MemoryManagerFactory.reset()
