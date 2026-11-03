"""P0-1 A4 — event_log UNIQUE migration tests.

Covers the in-place upgrade of ``UNIQUE (aggregate_id, seq)`` →
``UNIQUE (aggregate_id, branch_id, seq)`` plus data fidelity and idempotence.
"""
from __future__ import annotations

import sqlite3
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from strategy_research.foundation.storage.event_schema import (
    _existing_unique_sets,
    ensure_event_log_schema,
    migrate_event_log_unique,
)


def _create_old_event_log(conn: sqlite3.Connection) -> None:
    """Simulate a pre-A4 event_log table: 6 columns + old UNIQUE."""
    conn.execute(
        """
        CREATE TABLE event_log (
            id TEXT PRIMARY KEY,
            aggregate_id TEXT NOT NULL,
            seq INTEGER NOT NULL,
            type TEXT NOT NULL,
            data_json TEXT,
            time_created REAL NOT NULL,
            UNIQUE (aggregate_id, seq)
        )
        """
    )
    conn.execute(
        "CREATE INDEX idx_event_log_aggregate_seq "
        "ON event_log(aggregate_id, seq)"
    )


class TestMigrateFreshNoOp(unittest.TestCase):
    """Fresh DB (created via the canonical DDL) is already at the A4 shape."""

    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute("CREATE TABLE sessions(id TEXT PRIMARY KEY)")
        ensure_event_log_schema(self.conn)

    def test_migrate_returns_false(self) -> None:
        self.assertFalse(migrate_event_log_unique(self.conn))

    def test_unique_includes_branch_id(self) -> None:
        uniques = _existing_unique_sets(self.conn, "event_log")
        self.assertIn({"aggregate_id", "branch_id", "seq"}, uniques)


class TestMigrateOldDb(unittest.TestCase):
    """Pre-A4 DB is rebuilt to the new shape; data is preserved."""

    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute("CREATE TABLE sessions(id TEXT PRIMARY KEY)")
        _create_old_event_log(self.conn)
        self.conn.execute(
            "INSERT INTO event_log VALUES "
            "('e1','s1',1,'text.started','{\"k\":1}',1.0),"
            "('e2','s1',2,'text.started','{\"k\":2}',2.0),"
            "('e3','s2',1,'text.started','{}',3.0)"
        )
        self.conn.commit()

    def test_old_unique_shape_before_migration(self) -> None:
        uniques = _existing_unique_sets(self.conn, "event_log")
        self.assertIn({"aggregate_id", "seq"}, uniques)
        self.assertNotIn({"aggregate_id", "branch_id", "seq"}, uniques)

    def test_ensure_schema_adds_p0_1_columns(self) -> None:
        # This is what EventStore / web_session call first. It must
        # backfill parent_event_id / branch_id before the rebuild.
        ensure_event_log_schema(self.conn)
        cols = {row[1] for row in self.conn.execute("PRAGMA table_info(event_log)")}
        self.assertIn("parent_event_id", cols)
        self.assertIn("branch_id", cols)

    def test_migrate_returns_true(self) -> None:
        ensure_event_log_schema(self.conn)  # backfill columns first
        self.assertTrue(migrate_event_log_unique(self.conn))

    def test_unique_includes_branch_id_after_migration(self) -> None:
        ensure_event_log_schema(self.conn)
        migrate_event_log_unique(self.conn)
        uniques = _existing_unique_sets(self.conn, "event_log")
        self.assertIn({"aggregate_id", "branch_id", "seq"}, uniques)
        self.assertNotIn({"aggregate_id", "seq"}, uniques)

    def test_data_preserved_through_migration(self) -> None:
        ensure_event_log_schema(self.conn)
        migrate_event_log_unique(self.conn)
        rows = self.conn.execute(
            "SELECT id, aggregate_id, seq, type FROM event_log "
            "ORDER BY aggregate_id, seq"
        ).fetchall()
        self.assertEqual(
            rows,
            [("e1", "s1", 1, "text.started"),
             ("e2", "s1", 2, "text.started"),
             ("e3", "s2", 1, "text.started")],
        )

    def test_branch_id_defaulted_to_main_for_legacy_rows(self) -> None:
        ensure_event_log_schema(self.conn)
        migrate_event_log_unique(self.conn)
        branches = {r[0] for r in self.conn.execute(
            "SELECT DISTINCT branch_id FROM event_log"
        )}
        self.assertEqual(branches, {"main"})

    def test_index_recreated_after_rebuild(self) -> None:
        ensure_event_log_schema(self.conn)
        migrate_event_log_unique(self.conn)
        indexes = {
            row[1] for row in self.conn.execute("PRAGMA index_list(event_log)")
        }
        self.assertIn("idx_event_log_aggregate_seq", indexes)
        self.assertIn("idx_event_log_type_time", indexes)


class TestMigrateIdempotent(unittest.TestCase):
    def test_running_twice_is_a_noop(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE sessions(id TEXT PRIMARY KEY)")
        _create_old_event_log(conn)
        conn.commit()
        ensure_event_log_schema(conn)
        self.assertTrue(migrate_event_log_unique(conn))
        self.assertFalse(migrate_event_log_unique(conn))

    def test_missing_table_returns_false(self) -> None:
        conn = sqlite3.connect(":memory:")
        self.assertFalse(migrate_event_log_unique(conn))


class TestMigrateRejectsIncompleteTable(unittest.TestCase):
    def test_missing_required_columns_raises(self) -> None:
        """If ensure_event_log_schema hasn't been run first (i.e. the table
        is older than A1), the migration must fail loudly so callers can
        backfill the columns and re-run, instead of silently dropping
        data via INSERT INTO ... SELECT ... with mismatched columns.
        """
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE sessions(id TEXT PRIMARY KEY)")
        conn.execute(
            """
            CREATE TABLE event_log (
                id TEXT PRIMARY KEY,
                seq INTEGER NOT NULL,
                type TEXT NOT NULL,
                data_json TEXT,
                time_created REAL NOT NULL
            )
            """
        )
        conn.commit()
        with self.assertRaises(RuntimeError):
            migrate_event_log_unique(conn)


if __name__ == "__main__":
    unittest.main()
