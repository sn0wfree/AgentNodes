"""Tests for EventV2 dataclass + EventType registry (Level 3, B1 commit 2).

The EventV2 envelope is the data shape that EventBusV2 publishes and
the projector consumes. These tests verify:
1. EventType registry: all expected types are present, no typos
2. EventV2.create: assigns id/seq/timestamp, validates inputs
3. to_dict / from_dict: round-trip
4. to_json / from_json: round-trip
5. to_row / from_row: round-trip with event_log schema
6. Helper predicates: is_message_lifecycle, is_text_event, etc.
"""
from __future__ import annotations

import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from strategy_research.kernel.events.event_v2 import (
    EventType,
    EventV2,
    is_known_event_type,
)


class TestEventTypeRegistry(unittest.TestCase):
    """Verify the EventType registry has all expected types."""

    def test_text_events(self) -> None:
        self.assertEqual(EventType.TEXT_STARTED, "text.started")
        self.assertEqual(EventType.TEXT_DELTA, "text_delta")
        self.assertEqual(EventType.TEXT_ENDED, "text.ended")

    def test_tool_events(self) -> None:
        self.assertEqual(EventType.TOOL_CALL, "tool_call")
        self.assertEqual(EventType.TOOL_RESULT, "tool_result")
        self.assertEqual(EventType.TOOL_PROGRESS, "tool_progress")
        self.assertEqual(EventType.TOOL_HEARTBEAT, "tool_heartbeat")
        self.assertEqual(EventType.TOOL_INPUT, "tool.input")

    def test_message_lifecycle(self) -> None:
        self.assertEqual(EventType.MESSAGE_RECEIVED, "message_received")
        self.assertEqual(EventType.ASSISTANT_MESSAGE, "assistant_message")
        self.assertEqual(EventType.AGENT_DONE, "agent_done")

    def test_session_lifecycle(self) -> None:
        self.assertEqual(EventType.SESSION_CREATED, "session.created")
        self.assertEqual(EventType.ATTEMPT_CREATED, "attempt.created")
        self.assertEqual(EventType.QUEUE_STATE, "queue_state")
        self.assertEqual(EventType.QUEUE_PAUSED, "queue_paused")

    def test_thinking_events(self) -> None:
        self.assertEqual(EventType.THINKING_START, "thinking_start")
        self.assertEqual(EventType.THINKING_DELTA, "thinking_delta")
        self.assertEqual(EventType.THINKING_DONE, "thinking_done")
        self.assertEqual(EventType.THINKING_END, "thinking_end")

    def test_iteration_events(self) -> None:
        self.assertEqual(EventType.ITER_START, "iter_start")
        self.assertEqual(EventType.ITER_END, "iter_end")

    def test_llm_usage(self) -> None:
        self.assertEqual(EventType.LLM_USAGE, "llm_usage")
        self.assertEqual(EventType.SESSION_TOTAL_TOKENS, "session_total_tokens")

    def test_compaction(self) -> None:
        self.assertEqual(EventType.COMPACT, "compact")
        self.assertEqual(EventType.COMPACT_STARTED, "compact.started")
        self.assertEqual(EventType.COMPACT_ENDED, "compact.ended")

    def test_is_known_event_type(self) -> None:
        self.assertTrue(is_known_event_type(EventType.TEXT_DELTA))
        self.assertTrue(is_known_event_type("message_received"))
        self.assertFalse(is_known_event_type("not.a.real.type"))
        self.assertFalse(is_known_event_type(""))

    def test_all_constants_are_strings(self) -> None:
        """All EventType constants must be non-empty strings."""
        for name in dir(EventType):
            if name.isupper() and not name.startswith("_"):
                value = getattr(EventType, name)
                self.assertIsInstance(value, str)
                self.assertGreater(len(value), 0, f"{name} is empty")


