"""Unit tests for Projector event handlers (direct, not through project()).

These tests call each _on_* handler directly to verify:
- Correct state transitions for each event type
- Edge cases (missing fields, duplicates, error paths)
- Handler exception handling in _apply
- Cross-session isolation
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from strategy_research.api.session import projection_handlers as _ph
from strategy_research.api.session.projector import (
    ProjectedMessage,
    ProjectedPart,
    ProjectedSession,
    Projector,
)
from strategy_research.kernel.events.event_v2 import EventType, EventV2


def _make_event(
    event_type: str,
    data: dict | None = None,
    aggregate_id: str = "s1",
    seq: int = 1,
) -> EventV2:
    return EventV2(
        id=f"evt_{seq:04d}",
        aggregate_id=aggregate_id,
        seq=seq,
        type=event_type,
        data=data or {},
        time_created=1000.0 + seq,
    )


# _apply dispatch


class TestProjectorApplyDispatch(unittest.TestCase):
    def setUp(self) -> None:
        self.proj = Projector(Path("/tmp/test.db"))
        self.state = ProjectedSession(session_id="s1")

    def test_apply_known_type_calls_handler(self) -> None:
        event = _make_event(EventType.MESSAGE_RECEIVED, {
            "message_id": "m1", "content": "hello",
        })
        self.proj._apply(event, self.state)
        self.assertIn("m1", self.state.messages)
        self.assertEqual(self.state.messages["m1"].content, "hello")

    def test_apply_unknown_type_logs_and_skips(self) -> None:
        with self.assertLogs(level="DEBUG") as logs:
            event = _make_event("unknown_type", {})
            self.proj._apply(event, self.state)
        self.assertEqual(len(self.state.messages), 0)
        self.assertTrue(any("skipping unknown event type" in m for m in logs.output))

    def test_apply_wrong_aggregate_id_skips(self) -> None:
        with self.assertLogs(level="WARNING") as logs:
            event = _make_event(
                EventType.MESSAGE_RECEIVED, {"message_id": "m1"}, aggregate_id="s2",
            )
            self.proj._apply(event, self.state)
        self.assertEqual(len(self.state.messages), 0)
        self.assertTrue(any("aggregate_id" in m and "!=" in m for m in logs.output))

    def test_apply_handler_exception_logged(self) -> None:
        with self.assertLogs(level="ERROR") as logs:
            event = EventV2(id="bad", aggregate_id="s1", seq=1, type="message_received", data=None, time_created=1000.0)
            self.proj._apply(event, self.state)
        self.assertEqual(len(self.state.messages), 0)
        self.assertTrue(
            any("handler for" in m for m in logs.output) or
            any("raised" in m for m in logs.output)
        )


# text.ended

class TestProjectorOnTextEnded(unittest.TestCase):
    def setUp(self) -> None:
        self.proj = Projector(Path("/tmp/test.db"))
        self.state = ProjectedSession(session_id="s1")

    def test_updates_final_text(self) -> None:
        self.state.messages["m2"] = ProjectedMessage(id="m2", session_id="s1", role="assistant", content="", seq=1)
        self.state.messages["m2"].parts["t1"] = ProjectedPart(id="t1", type="text", data={"text": "partia"}, seq=0)
        e = _make_event(EventType.TEXT_ENDED, {"message_id": "m2", "text_id": "t1", "text": "complete"})
        _ph._on_text_ended(e, self.state)
        self.assertEqual(self.state.messages["m2"].parts["t1"].data["text"], "complete")

    def test_noop_when_text_id_missing(self) -> None:
        self.state.messages["m2"] = ProjectedMessage(id="m2", session_id="s1", role="assistant", content="", seq=1)
        e = _make_event(EventType.TEXT_ENDED, {"message_id": "m2", "text": "done"})
        _ph._on_text_ended(e, self.state)
        self.assertEqual(len(self.state.messages["m2"].parts), 0)

    def test_noop_when_no_message_id(self) -> None:
        e = _make_event(EventType.TEXT_ENDED, {"text": "done"})
        _ph._on_text_ended(e, self.state)
        self.assertEqual(len(self.state.messages), 0)


class TestProjectorOnToolCall(unittest.TestCase):
    def setUp(self) -> None:
        self.proj = Projector(Path("/tmp/test.db"))
        self.state = ProjectedSession(session_id="s1")

    def test_creates_tool_call_part_flat(self) -> None:
        e = _make_event(EventType.TOOL_CALL, {"message_id": "m2", "id": "tc1", "tool": "search", "input": "query"})
        _ph._on_tool_call(e, self.state)
        self.assertIn("tc1", self.state.messages["m2"].parts)
        part = self.state.messages["m2"].parts["tc1"]
        self.assertEqual(part.type, "tool_call")
        self.assertEqual(part.data["tool"], "search")
        self.assertEqual(part.data["input"], "query")
        self.assertEqual(part.data["state"], "call")

    def test_creates_tool_call_part_nested_function(self) -> None:
        e = _make_event(EventType.TOOL_CALL, {"message_id": "m2", "id": "tc1", "function": {"name": "search", "arguments": '{"q": "test"}'}})
        _ph._on_tool_call(e, self.state)
        part = self.state.messages["m2"].parts["tc1"]
        self.assertEqual(part.data["tool"], "search")
        self.assertEqual(part.data["input"], '{"q": "test"}')
        self.assertIn("function", part.data)

    def test_uses_call_id_fallback(self) -> None:
        e = _make_event(EventType.TOOL_CALL, {"message_id": "m2", "call_id": "tc1", "tool": "search"})
        _ph._on_tool_call(e, self.state)
        self.assertIn("tc1", self.state.messages["m2"].parts)

    def test_missing_id_warns(self) -> None:
        with self.assertLogs(level="WARNING") as logs:
            e = _make_event(EventType.TOOL_CALL, {"message_id": "m2", "tool": "search"})
            _ph._on_tool_call(e, self.state)
        self.assertEqual(len(self.state.messages["m2"].parts), 0)
        self.assertTrue(any("without id" in m for m in logs.output))

    def test_duplicate_tool_call_id_is_noop(self) -> None:
        self.state.messages["m2"] = ProjectedMessage(id="m2", session_id="s1", role="assistant", content="", seq=1)
        self.state.messages["m2"].parts["tc1"] = ProjectedPart(id="tc1", type="tool_call", data={"state": "done"}, seq=0)
        e = _make_event(EventType.TOOL_CALL, {"message_id": "m2", "id": "tc1", "tool": "search"})
        _ph._on_tool_call(e, self.state)
        self.assertEqual(self.state.messages["m2"].parts["tc1"].data["state"], "done")

    def test_uses_arguments_when_no_input(self) -> None:
        e = _make_event(EventType.TOOL_CALL, {"message_id": "m2", "id": "tc1", "tool": "search", "arguments": "args"})
        _ph._on_tool_call(e, self.state)
        self.assertEqual(self.state.messages["m2"].parts["tc1"].data["input"], "args")

    def test_noop_when_no_message_id(self) -> None:
        e = _make_event(EventType.TOOL_CALL, {"id": "tc1", "tool": "search"})
        _ph._on_tool_call(e, self.state)
        self.assertEqual(len(self.state.messages), 0)


class TestProjectorOnToolResult(unittest.TestCase):
    def setUp(self) -> None:
        self.proj = Projector(Path("/tmp/test.db"))
        self.state = ProjectedSession(session_id="s1")

    def test_updates_existing_tool_call(self) -> None:
        self.state.messages["m2"] = ProjectedMessage(id="m2", session_id="s1", role="assistant", content="", seq=1)
        self.state.messages["m2"].parts["tc1"] = ProjectedPart(id="tc1", type="tool_call", data={"state": "call"}, seq=0)
        e = _make_event(EventType.TOOL_RESULT, {"message_id": "m2", "id": "tc1", "result": "done"})
        _ph._on_tool_result(e, self.state)
        part = self.state.messages["m2"].parts["tc1"]
        self.assertEqual(part.data["result"], "done")
        self.assertEqual(part.data["status"], "done")
        self.assertEqual(part.data["state"], "done")

    def test_creates_part_if_not_exists(self) -> None:
        self.state.messages["m2"] = ProjectedMessage(id="m2", session_id="s1", role="assistant", content="", seq=1)
        e = _make_event(EventType.TOOL_RESULT, {"message_id": "m2", "id": "tc1", "result": "done"})
        _ph._on_tool_result(e, self.state)
        self.assertIn("tc1", self.state.messages["m2"].parts)
        self.assertEqual(self.state.messages["m2"].parts["tc1"].data["result"], "done")

    def test_uses_preview_as_result_fallback(self) -> None:
        self.state.messages["m2"] = ProjectedMessage(id="m2", session_id="s1", role="assistant", content="", seq=1)
        self.state.messages["m2"].parts["tc1"] = ProjectedPart(id="tc1", type="tool_call", data={}, seq=0)
        e = _make_event(EventType.TOOL_RESULT, {"message_id": "m2", "id": "tc1", "preview": "preview result"})
        _ph._on_tool_result(e, self.state)
        self.assertEqual(self.state.messages["m2"].parts["tc1"].data["result"], "preview result")

    def test_missing_id_warns(self) -> None:
        self.state.messages["m2"] = ProjectedMessage(id="m2", session_id="s1", role="assistant", content="", seq=1)
        with self.assertLogs(level="WARNING") as logs:
            e = _make_event(EventType.TOOL_RESULT, {"message_id": "m2", "result": "done"})
            _ph._on_tool_result(e, self.state)
        self.assertTrue(any("without id" in m for m in logs.output))

    def test_noop_when_no_message_id(self) -> None:
        e = _make_event(EventType.TOOL_RESULT, {"id": "tc1", "result": "done"})
        _ph._on_tool_result(e, self.state)
        self.assertEqual(len(self.state.messages), 0)


class TestProjectorOnToolProgress(unittest.TestCase):
    def setUp(self) -> None:
        self.proj = Projector(Path("/tmp/test.db"))
        self.state = ProjectedSession(session_id="s1")

    def test_appends_progress_to_existing_part(self) -> None:
        self.state.messages["m2"] = ProjectedMessage(id="m2", session_id="s1", role="assistant", content="", seq=1)
        self.state.messages["m2"].parts["tc1"] = ProjectedPart(id="tc1", type="tool_call", data={}, seq=0)
        e = _make_event(EventType.TOOL_PROGRESS, {"message_id": "m2", "id": "tc1", "stage": "running", "current": 1, "total": 5})
        _ph._on_tool_progress(e, self.state)
        progress = self.state.messages["m2"].parts["tc1"].data["progress"]
        self.assertEqual(len(progress), 1)
        self.assertEqual(progress[0]["stage"], "running")
        self.assertEqual(progress[0]["current"], 1)

    def test_accumulates_multiple_progress(self) -> None:
        self.state.messages["m2"] = ProjectedMessage(id="m2", session_id="s1", role="assistant", content="", seq=1)
        self.state.messages["m2"].parts["tc1"] = ProjectedPart(id="tc1", type="tool_call", data={}, seq=0)
        for i in range(3):
            e = _make_event(EventType.TOOL_PROGRESS, {"message_id": "m2", "id": "tc1", "stage": f"step_{i}"}, seq=i + 1)
            _ph._on_tool_progress(e, self.state)
        self.assertEqual(len(self.state.messages["m2"].parts["tc1"].data["progress"]), 3)

    def test_noop_when_part_not_found(self) -> None:
        self.state.messages["m2"] = ProjectedMessage(id="m2", session_id="s1", role="assistant", content="", seq=1)
        e = _make_event(EventType.TOOL_PROGRESS, {"message_id": "m2", "id": "nonexistent", "stage": "running"})
        _ph._on_tool_progress(e, self.state)
        self.assertEqual(len(self.state.messages["m2"].parts), 0)

    def test_noop_when_no_message_id(self) -> None:
        e = _make_event(EventType.TOOL_PROGRESS, {"id": "tc1", "stage": "running"})
        _ph._on_tool_progress(e, self.state)
        self.assertEqual(len(self.state.messages), 0)


class TestProjectorOnCompactMarker(unittest.TestCase):
    def setUp(self) -> None:
        self.proj = Projector(Path("/tmp/test.db"))
        self.state = ProjectedSession(session_id="s1")

    def test_creates_compaction_marker(self) -> None:
        e = _make_event(EventType.COMPACT_ENDED, {"summary": "compressed history"})
        _ph._on_compact(e, self.state)
        self.assertEqual(len(self.state.messages), 1)
        msg = list(self.state.messages.values())[0]
        self.assertEqual(msg.role, "system")
        self.assertEqual(msg.content, "compressed history")
        self.assertEqual(msg.message_type, "compaction")
        self.assertEqual(msg.seq, 1)

    def test_uses_data_content_as_fallback_summary(self) -> None:
        e = _make_event(EventType.COMPACT, {"content": "fallback summary"})
        _ph._on_compact(e, self.state)
        msg = list(self.state.messages.values())[0]
        self.assertEqual(msg.content, "fallback summary")

    def test_empty_summary_becomes_empty_string(self) -> None:
        e = _make_event(EventType.COMPACT_ENDED, {})
        _ph._on_compact(e, self.state)
        msg = list(self.state.messages.values())[0]
        self.assertEqual(msg.content, "")

    def test_updates_existing_compaction_marker(self) -> None:
        # Use same event id for both events so they share the same compaction message id
        e1 = EventV2(id="cmpevt", aggregate_id="s1", seq=1, type=EventType.COMPACT_ENDED, data={"summary": "first"}, time_created=1001.0)
        _ph._on_compact(e1, self.state)
        e2 = EventV2(id="cmpevt", aggregate_id="s1", seq=2, type=EventType.COMPACT_ENDED, data={"summary": "updated"}, time_created=1002.0)
        _ph._on_compact(e2, self.state)
        self.assertEqual(len(self.state.messages), 1)
        msg = list(self.state.messages.values())[0]
        self.assertEqual(msg.content, "updated")


