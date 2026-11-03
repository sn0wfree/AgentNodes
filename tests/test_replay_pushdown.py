"""P0-1 B1 — replay SQL filter pushdown tests.

Covers the new ``EventStore.replay(types=, branch_id=, limit=)`` pushdown
and TraceProjection's downstream consumption.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from strategy_research.kernel.agent.cache import CacheConfig
from strategy_research.kernel.agent.event_store import EventStore
from strategy_research.kernel.events.event_v2 import EventType, EventV2

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def es(tmp_path):
    """EventStore backed by a real SQLite file in tmp_path."""
    # EventStoreFactory removed — direct construction used instead
    db = tmp_path / "events.db"
    store = EventStore(
        db_path=db,
        cache_config=CacheConfig(max_entries=64),
    )
    yield store
    store._cache.clear()


def _emit(es: EventStore, sid: str, etype: str, *, branch="main") -> EventV2:
    return es.emit(sid, etype, {"x": 1}, branch_id=branch)


class TestReplayTypeFilter:
    async def test_types_filter_pushes_down(self, es: EventStore) -> None:
        # 100 text_deltas + 1 llm_request; only llm_request survives.
        for _ in range(100):
            _emit(es, "s1", EventType.TEXT_DELTA)
        _emit(es, "s1", EventType.LLM_REQUEST)
        replayed = es.replay("s1", types=[EventType.LLM_REQUEST])
        assert len(replayed) == 1
        assert replayed[0].type == EventType.LLM_REQUEST

    async def test_types_multi(self, es: EventStore) -> None:
        _emit(es, "s1", EventType.TEXT_DELTA)
        _emit(es, "s1", EventType.ITER_START)
        _emit(es, "s1", EventType.TOOL_CALL)
        replayed = es.replay(
            "s1", types=[EventType.ITER_START, EventType.TOOL_CALL]
        )
        assert {ev.type for ev in replayed} == {
            EventType.ITER_START, EventType.TOOL_CALL,
        }

    async def test_types_tuple_accepted(self, es: EventStore) -> None:
        _emit(es, "s1", EventType.TEXT_DELTA)
        _emit(es, "s1", EventType.ITER_START)
        replayed = es.replay("s1", types=(EventType.ITER_START,))
        assert len(replayed) == 1

    async def test_no_types_returns_all(self, es: EventStore) -> None:
        _emit(es, "s1", EventType.TEXT_DELTA)
        _emit(es, "s1", EventType.ITER_START)
        replayed = es.replay("s1")
        assert len(replayed) == 2


class TestReplayBranchFilter:
    async def test_branch_filter(self, es: EventStore) -> None:
        _emit(es, "s1", EventType.LOOP_START, branch="main")
        _emit(es, "s1", EventType.LOOP_START, branch="exp1")
        _emit(es, "s1", EventType.LOOP_START, branch="exp2")
        main_only = es.replay("s1", branch_id="main")
        assert len(main_only) == 1
        assert main_only[0].branch_id == "main"
        exp1_only = es.replay("s1", branch_id="exp1")
        assert len(exp1_only) == 1
        assert exp1_only[0].branch_id == "exp1"

    async def test_branch_and_types_combined(self, es: EventStore) -> None:
        _emit(es, "s1", EventType.LOOP_START, branch="main")
        _emit(es, "s1", EventType.ITER_START, branch="main")
        _emit(es, "s1", EventType.ITER_START, branch="exp1")
        replayed = es.replay(
            "s1", types=[EventType.ITER_START], branch_id="main",
        )
        assert len(replayed) == 1
        assert replayed[0].type == EventType.ITER_START
        assert replayed[0].branch_id == "main"


class TestReplayLimit:
    async def test_limit_caps_rows(self, es: EventStore) -> None:
        for _ in range(10):
            _emit(es, "s1", EventType.ITER_START)
        replayed = es.replay("s1", limit=3)
        assert len(replayed) == 3
        # The 3 returned are the first 3 by seq (LIMIT applies before
        # ORDER BY … ASC's tail semantics).
        assert [ev.seq for ev in replayed] == [1, 2, 3]


class TestTraceProjectionConsumesFilteredReplay:
    async def test_projection_only_deserializes_trace_types(
        self, es: EventStore,
    ) -> None:
        from strategy_research.interfaces.api.session.trace_projection import TraceProjection
        # 95 deltas + 5 trace events.
        for i in range(95):
            _emit(es, "s1", EventType.TEXT_DELTA)
        for _ in range(5):
            _emit(es, "s1", EventType.ITER_START)

        tp = TraceProjection(es)
        records = tp.project("s1", limit=100)
        # Only ITER_START events pass through; the 95 deltas are filtered
        # at the SQLite layer, not in Python.
        assert len(records) == 5
        for r in records:
            assert r["type"] == EventType.ITER_START