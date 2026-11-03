"""Tests for Projector (Level 3, B1 commit 4).

The projector reads events from event_log and builds an in-memory
ProjectedSession. The B1 projector is a pure read tool (no DB writes).

These tests verify:
1. State shape: ProjectedSession, ProjectedMessage, ProjectedPart
2. Event handlers: each event type produces the expected state
3. Idempotency: re-applying the same event is a no-op
4. Project + compare: project() produces the same state as a
   hand-built expected state
5. Real-DB round-trip: events inserted via EventBusV2 + SQLite
   can be projected back into a ProjectedSession that matches
   the messages + message_parts tables (consistency check)
"""
from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tests._fixtures.legacy_event_bus_v2 import EventBusV2
from strategy_research.kernel.events.event_v2 import EventType, EventV2
from tests._fixtures.legacy_events import EventBus
from strategy_research.api.session.projector import (
    ProjectedMessage,
    ProjectedPart,
    ProjectedSession,
    Projector,
)


def _setup_db(db_path: Path) -> None:
    """Create the full schema (sessions, messages, message_parts, event_log)."""
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("""
            CREATE TABLE sessions (
                id TEXT PRIMARY KEY, user_id TEXT, title TEXT,
                created_at REAL, updated_at REAL,
                starred INTEGER DEFAULT 0, tags_json TEXT DEFAULT '[]',
                message_count INTEGER DEFAULT 0, archived INTEGER DEFAULT 0
            )
        """)
        conn.execute("""
            CREATE TABLE messages (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL DEFAULT '',
                created_at REAL NOT NULL,
                metadata_json TEXT,
                message_type TEXT,
                seq INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
            )
        """)
        conn.execute("""
            CREATE TABLE message_parts (
                id TEXT PRIMARY KEY,
                message_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                type TEXT NOT NULL,
                data_json TEXT NOT NULL,
                seq INTEGER NOT NULL DEFAULT 0,
                time_created REAL NOT NULL,
                FOREIGN KEY (message_id) REFERENCES messages(id) ON DELETE CASCADE,
                FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
            )
        """)
        conn.execute("""
            CREATE TABLE event_log (
                id TEXT PRIMARY KEY,
                aggregate_id TEXT NOT NULL,
                seq INTEGER NOT NULL,
                type TEXT NOT NULL,
                data_json TEXT NOT NULL,
                time_created REAL NOT NULL,
                FOREIGN KEY (aggregate_id) REFERENCES sessions(id) ON DELETE CASCADE,
                UNIQUE (aggregate_id, seq)
            )
        """)
        conn.commit()
    finally:
        conn.close()


class TestProjectedState(unittest.TestCase):
    """Test the data classes used by the projector."""

    def test_projected_message_defaults(self) -> None:
        m = ProjectedMessage(
            id="m1", session_id="s1", role="user", content="hi"
        )
        self.assertEqual(m.parts, {})
        self.assertEqual(m.message_type, "assistant")
        self.assertEqual(m.seq, 0)
        self.assertIsNone(m.attempt_id)

    def test_parts_in_order_by_seq(self) -> None:
        m = ProjectedMessage(
            id="m1", session_id="s1", role="assistant", content=""
        )
        m.parts["p1"] = ProjectedPart(id="p1", type="text", data={}, seq=2)
        m.parts["p2"] = ProjectedPart(id="p2", type="tool_call", data={}, seq=0)
        m.parts["p3"] = ProjectedPart(id="p3", type="text", data={}, seq=1)
        ordered = m.parts_in_order()
        self.assertEqual([p.id for p in ordered], ["p2", "p3", "p1"])

    def test_session_messages_in_order(self) -> None:
        s = ProjectedSession(session_id="s1")
        s.messages["m1"] = ProjectedMessage(
            id="m1", session_id="s1", role="user", content="", seq=2
        )
        s.messages["m2"] = ProjectedMessage(
            id="m2", session_id="s1", role="assistant", content="", seq=1
        )
        ordered = s.messages_in_order()
        self.assertEqual([m.id for m in ordered], ["m2", "m1"])

    def test_to_message_rows(self) -> None:
        s = ProjectedSession(session_id="s1")
        s.messages["m1"] = ProjectedMessage(
            id="m1", session_id="s1", role="user",
            content="hi", seq=1, created_at=1.0, message_type="user"
        )
        rows = s.to_message_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], "m1")
        self.assertEqual(rows[0]["role"], "user")
        self.assertEqual(rows[0]["content"], "hi")
        self.assertNotIn("parts", rows[0])

    def test_to_part_rows(self) -> None:
        s = ProjectedSession(session_id="s1")
        m = ProjectedMessage(
            id="m1", session_id="s1", role="assistant", content="", seq=1
        )
        m.parts["p1"] = ProjectedPart(
            id="p1", type="text", data={"text": "hi"}, seq=0,
            time_created=1.0
        )
        s.messages["m1"] = m
        rows = s.to_part_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], "p1")
        self.assertEqual(rows[0]["message_id"], "m1")
        self.assertEqual(rows[0]["type"], "text")
        self.assertEqual(json.loads(rows[0]["data_json"]), {"text": "hi"})


