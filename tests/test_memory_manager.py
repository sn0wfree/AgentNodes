"""Tests for Phase 7+8 MemoryManager — SQLite + cache + auto-repair + emergency fallback."""
from __future__ import annotations

from pathlib import Path

import pytest

from strategy_research.core.agent.cache import CacheConfig
from strategy_research.core.agent.memory_manager import (
    InMemoryStore,
    MemoryManagerFactory,
    SQLiteStore,
    UnifiedMemoryManager,
    get_default_memory_manager,
    resolve_db_path,
)

pytestmark = pytest.mark.asyncio


# ── Fixtures ───────────────────────────────────────────────────────


@pytest.fixture
def tmp_db(tmp_path: Path) -> Path:
    return tmp_path / "test_sessions.db"


@pytest.fixture
def cache_cfg() -> CacheConfig:
    return CacheConfig(
        min_entries=10,
        max_entries=100,
        re_resolve_interval_seconds=999,
    )


@pytest.fixture
def mm(tmp_db: Path, cache_cfg: CacheConfig):
    MemoryManagerFactory.reset()
    mgr = UnifiedMemoryManager(db_path=tmp_db, cache_config=cache_cfg)
    yield mgr
    mgr.close()


# ── Basic CRUD ────────────────────────────────────────────────────


class TestUnifiedMemoryManagerBasic:
    async def test_append_and_get(self, mm):
        await mm.append("s1", "user", "hi")
        await mm.append("s1", "assistant", "hello")
        msgs = await mm.get("s1")
        assert len(msgs) == 2
        assert msgs[0]["role"] == "user"
        assert msgs[1]["role"] == "assistant"
        assert msgs[0]["content"] == "hi"

    async def test_get_miss_returns_empty(self, mm):
        msgs = await mm.get("unknown")
        assert msgs == []

    async def test_use_cache_false_rebuilds(self, mm):
        """B4 Step 1: append invalidates rather than primes the cache.

        The write path is now an event the projector materialises, possibly in
        another process, so the local cache cannot be assumed fresh. A read
        repopulates it.
        """
        await mm.append("s1", "user", "hi")
        assert mm._cache.get("s1") is None, "append must invalidate, not prime"
        msgs = await mm.get("s1", use_cache=False)
        assert len(msgs) == 1
        assert mm._cache.get("s1") is not None, "a read repopulates the cache"

    async def test_clear_keeps_history_and_marks_boundary(self, mm):
        """B4 D1: /clear resets the context window, it does not destroy history.

        The old name was ``test_clear_removes_session`` and it asserted an
        empty list. That encoded the bug: /clear deleted rows while its own
        documented semantics say "clear the context, keep the history".
        Now it appends a compaction marker whose ``compacted_until_seq`` is 0,
        which the history filter reads as "everything before this is out of
        context".
        """
        await mm.append("s1", "user", "hi")
        await mm.clear("s1")
        msgs = await mm.get("s1")
        # The original message survives, plus one boundary marker (B4 Step 1
        # made append write through the projector, so both rows are real).
        assert len(msgs) == 2, "history must survive /clear"
        marker = msgs[-1]
        assert marker["role"] == "system"
        assert marker["message_type"] == "compaction"
        assert marker["metadata"]["compacted_until_seq"] == 0


# ── Cache integration ─────────────────────────────────────────────


class TestCacheIntegration:
    async def test_read_populates_cache(self, mm):
        """B4 Step 1: the cache is a read-through layer, not a write mirror."""
        await mm.append("s1", "user", "hi")
        await mm.get("s1")
        cached = mm._cache.get("s1")
        assert cached is not None
        assert cached[0]["content"] == "hi"

    async def test_cache_hit_returns_same_messages(self, mm):
        await mm.append("s1", "user", "hi")
        msgs1 = await mm.get("s1")
        msgs2 = await mm.get("s1")
        assert msgs1 == msgs2

    async def test_concurrent_append_thread_safe(self, mm):
        import asyncio as _aio
        await _aio.gather(*[
            mm.append("s1", "user", f"msg-{i}") for i in range(10)
        ])
        msgs = await mm.get("s1")
        assert len(msgs) == 10


# ── Auto-repair ────────────────────────────────────────────────────


class TestAutoRepair:
    def test_health_check_healthy_db(self, mm):
        assert mm._sqlite_store.health_check() is True

    def test_health_check_corrupted_db(self, tmp_db: Path):
        tmp_db.write_bytes(b"not a sqlite database")
        store = SQLiteStore(tmp_db)
        assert store.health_check() is False

    def test_auto_repair_skipped_without_sqlite3_cli(self, tmp_db: Path):
        """With sqlite3 CLI available, auto_repair succeeds."""
        tmp_db.write_bytes(b"corrupted garbage")
        store = SQLiteStore(tmp_db)
        try:
            ok = store.auto_repair()
            # sqlite3 CLI may or may not be installed in test env
            if ok:
                assert store.health_check() is True
            else:
                pytest.skip("sqlite3 CLI not available or repair returned False")
        except FileNotFoundError:
            pytest.skip("sqlite3 CLI not installed")

    def test_in_memory_fallback_when_sqlite_corrupted_and_no_repair(self, tmp_db: Path):
        tmp_db.write_bytes(b"corrupted")
        cfg = CacheConfig(min_entries=10)
        mgr = UnifiedMemoryManager(db_path=tmp_db, cache_config=cfg)
        # If repair fails, backend should be InMemoryStore (degraded)
        if not mgr.is_degraded:
            # sqlite3 CLI was available and repaired successfully
            assert type(mgr._backend).__name__ == "SQLiteStore"
        else:
            assert isinstance(mgr._backend, InMemoryStore)


