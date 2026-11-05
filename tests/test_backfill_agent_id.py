"""Tests for the backfill_agent_id script (Fix E)."""
from __future__ import annotations

import json
import sqlite3
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agentnodes.scripts.backfill_agent_id import backfill


def _setup_db(db_path: Path) -> None:
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE messages (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL DEFAULT '',
            created_at REAL,
            metadata_json TEXT,
            message_type TEXT,
            seq INTEGER DEFAULT 0
        )
    """)
    now = 1.0
    rows = [
        # (id, session_id, metadata_json)
        ("study:study_x:r3:researcher", "study:study_x:round:3", None),
        ("study:study_x:r3:strategist", "study:study_x:round:3", '{"model": "m1"}'),
        ("study:study_x:r3:data_quality", "study:study_x:round:3", '{"agent_id": "already"}'),
        # non-study message — must be untouched
        ("msg-uuid-1", "normal-session", None),
    ]
    for mid, sid, meta in rows:
        conn.execute(
            "INSERT INTO messages (id, session_id, role, content, created_at, metadata_json) "
            "VALUES (?, ?, 'assistant', 'c', ?, ?)",
            (mid, sid, now, meta),
        )
    conn.commit()
    conn.close()


class TestBackfillAgentId(unittest.TestCase):
    def test_backfill_idempotent_and_scoped(self):
        with TemporaryDirectory() as td:
            db_path = Path(td) / "test.db"
            _setup_db(db_path)

            # First run: 2 rows updated (researcher + strategist)
            updated = backfill(db_path, session_id=None, dry_run=False)
            self.assertEqual(updated, 2)

            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row

            # NULL metadata → fresh dict with agent_id
            r = conn.execute(
                "SELECT metadata_json FROM messages WHERE id = 'study:study_x:r3:researcher'",
            ).fetchone()
            self.assertEqual(json.loads(r["metadata_json"]), {"agent_id": "researcher"})

            # Existing metadata → merged, model preserved
            r = conn.execute(
                "SELECT metadata_json FROM messages WHERE id = 'study:study_x:r3:strategist'",
            ).fetchone()
            meta = json.loads(r["metadata_json"])
            self.assertEqual(meta["agent_id"], "strategist")
            self.assertEqual(meta["model"], "m1")

            # Already-set agent_id → unchanged (idempotent)
            r = conn.execute(
                "SELECT metadata_json FROM messages WHERE id = 'study:study_x:r3:data_quality'",
            ).fetchone()
            self.assertEqual(json.loads(r["metadata_json"]), {"agent_id": "already"})

            # Non-study message untouched
            r = conn.execute(
                "SELECT metadata_json FROM messages WHERE id = 'msg-uuid-1'",
            ).fetchone()
            self.assertIsNone(r["metadata_json"])
            conn.close()

            # Second run: 0 updates (idempotent)
            updated = backfill(db_path, session_id=None, dry_run=False)
            self.assertEqual(updated, 0)

    def test_session_filter_limits_scope(self):
        with TemporaryDirectory() as td:
            db_path = Path(td) / "test.db"
            _setup_db(db_path)
            conn = sqlite3.connect(str(db_path))
            conn.execute(
                "INSERT INTO messages (id, session_id, role, content, created_at) "
                "VALUES ('study:study_x:r2:researcher', 'study:study_x:round:2', 'assistant', 'c', 2.0)",
            )
            conn.commit()
            conn.close()

            updated = backfill(
                db_path, session_id="study:study_x:round:3", dry_run=False,
            )
            self.assertEqual(updated, 2)

    def test_dry_run_writes_nothing(self):
        with TemporaryDirectory() as td:
            db_path = Path(td) / "test.db"
            _setup_db(db_path)
            updated = backfill(db_path, session_id=None, dry_run=True)
            self.assertEqual(updated, 2)
            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row
            r = conn.execute(
                "SELECT metadata_json FROM messages WHERE id = 'study:study_x:r3:researcher'",
            ).fetchone()
            self.assertIsNone(r["metadata_json"])
            conn.close()


if __name__ == "__main__":
    unittest.main()
