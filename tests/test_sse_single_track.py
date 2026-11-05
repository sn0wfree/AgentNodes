"""A2: SSE single-track regression.

The events endpoint now streams from ``EventStore.subscribe()`` instead of
the retired in-process ``SSEEventBuffer``. These tests pin:

- the wire format (id / event / data) the frontend consumes;
- replay of persisted events after reconnect (Last-Event-ID semantics);
- that the buffer track is gone (architecture guard).
"""
from __future__ import annotations

from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src" / "agentnodes"


def test_format_event_v2_wire_shape():
    """EventV2 -> SSE frame: id from .id, event from .type, data as JSON."""
    from agentnodes.interfaces.api.routers.chat import _format_event_v2
    from agentnodes.kernel.events.event_v2 import EventV2

    ev = EventV2.create(
        aggregate_id="s1", seq=7, type="text.delta",
        data={"text": "hi"},
    )
    frame = _format_event_v2(ev)

    assert f"id: {ev.id}" in frame
    assert "event: text.delta" in frame
    assert 'data: {"text": "hi"}' in frame
    assert frame.endswith("\n\n")


def test_format_event_v2_handles_non_json_payload():
    """Defensive: unserialisable payload degrades to {} instead of raising."""
    from agentnodes.interfaces.api.routers.chat import _format_event_v2
    from agentnodes.kernel.events.event_v2 import EventV2

    ev = EventV2.create(
        aggregate_id="s1", seq=1, type="x",
        data={"bad": object()},
    )
    frame = _format_event_v2(ev)
    # default=str makes it serialisable, so we get something usable
    assert "event: x" in frame


@pytest.mark.asyncio
async def test_subscribe_replays_persisted_events(tmp_path):
    """Reconnect: events persisted before subscribe are replayed."""
    from agentnodes.kernel.agent.event_store import EventStore

    store = EventStore(db_path=tmp_path / "events.db", flush_to_messages=False)
    store.emit("s1", "text.started", {"message_id": "m1"})
    store.emit("s1", "text.delta", {"text": "a"})

    seen = []
    async for ev in store.subscribe("s1"):
        seen.append(ev)
        if len(seen) == 2:
            break

    assert [e.type for e in seen] == ["text.started", "text.delta"]
    assert seen[0].data["message_id"] == "m1"


@pytest.mark.asyncio
async def test_subscribe_delivers_live_events(tmp_path):
    """Live push after subscribe reaches the subscriber."""
    import asyncio

    from agentnodes.kernel.agent.event_store import EventStore

    store = EventStore(db_path=tmp_path / "events.db", flush_to_messages=False)

    async def _publish_later():
        await asyncio.sleep(0.05)
        store.emit("s1", "tool.started", {"tool": "run_backtest"})

    asyncio.create_task(_publish_later())
    seen = []
    async for ev in store.subscribe("s1"):
        if ev.type == "tool.started":
            seen.append(ev)
            break

    assert seen[0].data["tool"] == "run_backtest"


def test_sse_buffer_track_is_gone():
    """Architecture guard: the retired track must not come back."""
    assert not (SRC / "api" / "sse_buffer.py").exists()
    assert not (SRC / "api" / "session" / "bridge_v2.py").exists()

    offenders: list[str] = []
    for path in (SRC).rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if "sse_buffer" in text or "attach_eventstore_to_sse" in text:
            offenders.append(path.relative_to(SRC).as_posix())
    assert not offenders, (
        "SSE 双轨已退役（A2），不得再引用 sse_buffer / bridge_v2：\n"
        + "\n".join(offenders)
    )
