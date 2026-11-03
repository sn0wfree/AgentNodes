"""Backfill event_log comprehensive tests — migration logic, idempotency."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from strategy_research.foundation.storage.event_schema import ensure_event_log_schema


def _setup_legacy_db(db_path: Path, session_id: str = "s1", num_messages: int = 3):
    """Create a legacy DB with messages + message_parts tables (matching actual schema)."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            id TEXT PRIMARY KEY,
            title TEXT,
            status TEXT,
            created_at TEXT,
            updated_at TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id TEXT PRIMARY KEY,
            session_id TEXT,
            role TEXT,
            content TEXT,
            created_at TEXT,
            linked_attempt_id TEXT,
            metadata TEXT,
            message_type TEXT,
            seq INTEGER
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS message_parts (
            id TEXT PRIMARY KEY,
            message_id TEXT,
            type TEXT,
            data_json TEXT,
            seq INTEGER,
            time_created TEXT
        )
    """)
    conn.execute(
        "INSERT INTO sessions VALUES (?, ?, ?, ?, ?)",
        (session_id, "Test Study", "completed", "2026-01-01", "2026-01-01"),
    )
    for i in range(num_messages):
        msg_id = f"msg_{i}"
        role = "user" if i % 2 == 0 else "assistant"
        conn.execute(
            "INSERT INTO messages VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (msg_id, session_id, role, f"message {i}", "2026-01-01", None, None, "assistant", i),
        )
        conn.execute(
            "INSERT INTO message_parts VALUES (?, ?, ?, ?, ?, ?)",
            (f"part_{i}", msg_id, "text", f'{{"content": "content {i}"}}', 0, "2026-01-01"),
        )
    conn.commit()
    conn.close()


class TestBackfillEventLog:
    def test_backfill_empty_db(self, tmp_path):
        from strategy_research.api.session.backfill_event_log import backfill_event_log
        db_path = tmp_path / "empty.db"
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                title TEXT,
                status TEXT,
                created_at TEXT,
                updated_at TEXT
            )
        """)
        ensure_event_log_schema(conn)
        conn.close()
        result = backfill_event_log(db_path)
        assert result["sessions_total"] == 0
        assert result["events_inserted"] == 0

    def test_backfill_creates_events(self, tmp_path):
        from strategy_research.api.session.backfill_event_log import backfill_event_log
        db_path = tmp_path / "legacy.db"
        _setup_legacy_db(db_path, session_id="s1", num_messages=3)
        result = backfill_event_log(db_path)
        assert result["sessions_backfilled"] >= 1
        assert result["events_inserted"] > 0

    def test_backfill_idempotent(self, tmp_path):
        from strategy_research.api.session.backfill_event_log import backfill_event_log
        db_path = tmp_path / "legacy.db"
        _setup_legacy_db(db_path, session_id="s1", num_messages=3)
        backfill_event_log(db_path)
        result = backfill_event_log(db_path)
        assert result["sessions_skipped"] >= 1

    def test_backfill_force_reinserts(self, tmp_path):
        from strategy_research.api.session.backfill_event_log import backfill_event_log
        db_path = tmp_path / "legacy.db"
        _setup_legacy_db(db_path, session_id="s1", num_messages=3)
        backfill_event_log(db_path)
        result = backfill_event_log(db_path, force=True)
        assert result["sessions_backfilled"] >= 1

    def test_backfill_specific_session(self, tmp_path):
        from strategy_research.api.session.backfill_event_log import backfill_event_log
        db_path = tmp_path / "legacy.db"
        _setup_legacy_db(db_path, session_id="s1", num_messages=2)
        # Add second session manually
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("INSERT INTO sessions VALUES (?, ?, ?, ?, ?)", ("s2", "Study 2", "done", "2026-01-01", "2026-01-01"))
        conn.execute("INSERT INTO messages VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", ("msg_s2_0", "s2", "user", "hello", "2026-01-01", None, None, "assistant", 0))
        conn.execute("INSERT INTO message_parts VALUES (?, ?, ?, ?, ?, ?)", ("part_s2_0", "msg_s2_0", "text", '{"content": "hello"}', 0, "2026-01-01"))
        conn.commit()
        conn.close()
        result = backfill_event_log(db_path, session_id="s1")
        assert result["sessions_total"] >= 1

    def test_backfill_stats_structure(self, tmp_path):
        from strategy_research.api.session.backfill_event_log import backfill_event_log
        db_path = tmp_path / "legacy.db"
        _setup_legacy_db(db_path)
        result = backfill_event_log(db_path)
        assert "sessions_total" in result
        assert "sessions_backfilled" in result
        assert "sessions_skipped" in result
        assert "events_inserted" in result


class TestGenerateEvents:
    def test_generate_for_user_message(self, tmp_path):
        from strategy_research.api.session.backfill_event_log import _generate_events_for_session
        db_path = tmp_path / "legacy.db"
        _setup_legacy_db(db_path, session_id="s1", num_messages=1)
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        events = _generate_events_for_session(conn, "s1")
        conn.close()
        assert len(events) > 0
        types = [e["type"] for e in events]
        assert "message_received" in types or "text.started" in types

    def test_generate_for_assistant_message(self, tmp_path):
        from strategy_research.api.session.backfill_event_log import _generate_events_for_session
        db_path = tmp_path / "legacy.db"
        _setup_legacy_db(db_path, session_id="s1", num_messages=1)
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("UPDATE messages SET role='assistant' WHERE session_id='s1'")
        conn.commit()
        events = _generate_events_for_session(conn, "s1")
        conn.close()
        types = [e["type"] for e in events]
        assert "text.started" in types or "text.ended" in types

    def test_generate_empty_session(self, tmp_path):
        from strategy_research.api.session.backfill_event_log import _generate_events_for_session
        db_path = tmp_path / "legacy.db"
        _setup_legacy_db(db_path, session_id="empty", num_messages=0)
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        events = _generate_events_for_session(conn, "empty")
        conn.close()
        assert len(events) == 0
