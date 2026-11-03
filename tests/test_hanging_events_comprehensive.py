"""HangingEventsStore comprehensive tests — record, count, report, list, clear."""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from strategy_research.foundation.observability.hanging_events import (
    HangingEventsStore,
    EVENT_WALLCLOCK,
    EVENT_LOG_STALL,
    EVENT_NO_PROGRESS,
    EVENT_CIRCUIT_OPEN,
    EVENT_WATCHDOG,
    EVENT_CHAT_STALL,
    _ALL_EVENT_TYPES,
)


@pytest.fixture
def store(tmp_path):
    """Create a fresh HangingEventsStore."""
    s = HangingEventsStore(db_path=tmp_path / "hanging.db")
    yield s
    s.close()


# ── record() ──────────────────────────────────────────────────────


class TestHangingEventsRecord:
    def test_record_inserts_event(self, store):
        store.record(EVENT_WALLCLOCK, study_id="s1", session_id="sess1", detail="timeout")
        count = store.count_since(hours=1)
        assert count[EVENT_WALLCLOCK] == 1

    def test_record_all_event_types(self, store):
        for etype in _ALL_EVENT_TYPES:
            store.record(etype, study_id="s1")
        count = store.count_since(hours=1)
        for etype in _ALL_EVENT_TYPES:
            assert count[etype] == 1

    def test_record_unknown_type_ignored(self, store):
        store.record("unknown_type", study_id="s1")
        count = store.count_since(hours=1)
        assert all(v == 0 for v in count.values())

    def test_record_multiple_same_type(self, store):
        for _ in range(5):
            store.record(EVENT_NO_PROGRESS, study_id="s1")
        count = store.count_since(hours=1)
        assert count[EVENT_NO_PROGRESS] == 5

    def test_record_with_detail(self, store):
        store.record(EVENT_WALLCLOCK, study_id="s1", detail="wall clock exceeded")
        recent = store.list_recent(hours=1)
        assert len(recent) == 1
        assert recent[0]["detail"] == "wall clock exceeded"

    def test_record_with_session_id(self, store):
        store.record(EVENT_CIRCUIT_OPEN, study_id="s1", session_id="sess1")
        recent = store.list_recent(hours=1)
        assert recent[0]["session_id"] == "sess1"


# ── count_since() ────────────────────────────────────────────────


class TestHangingEventsCountSince:
    def test_count_empty(self, store):
        count = store.count_since(hours=1)
        assert all(v == 0 for v in count.values())

    def test_count_after_records(self, store):
        store.record(EVENT_WALLCLOCK, study_id="s1")
        store.record(EVENT_WALLCLOCK, study_id="s1")
        store.record(EVENT_LOG_STALL, study_id="s1")
        count = store.count_since(hours=1)
        assert count[EVENT_WALLCLOCK] == 2
        assert count[EVENT_LOG_STALL] == 1

    def test_count_filter_by_session(self, store):
        store.record(EVENT_NO_PROGRESS, session_id="s1")
        store.record(EVENT_NO_PROGRESS, session_id="s2")
        count = store.count_since(hours=1, session_id="s1")
        assert count[EVENT_NO_PROGRESS] == 1

    def test_count_filter_by_study(self, store):
        store.record(EVENT_CIRCUIT_OPEN, study_id="s1")
        store.record(EVENT_CIRCUIT_OPEN, study_id="s2")
        count = store.count_since(hours=1, study_id="s1")
        assert count[EVENT_CIRCUIT_OPEN] == 1

    def test_count_ignores_old_events(self, store):
        """Events older than hours window are excluded."""
        # Manually insert old event
        with store._lock:
            with store._conn:
                store._conn.execute(
                    "INSERT INTO hanging_events (event_type, study_id, created_at) VALUES (?, ?, ?)",
                    (EVENT_WALLCLOCK, "s1", time.time() - 86400 * 2),  # 2 days ago
                )
        count = store.count_since(hours=24)
        assert count[EVENT_WALLCLOCK] == 0

    def test_count_returns_all_types(self, store):
        count = store.count_since(hours=1)
        assert set(count.keys()) == _ALL_EVENT_TYPES


# ── report() ──────────────────────────────────────────────────────


