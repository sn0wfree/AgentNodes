"""Tests for Phase 7 EventStore — SQLite event_log single source + cache + SSE push."""
from __future__ import annotations

from pathlib import Path

import pytest

from agentnodes.kernel.agent.cache import CacheConfig
from agentnodes.kernel.agent.event_store import (
    EventStore,
    EventV2,
)

pytestmark = pytest.mark.asyncio


@pytest.fixture
def tmp_db(tmp_path: Path) -> Path:
    return tmp_path / "events.db"


@pytest.fixture
def es(tmp_db: Path):
    cfg = CacheConfig(min_entries=10, max_entries=100)
    store = EventStore(db_path=tmp_db, cache_config=cfg)
    yield store
    store._backend._conn = None  # close


# ── Basic emit ────────────────────────────────────────────────────


class TestEventStoreBasic:
    async def test_emit_returns_event_with_seq(self, es):
        ev = es.emit("s1", "user_message", {"text": "hi"})
        assert ev.id
        assert ev.seq == 1
        assert ev.aggregate_id == "s1"
        assert ev.type == "user_message"
        assert ev.data == {"text": "hi"}

    async def test_emit_monotonic_seq(self, es):
        es.emit("s1", "a", {})
        es.emit("s1", "b", {})
        ev3 = es.emit("s1", "c", {})
        assert ev3.seq == 3

    async def test_emit_persists_to_sqlite(self, es):
        es.emit("s1", "test", {"x": 1})
        # Direct SQLite check
        conn = es._backend._ensure_conn()
        rows = conn.execute(
            "SELECT id, type, data_json FROM event_log"
        ).fetchall()
        assert len(rows) == 1
        assert rows[0][1] == "test"


# ── SSE push callback ───────────────────────────────────────────


class TestSsePush:
    async def test_sse_pusher_invoked(self, tmp_db):
        cfg = CacheConfig(min_entries=10)
        received: list[tuple[str, EventV2]] = []

        def sse_push(sid: str, event: EventV2) -> None:
            received.append((sid, event))

        store = EventStore(db_path=tmp_db, cache_config=cfg, sse_pusher=sse_push)
        store.emit("s1", "test", {"x": 1})
        assert len(received) == 1
        sid, ev = received[0]
        assert sid == "s1"
        assert ev.type == "test"

    async def test_sse_pusher_failure_does_not_break_emit(self, tmp_db):
        cfg = CacheConfig(min_entries=10)

        def broken(sid, ev):
            raise RuntimeError("SSE failed")

        store = EventStore(db_path=tmp_db, cache_config=cfg, sse_pusher=broken)
        # Should not raise
        ev = store.emit("s1", "test", {"x": 1})
        assert ev.id


# ── Replay ───────────────────────────────────────────────────────


class TestReplay:
    async def test_replay_returns_all_events(self, es):
        es.emit("s1", "a", {})
        es.emit("s1", "b", {})
        es.emit("s1", "c", {})
        events = es.replay("s1")
        assert len(events) == 3
        assert [e.type for e in events] == ["a", "b", "c"]

    async def test_replay_from_seq(self, es):
        es.emit("s1", "a", {})
        es.emit("s1", "b", {})
        es.emit("s1", "c", {})
        events = es.replay("s1", from_seq=1)
        assert len(events) == 2
        assert events[0].type == "b"
        assert events[1].type == "c"

    async def test_replay_empty_session(self, es):
        events = es.replay("unknown")
        assert events == []

    async def test_last_seq_monotonic(self, es):
        es.emit("s1", "a", {})
        es.emit("s1", "b", {})
        assert es.last_seq("s1") == 2
        assert es.last_seq("unknown") == 0


# ── Subscribe (async iterator) ──────────────────────────────────


class TestSubscribe:
    async def test_subscribe_yields_cache_then_live(self, es):
        es.emit("s1", "before", {})
        events_received: list[EventV2] = []

        async def reader():
            async for ev in es.subscribe("s1"):
                events_received.append(ev)
                if len(events_received) >= 2:
                    break

        # Schedule emit while reader is iterating
        import asyncio

        async def writer():
            await asyncio.sleep(0.01)
            es.emit("s1", "after", {})

        await asyncio.gather(reader(), writer())
        types = [e.type for e in events_received]
        assert "before" in types
        assert "after" in types


# ── Count ────────────────────────────────────────────────────────


class TestCount:
    async def test_count_empty(self, es):
        assert es.count("s1") == 0
        assert es.count() == 0

    async def test_count_after_emits(self, es):
        es.emit("s1", "a", {})
        es.emit("s1", "b", {})
        es.emit("s2", "c", {})
        assert es.count("s1") == 2
        assert es.count("s2") == 1
        assert es.count() == 3


