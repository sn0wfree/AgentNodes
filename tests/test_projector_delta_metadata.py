"""Tests for projector delta-flush metadata_json serialization (Fix D).

The delta-flush path used Projector._message_row which omitted
metadata_json, so the first INSERT wrote NULL and the UPSERT's
COALESCE kept it NULL forever — losing agent_id for study messages.
"""
from __future__ import annotations

import json
import sqlite3
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agentnodes.interfaces.api.session.projector import (
    ProjectedMessage,
    ProjectedSession,
    Projector,
)


def _setup_db(db_path: Path) -> None:
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
        conn.commit()
    finally:
        conn.close()


class TestDeltaFlushMetadata(unittest.TestCase):
    def test_message_row_includes_metadata_json(self):
        msg = ProjectedMessage(
            id="m1", session_id="s1", role="assistant", content="hi",
            created_at=time.time(), metadata={"agent_id": "researcher"},
        )
        row = Projector._message_row(msg)
        self.assertIn("metadata_json", row)
        self.assertEqual(json.loads(row["metadata_json"])["agent_id"], "researcher")

    def test_message_row_metadata_none_when_empty(self):
        msg = ProjectedMessage(
            id="m2", session_id="s1", role="assistant", content="hi",
            created_at=time.time(),
        )
        row = Projector._message_row(msg)
        self.assertIsNone(row["metadata_json"])

    def test_delta_flush_persists_agent_id(self):
        """End-to-end: delta flush writes agent_id to messages table."""
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "test.db"
            _setup_db(db_path)
            conn = sqlite3.connect(str(db_path))
            conn.execute(
                "INSERT INTO sessions (id) VALUES ('s1')",
            )
            conn.commit()
            conn.close()

            proj = Projector(db_path)
            state = ProjectedSession(session_id="s1")
            state.messages["m1"] = ProjectedMessage(
                id="m1", session_id="s1", role="assistant", content="answer",
                created_at=time.time(),
                metadata={"agent_id": "strategist"},
            )
            # Delta flush: touched set (not full)
            proj.flush(state, touched={"m1"})
            # Release the sqlite handle before TemporaryDirectory
            # cleanup — Windows keeps the file locked otherwise
            # (WinError-32 flake family).
            proj.close()

            check = sqlite3.connect(str(db_path))
            row = check.execute(
                "SELECT metadata_json FROM messages WHERE id = 'm1'",
            ).fetchone()
            check.close()
            self.assertIsNotNone(row)
            self.assertIsNotNone(row[0], "metadata_json was NULL after delta flush")
            self.assertEqual(json.loads(row[0])["agent_id"], "strategist")

    def test_full_flush_and_delta_flush_serialize_identically(self):
        """Both serialization paths must produce the same keys."""
        msg = ProjectedMessage(
            id="m3", session_id="s1", role="assistant", content="x",
            created_at=123.0, metadata={"k": "v"},
        )
        state = ProjectedSession(session_id="s1")
        state.messages["m3"] = msg
        full_row = state.to_message_rows()[0]
        delta_row = Projector._message_row(msg)
        self.assertEqual(set(full_row.keys()), set(delta_row.keys()))
        self.assertEqual(full_row["metadata_json"], delta_row["metadata_json"])


if __name__ == "__main__":
    unittest.main()