class TestHangingEventsReport:
    def test_report_empty(self, store):
        report = store.report(hours=1)
        assert report["total_events"] == 0
        assert report["by_type"] == {t: 0 for t in _ALL_EVENT_TYPES}
        assert report["by_study"] == []
        assert report["recent"] == []

    def test_report_with_events(self, store):
        store.record(EVENT_WALLCLOCK, study_id="s1", detail="timeout1")
        store.record(EVENT_WALLCLOCK, study_id="s1", detail="timeout2")
        store.record(EVENT_LOG_STALL, study_id="s2")
        report = store.report(hours=1)
        assert report["total_events"] == 3
        assert report["by_type"][EVENT_WALLCLOCK] == 2
        assert report["by_type"][EVENT_LOG_STALL] == 1
        assert len(report["by_study"]) == 2
        assert len(report["recent"]) == 3

    def test_report_by_study_sorted(self, store):
        for _ in range(3):
            store.record(EVENT_NO_PROGRESS, study_id="s1")
        store.record(EVENT_NO_PROGRESS, study_id="s2")
        report = store.report(hours=1)
        assert report["by_study"][0]["study_id"] == "s1"
        assert report["by_study"][0]["count"] == 3

    def test_report_recent_has_iso_timestamps(self, store):
        store.record(EVENT_WALLCLOCK, study_id="s1")
        report = store.report(hours=1)
        assert "created_at_iso" in report["recent"][0]

    def test_report_window_hours(self, store):
        report = store.report(hours=1)
        assert report["window_hours"] == 1


# ── list_recent() ────────────────────────────────────────────────


class TestHangingEventsListRecent:
    def test_list_recent_empty(self, store):
        assert store.list_recent(hours=1) == []

    def test_list_recent_with_events(self, store):
        store.record(EVENT_WALLCLOCK, study_id="s1")
        store.record(EVENT_LOG_STALL, study_id="s1")
        events = store.list_recent(hours=1)
        assert len(events) == 2

    def test_list_recent_filter_by_study(self, store):
        store.record(EVENT_WALLCLOCK, study_id="s1")
        store.record(EVENT_WALLCLOCK, study_id="s2")
        events = store.list_recent(hours=1, study_id="s1")
        assert len(events) == 1

    def test_list_recent_ordered_newest_first(self, store):
        store.record(EVENT_WALLCLOCK, study_id="s1")
        time.sleep(0.01)
        store.record(EVENT_LOG_STALL, study_id="s1")
        events = store.list_recent(hours=1)
        assert events[0]["event_type"] == EVENT_LOG_STALL
        assert events[1]["event_type"] == EVENT_WALLCLOCK

    def test_list_recent_limit(self, store):
        for _ in range(10):
            store.record(EVENT_NO_PROGRESS, study_id="s1")
        events = store.list_recent(hours=1, limit=3)
        assert len(events) == 3

    def test_list_recent_has_iso_timestamps(self, store):
        store.record(EVENT_WALLCLOCK, study_id="s1")
        events = store.list_recent(hours=1)
        assert "created_at_iso" in events[0]


# ── clear() ───────────────────────────────────────────────────────


class TestHangingEventsClear:
    def test_clear_all(self, store):
        store.record(EVENT_WALLCLOCK, study_id="s1")
        store.record(EVENT_LOG_STALL, study_id="s1")
        removed = store.clear()
        assert removed == 2
        count = store.count_since(hours=1)
        assert all(v == 0 for v in count.values())

    def test_clear_old_only(self, store):
        # Insert old event
        with store._lock:
            with store._conn:
                store._conn.execute(
                    "INSERT INTO hanging_events (event_type, study_id, created_at) VALUES (?, ?, ?)",
                    (EVENT_WALLCLOCK, "s1", time.time() - 86400 * 2),
                )
        store.record(EVENT_LOG_STALL, study_id="s1")
        removed = store.clear(hours=24)
        assert removed == 1
        count = store.count_since(hours=1)
        assert count[EVENT_LOG_STALL] == 1

    def test_clear_empty(self, store):
        removed = store.clear()
        assert removed == 0


# ── context manager ──────────────────────────────────────────────


class TestHangingEventsContextManager:
    def test_context_manager(self, tmp_path):
        with HangingEventsStore(db_path=tmp_path / "ctx.db") as store:
            store.record(EVENT_WALLCLOCK, study_id="s1")
            assert store.count_since(hours=1)[EVENT_WALLCLOCK] == 1
        # Store is closed after context manager
        assert store._conn is None
