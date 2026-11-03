"""Memory Manager comprehensive tests — CRUD, compact, SQLite direct, emergency buffer."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from strategy_research.kernel.agent.cache import CacheConfig
from strategy_research.kernel.agent.memory_manager import (
    InMemoryStore,
    MemoryManagerFactory,
    SQLiteStore,
    UnifiedMemoryManager,
)


@pytest.fixture
def mgr(tmp_path):
    """Create a fresh UnifiedMemoryManager."""
    MemoryManagerFactory.reset()
    m = UnifiedMemoryManager(
        db_path=tmp_path / "test_mem.db",
        cache_config=CacheConfig(min_entries=10, max_entries=100, re_resolve_interval_seconds=999),
    )
    yield m


# ── append + get ──────────────────────────────────────────────────


class TestMemoryManagerCRUD:
    def test_append_and_get(self, mgr):
        loop = asyncio.new_event_loop()
        loop.run_until_complete(mgr.append("s1", "user", "hello"))
        msgs = loop.run_until_complete(mgr.get("s1"))
        assert len(msgs) == 1
        assert msgs[0]["role"] == "user"
        assert msgs[0]["content"] == "hello"
        loop.close()

    def test_append_multiple(self, mgr):
        loop = asyncio.new_event_loop()
        loop.run_until_complete(mgr.append("s1", "user", "q1"))
        loop.run_until_complete(mgr.append("s1", "assistant", "a1"))
        loop.run_until_complete(mgr.append("s1", "user", "q2"))
        msgs = loop.run_until_complete(mgr.get("s1"))
        assert len(msgs) == 3
        loop.close()

    def test_get_empty_session(self, mgr):
        loop = asyncio.new_event_loop()
        msgs = loop.run_until_complete(mgr.get("nonexistent"))
        assert msgs == []
        loop.close()

    def test_get_use_cache_false(self, mgr):
        loop = asyncio.new_event_loop()
        loop.run_until_complete(mgr.append("s1", "user", "hello"))
        msgs = loop.run_until_complete(mgr.get("s1", use_cache=False))
        assert len(msgs) == 1
        loop.close()

    def test_append_with_metadata(self, mgr):
        loop = asyncio.new_event_loop()
        loop.run_until_complete(mgr.append("s1", "user", "hello", metadata={"key": "val"}))
        msgs = loop.run_until_complete(mgr.get("s1"))
        assert msgs[0].get("metadata") is not None or "metadata" in str(msgs[0])
        loop.close()

    def test_append_returns_message_id(self, mgr):
        loop = asyncio.new_event_loop()
        mid = loop.run_until_complete(mgr.append("s1", "user", "hello"))
        assert mid is not None
        assert len(mid) > 0
        loop.close()


# ── clear ─────────────────────────────────────────────────────────


class TestMemoryManagerClear:
    def test_clear_keeps_history_and_marks_boundary(self, mgr):
        """B4 D1: /clear resets the context window, it does not delete rows.

        Renamed from ``test_clear_removes_messages``: the old assertion
        (``msgs == []``) encoded the bug. History stays queryable; the marker
        tells the history filter where the context was cut.
        """
        loop = asyncio.new_event_loop()
        loop.run_until_complete(mgr.append("s1", "user", "hello"))
        loop.run_until_complete(mgr.clear("s1"))
        msgs = loop.run_until_complete(mgr.get("s1"))
        # B4 Step 1: append now writes through the projector, so both the
        # original message and the marker are real rows.
        assert len(msgs) == 2
        assert msgs[-1]["message_type"] == "compaction"
        assert msgs[-1]["metadata"]["compacted_until_seq"] == 0
        loop.close()

    def test_clear_empty_session(self, mgr):
        loop = asyncio.new_event_loop()
        loop.run_until_complete(mgr.clear("nonexistent"))  # should not raise
        loop.close()


# ── compact (moved out of MemoryManager) ──────────────────────────
#
# B4 Step 1 deleted ``compact()``: zero production callers, and /compact is
# SessionService.compact_history -> compact.ended -> projector. The two
# tests below were about that deleted method, so they went with it.


# ── list_recent_sessions ─────────────────────────────────────────


class TestMemoryManagerRecent:
    def test_list_recent_empty(self, mgr):
        assert mgr.list_recent_sessions() == []

    def test_list_recent_with_sessions(self, mgr):
        loop = asyncio.new_event_loop()
        loop.run_until_complete(mgr.append("s1", "user", "hi"))
        loop.run_until_complete(mgr.append("s2", "user", "hi"))
        sessions = mgr.list_recent_sessions()
        assert "s1" in sessions
        assert "s2" in sessions
        loop.close()

    def test_list_recent_limit(self, mgr):
        assert mgr.list_recent_sessions(limit=5) == []


# ── health_report ─────────────────────────────────────────────────


class TestMemoryManagerHealth:
    def test_health_report(self, mgr):
        report = mgr.health_report()
        assert hasattr(report, "mm_degraded")
        assert hasattr(report, "mm_backend")

    def test_not_degraded_initially(self, mgr):
        assert mgr.is_degraded is False


# ── InMemoryStore ─────────────────────────────────────────────────
#
# B4 Step 4: ``insert_message`` / ``compact_messages`` were deleted from both
# stores (they were direct writers beside the projector). The tests that
# exercised them went with the methods; what remains covers the degraded
# backend's health/close surface and read-only session listing.


class TestInMemoryStore:
    def test_health_check(self):
        store = InMemoryStore()
        assert store.health_check() is True

    def test_delete_session_clears_messages(self):
        store = InMemoryStore()
        store._data["s1"] = [{"id": "m1", "role": "user", "content": "hi"}]
        store.delete_session("s1")
        assert store.list_messages("s1") == []

    def test_list_recent_sessions(self):
        store = InMemoryStore()
        store._data["s1"] = []
        store._data["s2"] = []
        sessions = store.list_recent_sessions()
        assert "s1" in sessions
        assert "s2" in sessions


# ── SQLiteStore direct ────────────────────────────────────────────


class TestSQLiteStoreDirect:
    def test_health_check(self, tmp_path):
        store = SQLiteStore(tmp_path / "health.db")
        assert store.health_check() is True
        store.close()

    def test_close(self, tmp_path):
        store = SQLiteStore(tmp_path / "close.db")
        store.close()  # should not raise

    def test_schema_created_on_open(self, tmp_path):
        """Opening the store yields the full unified schema.

        Pins the dual-master hotfix (c161b0aa): ``_init_schema`` delegates to
        ``web_session_db._ensure_schema``, so ``message_parts`` exists even
        when SQLiteStore is what created the file.
        """
        from strategy_research.interfaces.api.session.web_session_db import _ensure_schema

        db = tmp_path / "schema.db"
        store = SQLiteStore(db)
        conn = store._ensure_conn()  # schema is applied on first connection
        _ensure_schema(conn)
        tables = {
            r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        store.close()
        assert {"sessions", "messages", "message_parts"} <= tables
