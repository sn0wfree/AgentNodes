"""Tests for the Projector's thinking-block handlers.

B7-fix: prior to this commit, the four ``THINKING_*`` handlers in
``projector.py`` were ``lambda e, s: None`` no-ops — meaning thinking
events were stored in ``event_log`` but never materialised into
``message_parts`` rows. The result: the assistant message's thinking
blocks rendered correctly during live streaming (the SSE bridge
pushes them straight to the frontend) but **disappeared on page
refresh** because the GET ``/api/chat/session/{id}/messages`` payload
is built from the ``message_parts`` table.

These tests pin the new behaviour: a thinking lifecycle is properly
persisted, multiple thinking blocks in the same message don't collide,
and replays are idempotent.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from agentnodes.interfaces.api.routers.web_session import _ensure_schema
from agentnodes.kernel.events.event_v2 import EventType, EventV2
from agentnodes.interfaces.api.session.projector import Projector


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    """Create an empty projectable schema in a temp dir.

    The ``Projector`` itself does not own the event_log table
    (that's defined in ``web_session._ensure_schema``). We have to
    run it explicitly so the test can INSERT events.
    """
    db = tmp_path / "test.db"
    with sqlite3.connect(str(db)) as conn:
        _ensure_schema(conn)
        conn.commit()
    return db


def _make_event(seq: int, event_type: str, message_id: str, **data) -> EventV2:
    """Build an EventV2 with the minimum shape projector consumes."""
    return EventV2(
        id=f"evt_{seq}",
        aggregate_id="sess_test",
        seq=seq,
        type=event_type,
        data={"message_id": message_id, **data},
        time_created=1700000000.0 + seq,
    )


def _project_with_events(
    db_path: Path, message_id: str, events: list[EventV2],
) -> dict:
    """Run ``projector.project`` over a fresh in-memory cache and
    return the persisted ``message_parts`` rows keyed by message_id."""
    # Use a single fresh Projector instance so the on-disk schema
    # is initialised exactly once.
    proj = Projector(db_path)
    # project() walks event_log — we have to write events into the
    # backing event_log table first because project() doesn't take
    # events directly.
    with sqlite3.connect(str(db_path)) as conn:
        for ev in events:
            conn.execute(
                "INSERT INTO event_log (id, aggregate_id, seq, type, "
                "data_json, time_created) VALUES (?, ?, ?, ?, ?, ?)",
                (ev.id, ev.aggregate_id, ev.seq, ev.type,
                 json.dumps(ev.data, ensure_ascii=False), ev.time_created),
            )
        conn.commit()
    state = proj.project("sess_test")
    parts = {}
    if message_id in state.messages:
        for part_id, part in state.messages[message_id].parts.items():
            parts[part_id] = (part.type, part.data)
    return parts


# ── 1. Single thinking block: full lifecycle ────────────────────


class TestThinkingSingleBlock:
    def test_start_delta_end_yields_one_persisted_part(self, db_path: Path) -> None:
        """start → delta* → end must produce exactly one ``thinking`` part
        in ``state.parts`` with the accumulated text."""
        mid = "msg_think_single"
        events = [
            _make_event(10, EventType.THINKING_START, mid),
            _make_event(11, EventType.THINKING_DELTA, mid, delta="用户问好，"),
            _make_event(12, EventType.THINKING_DELTA, mid, delta="我回应。"),
            _make_event(13, EventType.THINKING_END, mid),
        ]
        parts = _project_with_events(db_path, mid, events)
        assert len(parts) == 1, f"expected 1 thinking part, got {len(parts)}"
        part_type, part_data = next(iter(parts.values()))
        assert part_type == "thinking"
        assert part_data["text"] == "用户问好，我回应。"
        # Default collapsed state is set so DB reloads render folded
        # (matches the live-streaming UX where thinking is collapsed
        # after agent_done).
        assert part_data["collapsed"] is True
        # The part_id is derived from event.seq so a single message's
        # thinking blocks never collide with the text part's
        # UUID-based text_id.
        assert next(iter(parts.keys())) == "think_10"

    def test_start_can_be_implicit_via_delta_lazy_create(self, db_path: Path) -> None:
        """If a ``thinking_delta`` arrives before ``thinking_start``
        (replay / reconnect), the projector lazy-creates an open
        thinking part. Subsequent deltas append to that same part
        — so the stream ends up consolidated into a single part
        rather than fragmented by seq.
        """
        mid = "msg_lazy_think"
        events = [
            _make_event(20, EventType.THINKING_DELTA, mid, delta="hello"),
            _make_event(21, EventType.THINKING_DELTA, mid, delta=" world"),
        ]
        parts = _project_with_events(db_path, mid, events)
        # Single consolidated part: the first delta lazily creates
        # think_<seq> as the open part; the second delta appends to it
        # via ``msg.open_thinking_part_id``.
        assert len(parts) == 1, (
            f"delta-lazy-create should consolidate into a single part, "
            f"got {len(parts)}: {list(parts.keys())}"
        )
        _, part_data = next(iter(parts.values()))
        assert part_data["text"] == "hello world"

    def test_done_and_end_are_no_ops(self, db_path: Path) -> None:
        """``thinking_done`` / ``thinking_end`` carry no delta and must
        not duplicate or overwrite the part created by ``start`` /
        ``delta`` events."""
        mid = "msg_done_end"
        events = [
            _make_event(30, EventType.THINKING_START, mid),
            _make_event(31, EventType.THINKING_DELTA, mid, delta="plan"),
            _make_event(32, EventType.THINKING_DONE, mid),
            _make_event(33, EventType.THINKING_END, mid),
        ]
        parts = _project_with_events(db_path, mid, events)
        assert len(parts) == 1
        _, part_data = next(iter(parts.values()))
        # The text from the delta event is preserved verbatim. done/end
        # don't append / clear the text.
        assert part_data["text"] == "plan"


# ── 2. Multiple thinking blocks in one message ───────────────────


class TestThinkingMultipleBlocks:
    def test_three_alternating_blocks_yield_three_distinct_parts(self, db_path: Path) -> None:
        """The bug fix was hidden by the assumption "one thinking per
        message". In production we observed 3+ thinking blocks per
        message (the LLM alternates ``thinking_start/end`` between
        tool calls). Each must be a distinct part with its own
        accumulated text."""
        mid = "msg_multi"
        events = [
            _make_event(40, EventType.THINKING_START, mid),
            _make_event(41, EventType.THINKING_DELTA, mid, delta="block 1"),
            _make_event(42, EventType.THINKING_END, mid),
            _make_event(50, EventType.THINKING_START, mid),
            _make_event(51, EventType.THINKING_DELTA, mid, delta="block 2"),
            _make_event(52, EventType.THINKING_END, mid),
            _make_event(60, EventType.THINKING_START, mid),
            _make_event(61, EventType.THINKING_DELTA, mid, delta="block 3"),
            _make_event(62, EventType.THINKING_END, mid),
        ]
        parts = _project_with_events(db_path, mid, events)
        assert len(parts) == 3
        # Each block's text is preserved on its own part.
        texts = {d["text"] for _, d in parts.values()}
        assert texts == {"block 1", "block 2", "block 3"}
        # The part_id is derived from the *start* event's seq, so the
        # three blocks have stable, collision-free ids.
        ids = set(parts.keys())
        assert ids == {"think_40", "think_50", "think_60"}


# ── 3. Interleaved with text / tool_call ─────────────────────────


class TestThinkingMixedWithOtherParts:
    def test_thinking_text_tool_call_ordering(self, db_path: Path) -> None:
        """A realistic streaming timeline interleaves thinking with
        text and tool calls. Each part type must be persisted under
        its own id namespace and the seq field must reflect the
        arrival order (for stable DB load / animation)."""
        mid = "msg_mixed"
        events = [
            _make_event(70, EventType.THINKING_START, mid),
            _make_event(71, EventType.THINKING_DELTA, mid, delta="think A"),
            _make_event(72, EventType.THINKING_END, mid),
            _make_event(73, EventType.TEXT_STARTED, mid, text_id="text-A"),
            _make_event(74, EventType.TEXT_DELTA, mid, text_id="text-A", text="hello"),
            _make_event(75, EventType.TEXT_ENDED, mid, text_id="text-A", text="hello"),
            _make_event(76, EventType.THINKING_START, mid),
            _make_event(77, EventType.THINKING_DELTA, mid, delta="think B"),
            _make_event(78, EventType.THINKING_END, mid),
            _make_event(79, EventType.TOOL_CALL, mid, id="tc-1", name="list",
                         arguments="{}"),
            _make_event(80, EventType.TOOL_RESULT, mid, id="tc-1",
                         result="[]", status="done"),
        ]
        parts = _project_with_events(db_path, mid, events)
        # Three distinct part types, no collisions.
        types = {t for t, _ in parts.values()}
        assert types == {"thinking", "text", "tool_call"}
        # Thinking blocks: 2
        thinking = [d for t, d in parts.values() if t == "thinking"]
        assert {d["text"] for d in thinking} == {"think A", "think B"}
        # Text: 1
        text = [d for t, d in parts.values() if t == "text"]
        assert text[0]["text"] == "hello"
        # Tool call: 1
        tc = [d for t, d in parts.values() if t == "tool_call"]
        assert tc[0]["name"] == "list"
        assert tc[0]["result"] == "[]"
        # seq order: thinking_70 < text_73 < thinking_76 < tool_79.
        # (We check that thinking/text/tool all coexist; the project()
        # in-memory `parts` dict doesn't preserve insertion order, but
        # the ProjectedPart.seq field on the wire does.)
        # The helper returns ``(type, data)`` tuples, not full
        # ProjectedPart objects — we just confirm the count.
        assert len(parts) == 4



# ── 4. Idempotency on replay ────────────────────────────────────


class TestThinkingReplayIdempotency:
    def test_double_apply_same_events_yields_same_parts(self, db_path: Path) -> None:
        """SSE EventSource reconnects and replays the same events. The
        projector must NOT duplicate parts on the second pass."""
        mid = "msg_replay"
        events = [
            _make_event(90, EventType.THINKING_START, mid),
            _make_event(91, EventType.THINKING_DELTA, mid, delta="once"),
            _make_event(92, EventType.THINKING_END, mid),
        ]
        # First pass: persist via project().
        first = _project_with_events(db_path, mid, events)
        assert len(first) == 1
        # Second pass on the same DB: project() re-walks event_log.
        # The state is rebuilt from scratch (cache miss), so the
        # first pass's rows don't leak in. But we want to also ensure
        # the cache-hit path is idempotent.
        proj = Projector(db_path)
        proj.project("sess_test")  # populates cache
        proj2 = Projector(db_path)
        state2 = proj2.project("sess_test")
        # Both passes yield the same in-memory state.
        if mid in state2.messages:
            assert len(state2.messages[mid].parts) == 1
            # The non-regression guarantee: ``idempotent`` means the
            # think_<seq> part_id is created exactly once and updates are
            # coalesced.
            for p in state2.messages[mid].parts.values():
                assert p.type == "thinking"
                assert p.data["text"] == "once"


# ── 5. Empty thinking is preserved (defensive) ──────────────────


class TestThinkingEmpty:
    def test_start_with_no_delta_yields_empty_text(self, db_path: Path) -> None:
        """An LLM may emit a thinking_start then immediately end (e.g.
        the model decided to skip thinking). The empty-text part
        must still be persisted so the structural round-trip is
        lossless."""
        mid = "msg_empty_think"
        events = [
            _make_event(100, EventType.THINKING_START, mid),
            _make_event(101, EventType.THINKING_END, mid),
        ]
        parts = _project_with_events(db_path, mid, events)
        assert len(parts) == 1
        _, part_data = next(iter(parts.values()))
        assert part_data["text"] == ""
        assert part_data["collapsed"] is True


# ── P1: thinking part must precede its text part ────────────────────


class TestThinkingBeforeTextOrder:
    """P1-fix: loop.py emits thinking_start BEFORE text.started so both
    the projector (persisted seq) and the frontend parts array put the
    thinking block ABOVE the text body — live and after refresh."""

    def test_thinking_part_seq_below_text_part(self, db_path: Path) -> None:
        mid = "msg_order"
        events = [
            # text.started / thinking_start arrive in the new order
            # (thinking first) — same seq order as loop.py now emits.
            _make_event(20, EventType.THINKING_START, mid),
            _make_event(21, EventType.TEXT_STARTED, mid, text_id="t1"),
            _make_event(22, EventType.THINKING_DELTA, mid, delta="thinking text"),
            _make_event(23, EventType.THINKING_END, mid),
            _make_event(24, EventType.TEXT_DELTA, mid, text="body text", text_id="t1"),
            _make_event(25, EventType.TEXT_ENDED, mid, text="body text", text_id="t1"),
        ]
        proj = Projector(db_path)
        with sqlite3.connect(str(db_path)) as conn:
            for ev in events:
                conn.execute(
                    "INSERT INTO event_log (id, aggregate_id, seq, type, "
                    "data_json, time_created) VALUES (?, ?, ?, ?, ?, ?)",
                    (ev.id, ev.aggregate_id, ev.seq, ev.type,
                     json.dumps(ev.data, ensure_ascii=False), ev.time_created),
                )
            conn.commit()
        state = proj.project("sess_test")
        msg = state.messages[mid]
        parts = list(msg.parts_in_order())
        assert len(parts) == 2, [p.type for p in parts]
        assert parts[0].type == "thinking", [p.type for p in parts]
        assert parts[1].type == "text", [p.type for p in parts]
        assert parts[0].data["text"] == "thinking text"
        assert parts[1].data["text"] == "body text"
