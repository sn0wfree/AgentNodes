"""Tests for SSE heartbeat behavior.

The first yield of every event_generator must be a comment line so
StreamingResponse flushes headers immediately. Subsequent heartbeats
must be a **named** ``heartbeat`` event: comment lines are invisible
to JavaScript, which left the frontend with no liveness signal (the
status pill stayed 「已连接」 after backend death — P0-5 finding).
Named events dispatch to ``addEventListener('heartbeat')`` without
polluting the frontend's event-type switch (``heartbeat`` is not in
EVENT_TYPES).
"""

from __future__ import annotations

import pytest

from agentnodes.interfaces.api.routers.chat import _heartbeat_sse


def test_heartbeat_is_named_event():
    """Heartbeat must be a named ``heartbeat`` event so the browser
    dispatches it to the frontend's liveness watcher."""
    out = _heartbeat_sse(1)
    assert out == 'event: heartbeat\ndata: {"seq": 1}\n\n'
    assert out.startswith("event: heartbeat\n")
    # 仍不进入前端事件类型 switch：heartbeat 不在 EVENT_TYPES 里，
    # 这里只钉线格式本身。
    assert 'data: {"seq":' in out


def test_heartbeat_count_argument_in_seq():
    """The count flows into the payload seq so drops/replays are
    observable in principle."""
    assert _heartbeat_sse(0) == 'event: heartbeat\ndata: {"seq": 0}\n\n'
    assert _heartbeat_sse(999) == 'event: heartbeat\ndata: {"seq": 999}\n\n'


@pytest.mark.asyncio
async def test_event_generator_yields_connected_first():
    """The SSE generator must yield ': connected\\n\\n' as its first
    chunk so FastAPI flushes response headers and the browser fires
    onopen without waiting for the first real event or a heartbeat.

    A2: the generator is now ``_event_generator_v2`` (EventStore
    subscribe track) — the immediate-flush contract is unchanged.
    """
    from agentnodes.interfaces.api.routers.chat import _event_generator_v2

    gen = _event_generator_v2("test-sse-immediate-flush", "")
    first_chunk = await gen.__anext__()
    assert first_chunk == ": connected\n\n"
    await gen.aclose()
