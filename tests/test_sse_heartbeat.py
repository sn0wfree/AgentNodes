"""Tests for SSE heartbeat behavior.

The heartbeat mechanism prevents the browser's EventSource from
reporting onerror on idle streams. The first yield of every
event_generator must be a comment line so StreamingResponse flushes
headers immediately, and subsequent heartbeats must be comment lines
so they don't pollute the frontend's switch statement.
"""

from __future__ import annotations

import pytest

from strategy_research.interfaces.api.routers.chat import _heartbeat_sse


def test_heartbeat_returns_comment_line():
    """Heartbeat must be an SSE comment line (: prefix) so the browser
    ignores it without triggering onerror or onmessage."""
    out = _heartbeat_sse(1)
    assert out == ": heartbeat\n\n"
    assert out.startswith(":")
    # Comment lines ARE NOT a named event — no `event:` or `data:` fields.
    assert "event:" not in out
    assert "data:" not in out


def test_heartbeat_count_argument_ignored():
    """The count is a no-op for comment-line keep-alive; we keep it
    in the signature so the call site doesn't change. Ensure the
    output is stable regardless of count."""
    assert _heartbeat_sse(0) == _heartbeat_sse(999) == ": heartbeat\n\n"


@pytest.mark.asyncio
async def test_event_generator_yields_connected_first():
    """The SSE generator must yield ': connected\\n\\n' as its first
    chunk so FastAPI flushes response headers and the browser fires
    onopen without waiting for the first real event or a heartbeat.

    A2: the generator is now ``_event_generator_v2`` (EventStore
    subscribe track) — the immediate-flush contract is unchanged.
    """
    from strategy_research.interfaces.api.routers.chat import _event_generator_v2

    gen = _event_generator_v2("test-sse-immediate-flush", "")
    first_chunk = await gen.__anext__()
    assert first_chunk == ": connected\n\n"
    await gen.aclose()