class TestProjectorHandlers(unittest.TestCase):
    """Test individual event handlers in isolation."""

    def setUp(self) -> None:
        self.state = ProjectedSession(session_id="s1")
        self.projector = Projector(Path("/tmp/nonexistent"))  # handlers don't touch DB

    def test_message_received_creates_user_message(self) -> None:
        e = EventV2.create("s1", 1, EventType.MESSAGE_RECEIVED, {
            "message_id": "user-msg-1",
            "user_message_id": "user-msg-1",
            "role": "user",
            "content": "你好",
        })
        self.projector.apply(e, self.state)
        m = self.state.messages["user-msg-1"]
        self.assertEqual(m.role, "user")
        self.assertEqual(m.content, "你好")
        self.assertEqual(m.message_type, "user")
        self.assertEqual(m.seq, 1)

    def test_assistant_message_creates_assistant_message(self) -> None:
        e = EventV2.create("s1", 1, EventType.ASSISTANT_MESSAGE, {
            "message_id": "asst-msg-1",
            "content": "Hello",
        })
        self.projector.apply(e, self.state)
        m = self.state.messages["asst-msg-1"]
        self.assertEqual(m.role, "assistant")
        self.assertEqual(m.content, "Hello")

    def test_text_started_creates_text_part_lazily(self) -> None:
        """text.started should lazy-create the assistant message."""
        e = EventV2.create("s1", 1, EventType.TEXT_STARTED, {
            "message_id": "asst-msg-1",
            "text_id": "t1",
        })
        self.projector.apply(e, self.state)
        m = self.state.messages["asst-msg-1"]
        self.assertEqual(m.role, "assistant")
        self.assertIn("t1", m.parts)
        self.assertEqual(m.parts["t1"].type, "text")
        self.assertEqual(m.parts["t1"].data["text"], "")

    def test_text_delta_appends_to_text_part(self) -> None:
        e1 = EventV2.create("s1", 1, EventType.TEXT_STARTED, {
            "message_id": "asst-msg-1",
            "text_id": "t1",
        })
        e2 = EventV2.create("s1", 2, EventType.TEXT_DELTA, {
            "message_id": "asst-msg-1",
            "text_id": "t1",
            "text": "Hello ",
        })
        e3 = EventV2.create("s1", 3, EventType.TEXT_DELTA, {
            "message_id": "asst-msg-1",
            "text_id": "t1",
            "text": "world",
        })
        for e in (e1, e2, e3):
            self.projector.apply(e, self.state)
        text = self.state.messages["asst-msg-1"].parts["t1"].data["text"]
        self.assertEqual(text, "Hello world")

    def test_text_ended_overrides_with_final_text(self) -> None:
        e1 = EventV2.create("s1", 1, EventType.TEXT_STARTED, {
            "message_id": "asst-msg-1",
            "text_id": "t1",
        })
        e2 = EventV2.create("s1", 2, EventType.TEXT_DELTA, {
            "message_id": "asst-msg-1",
            "text_id": "t1",
            "text": "partial",
        })
        e3 = EventV2.create("s1", 3, EventType.TEXT_ENDED, {
            "message_id": "asst-msg-1",
            "text_id": "t1",
            "text": "final",
        })
        for e in (e1, e2, e3):
            self.projector.apply(e, self.state)
        text = self.state.messages["asst-msg-1"].parts["t1"].data["text"]
        self.assertEqual(text, "final")

    def test_tool_call_creates_tool_call_part(self) -> None:
        e = EventV2.create("s1", 1, EventType.TOOL_CALL, {
            "message_id": "asst-msg-1",
            "id": "tc-1",
            "tool": "get_market_data",
            "input": {"symbol": "000001.SZ"},
        })
        self.projector.apply(e, self.state)
        m = self.state.messages["asst-msg-1"]
        self.assertIn("tc-1", m.parts)
        part = m.parts["tc-1"]
        self.assertEqual(part.type, "tool_call")
        self.assertEqual(part.data["tool"], "get_market_data")
        self.assertEqual(part.data["state"], "call")
        self.assertEqual(part.data["input"], {"symbol": "000001.SZ"})

    def test_tool_result_updates_tool_call_part(self) -> None:
        """opencode pattern: tool_result is a state update, not a new part."""
        e1 = EventV2.create("s1", 1, EventType.TOOL_CALL, {
            "message_id": "asst-msg-1",
            "id": "tc-1",
            "tool": "calc",
            "input": {"x": 1},
        })
        e2 = EventV2.create("s1", 2, EventType.TOOL_RESULT, {
            "message_id": "asst-msg-1",
            "id": "tc-1",
            "result": "42",
            "status": "done",
        })
        for e in (e1, e2):
            self.projector.apply(e, self.state)
        m = self.state.messages["asst-msg-1"]
        self.assertIn("tc-1", m.parts)
        part = m.parts["tc-1"]
        self.assertEqual(part.data["result"], "42")
        self.assertEqual(part.data["status"], "done")
        self.assertEqual(part.data["state"], "done")
        # No NEW part was created — tc-1 is still the only tool part
        tool_parts = [p for p in m.parts.values() if p.type == "tool_call"]
        self.assertEqual(len(tool_parts), 1)

    def test_tool_progress_appends_to_part(self) -> None:
        e1 = EventV2.create("s1", 1, EventType.TOOL_CALL, {
            "message_id": "asst-msg-1",
            "id": "tc-1",
            "tool": "fetch",
            "input": {},
        })
        e2 = EventV2.create("s1", 2, EventType.TOOL_PROGRESS, {
            "message_id": "asst-msg-1",
            "id": "tc-1",
            "stage": "downloading",
            "current": 3,
            "total": 10,
        })
        e3 = EventV2.create("s1", 3, EventType.TOOL_PROGRESS, {
            "message_id": "asst-msg-1",
            "id": "tc-1",
            "stage": "downloading",
            "current": 7,
            "total": 10,
        })
        for e in (e1, e2, e3):
            self.projector.apply(e, self.state)
        progress = self.state.messages["asst-msg-1"].parts["tc-1"].data["progress"]
        self.assertEqual(len(progress), 2)
        self.assertEqual(progress[0]["current"], 3)
        self.assertEqual(progress[1]["current"], 7)

    def test_idempotent_message_received(self) -> None:
        """Re-applying the same event is a no-op."""
        e = EventV2.create("s1", 1, EventType.MESSAGE_RECEIVED, {
            "message_id": "user-msg-1",
            "content": "hi",
        })
        self.projector.apply(e, self.state)
        # Re-apply with different content (simulating re-emit)
        e2 = EventV2.create("s1", 1, EventType.MESSAGE_RECEIVED, {
            "message_id": "user-msg-1",
            "content": "different",
        })
        self.projector.apply(e2, self.state)
        # First event wins
        self.assertEqual(self.state.messages["user-msg-1"].content, "hi")

    def test_unknown_event_type_skipped(self) -> None:
        """Unknown event types don't crash the projector."""
        e = EventV2.create("s1", 1, "future.event.type", {})
        self.projector.apply(e, self.state)  # should not raise
        self.assertEqual(len(self.state.messages), 0)

    def test_thinking_events_absorbed(self) -> None:
        """Thinking events render as a collapsed ``thinking`` part in B1
        (B7-fix: _on_thinking_start creates a collapsed part; deltas append).
        """
        for et in (EventType.THINKING_START, EventType.THINKING_DELTA,
                   EventType.THINKING_DONE, EventType.THINKING_END):
            e = EventV2.create("s1", 1, et, {
                "message_id": "asst-msg-1",
                "delta": "thought",
            })
            self.projector.apply(e, self.state)
        m = self.state.messages.get("asst-msg-1")
        if m:
            part = m.parts.get("think_1")
            assert part is not None, f"expected thinking part, got {m.parts}"
            self.assertEqual(part.type, "thinking")
            self.assertTrue(part.data.get("collapsed"))
            self.assertEqual(part.data.get("text"), "thought")


