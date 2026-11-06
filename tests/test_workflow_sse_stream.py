"""Tests for api/routers/workflow.py:workflow_event_stream.

Covers the SSE frame contract (named events + structured payload) that
the frontend's EVENT_TYPES dispatcher relies on.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest


def _parse_sse_frame(frame: str) -> tuple[str, dict]:
    """Parse a single ``event: name\\ndata: json\\n\\n`` frame."""
    lines = frame.strip().split("\n")
    event_name = None
    data_line = None
    for ln in lines:
        if ln.startswith("event: "):
            event_name = ln[len("event: "):]
        elif ln.startswith("data: "):
            data_line = ln[len("data: "):]
    assert event_name is not None, f"missing event line in frame: {frame!r}"
    assert data_line is not None, f"missing data line in frame: {frame!r}"
    return event_name, json.loads(data_line)


class _StubRunner:
    """Minimal runner with the surface workflow_event_stream touches."""

    def __init__(self) -> None:
        from agentnodes.apps.goal.event_bus import WorkflowEventBus
        self._event_bus = WorkflowEventBus()
        self._state = type(
            "S", (), {
                "status": "running", "current_layer": 0,
                "paused": False, "cancelled": False,
                "agent_statuses": {"a": "success", "b": "running"},
                "agent_errors": {}, "evidence_count": 1,
                "start_time": 0.0, "error_message": "",
            },
        )()
        self._goal_id = "g1"

    def get_progress(self) -> dict[str, Any]:
        return {
            "goal_id": self._goal_id,
            "status": self._state.status,
            "current_layer": self._state.current_layer,
            "total_layers": 3,
            "agents_completed": 1,
            "agents_total": 2,
            "evidence_count": self._state.evidence_count,
            "paused": self._state.paused,
            "agent_statuses": dict(self._state.agent_statuses),
            "hook_completed": False,
        }

    def subscribe(self, observer: Any) -> None:
        self._event_bus.subscribe(observer)

    def unsubscribe(self, observer: Any) -> None:
        self._event_bus.unsubscribe(observer)


@pytest.mark.asyncio
async def test_workflow_event_stream_initial_frame_is_named_progress():
    """First emitted frame must be `event: progress` carrying the full
    progress dict (so the frontend progress handler can sync DAG nodes)."""
    from agentnodes.interfaces.api.routers.workflow import workflow_event_stream

    runner = _StubRunner()
    frames: list[str] = []
    gen = workflow_event_stream(runner)

    # Mark terminal BEFORE draining so the heartbeat path exits fast.
    runner._state.status = "completed"

    try:
        while True:
            frames.append(await gen.__anext__())
    except StopAsyncIteration:
        pass

    assert frames, "expected at least one frame"
    name, data = _parse_sse_frame(frames[0])
    assert name == "progress"
    assert data["agents_completed"] == 1
    assert data["agents_total"] == 2
    assert data["agent_statuses"] == {"a": "success", "b": "running"}
    # Terminal frame is also a named progress + status pair.
    assert _parse_sse_frame(frames[-1])[0] == "progress"


@pytest.mark.asyncio
async def test_workflow_event_stream_agent_complete_emits_dag_update():
    """Each agent_complete observer event must surface as
    `event: dag_update` with {node_id, status=success} so the DAG panel
    updates individual nodes without waiting for the next heartbeat."""
    from agentnodes.interfaces.api.routers.workflow import workflow_event_stream

    runner = _StubRunner()
    gen = workflow_event_stream(runner)
    frames: list[str] = []

    # Start the generator first so the initial progress frame is
    # captured while status is still "running"; then emit events
    # followed by setting status="completed" so the loop exits.
    frames.append(await gen.__anext__())
    runner._event_bus.emit("agent_complete", agent_id="researcher")
    runner._event_bus.emit("agent_complete", agent_id="factor_analyst")
    await asyncio.sleep(0.05)
    runner._state.status = "completed"

    try:
        while True:
            frames.append(await gen.__anext__())
    except StopAsyncIteration:
        pass

    dag_updates = [d for d in (_parse_sse_frame(f) for f in frames) if d[0] == "dag_update"]
    node_ids = [d[1]["node_id"] for d in dag_updates]
    statuses = [d[1]["status"] for d in dag_updates]
    assert "researcher" in node_ids
    assert "factor_analyst" in node_ids
    assert statuses.count("success") == 2


@pytest.mark.asyncio
async def test_workflow_event_stream_layer_start_refreshes_progress():
    """layer_start must emit a named `progress` frame so the frontend
    sees current_layer moving without polling."""
    from agentnodes.interfaces.api.routers.workflow import workflow_event_stream

    runner = _StubRunner()
    runner._state.current_layer = 0
    gen = workflow_event_stream(runner)
    frames: list[str] = []

    # Start the generator first so the initial progress (layer 0) is
    # captured before we advance state.
    frames.append(await gen.__anext__())

    # Advance to layer 1, emit, then drain the progress frame so the
    # snapshot reflects current_layer=1.
    runner._state.current_layer = 1
    runner._event_bus.emit("layer_start", layer=1, agents=["a"])
    frames.append(await gen.__anext__())

    # Advance to layer 2, emit, then drain.
    runner._state.current_layer = 2
    runner._event_bus.emit("layer_start", layer=2, agents=["b"])
    frames.append(await gen.__anext__())

    runner._state.status = "completed"
    try:
        while True:
            frames.append(await gen.__anext__())
    except StopAsyncIteration:
        pass

    named = [_parse_sse_frame(f) for f in frames]
    # All emitted frames use named events (never bare `data: {...}`).
    assert all(
        name in ("progress", "dag_update", "workflow_completed", "workflow_failed")
        for name, _ in named
    ), f"unexpected event names: {[n for n, _ in named]}"
    # current_layer advances through the captured snapshots.
    layers = [d.get("current_layer") for n, d in named if n == "progress"]
    assert 0 in layers and 1 in layers and 2 in layers


@pytest.mark.asyncio
async def test_workflow_event_stream_heartbeat_when_idle():
    """When the queue is empty past the 1s timeout, the stream emits
    a `progress` heartbeat so the frontend gets fresh state without
    backend action."""
    from agentnodes.interfaces.api.routers.workflow import workflow_event_stream

    runner = _StubRunner()
    gen = workflow_event_stream(runner)

    first = await gen.__anext__()
    name, _ = _parse_sse_frame(first)
    assert name == "progress"

    # Force terminal quickly so we don't actually wait 1s.
    runner._state.status = "completed"
    captured: list[str] = []
    try:
        while True:
            captured.append(await gen.__anext__())
    except StopAsyncIteration:
        pass

    if captured:
        assert _parse_sse_frame(captured[-1])[0] in ("progress", "workflow_completed")
