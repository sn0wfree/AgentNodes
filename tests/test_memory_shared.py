"""Cross-process SQLite sharing test — TUI + Web share ~/.quantnodes/sessions.db.

Phase 7+8 Q4: TUI and web must read/write the same SQLite database.
This test simulates two MemoryManager instances pointing at the same file
(separate processes would do this; in test we use separate instances).
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from strategy_research.core.agent.cache import CacheConfig
from strategy_research.core.agent.memory_manager import (
    MemoryManagerFactory,
    UnifiedMemoryManager,
)

pytestmark = pytest.mark.asyncio


@pytest.fixture
def shared_db(tmp_path: Path) -> Path:
    return tmp_path / "shared.db"


class TestCrossProcessSqlite:
    async def test_two_managers_share_db(self, shared_db):
        """Two UnifiedMemoryManager instances on the same db_path see each other."""
        cfg = CacheConfig(min_entries=10, max_entries=100)

        # Simulate "process A" (TUI)
        mm_a = UnifiedMemoryManager(db_path=shared_db, cache_config=cfg)
        await mm_a.append("s1", "user", "from A")

        # Simulate "process B" (web) — fresh instance, no cache
        MemoryManagerFactory.reset()
        mm_b = UnifiedMemoryManager(db_path=shared_db, cache_config=cfg)
        msgs_b = await mm_b.get("s1")
        assert len(msgs_b) == 1
        assert msgs_b[0]["content"] == "from A"
        assert msgs_b[0]["role"] == "user"

        mm_a.close()
        mm_b.close()

    async def test_wal_mode_allows_concurrent_writes(self, shared_db):
        """SQLite WAL allows multiple writers concurrently.

        Note: per-process cache may be stale across writers. Always use
        ``use_cache=False`` to get the freshest view from SQLite.
        """
        cfg = CacheConfig(min_entries=10)

        mm_a = UnifiedMemoryManager(db_path=shared_db, cache_config=cfg)
        mm_b = UnifiedMemoryManager(db_path=shared_db, cache_config=cfg)

        # Both write concurrently
        await asyncio.gather(
            mm_a.append("s1", "user", "msg-from-a"),
            mm_b.append("s1", "user", "msg-from-b"),
        )

        # Fresh SQLite read (bypass per-process cache)
        msgs = await mm_a.get("s1", use_cache=False)
        assert len(msgs) == 2
        contents = {m["content"] for m in msgs}
        assert "msg-from-a" in contents
        assert "msg-from-b" in contents

        mm_a.close()
        mm_b.close()

    async def test_session_consistency_across_instances(self, shared_db):
        cfg = CacheConfig(min_entries=10)
        mm_a = UnifiedMemoryManager(db_path=shared_db, cache_config=cfg)
        await mm_a.append("s1", "user", "hi")
        await mm_a.append("s1", "assistant", "hello")
        await mm_a.append("s1", "user", "how are you?")
        mm_a.close()

        # Fresh instance — should see all 3 messages
        mm_b = UnifiedMemoryManager(db_path=shared_db, cache_config=cfg)
        msgs = await mm_b.get("s1")
        assert len(msgs) == 3
        assert msgs[0]["content"] == "hi"
        assert msgs[1]["content"] == "hello"
        assert msgs[2]["content"] == "how are you?"
        mm_b.close()

    async def test_clear_visible_across_instances(self, shared_db):
        """B4 D1: the clear marker must be readable by other instances.

        Before D1 this asserted ``msgs == []`` because clear deleted rows.
        The cross-instance property under test is unchanged -- a clear done
        by one process must be observable by another -- only the observable
        artifact is now a compaction marker instead of an empty list.
        """
        cfg = CacheConfig(min_entries=10)
        mm_a = UnifiedMemoryManager(db_path=shared_db, cache_config=cfg)
        await mm_a.append("s1", "user", "data")
        mm_a.close()

        mm_b = UnifiedMemoryManager(db_path=shared_db, cache_config=cfg)
        await mm_b.clear("s1")
        mm_b.close()

        # Fresh instance sees the boundary the other process wrote
        mm_c = UnifiedMemoryManager(db_path=shared_db, cache_config=cfg)
        msgs = await mm_c.get("s1")
        # B4 Step 1: append writes through the projector, so the original
        # message plus the marker are both visible cross-process.
        assert len(msgs) == 2
        assert msgs[-1]["message_type"] == "compaction"
        assert msgs[-1]["metadata"]["compacted_until_seq"] == 0
        mm_c.close()


class TestServicePySharedBackend:
    """Verify service.py uses the same SQLite as MemoryManager (no data divergence)."""

    async def test_messages_table_shared(self, shared_db):
        """MemoryManager writes should be visible to raw SQLiteStore query."""
        from strategy_research.core.agent.memory_manager import SQLiteStore

        cfg = CacheConfig(min_entries=10)
        mm = UnifiedMemoryManager(db_path=shared_db, cache_config=cfg)
        await mm.append("s1", "user", "via mm")

        # Direct SQLiteStore read (bypass MemoryManager)
        store = SQLiteStore(shared_db)
        msgs = store.list_messages("s1")
        assert len(msgs) == 1
        assert msgs[0]["content"] == "via mm"

        mm.close()