class TestProjectorIntegration(unittest.TestCase):
    """Test projector against a real SQLite DB."""

    def setUp(self) -> None:
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db_path = Path(self.tmp.name)
        _setup_db(self.db_path)
        # Create a session for FK constraints
        conn = sqlite3.connect(str(self.db_path))
        try:
            conn.execute(
                "INSERT INTO sessions (id, user_id, title, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?)",
                ("s1", "u1", "test", 1.0, 1.0),
            )
            conn.commit()
        finally:
            conn.close()
        self.bus = EventBus()
        self.v2 = EventBusV2(self.bus, self.db_path)
        self.projector = Projector(self.db_path)

    def tearDown(self) -> None:
        try:
            self.db_path.unlink(missing_ok=True)
        except OSError:
            pass  # Windows may still hold sqlite handles

    def test_project_empty_session(self) -> None:
        state = self.projector.project("s1")
        self.assertEqual(len(state.messages), 0)
        self.assertEqual(state.last_seq, 0)

    def test_project_full_conversation(self) -> None:
        """User msg → assistant text + tool call + tool result."""
        # User sends a message
        self.v2.publish(EventV2.create("s1", 1, EventType.MESSAGE_RECEIVED, {
            "message_id": "u1",
            "user_message_id": "u1",
            "role": "user",
            "content": "What's the price?",
        }))
        # Assistant starts streaming
        self.v2.publish(EventV2.create("s1", 2, EventType.TEXT_STARTED, {
            "message_id": "a1",
            "text_id": "t1",
        }))
        self.v2.publish(EventV2.create("s1", 3, EventType.TEXT_DELTA, {
            "message_id": "a1",
            "text_id": "t1",
            "text": "Let me check.",
        }))
        # Tool call
        self.v2.publish(EventV2.create("s1", 4, EventType.TOOL_CALL, {
            "message_id": "a1",
            "id": "tc-1",
            "tool": "get_price",
            "input": {"symbol": "000001.SZ"},
        }))
        # Tool result
        self.v2.publish(EventV2.create("s1", 5, EventType.TOOL_RESULT, {
            "message_id": "a1",
            "id": "tc-1",
            "result": "10.5",
            "status": "done",
        }))
        # Final assistant message
        self.v2.publish(EventV2.create("s1", 6, EventType.ASSISTANT_MESSAGE, {
            "message_id": "a1",
            "content": "Let me check. The price is 10.5.",
        }))

        state = self.projector.project("s1")

        # Should have 2 messages: user and assistant
        self.assertEqual(len(state.messages), 2)
        self.assertIn("u1", state.messages)
        self.assertIn("a1", state.messages)

        # User message
        u = state.messages["u1"]
        self.assertEqual(u.role, "user")
        self.assertEqual(u.content, "What's the price?")

        # Assistant message: 2 parts (text + tool_call)
        a = state.messages["a1"]
        self.assertEqual(a.role, "assistant")
        self.assertEqual(a.content, "Let me check. The price is 10.5.")
        self.assertIn("t1", a.parts)
        self.assertIn("tc-1", a.parts)
        self.assertEqual(a.parts["t1"].data["text"], "Let me check.")
        self.assertEqual(a.parts["tc-1"].data["result"], "10.5")
        self.assertEqual(a.parts["tc-1"].data["state"], "done")

    def test_project_isolates_sessions(self) -> None:
        conn = sqlite3.connect(str(self.db_path))
        try:
            conn.execute(
                "INSERT INTO sessions (id, user_id, title, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?)",
                ("s2", "u1", "test", 1.0, 1.0),
            )
            conn.commit()
        finally:
            conn.close()

        self.v2.publish(EventV2.create("s1", 1, EventType.MESSAGE_RECEIVED, {
            "message_id": "u1",
            "content": "s1 msg",
        }))
        self.v2.publish(EventV2.create("s2", 1, EventType.MESSAGE_RECEIVED, {
            "message_id": "u2",
            "content": "s2 msg",
        }))

        s1_state = self.projector.project("s1")
        s2_state = self.projector.project("s2")
        self.assertEqual(len(s1_state.messages), 1)
        self.assertEqual(len(s2_state.messages), 1)
        self.assertEqual(s1_state.messages["u1"].content, "s1 msg")
        self.assertEqual(s2_state.messages["u2"].content, "s2 msg")

    def test_to_message_rows_matches_insertable_shape(self) -> None:
        """The projected state can be written back to messages table."""
        self.v2.publish(EventV2.create("s1", 1, EventType.MESSAGE_RECEIVED, {
            "message_id": "u1",
            "user_message_id": "u1",
            "role": "user",
            "content": "hi",
        }))
        state = self.projector.project("s1")
        rows = state.to_message_rows()
        self.assertEqual(len(rows), 1)
        # Verify it can be inserted into messages table
        conn = sqlite3.connect(str(self.db_path))
        try:
            for r in rows:
                conn.execute(
                    "INSERT INTO messages (id, session_id, role, content, "
                    "created_at, message_type, seq) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (r["id"], r["session_id"], r["role"], r["content"],
                     r["created_at"], r["message_type"], r["seq"]),
                )
            conn.commit()
            count = conn.execute(
                "SELECT COUNT(*) FROM messages WHERE session_id = ?", ("s1",)
            ).fetchone()[0]
            self.assertEqual(count, 1)
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