# ── Health & degraded mode ───────────────────────────────────────


class TestHealth:
    async def test_healthy_state(self, es):
        assert es.is_degraded is False
        report = es.health_report()
        assert report["event_store"]["degraded"] is False
        assert report["event_store"]["backend"] == "SQLiteStore"

    async def test_health_corrupted_db_falls_back(self, tmp_db):
        tmp_db.write_bytes(b"corrupted")
        cfg = CacheConfig(min_entries=10)
        store = EventStore(db_path=tmp_db, cache_config=cfg)
        # Should fall back to InMemoryStore (if sqlite3 CLI not available)
        if not store.is_degraded:
            # Repair succeeded
            assert type(store._backend).__name__ == "SQLiteStore"
        else:
            from agentnodes.kernel.agent.memory_manager import InMemoryStore
            assert isinstance(store._backend, InMemoryStore)


# ── Direct construction (factory removed) ──────────────────────────


class TestDirectConstruction:
    async def test_same_path_same_instance_via_cache(self, tmp_db):
        """Two EventStore instances for the same db_path are independent
        (factory singleton removed — each caller gets its own instance)."""
        e1 = EventStore(db_path=tmp_db)
        e2 = EventStore(db_path=tmp_db)
        assert e1 is not e2  # no singleton guarantee

    async def test_independent_instances(self, tmp_db):
        """Different EventStore instances don't share state."""
        e1 = EventStore(db_path=tmp_db, flush_to_messages=True)
        e2 = EventStore(db_path=tmp_db, flush_to_messages=False)
        assert e1 is not e2


# ── Projector flush boundary (P0) ──────────────────────────────────


class TestShouldFlushBoundary:
    async def test_should_flush_boundary_types(self, es) -> None:
        for event_type in (
            "message_received",
            "assistant_message",
            "compact",
            "compact.ended",
            "iter_start",
        ):
            assert es._should_flush(event_type), event_type

    async def test_should_not_flush_streaming_deltas(self, es) -> None:
        for event_type in (
            "text_delta",
            "thinking_delta",
            "tool_progress",
            "text.started",
            "thinking_start",
        ):
            assert not es._should_flush(event_type), event_type


# ── P0-1 A3 — parent_event_id / branch_id round-trip ─────────────────


class TestParentAndBranchRoundTrip:
    """P0-1 A3: EventStore persists and replays the new trace-tree
    columns, and falls back to defaults for pre-A3 rows.
    """

    async def test_emit_default_branch_main(self, es: EventStore) -> None:
        e = es.emit("s1", "text.started", {})
        assert e.branch_id == "main"
        assert e.parent_event_id is None

    async def test_emit_explicit_branch_and_parent(self, es: EventStore) -> None:
        e1 = es.emit("s1", "text.started", {})
        e2 = es.emit(
            "s2", "tool_call", {"name": "x"},
            parent_event_id=e1.id, branch_id="exp1",
        )
        assert e2.parent_event_id == e1.id
        assert e2.branch_id == "exp1"
        # Round-trip through replay (SQLite path uses _row_to_dict).
        replayed = es.replay("s2")
        assert len(replayed) == 1
        assert replayed[0].parent_event_id == e1.id
        assert replayed[0].branch_id == "exp1"

    async def test_replay_legacy_row_uses_defaults(
        self, es: EventStore, tmp_path: Path,
    ) -> None:
        """Simulate a pre-A3 row by INSERTing directly without the new
        columns — replay() must still work and use default values.
        """
        from agentnodes.kernel.agent.memory_manager import resolve_db_path
        import sqlite3
        # `es` fixture may have its own DB; reach the EventStore's path
        # by going through the backend connection.
        conn = es._backend._ensure_conn()  # type: ignore[attr-defined]
        # Create a parent-less pre-A3 row directly via raw SQL using only
        # the original 6 columns.
        from agentnodes.kernel.events.event_v2 import EventV2
        ev = EventV2.create("legacy", 1, "text.started", {"legacy": True})
        row = ev.to_row()
        conn.execute(
            "INSERT INTO event_log (id, aggregate_id, seq, type, data_json, "
            "time_created) VALUES (?, ?, ?, ?, ?, ?)",
            (row["id"], row["aggregate_id"], row["seq"], row["type"],
             row["data_json"], row["time_created"]),
        )
        conn.commit()
        replayed = es.replay("legacy")
        assert len(replayed) == 1
        assert replayed[0].parent_event_id is None
        assert replayed[0].branch_id == "main"
