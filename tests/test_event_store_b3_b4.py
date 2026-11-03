"""P0-1 B3+B4 — InMemoryStore from_seq fix + cache hit stats.

B3: EventStore._replay() must apply from_seq / types / branch_id / limit
    in the InMemoryStore path, matching the SQLite path's semantics.
B4: EventStore.health_report() exposes cache.hit_rate (and hits/misses/
    evictions/invalidations) so operators can size the LRU cap.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from strategy_research.kernel.agent.cache import CacheConfig
from strategy_research.kernel.agent.event_store import EventStore
from strategy_research.kernel.agent.memory_manager import InMemoryStore
from strategy_research.kernel.events.event_v2 import EventType


def _emit(es, sid, etype, *, branch="main", seq=None):
    return es.emit(sid, etype, {"x": 1}, branch_id=branch)


class TestInMemoryStoreFromSeq:
    async def test_from_seq_filters_in_memory(self, tmp_path):
        """B3 regression — InMemoryStore used to ignore from_seq and
        return the full list. After B1's filter rewrite, this works.
        """
        # Force the InMemoryStore path by overriding the SQLite init
        # failure (set db_path to an invalid location, watch auto_repair
        # fail). Simpler: build an EventStore with InMemoryStore
        # directly.
        from strategy_research.kernel.agent.event_store import EventStore
        store = EventStore(
            db_path=tmp_path / "events.db",
            cache_config=CacheConfig(max_entries=4),
        )
        store._backend = InMemoryStore()
        for i in range(5):
            store.emit("s1", EventType.ITER_START, {"i": i})
        replayed = store.replay("s1", from_seq=3)
        assert [e.seq for e in replayed] == [4, 5]

    async def test_in_memory_types_filter(self, tmp_path):
        from strategy_research.kernel.agent.event_store import EventStore
        store = EventStore(
            db_path=tmp_path / "events.db",
            cache_config=CacheConfig(max_entries=4),
        )
        store._backend = InMemoryStore()
        for _ in range(3):
            store.emit("s1", EventType.TEXT_DELTA)
        store.emit("s1", EventType.ITER_START)
        replayed = store.replay(
            "s1", types=[EventType.ITER_START]
        )
        assert len(replayed) == 1
        assert replayed[0].type == EventType.ITER_START

    async def test_in_memory_branch_filter(self, tmp_path):
        from strategy_research.kernel.agent.event_store import EventStore
        store = EventStore(
            db_path=tmp_path / "events.db",
            cache_config=CacheConfig(max_entries=4),
        )
        store._backend = InMemoryStore()
        store.emit("s1", EventType.LOOP_START, branch_id="main")
        store.emit("s1", EventType.LOOP_START, branch_id="exp1")
        replayed = store.replay("s1", branch_id="exp1")
        assert len(replayed) == 1
        assert replayed[0].branch_id == "exp1"

    async def test_in_memory_limit(self, tmp_path):
        from strategy_research.kernel.agent.event_store import EventStore
        store = EventStore(
            db_path=tmp_path / "events.db",
            cache_config=CacheConfig(max_entries=8),
        )
        store._backend = InMemoryStore()
        for i in range(5):
            store.emit("s1", EventType.ITER_START)
        replayed = store.replay("s1", limit=2)
        assert len(replayed) == 2


class TestCacheHitRate:
    async def test_hit_rate_zero_when_no_reads(self, tmp_path):
        """hit_rate is 0.0 before any read, never raises."""
        from strategy_research.kernel.agent.cache import CacheStats
        s = CacheStats()
        assert s.hit_rate == 0.0

    async def test_hit_rate_proportional(self, tmp_path):
        from strategy_research.kernel.agent.cache import CacheStats
        s = CacheStats(hits=3, misses=1)
        assert s.hit_rate == 0.75

    async def test_hit_rate_full(self, tmp_path):
        from strategy_research.kernel.agent.cache import CacheStats
        s = CacheStats(hits=10, misses=0)
        assert s.hit_rate == 1.0

    async def test_health_report_includes_cache_stats(self, tmp_path):
        store = EventStore(
            db_path=tmp_path / "events.db",
            cache_config=CacheConfig(max_entries=4),
        )
        # emit() writes through cache; replay() reads SQLite directly
        # but the cache still records gets via subscribe(). The exact
        # counts depend on test ordering so we only assert the schema.
        store.emit("s1", EventType.ITER_START)
        report = store.health_report()
        es = report["event_store"]
        for key in ("cache_session_count", "cache_hits", "cache_misses",
                    "cache_hit_rate", "cache_evictions",
                    "cache_invalidations"):
            assert key in es, f"missing {key}"
        assert 0.0 <= es["cache_hit_rate"] <= 1.0