class TestEventV2Create(unittest.TestCase):
    """Verify EventV2.create factory method."""

    def test_create_minimal(self) -> None:
        e = EventV2.create(
            aggregate_id="s1",
            seq=1,
            type=EventType.TEXT_DELTA,
        )
        self.assertEqual(e.aggregate_id, "s1")
        self.assertEqual(e.seq, 1)
        self.assertEqual(e.type, EventType.TEXT_DELTA)
        self.assertEqual(e.data, {})
        self.assertIsInstance(e.id, str)
        self.assertGreater(len(e.id), 0)
        self.assertIsInstance(e.time_created, float)

    def test_create_with_data(self) -> None:
        e = EventV2.create(
            aggregate_id="s1",
            seq=2,
            type=EventType.TEXT_DELTA,
            data={"text_id": "abc", "text": "hello"},
        )
        self.assertEqual(e.data, {"text_id": "abc", "text": "hello"})

    def test_create_unique_ids(self) -> None:
        """Each call should produce a unique id."""
        ids = set()
        for _ in range(100):
            e = EventV2.create("s1", 1, EventType.TEXT_DELTA)
            ids.add(e.id)
        self.assertEqual(len(ids), 100, "ids should be unique")

    def test_create_validates_aggregate_id(self) -> None:
        with self.assertRaises(ValueError):
            EventV2.create("", 1, EventType.TEXT_DELTA)

    def test_create_validates_seq(self) -> None:
        with self.assertRaises(ValueError):
            EventV2.create("s1", 0, EventType.TEXT_DELTA)
        with self.assertRaises(ValueError):
            EventV2.create("s1", -1, EventType.TEXT_DELTA)

    def test_create_validates_type(self) -> None:
        with self.assertRaises(ValueError):
            EventV2.create("s1", 1, "")

    def test_create_timestamps_close_to_now(self) -> None:
        before = time.time()
        e = EventV2.create("s1", 1, EventType.TEXT_DELTA)
        after = time.time()
        self.assertGreaterEqual(e.time_created, before)
        self.assertLessEqual(e.time_created, after)


class TestEventV2Serialization(unittest.TestCase):
    """Verify to_dict / from_dict / to_json / from_json round-trip."""

    def test_dict_round_trip(self) -> None:
        e = EventV2.create(
            aggregate_id="s1",
            seq=42,
            type=EventType.TOOL_RESULT,
            data={"id": "tc-1", "result": "42", "status": "done"},
        )
        d = e.to_dict()
        e2 = EventV2.from_dict(d)
        self.assertEqual(e2.id, e.id)
        self.assertEqual(e2.aggregate_id, e.aggregate_id)
        self.assertEqual(e2.seq, e.seq)
        self.assertEqual(e2.type, e.type)
        self.assertEqual(e2.data, e.data)
        self.assertEqual(e2.time_created, e.time_created)

    def test_json_round_trip(self) -> None:
        e = EventV2.create(
            aggregate_id="s1",
            seq=1,
            type=EventType.TEXT_DELTA,
            data={"text": "你好", "unicode": "✓"},
        )
        s = e.to_json()
        e2 = EventV2.from_json(s)
        self.assertEqual(e2.id, e.id)
        self.assertEqual(e2.data, e.data)

    def test_from_dict_missing_field_raises(self) -> None:
        with self.assertRaises(ValueError):
            EventV2.from_dict({"id": "e1"})  # missing aggregate_id, etc.

    def test_from_dict_extra_fields_ignored(self) -> None:
        """Forward-compat: unknown fields are silently dropped."""
        d = {
            "id": "e1",
            "aggregate_id": "s1",
            "seq": 1,
            "type": EventType.TEXT_DELTA,
            "data": {},
            "time_created": time.time(),
            "future_field": "ignore me",
            "another": 42,
        }
        e = EventV2.from_dict(d)
        self.assertEqual(e.id, "e1")

    def test_from_dict_defensive_data(self) -> None:
        """data is optional; defaults to {}."""
        d = {
            "id": "e1",
            "aggregate_id": "s1",
            "seq": 1,
            "type": EventType.TEXT_DELTA,
            "time_created": time.time(),
        }
        e = EventV2.from_dict(d)
        self.assertEqual(e.data, {})

    def test_from_dict_none_data(self) -> None:
        d = {
            "id": "e1",
            "aggregate_id": "s1",
            "seq": 1,
            "type": EventType.TEXT_DELTA,
            "data": None,
            "time_created": time.time(),
        }
        e = EventV2.from_dict(d)
        self.assertEqual(e.data, {})


