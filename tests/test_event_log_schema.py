"""Tests for the event_log table (Level 3, Phase 3 B1 commit 1).

The event_log table is the foundation for event sourcing. These tests
verify the schema is correct, idempotent, and supports the opencode-style
event-sourcing invariants.

Invariants:
1. Schema: id (PK), aggregate_id (FK), seq, type, data_json, time_created
2. UNIQUE (aggregate_id, seq) — append-only guarantee
3. Foreign key to sessions(id) ON DELETE CASCADE
4. Indexes: (aggregate_id, seq) and (type, time_created)
5. Idempotent _ensure_schema (can be called multiple times)

The test uses a fresh in-memory SQLite DB to avoid coupling with
the production schema.
"""
from __future__ import annotations

import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path
import agentnodes.interfaces.api.session.web_session_db as _wsdb


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


class TestEventLogSchema(unittest.TestCase):
    """Verify event_log table schema and constraints."""

    def setUp(self) -> None:
        self.tmp = tempfile.NamedTemporaryFile(
            suffix=".db", delete=False
        )
        self.tmp.close()
        self.db_path = self.tmp.name
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self._create_minimal_schema()

    def tearDown(self) -> None:
        self.conn.close()
        Path(self.db_path).unlink(missing_ok=True)

    def _create_minimal_schema(self) -> None:
        """Create just the parent tables event_log references."""
        self.conn.execute(
            "CREATE TABLE sessions (id TEXT PRIMARY KEY)"
        )
        self._create_event_log()

    def _create_event_log(self) -> None:
        """Run the same CREATE TABLE statement as _ensure_schema."""
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS event_log (
                id TEXT PRIMARY KEY,
                aggregate_id TEXT NOT NULL,
                seq INTEGER NOT NULL,
                type TEXT NOT NULL,
                data_json TEXT NOT NULL,
                time_created REAL NOT NULL,
                FOREIGN KEY (aggregate_id) REFERENCES sessions(id) ON DELETE CASCADE,
                UNIQUE (aggregate_id, seq)
            )
            """
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_event_log_aggregate_seq "
            "ON event_log(aggregate_id, seq)"
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_event_log_type_time "
            "ON event_log(type, time_created)"
        )
        self.conn.commit()

    def test_columns(self) -> None:
        cols = {r[1]: r[2] for r in self.conn.execute(
            "PRAGMA table_info(event_log)"
        ).fetchall()}
        self.assertEqual(set(cols.keys()), {
            "id", "aggregate_id", "seq", "type",
            "data_json", "time_created",
        })
        self.assertEqual(cols["id"], "TEXT")
        self.assertEqual(cols["aggregate_id"], "TEXT")
        self.assertEqual(cols["seq"], "INTEGER")
        self.assertEqual(cols["type"], "TEXT")
        self.assertEqual(cols["data_json"], "TEXT")
        self.assertEqual(cols["time_created"], "REAL")

    def test_unique_aggregate_seq(self) -> None:
        """UNIQUE (aggregate_id, seq) prevents duplicate events."""
        self.conn.execute(
            "INSERT INTO sessions (id) VALUES (?)", ("s1",)
        )
        self.conn.execute(
            "INSERT INTO event_log (id, aggregate_id, seq, type, "
            "data_json, time_created) VALUES (?, ?, ?, ?, ?, ?)",
            ("e1", "s1", 1, "text.started", "{}", time.time()),
        )
        self.conn.commit()
        # Same aggregate, same seq → must fail
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                "INSERT INTO event_log (id, aggregate_id, seq, type, "
                "data_json, time_created) VALUES (?, ?, ?, ?, ?, ?)",
                ("e2", "s1", 1, "text.started", "{}", time.time()),
            )
        # Different aggregate, same seq → OK (per-aggregate seq)
        self.conn.execute(
            "INSERT INTO sessions (id) VALUES (?)", ("s2",)
        )
        self.conn.execute(
            "INSERT INTO event_log (id, aggregate_id, seq, type, "
            "data_json, time_created) VALUES (?, ?, ?, ?, ?, ?)",
            ("e3", "s2", 1, "text.started", "{}", time.time()),
        )
        self.conn.commit()

    def test_same_aggregate_increasing_seq_ok(self) -> None:
        """Same aggregate, increasing seq → OK."""
        self.conn.execute(
            "INSERT INTO sessions (id) VALUES (?)", ("s1",)
        )
        for i in range(1, 6):
            self.conn.execute(
                "INSERT INTO event_log (id, aggregate_id, seq, type, "
                "data_json, time_created) VALUES (?, ?, ?, ?, ?, ?)",
                (f"e{i}", "s1", i, "text.started", "{}", time.time()),
            )
        self.conn.commit()
        count = self.conn.execute(
            "SELECT COUNT(*) FROM event_log WHERE aggregate_id = ?",
            ("s1",),
        ).fetchone()[0]
        self.assertEqual(count, 5)

    def test_cascade_delete_on_session(self) -> None:
        """Deleting a session deletes its events (FK CASCADE)."""
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.execute(
            "INSERT INTO sessions (id) VALUES (?)", ("s1",)
        )
        self.conn.execute(
            "INSERT INTO event_log (id, aggregate_id, seq, type, "
            "data_json, time_created) VALUES (?, ?, ?, ?, ?, ?)",
            ("e1", "s1", 1, "text.started", "{}", time.time()),
        )
        self.conn.commit()
        self.conn.execute("DELETE FROM sessions WHERE id = ?", ("s1",))
        self.conn.commit()
        count = self.conn.execute(
            "SELECT COUNT(*) FROM event_log WHERE aggregate_id = ?",
            ("s1",),
        ).fetchone()[0]
        self.assertEqual(count, 0, "FK CASCADE should delete events")

    def test_index_aggregate_seq_exists(self) -> None:
        idx_names = {
            r[0] for r in self.conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='index' AND tbl_name='event_log'"
            ).fetchall()
        }
        self.assertIn("idx_event_log_aggregate_seq", idx_names)

    def test_index_type_time_exists(self) -> None:
        idx_names = {
            r[0] for r in self.conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='index' AND tbl_name='event_log'"
            ).fetchall()
        }
        self.assertIn("idx_event_log_type_time", idx_names)

    def test_idempotent_create(self) -> None:
        """Re-running CREATE TABLE IF NOT EXISTS is a no-op."""
        for _ in range(3):
            self._create_event_log()
        # Schema should still be valid
        cols = self.conn.execute(
            "PRAGMA table_info(event_log)"
        ).fetchall()
        self.assertEqual(len(cols), 6)

    def test_data_json_persists_complex_payload(self) -> None:
        """data_json can store nested JSON (event payloads are structured)."""
        import json
        self.conn.execute(
            "INSERT INTO sessions (id) VALUES (?)", ("s1",)
        )
        payload = {
            "text_id": "abc-123",
            "text": "Hello world",
            "tokens": 42,
            "nested": {"key": "value"},
        }
        self.conn.execute(
            "INSERT INTO event_log (id, aggregate_id, seq, type, "
            "data_json, time_created) VALUES (?, ?, ?, ?, ?, ?)",
            ("e1", "s1", 1, "text_delta", json.dumps(payload), time.time()),
        )
        self.conn.commit()
        row = self.conn.execute(
            "SELECT data_json FROM event_log WHERE id = ?", ("e1",)
        ).fetchone()
        loaded = json.loads(row["data_json"])
        self.assertEqual(loaded, payload)


class TestEventLogIntegrationWithEnsureSchema(unittest.TestCase):
    """Verify _ensure_schema in web_session.py creates event_log."""

    def setUp(self) -> None:
        import os
        # Redirect db to a fresh temp file
        self.tmp = tempfile.NamedTemporaryFile(
            suffix=".db", delete=False
        )
        self.tmp.close()
        self.db_path = self.tmp.name
        # Preserve the prior value so tearDown can restore it — leaking
        # SR_WORKSPACE_PATH into later tests redirects the whole suite's
        # session DB to this temp dir (shared-lock failures downstream).
        self._orig_workspace_env = os.environ.get("SR_WORKSPACE_PATH")
        os.environ["SR_WORKSPACE_PATH"] = str(Path(self.db_path).parent)
        # Patch the path lookup (facade binding too — value-bound re-exports
        # don't follow the impl-module patch).
        import agentnodes.interfaces.api.routers.web_session as ws
        self._orig_get_db_path = _wsdb._get_db_path
        _wsdb._get_db_path = lambda: Path(self.db_path)
        ws._get_db_path = lambda: Path(self.db_path)

    def tearDown(self) -> None:
        import os

        import agentnodes.interfaces.api.routers.web_session as ws
        # Release the per-thread cached connection before unlink
        # (WinError-32 family).
        try:
            conn = ws._get_db()
            conn.close()
            cache = getattr(_wsdb._db_thread_local, "conns", None)
            if cache is not None:
                cache.clear()
        except Exception:
            pass
        _wsdb._get_db_path = self._orig_get_db_path
        ws._get_db_path = self._orig_get_db_path
        if self._orig_workspace_env is None:
            os.environ.pop("SR_WORKSPACE_PATH", None)
        else:
            os.environ["SR_WORKSPACE_PATH"] = self._orig_workspace_env
        Path(self.db_path).unlink(missing_ok=True)

    def test_ensure_schema_creates_event_log(self) -> None:
        import agentnodes.interfaces.api.routers.web_session as ws
        with ws._get_db() as conn:
            tables = {
                r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            self.assertIn("event_log", tables)

    def test_ensure_schema_creates_event_log_indexes(self) -> None:
        import agentnodes.interfaces.api.routers.web_session as ws
        with ws._get_db() as conn:
            idx_names = {
                r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='index' AND tbl_name='event_log'"
                ).fetchall()
            }
            self.assertIn("idx_event_log_aggregate_seq", idx_names)
            self.assertIn("idx_event_log_type_time", idx_names)

    def test_ensure_schema_idempotent(self) -> None:
        import agentnodes.interfaces.api.routers.web_session as ws
        with ws._get_db():
            pass
        with ws._get_db():
            pass
        with ws._get_db():
            pass


if __name__ == "__main__":
    unittest.main()
