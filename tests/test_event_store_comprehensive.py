"""EventStore comprehensive tests — concurrent writes, fork, subscribe, replay, publish."""

from __future__ import annotations

import asyncio
import sys
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agentnodes.kernel.agent.event_store import EventStore
from agentnodes.kernel.agent.cache import CacheConfig
from agentnodes.kernel.events.event_v2 import EventV2


@pytest.fixture
def store(tmp_path):
    """Create a fresh EventStore for each test."""
    # EventStoreFactory removed — direct construction used instead
    s = EventStore(
        db_path=tmp_path / "test_events.db",
        cache_config=CacheConfig(min_entries=10, max_entries=100),
    )
    yield s
    try:
        s._backend._conn = None
    except Exception:
        pass


def _emit(s, sid, etype="test.event", data=None):
    """Helper to emit an event and return it."""
    return s.emit(sid, etype, data or {"msg": "hello"})


# ── emit() ────────────────────────────────────────────────────────


class TestEventStoreEmit:
    def test_emit_returns_event_with_seq(self, store):
        ev = _emit(store, "s1")
        assert ev.seq == 1
        assert ev.type == "test.event"

    def test_emit_monotonic_seq(self, store):
        for i in range(5):
            ev = _emit(store, "s1", data={"i": i})
            assert ev.seq == i + 1

    def test_emit_per_session_seq(self, store):
        _emit(store, "s1")
        _emit(store, "s2")
        _emit(store, "s1")
        assert store.last_seq("s1") == 2
        assert store.last_seq("s2") == 1

    def test_emit_persists_to_sqlite(self, store):
        _emit(store, "s1", data={"key": "value"})
        events = store.replay("s1")
        assert len(events) == 1
        assert events[0].data["key"] == "value"

    def test_emit_updates_cache(self, store):
        _emit(store, "s1")
        cached = store._cache.get("s1")
        assert cached is not None
        assert len(cached) == 1

    def test_emit_calls_sse_pusher(self, store):
        received = []
        store._sse_pusher = lambda sid, ev: received.append((sid, ev))
        _emit(store, "s1")
        assert len(received) == 1
        assert received[0][0] == "s1"

    def test_emit_sse_pusher_failure_doesnt_break(self, store):
        def _bad_pusher(sid, ev):
            raise RuntimeError("push failed")

        store._sse_pusher = _bad_pusher
        ev = _emit(store, "s1")
        assert ev.seq == 1

    def test_emit_with_branch_id(self, store):
        ev = store.emit("s1", "test.event", {"x": 1}, branch_id="feature-branch")
        assert ev.branch_id == "feature-branch"

    def test_emit_generates_uuid(self, store):
        ev1 = _emit(store, "s1")
        ev2 = _emit(store, "s1")
        assert ev1.id != ev2.id

    def test_emit_sets_time_created(self, store):
        before = time.time()
        ev = _emit(store, "s1")
        after = time.time()
        assert before <= ev.time_created <= after + 1


# ── publish() ─────────────────────────────────────────────────────


class TestEventStorePublish:
    def test_publish_pre_built_event_to_cache(self, store):
        """publish() adds event to cache (not SQLite)."""
        ev = EventV2(
            id="custom-id",
            aggregate_id="s1",
            seq=0,
            type="custom.event",
            data={"custom": True},
            branch_id="main",
            time_created=0,
        )
        store.publish(ev)
        cached = store._cache.get("s1")
        assert cached is not None
        assert len(cached) == 1
        assert cached[0].type == "custom.event"

    def test_publish_sets_id_if_missing(self, store):
        ev = EventV2(
            id="",
            aggregate_id="s1",
            seq=0,
            type="test",
            data={},
            branch_id="main",
            time_created=0,
        )
        store.publish(ev)
        assert ev.id != ""

    def test_publish_sets_time_if_missing(self, store):
        ev = EventV2(
            id="test-id",
            aggregate_id="s1",
            seq=0,
            type="test",
            data={},
            branch_id="main",
            time_created=0,
        )
        store.publish(ev)
        assert ev.time_created > 0


# ── replay() ──────────────────────────────────────────────────────


class TestEventStoreReplay:
    def test_replay_empty_session(self, store):
        assert store.replay("nonexistent") == []

    def test_replay_returns_all(self, store):
        for i in range(5):
            _emit(store, "s1", data={"i": i})
        events = store.replay("s1")
        assert len(events) == 5

    def test_replay_from_seq(self, store):
        for i in range(5):
            _emit(store, "s1", data={"i": i})
        # from_seq=3 returns events with seq > 3
        events = store.replay("s1", from_seq=3)
        assert len(events) == 2
        assert events[0].seq == 4

    def test_replay_filter_by_type(self, store):
        _emit(store, "s1", etype="type.a")
        _emit(store, "s1", etype="type.b")
        _emit(store, "s1", etype="type.a")
        events = store.replay("s1", types=["type.a"])
        assert len(events) == 2

    def test_replay_filter_by_branch(self, store):
        store.emit("s1", "test", {"x": 1}, branch_id="main")
        store.emit("s1", "test", {"x": 2}, branch_id="feature")
        events = store.replay("s1", branch_id="feature")
        assert len(events) == 1

    def test_replay_limit(self, store):
        for i in range(10):
            _emit(store, "s1")
        events = store.replay("s1", limit=3)
        assert len(events) == 3