class TestEventV2RowRoundTrip(unittest.TestCase):
    """Verify to_row / from_row with event_log schema."""

    def setUp(self) -> None:
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db_path = self.tmp.name
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(
            "CREATE TABLE sessions (id TEXT PRIMARY KEY)"
        )
        self.conn.execute(
            """
            CREATE TABLE event_log (
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
        self.conn.commit()

    def tearDown(self) -> None:
        self.conn.close()
        Path(self.db_path).unlink(missing_ok=True)

    def test_row_round_trip(self) -> None:
        e = EventV2.create(
            aggregate_id="s1",
            seq=1,
            type=EventType.TOOL_RESULT,
            data={"id": "tc-1", "result": "42", "status": "done"},
        )
        self.conn.execute(
            "INSERT INTO sessions (id) VALUES (?)", ("s1",)
        )
        row = e.to_row()
        self.conn.execute(
            "INSERT INTO event_log (id, aggregate_id, seq, type, "
            "data_json, time_created) VALUES (?, ?, ?, ?, ?, ?)",
            (row["id"], row["aggregate_id"], row["seq"], row["type"],
             row["data_json"], row["time_created"]),
        )
        self.conn.commit()

        # Read back
        r = self.conn.execute(
            "SELECT * FROM event_log WHERE id = ?", (e.id,)
        ).fetchone()
        e2 = EventV2.from_row(r)
        self.assertEqual(e2.id, e.id)
        self.assertEqual(e2.aggregate_id, e.aggregate_id)
        self.assertEqual(e2.seq, e.seq)
        self.assertEqual(e2.type, e.type)
        self.assertEqual(e2.data, e.data)
        self.assertEqual(e2.time_created, e.time_created)


class TestEventV2Predicates(unittest.TestCase):
    """Verify the is_* helper predicates."""

    def test_is_message_lifecycle(self) -> None:
        self.assertTrue(
            EventV2.create("s", 1, EventType.MESSAGE_RECEIVED).is_message_lifecycle()
        )
        self.assertTrue(
            EventV2.create("s", 1, EventType.ASSISTANT_MESSAGE).is_message_lifecycle()
        )
        self.assertFalse(
            EventV2.create("s", 1, EventType.TEXT_DELTA).is_message_lifecycle()
        )
        self.assertFalse(
            EventV2.create("s", 1, EventType.TOOL_RESULT).is_message_lifecycle()
        )

    def test_is_text_event(self) -> None:
        self.assertTrue(
            EventV2.create("s", 1, EventType.TEXT_STARTED).is_text_event()
        )
        self.assertTrue(
            EventV2.create("s", 1, EventType.TEXT_DELTA).is_text_event()
        )
        self.assertTrue(
            EventV2.create("s", 1, EventType.TEXT_ENDED).is_text_event()
        )
        self.assertFalse(
            EventV2.create("s", 1, EventType.TOOL_CALL).is_text_event()
        )

    def test_is_tool_event(self) -> None:
        self.assertTrue(
            EventV2.create("s", 1, EventType.TOOL_CALL).is_tool_event()
        )
        self.assertTrue(
            EventV2.create("s", 1, EventType.TOOL_RESULT).is_tool_event()
        )
        self.assertTrue(
            EventV2.create("s", 1, EventType.TOOL_PROGRESS).is_tool_event()
        )
        self.assertFalse(
            EventV2.create("s", 1, EventType.TEXT_DELTA).is_tool_event()
        )

    def test_is_thinking_event(self) -> None:
        self.assertTrue(
            EventV2.create("s", 1, EventType.THINKING_START).is_thinking_event()
        )
        self.assertTrue(
            EventV2.create("s", 1, EventType.THINKING_DELTA).is_thinking_event()
        )
        self.assertTrue(
            EventV2.create("s", 1, EventType.THINKING_DONE).is_thinking_event()
        )
        self.assertTrue(
            EventV2.create("s", 1, EventType.THINKING_END).is_thinking_event()
        )
        self.assertFalse(
            EventV2.create("s", 1, EventType.TEXT_DELTA).is_thinking_event()
        )


if __name__ == "__main__":
    unittest.main()


# ── study events registered (v2 design §16.2) ───────────────────────


def test_study_events_registered():
    from strategy_research.kernel.events.event_v2 import is_known_event_type
    for name in (
        "study_queued", "study_started", "study_paused", "study_resumed",
        "study_cancelled", "study_early_stopped", "study_completed",
        "study_failed", "study_executor_stopped", "study_interrupted",
        "study_round", "study_round_rejected", "study_phase",
        "study_review", "study_todos_updated", "study_evidence",
        "study_progress", "study_budget_limited",
        "study_monitoring_started", "study_monitor_check",
        "study_monitor_check_failed", "study_drift_detected",
        "study_knowledge_check", "study_knowledge_update",
        "study_knowledge_compacted", "study_directives_consumed",
    ):
        assert is_known_event_type(name), f"{name} not registered"


# ── P0-1 A3 — parent_event_id + branch_id fields ──────────────────────


class TestParentAndBranchFields(unittest.TestCase):
    """P0-1 A3: the unified EventV2 carries ``parent_event_id`` and
    ``branch_id`` for trace trees and fork support.
    """

    def test_default_parent_none(self) -> None:
        e = EventV2.create("s", 1, EventType.LOOP_START, {})
        self.assertIsNone(e.parent_event_id)
        self.assertEqual(e.branch_id, "main")

    def test_explicit_parent_and_branch(self) -> None:
        e = EventV2.create(
            "s", 2, EventType.TOOL_CALL, {"x": 1},
            parent_event_id="abc-123", branch_id="exp1",
        )
        self.assertEqual(e.parent_event_id, "abc-123")
        self.assertEqual(e.branch_id, "exp1")

    def test_branch_id_must_be_nonempty(self) -> None:
        with self.assertRaises(ValueError):
            EventV2.create("s", 1, EventType.LOOP_START, {}, branch_id="")

    def test_to_dict_round_trip_includes_new_fields(self) -> None:
        e = EventV2.create(
            "s", 1, EventType.TOOL_CALL, {"k": 1},
            parent_event_id="p1", branch_id="b1",
        )
        e2 = EventV2.from_dict(e.to_dict())
        self.assertEqual(e2.parent_event_id, "p1")
        self.assertEqual(e2.branch_id, "b1")

    def test_from_dict_backfills_defaults_for_missing_fields(self) -> None:
        # Pre-A3 dicts lack the new keys — round-tripping them must still
        # produce a valid EventV2 with default values.
        e = EventV2.create("s", 1, EventType.LOOP_START, {"k": 1})
        d = e.to_dict()
        d.pop("parent_event_id", None)
        d.pop("branch_id", None)
        e2 = EventV2.from_dict(d)
        self.assertIsNone(e2.parent_event_id)
        self.assertEqual(e2.branch_id, "main")

    def test_to_row_includes_new_columns(self) -> None:
        e = EventV2.create(
            "s", 1, EventType.TOOL_CALL, {"k": 1},
            parent_event_id="p1", branch_id="b1",
        )
        row = e.to_row()
        self.assertEqual(row["parent_event_id"], "p1")
        self.assertEqual(row["branch_id"], "b1")

    def test_from_row_tolerates_missing_columns(self) -> None:
        # Pre-A3 rows have only the original 6 columns.
        legacy_row = {
            "id": "x", "aggregate_id": "s", "seq": 1, "type": "t",
            "data_json": "{}", "time_created": 0.0,
        }
        e = EventV2.from_row(legacy_row)
        self.assertIsNone(e.parent_event_id)
        self.assertEqual(e.branch_id, "main")

    def test_from_row_accepts_sqlite3_row(self) -> None:
        import sqlite3
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE t (id TEXT, aggregate_id TEXT, seq INT, "
                     "type TEXT, data_json TEXT, time_created REAL, "
                     "parent_event_id TEXT, branch_id TEXT)")
        conn.execute(
            "INSERT INTO t VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("e1", "s1", 1, "t", "{}", 1.0, "p1", "b1"),
        )
        row = conn.execute("SELECT * FROM t").fetchone()
        e = EventV2.from_row(row)
        self.assertEqual(e.parent_event_id, "p1")
        self.assertEqual(e.branch_id, "b1")
