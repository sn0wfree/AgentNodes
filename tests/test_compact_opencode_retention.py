"""Opencode-aligned compaction retention tests.

Verifies the opencode behavior (docs/design/compaction-history-filter.md):
1. Compaction keeps ALL original messages in the projection / messages
   table (chat record preserved for UI / future review).
2. The compaction marker records `compacted_until_seq`.
3. `_convert_messages_to_history` hides messages covered by compaction
   from LLM context (only the most recent summary is kept), while the
   DB keeps the originals.
"""
from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from agentnodes.interfaces.api.session.projector import Projector


def _setup_db(db_path: Path) -> None:
    """Minimal schema for projector tests."""
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE sessions (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            title TEXT NOT NULL DEFAULT '',
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        );
        CREATE TABLE messages (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL DEFAULT '',
            created_at REAL NOT NULL,
            metadata_json TEXT,
            message_type TEXT NOT NULL DEFAULT 'assistant',
            seq INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE message_parts (
            id TEXT PRIMARY KEY,
            message_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            type TEXT NOT NULL,
            data_json TEXT NOT NULL,
            seq INTEGER NOT NULL DEFAULT 0,
            time_created REAL NOT NULL
        );
        CREATE TABLE event_log (
            id TEXT PRIMARY KEY,
            aggregate_id TEXT NOT NULL,
            seq INTEGER NOT NULL,
            type TEXT NOT NULL,
            data_json TEXT NOT NULL,
            time_created REAL NOT NULL,
            UNIQUE (aggregate_id, seq)
        );
        """
    )
    conn.execute(
        "INSERT INTO sessions (id, user_id, title, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?)",
        ("s1", "u", "test", 1.0, 1.0),
    )
    conn.commit()
    conn.close()


class TestProjectorKeepsMessages(unittest.TestCase):
    """Compaction keeps ALL original messages in the projection."""

    def setUp(self) -> None:
        self.tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
        self.tmp.close()
        self.db_path = Path(self.tmp.name)
        _setup_db(self.db_path)
        self.proj = Projector(self.db_path)
        self._seq = 0

    def tearDown(self) -> None:
        self.db_path.unlink(missing_ok=True)

    def _emit(self, event_type: str, data: dict, time_created: float) -> None:
        """Persist an event to event_log."""
        self._seq += 1
        conn = sqlite3.connect(str(self.db_path))
        conn.execute(
            "INSERT INTO event_log (id, aggregate_id, seq, type, data_json, time_created) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (f"evt_{self._seq}", "s1", self._seq, event_type,
             json.dumps(data, ensure_ascii=False), time_created),
        )
        conn.commit()
        conn.close()

    def _add_messages(self, n: int = 6, start: float = 1000.0) -> None:
        """Insert n user/assistant messages via message_received events."""
        for i in range(n):
            self._emit(
                "message_received",
                {"message_id": f"msg_{i}", "content": f"user content {i}"},
                start + i,
            )

    def test_compact_keeps_all_original_messages(self) -> None:
        """After compact.ended with a messages list, originals survive."""
        self._add_messages(6)
        self._emit(
            "compact.ended",
            {
                "summary": "## Objective\nCompacted summary",
                "messages": [
                    {"role": "system", "content": "## Objective\nCompacted summary"},
                    {"role": "user", "content": "user content 4"},
                    {"role": "assistant", "content": "assistant content 5"},
                ],
                "compaction_id": "cmp_1",
            },
            2000,
        )
        # Current turn (always follows the marker in production)
        self._emit("message_received", {"message_id": "msg_cur", "content": "current turn"}, 2500)

        msgs = self.proj.project_to_messages("s1")
        # ALL 6 original messages + 1 marker + 1 current turn = 8 in projection
        self.assertEqual(len(msgs), 8)
        types = [m.message_type for m in msgs]
        self.assertEqual(types.count("compaction"), 1)
        self.assertEqual(types.count("user"), 7)

        # Marker carries compacted_until_seq in metadata
        marker = next(m for m in msgs if m.message_type == "compaction")
        self.assertIn("compacted_until_seq", marker.metadata)
        # 6 pre-compaction messages, compressed = [summary]+2 recent →
        # the oldest recent is seq 5 → boundary hides seq 1-4
        self.assertEqual(marker.metadata["compacted_until_seq"], 4)

    def test_flush_preserves_original_messages(self) -> None:
        """flush() after compaction keeps originals in the messages table."""
        self._add_messages(6)
        self._emit(
            "compact.ended",
            {
                "summary": "## Objective\nCompacted summary",
                "messages": [
                    {"role": "system", "content": "## Objective\nCompacted summary"},
                    {"role": "user", "content": "user content 4"},
                    {"role": "assistant", "content": "assistant content 5"},
                ],
                "compaction_id": "cmp_1",
            },
            2000,
        )
        state = self.proj.project("s1")
        self.proj.flush(state)

        conn = sqlite3.connect(str(self.db_path))
        rows = conn.execute(
            "SELECT id, message_type FROM messages WHERE session_id = ?",
            ("s1",),
        ).fetchall()
        conn.close()
        # 6 originals + 1 marker, none deleted
        self.assertEqual(len(rows), 7)
        types = [r[1] for r in rows]
        self.assertEqual(types.count("compaction"), 1)
        self.assertEqual(types.count("user"), 6)

    def test_boundary_with_multiple_compactions(self) -> None:
        """Second compaction's boundary subsumes the first."""
        self._add_messages(6)
        self._emit(
            "compact.ended",
            {
                "summary": "summary 1",
                "messages": [
                    {"role": "system", "content": "summary 1"},
                    {"role": "user", "content": "user content 4"},
                    {"role": "assistant", "content": "assistant content 5"},
                ],
                "compaction_id": "cmp_1",
            },
            2000,
        )
        # New message after compaction
        self._emit("message_received", {"message_id": "msg_6", "content": "user content 6"}, 3000)
        # Compaction 2: compressed = [summary] + last 1
        self._emit(
            "compact.ended",
            {
                "summary": "summary 2",
                "messages": [
                    {"role": "system", "content": "summary 2"},
                    {"role": "user", "content": "user content 6"},
                ],
                "compaction_id": "cmp_2",
            },
            4000,
        )
        msgs = self.proj.project_to_messages("s1")
        self.assertEqual(len(msgs), 9)  # 7 originals + 2 markers
        markers = [m for m in msgs if m.message_type == "compaction"]
        self.assertEqual(len(markers), 2)
        # Marker 2's recent = [user content 6] (seq 8) → boundary = 7
        # (hides msg_0..msg_5 and the older marker, keeps msg_6)
        m2 = next(m for m in markers if m.metadata.get("compaction_id") == "cmp_2")
        self.assertEqual(m2.metadata["compacted_until_seq"], 7)


class TestHistoryBuilderHidesCovered(unittest.TestCase):
    """_convert_messages_to_history hides messages covered by compaction."""

    def setUp(self) -> None:
        self.tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
        self.tmp.close()
        self.db_path = Path(self.tmp.name)
        _setup_db(self.db_path)
        self.proj = Projector(self.db_path)
        self._seq = 0

    def tearDown(self) -> None:
        self.db_path.unlink(missing_ok=True)

    def _emit(self, event_type: str, data: dict, time_created: float) -> None:
        """Persist an event to event_log."""
        self._seq += 1
        conn = sqlite3.connect(str(self.db_path))
        conn.execute(
            "INSERT INTO event_log (id, aggregate_id, seq, type, data_json, time_created) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (f"evt_{self._seq}", "s1", self._seq, event_type,
             json.dumps(data, ensure_ascii=False), time_created),
        )
        conn.commit()
        conn.close()

    def _build_session(self) -> list:
        """Build a session with 6 messages + 1 compaction marker, return Message list."""
        for i in range(6):
            self._emit(
                "message_received",
                {"message_id": f"msg_{i}", "content": f"user content {i}"},
                1000 + i,
            )
        self._emit(
            "compact.ended",
            {
                "summary": "## Objective\nCompacted summary",
                "messages": [
                    {"role": "system", "content": "## Objective\nCompacted summary"},
                    {"role": "user", "content": "user content 4"},
                    {"role": "assistant", "content": "assistant content 5"},
                ],
                "compaction_id": "cmp_1",
            },
            2000,
        )
        # Current turn (always follows the marker in production)
        self._emit("message_received", {"message_id": "msg_cur", "content": "current turn"}, 2500)
        state = self.proj.project("s1")
        self.proj.flush(state)
        return self.proj.project_to_messages("s1", limit=100)

    def test_history_hides_covered_messages(self) -> None:
        """LLM context hides messages before the compaction boundary."""
        from agentnodes.interfaces.api.session.service import SessionService

        messages = self._build_session()
        self.assertEqual(len(messages), 8)  # 6 + marker + current turn in DB

        history = SessionService._convert_messages_to_history(messages)
        # 2 recent messages (seq 5,6) survive + 1 compaction checkpoint
        # (which is also projected as user role) = 3 user-role entries;
        # the 4 covered messages (seq 1-4) are hidden from LLM context
        roles = [h.get("role") for h in history]
        self.assertEqual(roles.count("user"), 3)
        self.assertEqual(roles.count("assistant"), 0)
        # The compaction checkpoint is present
        checkpoint = next(
            h for h in history
            if "<conversation-checkpoint>" in h.get("content", "")
        )
        self.assertIn("## Objective", checkpoint["content"])

    def test_history_keeps_only_recent_compaction(self) -> None:
        """Only the most recent compaction marker enters LLM context."""
        from agentnodes.interfaces.api.session.service import SessionService

        for i in range(6):
            self._emit(
                "message_received",
                {"message_id": f"msg_{i}", "content": f"user content {i}"},
                1000 + i,
            )
        # Compaction 1
        self._emit(
            "compact.ended",
            {
                "summary": "summary 1",
                "messages": [
                    {"role": "system", "content": "summary 1"},
                    {"role": "user", "content": "user content 4"},
                    {"role": "assistant", "content": "assistant content 5"},
                ],
                "compaction_id": "cmp_1",
            },
            2000,
        )
        # New message
        self._emit("message_received", {"message_id": "msg_6", "content": "user content 6"}, 3000)
        # Compaction 2
        self._emit(
            "compact.ended",
            {
                "summary": "summary 2",
                "messages": [
                    {"role": "system", "content": "summary 2"},
                    {"role": "user", "content": "user content 6"},
                ],
                "compaction_id": "cmp_2",
            },
            4000,
        )
        # Current turn after compaction 2
        self._emit("message_received", {"message_id": "msg_cur2", "content": "current turn 2"}, 4500)
        self.proj.flush(self.proj.project("s1"))


        messages = self.proj.project_to_messages("s1", limit=100)
        history = SessionService._convert_messages_to_history(messages)

        checkpoints = [
            h for h in history
            if "<conversation-checkpoint>" in h.get("content", "")
        ]
        self.assertEqual(len(checkpoints), 1)
        # Only the most recent summary (summary 2)
        self.assertIn("summary 2", checkpoints[0]["content"])
        self.assertNotIn("summary 1", checkpoints[0]["content"])
        # 1 recent user message (msg_6) + 1 checkpoint (also user role)
        self.assertEqual(sum(1 for h in history if h.get("role") == "user"), 2)


def _make_event(
    session_id: str,
    event_id: str,
    event_type: str,
    data: dict,
    time_created: float,
) -> object:
    """Build a minimal EventV2-like object for projector._apply."""
    from agentnodes.kernel.events.event_v2 import EventV2

    return EventV2(
        id=event_id,
        aggregate_id=session_id,
        seq=0,
        type=event_type,
        data=data,
        time_created=time_created,
    )


if __name__ == "__main__":
    unittest.main()
