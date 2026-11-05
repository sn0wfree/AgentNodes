"""Tests for the llm_request event (Phase A1).

Verifies that AgentLoop emits an ``llm_request`` event per LLM call and
that the ``_LoopEventForwarder`` offloads large fields (system_prompt,
tools_schema) to sidecar blobs before persisting to event_log, keeping
the stored event lean (metadata + sidecar refs).
"""

from __future__ import annotations

from typing import Any
from unittest import mock

from agentnodes.kernel.agent.loop import AgentLoop


class EventSink:
    """Collects every event passed to on_event."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    def __call__(self, event_type: str, data: dict[str, Any]) -> None:
        self.events.append((event_type, dict(data)))

    def of_type(self, t: str) -> list[dict[str, Any]]:
        return [d for et, d in self.events if et == t]


def _make_loop(sink: EventSink, *, max_iterations: int = 1) -> AgentLoop:
    from agentnodes.foundation.llm import LLMConfig

    cfg = LLMConfig(api_key="sk-test", model="fake-model", temperature=0.7)
    registry = mock.MagicMock()
    memory = mock.MagicMock()
    memory.history = []
    return AgentLoop(
        stream_mode=False,
        config=cfg,
        registry=registry,
        memory=memory,
        workspace=None,
        on_event=sink,
        max_iterations=max_iterations,
    )


def _stub_chat(loop: AgentLoop, *, content: str = "answer") -> None:
    loop._get_goal_snapshot = lambda: None  # noqa: E731
    loop.registry.get_definitions = mock.MagicMock(return_value=None)
    resp = mock.MagicMock()
    resp.content = content
    resp.finish_reason = "stop"
    resp.has_tool_calls = lambda: False  # noqa: E731
    resp.tool_calls = []
    loop.client = mock.MagicMock()
    loop.client.chat = mock.MagicMock(return_value=resp)


# ── AgentLoop emits llm_request ──────────────────────────────────


def test_loop_emits_llm_request_even_without_trace_writer() -> None:
    """llm_request fires on every LLM call even when no trace_dir is set
    (previously it was gated behind trace_writer)."""
    sink = EventSink()
    loop = _make_loop(sink)
    _stub_chat(loop)
    loop.run("hello")

    reqs = sink.of_type("llm_request")
    assert len(reqs) == 1
    r = reqs[0]
    assert r["type"] == "llm_request"
    assert r["iteration"] == 1
    assert r["history_count"] == 2  # system prompt + user message
    assert r["system_prompt_len"] > 0
    assert "system_prompt" in r
    assert "tools_schema" in r


def test_loop_emits_llm_request_with_tools_metadata() -> None:
    """tools are passed through (count + schema) when a registry returns some."""
    sink = EventSink()
    loop = _make_loop(sink)
    _stub_chat(loop)
    loop.registry.get_definitions = mock.MagicMock(
        return_value=[{"name": "t1", "parameters": {"type": "object"}}]
    )
    loop.run("hello")

    reqs = sink.of_type("llm_request")
    assert len(reqs) == 1
    r = reqs[0]
    assert r["tools_count"] == 1
    assert "name" in r["tools_schema"]


def test_loop_emits_trajectory_lifecycle_events() -> None:
    """The loop routes lifecycle events (loop_start, iter_start, llm_response,
    loop_end, loop_final) into the event stream, not just trace.jsonl."""
    sink = EventSink()
    loop = _make_loop(sink)
    _stub_chat(loop)
    loop.run("hello")

    types = {et for et, _ in sink.events}
    assert "loop_start" in types
    assert "loop_end" in types
    assert "loop_final" in types
    assert "llm_response" in types
    assert "iter_start" in types

    # llm_response carries the response envelope metadata.
    resp = sink.of_type("llm_response")[-1]
    assert resp["finish_reason"] == "stop"
    assert resp["has_tool_calls"] is False
    assert resp["tool_call_count"] == 0

    # iter_start now carries an estimated token count.
    iters = sink.of_type("iter_start")
    assert iters and "tokens" in iters[0]


# ── _LoopEventForwarder offload ──────────────────────────────────


class _FakeBus:
    """Minimal EventStore stand-in recording emits."""

    def __init__(self, db_path=None) -> None:
        self._db_path = db_path
        self.emitted: list[tuple[str, str, dict]] = []

    def emit(self, session_id: str, event_type: str, data: dict) -> None:
        self.emitted.append((session_id, event_type, dict(data)))


class _FakeAttempt:
    def __init__(self) -> None:
        self.attempt_id = "att-1"
        self.message_id = "msg-1"
        self.session_id = "sess-1"


def _make_forwarder(bus, cfg=None):
    from agentnodes.interfaces.api.session.service import _LoopEventForwarder

    attempt = _FakeAttempt()
    if cfg is None:
        from agentnodes.foundation.llm import LLMConfig
        cfg = LLMConfig(api_key="sk-test", model="fake-model")
    return _LoopEventForwarder(
        service=None,
        attempt=attempt,
        accumulated_parts=[],
        event_bus=bus,
        cfg=cfg,
    )


def test_forwarder_offloads_large_system_prompt(tmp_path, monkeypatch) -> None:
    """A system_prompt over the threshold is moved to a sidecar blob and the
    stored event carries path/preview/size instead of the inline value."""
    monkeypatch.setenv("SR_LLM_REQUEST_OFFLOAD_THRESHOLD", "64")
    bus = _FakeBus(db_path=tmp_path / "sessions.db")
    fwd = _make_forwarder(bus)

    big_prompt = "S" * 5000
    fwd("llm_request", {
        "type": "llm_request", "iteration": 1, "session_id": "sess-1",
        "history_count": 1, "system_prompt_len": len(big_prompt),
        "system_prompt": big_prompt, "tools_schema": "[]",
    })

    assert len(bus.emitted) == 1
    sid, etype, data = bus.emitted[0]
    assert etype == "llm_request"
    assert "system_prompt" not in data
    assert data["system_prompt_path"].startswith("trace-blobs/")
    assert data["system_prompt_size"] == len(big_prompt)
    assert data["system_prompt_preview"] == big_prompt[:512]

    # Blob actually written to disk next to the event DB.
    blob = tmp_path / data["system_prompt_path"]
    assert blob.exists()
    assert blob.read_text(encoding="utf-8") == big_prompt


def test_forwarder_keeps_small_fields_inline(tmp_path) -> None:
    """Fields under the threshold stay inline (no offload)."""
    bus = _FakeBus(db_path=tmp_path / "sessions.db")
    fwd = _make_forwarder(bus)

    small_prompt = "short system prompt"
    fwd("llm_request", {
        "type": "llm_request", "iteration": 1, "session_id": "sess-1",
        "history_count": 1, "system_prompt_len": len(small_prompt),
        "system_prompt": small_prompt, "tools_schema": "[]",
    })

    _, _, data = bus.emitted[0]
    assert data["system_prompt"] == small_prompt
    assert "system_prompt_path" not in data


def test_forwarder_offloads_large_tools_schema(tmp_path, monkeypatch) -> None:
    """tools_schema over the threshold is also offloaded."""
    monkeypatch.setenv("SR_LLM_REQUEST_OFFLOAD_THRESHOLD", "64")
    bus = _FakeBus(db_path=tmp_path / "sessions.db")
    fwd = _make_forwarder(bus)

    big_tools = "[" + ",".join(f"t{i}" for i in range(3000)) + "]"
    fwd("llm_request", {
        "type": "llm_request", "iteration": 1, "session_id": "sess-1",
        "history_count": 1, "system_prompt_len": 10,
        "system_prompt": "sys", "tools_schema": big_tools,
    })

    _, _, data = bus.emitted[0]
    assert "tools_schema" not in data
    assert data["tools_schema_path"].startswith("trace-blobs/")
    assert data["tools_schema_size"] == len(big_tools)


def test_forwarder_injects_attempt_and_message_ids(tmp_path) -> None:
    """The forwarder still stamps attempt_id/message_id on llm_request."""
    bus = _FakeBus(db_path=tmp_path / "sessions.db")
    fwd = _make_forwarder(bus)

    fwd("llm_request", {
        "type": "llm_request", "iteration": 1, "session_id": "sess-1",
        "history_count": 1, "system_prompt": "sys", "tools_schema": "[]",
    })

    _, _, data = bus.emitted[0]
    assert data["attempt_id"] == "att-1"
    assert data["message_id"] == "msg-1"


def test_forwarder_offloads_large_llm_response_content(tmp_path, monkeypatch) -> None:
    """A large llm_response ``content`` is offloaded to a sidecar blob and the
    stored event carries path/preview/size references."""
    monkeypatch.setenv("SR_LLM_REQUEST_OFFLOAD_THRESHOLD", "64")
    bus = _FakeBus(db_path=tmp_path / "sessions.db")
    fwd = _make_forwarder(bus)

    big_content = "A" * 5000
    fwd("llm_response", {
        "type": "llm_response", "iteration": 1, "finish_reason": "stop",
        "has_tool_calls": False, "tool_call_count": 0,
        "content": big_content, "content_preview": big_content[:200],
    })

    assert len(bus.emitted) == 1
    _, _, data = bus.emitted[0]
    assert "content" not in data
    assert data["content_path"].startswith("trace-blobs/")
    assert data["content_size"] == len(big_content)
    assert data["content_preview"] == big_content[:512]

    blob = tmp_path / data["content_path"]
    assert blob.exists()
    assert blob.read_text(encoding="utf-8") == big_content


def test_forwarder_keeps_small_llm_response_content_inline(tmp_path) -> None:
    """A short llm_response content stays inline (no offload)."""
    bus = _FakeBus(db_path=tmp_path / "sessions.db")
    fwd = _make_forwarder(bus)

    small = "short answer"
    fwd("llm_response", {
        "type": "llm_response", "iteration": 1, "finish_reason": "stop",
        "has_tool_calls": False, "tool_call_count": 0,
        "content": small, "content_preview": small,
    })

    _, _, data = bus.emitted[0]
    assert data["content"] == small
    assert "content_path" not in data
