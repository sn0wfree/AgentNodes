"""Tests for Projector connection reuse and flush lock.

Verifies:
1. _get_conn returns the same connection object across calls
2. Concurrent flush calls don't cause 'database is locked' errors
3. Connection is lazily created on first flush
"""
from __future__ import annotations

import sqlite3
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from strategy_research.interfaces.api.session.projector import (
    Projector,
    ProjectedMessage,
    ProjectedSession,
)
from strategy_research.kernel.events.event_v2 import EventType, EventV2


def _setup_db(db_path: Path) -> None:
    """Create the minimal schema needed for projector flush."""
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("""
            CREATE TABLE sessions (
                id TEXT PRIMARY KEY, user_id TEXT, title TEXT,
                created_at REAL, updated_at REAL,
                starred INTEGER DEFAULT 0, tags_json TEXT DEFAULT '[]',
                message_count INTEGER DEFAULT 0, archived INTEGER DEFAULT 0
            )
        """)
        conn.execute("""
            CREATE TABLE messages (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL DEFAULT '',
                created_at REAL NOT NULL,
                metadata_json TEXT,
                message_type TEXT,
                seq INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
            )
        """)
        conn.execute("""
            CREATE TABLE message_parts (
                id TEXT PRIMARY KEY,
                message_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                type TEXT NOT NULL,
                data_json TEXT NOT NULL,
                seq INTEGER NOT NULL DEFAULT 0,
                time_created REAL NOT NULL,
                FOREIGN KEY (message_id) REFERENCES messages(id) ON DELETE CASCADE,
                FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
            )
        """)
        conn.execute("""
            CREATE TABLE event_log (
                id TEXT PRIMARY KEY,
                aggregate_id TEXT NOT NULL,
                seq INTEGER NOT NULL,
                type TEXT NOT NULL,
                data_json TEXT NOT NULL,
                time_created REAL NOT NULL,
                parent_event_id TEXT,
                branch_id TEXT DEFAULT 'main',
                UNIQUE (aggregate_id, seq)
            )
        """)
        conn.execute("""
            CREATE TABLE snapshots (
                session_id TEXT PRIMARY KEY,
                seq INTEGER NOT NULL,
                snapshot_json TEXT NOT NULL,
                created_at REAL NOT NULL
            )
        """)
        conn.commit()
    finally:
        conn.close()


def _create_session(db_path: Path, session_id: str) -> None:
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "INSERT INTO sessions (id, user_id, title) VALUES (?, ?, ?)",
            (session_id, "test-user", "test"),
        )
        conn.commit()
    finally:
        conn.close()


class TestProjectorConnectionReuse:
    """Verify connection reuse and flush lock."""

    def test_get_conn_returns_same_object(self, tmp_path):
        """_get_conn should return the same connection on repeated calls."""
        db_path = tmp_path / "test.db"
        _setup_db(db_path)

        proj = Projector(db_path)
        conn1 = proj._get_conn()
        conn2 = proj._get_conn()
        assert conn1 is conn2
        assert isinstance(conn1, sqlite3.Connection)

    def test_get_conn_is_lazy(self, tmp_path):
        """Connection is not created until first _get_conn call."""
        db_path = tmp_path / "test.db"
        _setup_db(db_path)

        proj = Projector(db_path)
        assert proj._conn is None
        conn = proj._get_conn()
        assert proj._conn is conn

    def test_flush_uses_reused_connection(self, tmp_path):
        """After flush, the projector's _conn is still the same object."""
        db_path = tmp_path / "test.db"
        _setup_db(db_path)
        _create_session(db_path, "s1")

        proj = Projector(db_path)
        state = ProjectedSession(session_id="s1")
        proj.flush(state)
        first_conn = proj._conn

        proj.flush(state)
        assert proj._conn is first_conn

    def test_flush_concurrent_no_lock_errors(self, tmp_path):
        """Concurrent flushes on different sessions should not cause
        'database is locked' errors."""
        db_path = tmp_path / "test.db"
        _setup_db(db_path)
        proj = Projector(db_path)

        n_sessions = 10
        for i in range(n_sessions):
            _create_session(db_path, f"s{i}")

        errors: list[str] = []

        def flush_session(sid: str):
            try:
                state = ProjectedSession(session_id=sid)
                proj.flush(state)
            except Exception as exc:
                errors.append(f"{sid}: {exc}")

        threads = [threading.Thread(target=flush_session, args=(f"s{i}",))
                   for i in range(n_sessions)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert errors == [], f"Concurrent flush errors: {errors}"

    def test_flush_concurrent_same_session(self, tmp_path):
        """Concurrent flushes on the same session serialize correctly."""
        db_path = tmp_path / "test.db"
        _setup_db(db_path)
        _create_session(db_path, "s1")

        proj = Projector(db_path)
        errors: list[str] = []

        def flush_repeatedly():
            try:
                for _ in range(5):
                    state = ProjectedSession(session_id="s1")
                    proj.flush(state)
                    time.sleep(0.001)
            except Exception as exc:
                errors.append(str(exc))

        threads = [threading.Thread(target=flush_repeatedly) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert errors == [], f"Concurrent same-session flush errors: {errors}"


class TestProjectorFlushWithMissingSession:
    """Verify projector flush behavior when session row is missing."""

    def test_flush_missing_session_fk_error(self, tmp_path):
        """Flushing to a session that doesn't exist in sessions table
        raises IntegrityError (FK violation)."""
        db_path = tmp_path / "test.db"
        _setup_db(db_path)
        # Don't create session row for "missing"

        proj = Projector(db_path)
        state = ProjectedSession(session_id="missing")
        # Add a message so the flush actually tries to INSERT
        state.messages["m1"] = ProjectedMessage(
            id="m1", session_id="missing", role="user", content="hello",
            created_at=time.time(),
        )

        with pytest.raises(sqlite3.IntegrityError):
            proj.flush(state)

    def test_flush_after_session_soft_deleted(self, tmp_path):
        """Flushing to an archived (soft-deleted) session still works
        because the row still exists."""
        db_path = tmp_path / "test.db"
        _setup_db(db_path)
        _create_session(db_path, "s1")

        # Soft-delete
        conn = sqlite3.connect(str(db_path))
        conn.execute("UPDATE sessions SET archived = 1 WHERE id = ?", ("s1",))
        conn.commit()
        conn.close()

        proj = Projector(db_path)
        state = ProjectedSession(session_id="s1")
        # Should not raise — session row still exists
        proj.flush(state)
