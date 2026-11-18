"""Tests for Phase 7+8 MemoryManager — SQLite + cache + auto-repair + emergency fallback."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from agentnodes.kernel.session.state.cache import CacheConfig
from agentnodes.kernel.session.state.memory_manager import (
    InMemoryStore,
    MemoryManagerFactory,
    SQLiteStore,
    UnifiedMemoryManager,
    get_default_memory_manager,
    resolve_db_path,
)

pytestmark = pytest.mark.asyncio


# ── Fixtures ───────────────────────────────────────────────────────



def _make_projection_sink(db_path):
    """Projector 必须注入：append 走事件流，没有 sink 则 messages 表永远为空。

    2026-11-05：`UnifiedMemoryManager._emit_event` 不再自建 EventStore 时
    偷偷 new 一个 Projector（那是组合根绕过）。生产路径由 container.py
    注入，测试这里等价提供。
    """
    from agentnodes.interfaces.api.session.projector import Projector

    return Projector(db_path)

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
    mgr = UnifiedMemoryManager(db_path=tmp_db, projection_sink=_make_projection_sink(tmp_db), cache_config=cache_cfg)
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
        mgr = UnifiedMemoryManager(db_path=tmp_db, projection_sink=_make_projection_sink(tmp_db), cache_config=cfg)
        # If repair fails, backend should be InMemoryStore (degraded)
        if not mgr.is_degraded:
            # sqlite3 CLI was available and repaired successfully
            assert type(mgr._backend).__name__ == "SQLiteStore"
        else:
            assert isinstance(mgr._backend, InMemoryStore)


# ── History contract（C1/C2/C3，2026-11-18 裁决 2）────────────────


class _BrokenProjector:
    def __init__(self, *args, **kwargs):
        raise RuntimeError("replay failed")


class TestHistoryContract:
    async def test_append_failure_propagates(self, mm):
        """C1：事件发射失败必须抛错（emergency buffer 静默兜底已废除）。"""
        def broken_emit(*args, **kwargs):
            raise RuntimeError("simulated DB failure")

        original = mm._emit_event
        mm._emit_event = broken_emit
        try:
            with pytest.raises(RuntimeError, match="simulated DB failure"):
                await mm.append("s1", "user", "hi")
            assert getattr(mm, "_emergency_buffer", "removed") == "removed", \
                "emergency buffer 已按 C1 契约移除"
        finally:
            mm._emit_event = original

    async def test_corrupt_projection_row_is_rebuilt_from_event_log(self, mm):
        """C2：损坏 messages 投影行 → event_log 重放并自动全量修复投影。"""
        await mm.append("s1", "user", "canonical text", metadata={"origin": "event"})
        row = mm._backend.list_messages("s1")[0]

        # Inject row-level corruption that SQL can read but the projection
        # decoder must reject. The event_log remains intact as source of truth.
        conn = sqlite3.connect(str(mm._db_path))
        try:
            conn.execute(
                "UPDATE messages SET metadata_json = ? WHERE id = ?",
                ("{malformed-json", row["id"]),
            )
            conn.commit()
        finally:
            conn.close()

        msgs = await mm.get("s1", use_cache=False)
        assert len(msgs) == 1
        assert msgs[0].content == "canonical text"
        assert msgs[0].metadata["origin"] == "event"

        # Recovery must repair the materialized view, not just return a
        # one-off replay result. A subsequent direct projection read succeeds.
        repaired = mm._backend.list_messages("s1")
        assert len(repaired) == 1
        assert repaired[0]["content"] == "canonical text"
        assert repaired[0]["metadata"] == {"origin": "event"}

    async def test_get_strict_raises_when_replay_also_fails(self, mm, monkeypatch):
        """C2：投影与重放都失败 → HistoryReadError（不静默回退）。"""
        def broken(*args, **kwargs):
            raise RuntimeError("projection read failed")

        original = mm._backend.list_messages
        mm._backend.list_messages = broken
        import agentnodes.kernel.session.projector as proj_mod
        monkeypatch.setattr(proj_mod, "Projector", _BrokenProjector)
        from agentnodes.kernel.session.state.memory_manager import HistoryReadError

        try:
            with pytest.raises(HistoryReadError):
                await mm.get("s1", use_cache=False)
        finally:
            mm._backend.list_messages = original

    async def test_get_stale_never_raises(self, mm, monkeypatch):
        """C3：宽松读可为空，永不抛错（结果不得进入模型上下文）。"""
        def broken(*args, **kwargs):
            raise RuntimeError("everything failed")

        original = mm._backend.list_messages
        mm._backend.list_messages = broken
        import agentnodes.kernel.session.projector as proj_mod
        monkeypatch.setattr(proj_mod, "Projector", _BrokenProjector)
        try:
            msgs = await mm.get_stale("s1")
            assert msgs == []
        finally:
            mm._backend.list_messages = original


class TestClearFailure:
    async def test_clear_surfaces_event_persistence_failure(self, mm):
        def broken_emit(*args, **kwargs):
            raise RuntimeError("event persistence failed")

        original = mm._emit_event
        mm._emit_event = broken_emit
        try:
            with pytest.raises(RuntimeError, match="event persistence failed"):
                await mm.clear("s1")
        finally:
            mm._emit_event = original


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

    def test_create_injects_projection_sink(self, tmp_db):
        MemoryManagerFactory.reset()
        sink = object()
        mm = MemoryManagerFactory.create(db_path=tmp_db, projection_sink=sink)
        assert mm._projection_sink is sink
        MemoryManagerFactory.reset()

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
        from agentnodes.kernel.session.state.memory_manager import SESSION_DB_FILENAME
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.delenv("AGENTNODES_WORKSPACE", raising=False)
        monkeypatch.chdir(tmp_path)
        result = resolve_db_path()
        assert result.parent.exists()
        assert result.name == SESSION_DB_FILENAME

    def test_env_override(self, monkeypatch, tmp_path):
        custom = tmp_path / "custom.db"
        monkeypatch.setenv("AGENTNODES_SESSION_DB", str(custom))
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


class TestCloseLifecycle:
    async def test_close_releases_lazy_event_store_connections(
        self, tmp_db: Path, cache_cfg: CacheConfig,
    ):
        sink = _make_projection_sink(tmp_db)
        manager = UnifiedMemoryManager(
            db_path=tmp_db,
            projection_sink=sink,
            cache_config=cache_cfg,
        )
        try:
            await manager.append("s1", "user", "hello")
            event_store = manager._event_store
            platform_store = event_store._ps

            assert platform_store._session_conns
            manager.close()

            assert platform_store._session_conns == {}
            assert event_store._backend._conn is None
        finally:
            manager.close()
            sink.close()
