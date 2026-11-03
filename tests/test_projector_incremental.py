"""Incremental projector tests (docs/projector-incremental.md).

Covers:
- incremental replay equivalence: state built incrementally across
  multiple flushes == state built from a single full replay
- delta flush only rewrites touched messages (row-count assertions)
- cache invalidation on session delete (full rebuild afterwards)
- whole-session rewrite events (compact.ended) fall back to full flush
"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from tests._fixtures.legacy_event_bus_v2 import EventBusV2
from strategy_research.kernel.events.event_v2 import EventV2
from tests._fixtures.legacy_events import EventBus
from strategy_research.interfaces.api.session.projector import Projector


def _setup_db(db_path: Path) -> None:
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY)")
        conn.execute("""
            CREATE TABLE event_log (
                id TEXT PRIMARY KEY,
                aggregate_id TEXT NOT NULL,
                seq INTEGER NOT NULL,
                type TEXT NOT NULL,
                data_json TEXT NOT NULL,
                time_created REAL NOT NULL,
                FOREIGN KEY (aggregate_id) REFERENCES sessions(id) ON DELETE CASCADE
            )
        """)
        conn.execute("""
            CREATE TABLE messages (
                id TEXT PRIMARY KEY,
                session_id TEXT,
                role TEXT,
                content TEXT,
                created_at REAL,
                message_type TEXT,
                seq INTEGER,
                metadata_json TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE message_parts (
                id TEXT PRIMARY KEY,
                message_id TEXT,
                session_id TEXT,
                type TEXT,
                data_json TEXT,
                seq INTEGER,
                time_created REAL
            )
        """)
        conn.commit()
    finally:
        conn.close()


def _add_session(db_path: Path, sid: str) -> None:
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "INSERT INTO sessions (id) VALUES (?)", (sid,),
        )
        conn.commit()
    finally:
        conn.close()


def _event(sid: str, seq: int, type_: str, data: dict) -> EventV2:
    return EventV2.create(sid, seq, type_, data)


class IncrementalProjectorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db_path = Path(self.tmp.name)
        _setup_db(self.db_path)
        _add_session(self.db_path, "s1")
        self.bus = EventBus()
        self.v2 = EventBusV2(self.bus, self.db_path, flush_to_messages=True)

    def tearDown(self) -> None:
        try:
            self.db_path.unlink(missing_ok=True)
        except OSError:
            pass  # Windows may still hold sqlite handles

    def _publish_turn(self, sid: str, seq: int, msg_id: str, text: str) -> None:
        self.v2.publish(_event(sid, seq, "message_received", {
            "message_id": msg_id, "content": text,
        }))
        self.v2.publish(_event(sid, seq + 1, "text.started", {
            "message_id": msg_id, "text_id": f"t-{msg_id}", "text": "",
        }))
        self.v2.publish(_event(sid, seq + 2, "text_delta", {
            "message_id": msg_id, "text_id": f"t-{msg_id}", "text": "hi",
        }))
        self.v2.publish(_event(sid, seq + 3, "text.ended", {
            "message_id": msg_id, "text_id": f"t-{msg_id}", "text": "hi",
        }))
        self.v2.publish(_event(sid, seq + 4, "assistant_message", {
            "message_id": msg_id, "content": "hi",
        }))

    def test_incremental_equals_full_replay(self) -> None:
        """State built across incremental flushes == single full replay."""
        self._publish_turn("s1", 1, "m1", "a")
        self._publish_turn("s1", 6, "m2", "b")
        self._publish_turn("s1", 11, "m3", "c")

        # Incremental projector (cache warmed by the flushes above)
        proj = self.v2._get_projector()
        state, touched = proj.project_incremental("s1", collect_touched=True)

        # Full replay from scratch (fresh projector = cold cache)
        proj2 = Projector(self.db_path)
        full = proj2.project("s1")

        assert state.to_message_rows() == full.to_message_rows()
        assert state.to_part_rows() == full.to_part_rows()
        # No new events → nothing touched
        assert touched == set()

    def test_delta_flush_writes_only_touched_rows(self) -> None:
        """Second flush after one new message only rewrites that message."""
        self._publish_turn("s1", 1, "m1", "a")
        self._publish_turn("s1", 6, "m2", "b")

        conn = sqlite3.connect(str(self.db_path))
        try:
            before_msgs = conn.execute(
                "SELECT COUNT(*) FROM messages WHERE session_id='s1'"
            ).fetchone()[0]
        finally:
            conn.close()

        # Publish one more turn → flush must only touch m3
        self._publish_turn("s1", 11, "m3", "c")

        conn = sqlite3.connect(str(self.db_path))
        try:
            total = conn.execute(
                "SELECT COUNT(*) FROM messages WHERE session_id='s1'"
            ).fetchone()[0]
            m3 = conn.execute(
                "SELECT content FROM messages WHERE id='m3'"
            ).fetchone()
            parts = conn.execute(
                "SELECT COUNT(*) FROM message_parts WHERE message_id='m3'"
            ).fetchone()[0]
        finally:
            conn.close()

        assert total == before_msgs + 1
        assert m3 is not None and m3[0] == "hi"
        assert parts == 1

    def test_invalidate_forces_full_rebuild(self) -> None:
        """After invalidate(), the next flush must reflect DB-deleted
        rows (not a stale cached state)."""
        self._publish_turn("s1", 1, "m1", "a")

        # Simulate session deletion: wipe messages, invalidate cache,
        # then flush again — the projection must NOT resurrect rows
        # from the stale in-memory state.
        proj = self.v2._get_projector()
        assert "s1" in proj._cache
        proj.invalidate("s1")
        assert "s1" not in proj._cache

        conn = sqlite3.connect(str(self.db_path))
        try:
            conn.execute("DELETE FROM messages WHERE session_id='s1'")
            conn.execute("DELETE FROM message_parts WHERE session_id='s1'")
            conn.commit()
        finally:
            conn.close()

        # Rebuild after invalidation: events still in event_log, so the
        # messages are re-created (correct — event_log is source of truth)
        state, touched = proj.project_incremental("s1", collect_touched=True)
        proj.flush(state, touched=touched)
        conn = sqlite3.connect(str(self.db_path))
        try:
            total = conn.execute(
                "SELECT COUNT(*) FROM messages WHERE session_id='s1'"
            ).fetchone()[0]
        finally:
            conn.close()
        assert total == 1  # re-created from event_log, not stale cache

    def test_compact_full_rewrite_falls_back_to_full_flush(self) -> None:
        """compact.ended with a replacement list must trigger a full
        flush (touched contains the '*' sentinel)."""
        # Use a no-flush bus so we control when the projection is built
        bus = EventBus()
        v2 = EventBusV2(bus, self.db_path, flush_to_messages=False)
        v2.publish(_event("s1", 1, "message_received", {
            "message_id": "m1", "content": "a",
        }))
        v2.publish(_event("s1", 2, "assistant_message", {
            "message_id": "m2", "content": "b",
        }))

        # Warm the cache with the first two events (cache miss → full)
        proj = v2._get_projector()
        proj.project_incremental("s1")

        # Now publish the whole-session rewrite; the incremental replay
        # of this event must signal '*' (full flush needed)
        v2.publish(_event("s1", 3, "compact.ended", {
            "summary": "compressed",
            "messages": [
                {"role": "user", "content": "compressed user"},
                {"role": "assistant", "content": "compressed answer"},
            ],
        }))

        state, touched = proj.project_incremental("s1", collect_touched=True)
        assert touched is not None and "*" in touched

        proj.flush(state, touched=touched)
        conn = sqlite3.connect(str(self.db_path))
        try:
            roles = [r[0] for r in conn.execute(
                "SELECT role FROM messages WHERE session_id='s1' ORDER BY seq"
            )]
        finally:
            conn.close()
        # Old messages replaced by the compressed set (plus the
        # compaction marker message the handler appends)
        assert roles[:2] == ["user", "assistant"]
        assert "system" in roles

    def test_v2_invalidate_hook(self) -> None:
        """EventBusV2.invalidate drops the projector cache."""
        self._publish_turn("s1", 1, "m1", "a")
        assert "s1" in self.v2._get_projector()._cache
        self.v2.invalidate("s1")
        assert "s1" not in self.v2._get_projector()._cache


if __name__ == "__main__":
    unittest.main()
