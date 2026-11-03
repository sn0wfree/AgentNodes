"""P0-1 C1 — EventStore.fork() tests.

Covers: basic fork, multiple children from one source, at_seq boundary
checks, new_session_id collision, post-fork independence.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from strategy_research.core.agent.cache import CacheConfig
from strategy_research.core.agent.event_store import EventStore
from strategy_research.kernel.events.event_v2 import EventType

pytestmark = pytest.mark.asyncio


@pytest.fixture
def store(tmp_path):
    s = EventStore(
        db_path=tmp_path / "events.db",
        cache_config=CacheConfig(max_entries=8),
    )
    yield s
    s._cache.clear()


def _emit(s, sid, etype, data=None):
    return s.emit(sid, etype, data or {"x": 1})


class TestForkBasics:
    async def test_fork_copies_events(self, store):
        for i in range(10):
            _emit(store, "src", EventType.ITER_START, {"i": i})
        new_sid, n = store.fork("src", at_seq=10)
        assert n == 10
        replayed = store.replay(new_sid)
        assert len(replayed) == 10
        assert replayed[0].seq == 1
        assert replayed[-1].seq == 10

    async def test_fork_resets_seq_to_one(self, store):
        for i in range(5):
            _emit(store, "src", EventType.ITER_START, {"i": i})
        new_sid, _ = store.fork("src", at_seq=5)
        replayed = store.replay(new_sid)
        assert [e.seq for e in replayed] == [1, 2, 3, 4, 5]

    async def test_fork_partial_prefix(self, store):
        for i in range(10):
            _emit(store, "src", EventType.ITER_START, {"i": i})
        new_sid, n = store.fork("src", at_seq=3)
        assert n == 3
        replayed = store.replay(new_sid)
        assert len(replayed) == 3
        # The forked events correspond to the FIRST 3 of the source.
        assert replayed[0].data["i"] == 0
        assert replayed[2].data["i"] == 2

    async def test_fork_with_explicit_new_session_id(self, store):
        for i in range(3):
            _emit(store, "src", EventType.ITER_START, {"i": i})
        new_sid, n = store.fork(
            "src", at_seq=3, new_session_id="child-explicit",
        )
        assert new_sid == "child-explicit"
        assert n == 3

    async def test_fork_preserves_event_type_and_data(self, store):
        for i in range(5):
            _emit(store, "src", EventType.ITER_START, {"i": i})
        new_sid, _ = store.fork("src", at_seq=5)
        replayed = store.replay(new_sid)
        assert all(e.type == EventType.ITER_START for e in replayed)
        assert [e.data["i"] for e in replayed] == [0, 1, 2, 3, 4]

    async def test_fork_generates_new_event_ids(self, store):
        for i in range(5):
            _emit(store, "src", EventType.ITER_START, {"i": i})
        src_ids = {e.id for e in store.replay("src")}
        new_sid, _ = store.fork("src", at_seq=5)
        forked_ids = {e.id for e in store.replay(new_sid)}
        # No id reuse between source and fork (PK collision guard).
        assert src_ids.isdisjoint(forked_ids)


class TestForkErrorCases:
    async def test_at_seq_zero_raises(self, store):
        _emit(store, "src", EventType.ITER_START)
        with pytest.raises(ValueError):
            store.fork("src", at_seq=0)

    async def test_at_seq_negative_raises(self, store):
        _emit(store, "src", EventType.ITER_START)
        with pytest.raises(ValueError):
            store.fork("src", at_seq=-1)

    async def test_at_seq_exceeds_last_seq_raises(self, store):
        _emit(store, "src", EventType.ITER_START)
        with pytest.raises(ValueError):
            store.fork("src", at_seq=999)

    async def test_existing_new_session_id_raises(self, store):
        _emit(store, "src", EventType.ITER_START)
        _emit(store, "existing", EventType.ITER_START)
        with pytest.raises(ValueError):
            store.fork("src", at_seq=1, new_session_id="existing")


class TestForkIndependence:
    async def test_source_unchanged_after_fork(self, store):
        for i in range(5):
            _emit(store, "src", EventType.ITER_START, {"i": i})
        before = len(store.replay("src"))
        store.fork("src", at_seq=5)
        assert len(store.replay("src")) == before

    async def test_emitting_to_source_after_fork(self, store):
        for i in range(3):
            _emit(store, "src", EventType.ITER_START, {"i": i})
        new_sid, _ = store.fork("src", at_seq=3)
        # Source continues to grow; fork is frozen at the fork point.
        _emit(store, "src", EventType.ITER_START, {"i": 3})
        _emit(store, "src", EventType.ITER_START, {"i": 4})
        assert len(store.replay("src")) == 5
        assert len(store.replay(new_sid)) == 3

    async def test_fork_source_can_be_forked_again(self, store):
        for i in range(5):
            _emit(store, "src", EventType.ITER_START, {"i": i})
        a, na = store.fork("src", at_seq=5, new_session_id="a")
        b, nb = store.fork("src", at_seq=5, new_session_id="b")
        assert na == nb == 5
        assert a != b
        assert len(store.replay(a)) == 5
        assert len(store.replay(b)) == 5