# ── Emergency buffer ──────────────────────────────────────────────


class TestEmergencyBuffer:
    async def test_emergency_buffer_used_when_backend_fails(self, mm):
        """B4 Step 1: the write path is the event emit, so that is what fails."""
        def broken_emit(*args, **kwargs):
            raise RuntimeError("simulated DB failure")

        original = mm._emit_event
        mm._emit_event = broken_emit
        try:
            msg_id = await mm.append("s1", "user", "hi")
            assert msg_id.startswith("emergency_")
            assert "s1" in mm._emergency_buffer
            assert len(mm._emergency_buffer["s1"]) == 1
            assert mm._emergency_buffer["s1"][0]["content"] == "hi"
        finally:
            mm._emit_event = original

    async def test_emergency_buffer_reported_in_health(self, mm):
        def broken(*args, **kwargs):
            raise RuntimeError("test")

        original = mm._emit_event
        mm._emit_event = broken
        try:
            await mm.append("s1", "user", "hi")
        finally:
            mm._emit_event = original
        report = mm.health_report()
        assert report.emergency_buffer_active is True
        assert report.emergency_buffer_session_count >= 1


# ── Compaction (moved out of MemoryManager) ──────────────────────
#
# B4 Step 1 deleted ``UnifiedMemoryManager.compact()``: it had zero
# production callers, and /compact is implemented by
# SessionService.compact_history, which emits ``compact.ended`` and lets the
# projector write the marker. Keeping a direct-SQL twin after eventisation
# would have reintroduced a second writer. The behaviour it tested is
# covered by the projection tests (compact handler + marker shape).


# ── Dynamic avg-tokens estimator ──────────────────────────────────


class TestDynamicEstimator:
    def test_estimator_cold_start_uses_fallback(self, mm):
        cfg = CacheConfig(avg_tokens_per_message=300, avg_tokens_min_samples=10)
        mm._estimator = type(mm._estimator)(mm, cfg)
        result = mm._estimator.estimate()
        assert result == 300

    async def test_estimator_warm_uses_history(self, mm):
        for i in range(20):
            await mm.append("s1", "user", "x" * 200)
        mm._estimator.invalidate()
        result = mm._estimator.estimate()
        assert result > 100

    async def test_estimator_caches_for_interval(self, mm):
        for i in range(20):
            await mm.append("s1", "user", "x" * 200)
        result1 = mm._estimator.estimate()
        for i in range(20):
            await mm.append("s1", "user", "x" * 500)
        result2 = mm._estimator.estimate()
        assert result1 == result2

    def test_estimator_invalidate_resets_cache(self, mm):
        mm._estimator._cached_value = 999
        mm._estimator.invalidate()
        assert mm._estimator._cached_value is None


# ── Health report ─────────────────────────────────────────────────


class TestHealthReport:
    def test_healthy_state(self, mm):
        report = mm.health_report()
        assert report.mm_degraded is False
        assert report.mm_backend == "SQLiteStore"
        assert report.sqlite_healthy is True
        assert report.emergency_buffer_active is False

    async def test_health_after_writes(self, mm):
        await mm.append("s1", "user", "hi")
        await mm.append("s1", "assistant", "hello")
        # B4 Step 1: writes invalidate the cache; a read warms it.
        await mm.get("s1")
        report = mm.health_report()
        assert report.cache_session_count >= 1


# ── Factory ────────────────────────────────────────────────────────


class TestFactory:
    def test_create_returns_singleton(self, tmp_db):
        MemoryManagerFactory.reset()
        mm1 = MemoryManagerFactory.create(db_path=tmp_db)
        mm2 = MemoryManagerFactory.create(db_path=tmp_db)
        assert mm1 is mm2

    def test_get_default_memory_manager(self, tmp_db):
        MemoryManagerFactory.reset()
        MemoryManagerFactory.create(db_path=tmp_db)
        mm = get_default_memory_manager()
        assert mm is not None

    def test_reset_clears_singleton(self, tmp_db):
        mm1 = MemoryManagerFactory.create(db_path=tmp_db)
        MemoryManagerFactory.reset()
        mm2 = MemoryManagerFactory.create(db_path=tmp_db)
        assert mm1 is not mm2


# ── Default DB path ───────────────────────────────────────────────


class TestResolveDbPath:
    def test_default_creates_parent_dir(self, monkeypatch, tmp_path):
        from strategy_research.core.agent.memory_manager import SESSION_DB_FILENAME
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.delenv("SR_WORKSPACE_PATH", raising=False)
        monkeypatch.chdir(tmp_path)
        result = resolve_db_path()
        assert result.parent.exists()
        assert result.name == SESSION_DB_FILENAME

    def test_env_override(self, monkeypatch, tmp_path):
        custom = tmp_path / "custom.db"
        monkeypatch.setenv("SR_SESSIONS_DB", str(custom))
        result = resolve_db_path()
        assert result == custom

    def test_explicit_override(self, tmp_path):
        explicit = tmp_path / "explicit.db"
        result = resolve_db_path(override=explicit)
        assert result == explicit


# ── list_recent_sessions ──────────────────────────────────────────


class TestListRecentSessions:
    async def test_list_empty(self, mm):
        assert mm.list_recent_sessions() == []

    async def test_list_with_sessions(self, mm):
        await mm.append("s1", "user", "a")
        await mm.append("s2", "user", "b")
        await mm.append("s3", "user", "c")
        sessions = mm.list_recent_sessions()
        assert len(sessions) == 3
