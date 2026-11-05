"""PR-E regression: round→parent SSE fan-out.

Round-scoped agent events (aggregate_id ``study:{id}:round:{n}``) must
reach subscribers of the bare study channel (``{id}``) — the study
detail page only listens there. Fan-out covers the sse_pusher callback
and live queues; event_log persistence stays round-scoped.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTNODES_GOAL_DB_PATH", str(tmp_path / "g.db"))
    monkeypatch.setenv("AGENTNODES_HYPOTHESES_PATH", str(tmp_path / "h.json"))
    from agentnodes.kernel.agent.event_store import EventStore

    pushes: list[tuple[str, object]] = []

    def pusher(session_id, event):
        pushes.append((session_id, event))

    es = EventStore(db_path=tmp_path / "ev.db", sse_pusher=pusher)
    return es, pushes


def test_parent_study_channel_parsing():
    from agentnodes.kernel.agent.event_store import parent_study_channel

    assert parent_study_channel("study:abc:round:3") == "study:abc"
    assert parent_study_channel("study:abc") is None
    assert parent_study_channel("sess-123") is None
    assert parent_study_channel("study:abc:round:12") == "study:abc"
    # ids with colons beyond the round pattern are not matched
    assert parent_study_channel("study:a:b:round:1") is None or True  # sid capture stops at ':'
    # actually: [^:]+ excludes inner colons → None
    assert parent_study_channel("study:a:b:round:1") is None


def test_emit_fans_out_to_parent_sse(store):
    es, pushes = store
    es.emit("study:x1:round:2", "agent_text_delta", {"text": "hi"})
    channels = [sid for sid, _ in pushes]
    assert "study:x1:round:2" in channels
    assert "study:x1" in channels, (
        "parent study channel must receive the round event via SSE"
    )


def test_emit_no_fanout_for_plain_session(store):
    es, pushes = store
    es.emit("sess-99", "message.created", {})
    assert all(sid == "sess-99" for sid, _ in pushes)


def test_event_log_stays_round_scoped(store):
    es, _ = store
    es.emit("study:y2:round:5", "agent_tool_call", {"tool": "run_backtest"})
    # Parent channel must NOT gain a persisted event (fan-out is
    # SSE/live-only) while the round channel does.
    parent_events = es.replay("study:y2")
    assert all(e.type != "agent_tool_call" for e in parent_events)
    persisted = es.replay("study:y2:round:5")
    assert any(e.type == "agent_tool_call" for e in persisted)


def test_live_subscriber_on_parent_receives(store):
    es, _ = store

    async def main():
        q: asyncio.Queue = asyncio.Queue()
        with es._live_lock:
            es._live_queues.setdefault("study:z3", []).append(q)
            try:
                es.emit("study:z3:round:1", "agent_assistant_message", {"m": 1})
                got = await asyncio.wait_for(q.get(), timeout=1.0)
            finally:
                es._live_queues.get("study:z3", []).remove(q)
        assert got.type == "agent_assistant_message"

    asyncio.run(main())
