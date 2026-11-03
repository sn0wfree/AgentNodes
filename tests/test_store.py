"""Tests for SessionStore (data access layer for webui).

SessionStore is the SQLite-backed persistence layer used by the webui
chat endpoint. It delegates message CRUD to web_session.persist_message
and manages Attempt CRUD directly.

These tests verify:
- Attempt CRUD: create, update, get, list
- row_to_attempt helper
- Env var toggle for event_log read path
- Fallback behavior when event_log is empty
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from strategy_research.interfaces.api.session.models import Attempt, AttemptStatus
from strategy_research.interfaces.api.session.store import SessionStore, _row_to_attempt


def _setup_full_db(db_path: Path) -> None:
    """Create sessions, messages, event_log, and attempts tables."""
    # We need the full schema that web_session._ensure_schema creates,
    # plus event_log and attempts. Replicate what the real app does.
    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript("""
            PRAGMA foreign_keys = ON;

            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                user_id TEXT,
                title TEXT,
                created_at REAL,
                updated_at REAL,
                starred INTEGER DEFAULT 0,
                tags_json TEXT DEFAULT '[]',
                message_count INTEGER DEFAULT 0,
                archived INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS messages (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL DEFAULT '',
                created_at REAL NOT NULL,
                metadata_json TEXT,
                message_type TEXT,
                seq INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS message_parts (
                id TEXT PRIMARY KEY,
                message_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                type TEXT NOT NULL,
                data_json TEXT NOT NULL,
                seq INTEGER NOT NULL DEFAULT 0,
                time_created REAL NOT NULL,
                FOREIGN KEY (message_id) REFERENCES messages(id) ON DELETE CASCADE,
                FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS event_log (
                id TEXT PRIMARY KEY,
                aggregate_id TEXT NOT NULL,
                seq INTEGER NOT NULL,
                type TEXT NOT NULL,
                data_json TEXT NOT NULL,
                time_created REAL NOT NULL,
                FOREIGN KEY (aggregate_id) REFERENCES sessions(id) ON DELETE CASCADE,
                UNIQUE (aggregate_id, seq)
            );

            CREATE TABLE IF NOT EXISTS attempts (
                attempt_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                parent_attempt_id TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                prompt TEXT NOT NULL DEFAULT '',
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
                thinking TEXT NOT NULL DEFAULT 'auto',
                FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
            );
        """)
        conn.commit()
    finally:
        conn.close()


def _make_attempt(
    session_id: str = "s1",
    attempt_id: str = "a1",
    status: AttemptStatus = AttemptStatus.PENDING,
) -> Attempt:
    return Attempt(
        attempt_id=attempt_id,
        session_id=session_id,
        status=status,
        prompt="test prompt",
        run_dir="/tmp/runs/test",
        summary="test summary",
        react_trace=[{"step": "think", "output": "thinking..."}],
        metrics={"accuracy": 0.95},
        message_id="msg_1",
    )


class TestSessionStoreAttemptCRUD(unittest.TestCase):
    """Verify Attempt CRUD operations."""

    def setUp(self) -> None:
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db_path = Path(self.tmp.name)
        _setup_full_db(self.db_path)
        # Insert a session
        conn = sqlite3.connect(str(self.db_path))
        conn.execute("INSERT INTO sessions (id, title) VALUES (?, ?)", ("s1", "Test"))
        conn.commit()
        conn.close()
        self.store = SessionStore(self.db_path)

    def tearDown(self) -> None:
        os.unlink(self.db_path)

    def test_create_attempt(self) -> None:
        attempt = _make_attempt()
        result = self.store.create_attempt(attempt)
        self.assertEqual(result.attempt_id, "a1")
        self.assertEqual(result.status, AttemptStatus.PENDING)
        self.assertIsNotNone(result.created_at)

        # Verify in DB
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM attempts WHERE attempt_id = ?", ("a1",)
        ).fetchone()
        conn.close()
        self.assertIsNotNone(row)
        self.assertEqual(row["session_id"], "s1")
        self.assertEqual(row["status"], "pending")
        self.assertEqual(row["prompt"], "test prompt")
        self.assertEqual(row["run_dir"], "/tmp/runs/test")
        self.assertEqual(row["summary"], "test summary")
        self.assertEqual(row["message_id"], "msg_1")

    def test_create_attempt_with_react_trace(self) -> None:
        attempt = _make_attempt()
        self.store.create_attempt(attempt)
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM attempts WHERE attempt_id = ?", ("a1",)
        ).fetchone()
        conn.close()
        trace = json.loads(row["react_trace_json"])
        self.assertEqual(trace, [{"step": "think", "output": "thinking..."}])

    def test_create_attempt_with_metrics(self) -> None:
        attempt = _make_attempt()
        self.store.create_attempt(attempt)
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM attempts WHERE attempt_id = ?", ("a1",)
        ).fetchone()
        conn.close()
        metrics = json.loads(row["metrics_json"])
        self.assertEqual(metrics, {"accuracy": 0.95})

    def test_update_attempt(self) -> None:
        attempt = _make_attempt()
        self.store.create_attempt(attempt)
        attempt.status = AttemptStatus.COMPLETED
        attempt.summary = "completed summary"
        attempt.completed_at = "2026-08-01T12:00:00"
        self.store.update_attempt(attempt)

        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM attempts WHERE attempt_id = ?", ("a1",)
        ).fetchone()
        conn.close()
        self.assertEqual(row["status"], "completed")
        self.assertEqual(row["summary"], "completed summary")
        self.assertEqual(row["completed_at"], "2026-08-01T12:00:00")

    def test_get_attempt_found(self) -> None:
        attempt = _make_attempt()
        self.store.create_attempt(attempt)
        result = self.store.get_attempt("s1", "a1")
        self.assertIsNotNone(result)
        self.assertEqual(result.attempt_id, "a1")
        self.assertEqual(result.session_id, "s1")
        self.assertEqual(result.status, AttemptStatus.PENDING)
        self.assertEqual(result.prompt, "test prompt")
        self.assertEqual(result.run_dir, "/tmp/runs/test")
        self.assertEqual(result.react_trace, [{"step": "think", "output": "thinking..."}])
        self.assertEqual(result.metrics, {"accuracy": 0.95})

    def test_get_attempt_not_found(self) -> None:
        result = self.store.get_attempt("s1", "nonexistent")
        self.assertIsNone(result)

    def test_get_attempt_wrong_session(self) -> None:
        attempt = _make_attempt()
        self.store.create_attempt(attempt)
        result = self.store.get_attempt("s2", "a1")
        self.assertIsNone(result)





    def test_attempt_without_react_trace(self) -> None:
        attempt = Attempt(
            attempt_id="a1", session_id="s1", status=AttemptStatus.PENDING, prompt="hi"
        )
        self.store.create_attempt(attempt)
        result = self.store.get_attempt("s1", "a1")
        self.assertEqual(result.react_trace, [])

    def test_attempt_without_metrics(self) -> None:
        attempt = Attempt(
            attempt_id="a1", session_id="s1", status=AttemptStatus.PENDING, prompt="hi"
        )
        self.store.create_attempt(attempt)
        result = self.store.get_attempt("s1", "a1")
        self.assertIsNone(result.metrics)


class TestRowToAttempt(unittest.TestCase):
    """Verify _row_to_attempt helper."""

    def test_row_to_attempt_basic(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("""
            CREATE TABLE attempts (
                attempt_id TEXT, session_id TEXT, parent_attempt_id TEXT,
                status TEXT, prompt TEXT, run_dir TEXT, summary TEXT,
                react_trace_json TEXT, metrics_json TEXT, created_at TEXT,
                completed_at TEXT, error TEXT, message_id TEXT
            )
        """)
        conn.execute(
            "INSERT INTO attempts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("a1", "s1", None, "pending", "hi", None, None, None, None, "now", None, None, None),
        )
        row = conn.execute("SELECT * FROM attempts").fetchone()
        conn.close()
        attempt = _row_to_attempt(row)
        self.assertEqual(attempt.attempt_id, "a1")
        self.assertEqual(attempt.session_id, "s1")
        self.assertEqual(attempt.status, AttemptStatus.PENDING)
        self.assertEqual(attempt.prompt, "hi")
        self.assertIsNone(attempt.run_dir)
        self.assertIsNone(attempt.summary)
        self.assertEqual(attempt.react_trace, [])


class TestSessionStoreEnvVar(unittest.TestCase):
    """Verify SR_EVENT_LOG_READ env var behavior."""

    def setUp(self) -> None:
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db_path = Path(self.tmp.name)
        _setup_full_db(self.db_path)

    def tearDown(self) -> None:
        os.unlink(self.db_path)
        os.environ.pop("SR_EVENT_LOG_READ", None)

    def test_default_is_event_log_read(self) -> None:
        os.environ.pop("SR_EVENT_LOG_READ", None)
        store = SessionStore(self.db_path)
        self.assertTrue(store._use_event_log_read)

    def test_export_0_disables_event_log(self) -> None:
        os.environ["SR_EVENT_LOG_READ"] = "0"
        store = SessionStore(self.db_path)
        self.assertFalse(store._use_event_log_read)

    def test_export_1_enables_event_log(self) -> None:
        os.environ["SR_EVENT_LOG_READ"] = "1"
        store = SessionStore(self.db_path)
        self.assertTrue(store._use_event_log_read)

    def test_export_false_enables_event_log(self) -> None:
        os.environ["SR_EVENT_LOG_READ"] = "false"
        store = SessionStore(self.db_path)
        self.assertTrue(store._use_event_log_read)

    def test_export_empty_enables_event_log(self) -> None:
        os.environ["SR_EVENT_LOG_READ"] = ""
        store = SessionStore(self.db_path)
        self.assertTrue(store._use_event_log_read)


class TestSessionStoreConn(unittest.TestCase):
    """Verify _conn returns a working connection."""

    def setUp(self) -> None:
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db_path = Path(self.tmp.name)
        _setup_full_db(self.db_path)
        self.store = SessionStore(self.db_path)

    def tearDown(self) -> None:
        os.unlink(self.db_path)

    def test_conn_returns_row_factory(self) -> None:
        with self.store._conn() as conn:
            self.assertIsNotNone(conn)
            self.assertIs(conn.row_factory, sqlite3.Row)

    def test_conn_queriable(self) -> None:
        with self.store._conn() as conn:
            row = conn.execute("SELECT 1 AS x").fetchone()
            self.assertEqual(row["x"], 1)

    def test_conn_creates_parent_dir(self) -> None:
        tmpdir = tempfile.mkdtemp()
        nested = Path(tmpdir) / "subdir" / "test.db"
        store = SessionStore(nested)
        with store._conn() as conn:
            self.assertTrue(nested.parent.exists())
        import shutil
        shutil.rmtree(tmpdir)


class TestSessionStoreEventLogFallback(unittest.TestCase):
    """Verify event_log read path fallback to DB when empty."""

    def setUp(self) -> None:
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db_path = Path(self.tmp.name)
        _setup_full_db(self.db_path)
        conn = sqlite3.connect(str(self.db_path))
        conn.execute("INSERT INTO sessions (id, title) VALUES (?, ?)", ("s1", "Test"))
        conn.commit()
        conn.close()
        self.store = SessionStore(self.db_path)

    def tearDown(self) -> None:
        os.unlink(self.db_path)

    def test_get_messages_empty_event_log_returns_empty(self) -> None:
        msgs = self.store.get_messages("s1")
        self.assertEqual(msgs, [])

    def test_get_messages_with_db_fallback(self) -> None:
        self.store._use_event_log_read = False
        msgs = self.store.get_messages("s1")
        self.assertEqual(msgs, [])

    def test_get_messages_from_db_ignores_other_session(self) -> None:
        conn = sqlite3.connect(str(self.db_path))
        conn.execute("INSERT INTO sessions (id, title) VALUES (?, ?)", ("s2", "Other"))
        conn.execute(
            "INSERT INTO messages (id, session_id, role, content, created_at, seq) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("m1", "s2", "user", "hello", 1000.0, 1),
        )
        conn.commit()
        conn.close()
        self.store._use_event_log_read = False
        msgs = self.store.get_messages("s1")
        self.assertEqual(msgs, [])


if __name__ == "__main__":
    unittest.main()