# ── fork() ────────────────────────────────────────────────────────


class TestEventStoreFork:
    def test_fork_copies_events(self, store):
        for i in range(5):
            _emit(store, "s1", data={"i": i})
        new_id, new_seq = store.fork("s1", at_seq=3)
        assert new_seq == 3
        events = store.replay(new_id)
        assert len(events) == 3

    def test_fork_preserves_type_and_data(self, store):
        _emit(store, "s1", etype="important.event", data={"key": "val"})
        new_id, _ = store.fork("s1", at_seq=1)
        events = store.replay(new_id)
        assert events[0].type == "important.event"
        assert events[0].data["key"] == "val"

    def test_fork_generates_new_ids(self, store):
        _emit(store, "s1")
        new_id, _ = store.fork("s1", at_seq=1)
        original = store.replay("s1")[0]
        forked = store.replay(new_id)[0]
        assert original.id != forked.id

    def test_fork_resets_seq(self, store):
        for i in range(5):
            _emit(store, "s1")
        new_id, new_seq = store.fork("s1", at_seq=3)
        assert new_seq == 3
        # Forked session has its own seq space
        _emit(store, new_id)
        assert store.last_seq(new_id) == 4

    def test_fork_explicit_new_session_id(self, store):
        _emit(store, "s1")
        new_id, _ = store.fork("s1", at_seq=1, new_session_id="my-fork")
        assert new_id == "my-fork"

    def test_fork_error_at_seq_zero(self, store):
        _emit(store, "s1")
        with pytest.raises(ValueError):
            store.fork("s1", at_seq=0)

    def test_fork_error_exceeds_last_seq(self, store):
        _emit(store, "s1")
        with pytest.raises(ValueError):
            store.fork("s1", at_seq=999)

    def test_fork_error_session_exists(self, store):
        _emit(store, "s1")
        _emit(store, "existing")
        with pytest.raises(ValueError):
            store.fork("s1", at_seq=1, new_session_id="existing")

    def test_fork_independence(self, store):
        for i in range(3):
            _emit(store, "s1")
        new_id, _ = store.fork("s1", at_seq=2)
        _emit(store, "s1", data={"after_fork": True})
        # Original has 4, forked has 2
        assert store.last_seq("s1") == 4
        assert store.last_seq(new_id) == 2


# ── subscribe() ───────────────────────────────────────────────────


class TestEventStoreSubscribe:
    @pytest.mark.asyncio
    async def test_subscribe_yields_existing_events(self, store):
        _emit(store, "s1", data={"a": 1})
        _emit(store, "s1", data={"a": 2})
        events = []
        async for ev in store.subscribe("s1"):
            events.append(ev)
            if len(events) >= 2:
                break
        assert len(events) >= 2

    @pytest.mark.asyncio
    async def test_subscribe_live_events(self, store):
        received = []

        async def _reader():
            async for ev in store.subscribe("s1"):
                received.append(ev)
                if len(received) >= 3:
                    break

        task = asyncio.create_task(_reader())
        await asyncio.sleep(0.05)
        for i in range(3):
            _emit(store, "s1", data={"live": i})
        await asyncio.wait_for(task, timeout=2.0)
        assert len(received) >= 3


# ── count() ───────────────────────────────────────────────────────


class TestEventStoreCount:
    def test_count_empty(self, store):
        assert store.count() == 0

    def test_count_after_emits(self, store):
        _emit(store, "s1")
        _emit(store, "s1")
        _emit(store, "s2")
        assert store.count() == 3

    def test_count_per_session(self, store):
        _emit(store, "s1")
        _emit(store, "s1")
        _emit(store, "s2")
        assert store.count("s1") == 2
        assert store.count("s2") == 1


# ── health_report() ──────────────────────────────────────────────


class TestEventStoreHealth:
    def test_health_report(self, store):
        report = store.health_report()
        assert "event_store" in report
        assert "degraded" in report["event_store"]

    def test_is_degraded_property(self, store):
        assert store.is_degraded is False


# ── concurrent writes ────────────────────────────────────────────


class TestEventStoreConcurrency:
    def test_concurrent_emits(self, store):
        """Multiple threads emitting to same session."""
        errors = []

        def _emit_many(start):
            try:
                for i in range(20):
                    store.emit("s1", "test.event", {"thread": start, "i": i})
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=_emit_many, args=(t,)) for t in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(errors) == 0
        assert store.last_seq("s1") == 80

    def test_concurrent_emits_different_sessions(self, store):
        """Multiple threads emitting to different sessions."""
        errors = []

        def _emit_many(session_id):
            try:
                for i in range(20):
                    store.emit(session_id, "test.event", {"i": i})
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=_emit_many, args=(f"s{t}",)) for t in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(errors) == 0
        for t in range(4):
            assert store.last_seq(f"s{t}") == 20


# ── boundary flush ────────────────────────────────────────────────


class TestEventStoreBoundaryFlush:
    def test_should_flush_boundary_types(self, store):
        assert store._should_flush("message_received") is True
        assert store._should_flush("assistant_message") is True
        assert store._should_flush("compact") is True
        assert store._should_flush("compact.ended") is True
        assert store._should_flush("iter_start") is True

    def test_should_not_flush_non_boundary(self, store):
        assert store._should_flush("test.event") is False
        assert store._should_flush("tool_call") is False
