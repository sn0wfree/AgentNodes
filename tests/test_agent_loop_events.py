"""Tests for AgentLoop event vocabulary (Stage 1).

Verifies that the event bus emits the expected types and payloads after
the vibe-trading-inspired expansion. Each test captures all events
emitted during a single run and asserts on their structure.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest import mock

import pytest

from strategy_research.kernel.agent.loop import AgentLoop
from strategy_research.kernel.agent.tools import BaseTool, ToolRegistry
from strategy_research.foundation.llm import LLMConfig, LLMResponse, ToolCall


class EventSink:
    """Collects every event passed to on_event."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    def __call__(self, event_type: str, data: dict[str, Any]) -> None:
        self.events.append((event_type, dict(data)))

    def types(self) -> list[str]:
        return [e[0] for e in self.events]

    def of_type(self, t: str) -> list[dict[str, Any]]:
        return [d for et, d in self.events if et == t]


class _ReadTool(BaseTool):
    """Minimal read tool returning a fixed string."""

    name = "read"
    description = "read a file"
    parameters = {"type": "object", "properties": {"path": {"type": "string"}}}

    def __init__(self, output: str = "ok") -> None:
        super().__init__()
        self._output = output

    def execute(self, **kwargs) -> str:
        return self._output


class _BrokenTool(BaseTool):
    """Tool that returns a JSON error marker (loop translates to status='error')."""

    name = "broken"
    description = "always errors"
    parameters = {"type": "object", "properties": {}}

    def execute(self, **kwargs) -> str:
        return json.dumps({"status": "error", "error": "boom"})


def _make_loop(
    sink: EventSink,
    *,
    registry: ToolRegistry | None = None,
    workspace: Path | None = None,
    max_iterations: int = 1,
) -> AgentLoop:
    """Construct a minimal AgentLoop with a captured event sink."""
    cfg = LLMConfig(api_key="sk-test", model="fake-model", temperature=0.7)
    return AgentLoop(
        stream_mode=False,
        config=cfg,
        registry=registry or ToolRegistry(),
        workspace=workspace,
        on_event=sink,
        max_iterations=max_iterations,
    )


def _text_resp(content: str) -> LLMResponse:
    return LLMResponse(content=content, tool_calls=[], finish_reason="stop")


def _tool_resp(name: str, arguments: dict, call_id: str = "c1") -> LLMResponse:
    return LLMResponse(
        content="",
        tool_calls=[ToolCall(id=call_id, name=name, arguments=arguments)],
        finish_reason="tool_calls",
    )


class TestIterLifecycle:
    def test_iter_start_and_end_emit(self):
        sink = EventSink()
        loop = _make_loop(sink, max_iterations=3)

        def fake_chat(messages, **kwargs):
            return _text_resp("answer")

        loop.client = mock.MagicMock()
        loop.client.chat = fake_chat
        loop.run('hello')

        iter_starts = [d for et, d in sink.events if et == "iter_start"]
        iter_ends = [d for et, d in sink.events if et == "iter_end"]

        assert len(iter_starts) == 1
        assert iter_starts[0]["iteration"] == 1
        assert iter_starts[0]["max_iterations"] == 3

        assert len(iter_ends) == 1
        assert iter_ends[0]["iteration"] == 1
        assert iter_ends[0]["finish_reason"] == "stop"
        assert iter_ends[0]["tool_calls_made"] == 0


class TestToolEventPayload:
    def test_tool_call_has_arguments_and_iter(self):
        """tool_call emits {tool, arguments, call_id, iter}; 'args' is dropped."""
        sink = EventSink()
        reg = ToolRegistry()
        reg.register(_ReadTool())
        loop = _make_loop(sink, registry=reg)

        responses = [
            _tool_resp("read", {"path": "/x.py"}, call_id="call_abc"),
            _text_resp("ok"),
        ]
        loop.client = mock.MagicMock()
        loop.client.chat = mock.MagicMock(side_effect=lambda *a, **kw: responses.pop(0))
        loop.run("do something")

        tool_calls = [d for et, d in sink.events if et == "tool_call"]
        assert len(tool_calls) == 1
        tc_payload = tool_calls[0]
        assert tc_payload["tool"] == "read"
        # arguments is serialized to a JSON string
        assert tc_payload["arguments"] == '{"path": "/x.py"}'
        assert tc_payload["call_id"] == "call_abc"
        assert tc_payload["iter"] == 1
        # Old key must not be present
        assert "args" not in tc_payload

    def test_tool_result_has_status_preview_and_ok_compat(self):
        """tool_result emits {status, preview, ok (compat), elapsed_ms}."""
        sink = EventSink()
        reg = ToolRegistry()
        reg.register(_ReadTool(output="output preview text"))
        loop = _make_loop(sink, registry=reg)

        responses = [
            _tool_resp("read", {"text": "hi"}, call_id="call_xyz"),
            _text_resp("ok"),
        ]
        loop.client = mock.MagicMock()
        loop.client.chat = mock.MagicMock(side_effect=lambda *a, **kw: responses.pop(0))
        loop.run("do something")

        results = [d for et, d in sink.events if et == "tool_result"]
        assert len(results) == 1
        payload = results[0]
        assert payload["status"] == "done"
        assert payload["ok"] is True  # backward compat
        assert "elapsed_ms" in payload
        assert payload["preview"] == "output preview text"

    def test_tool_result_error_status(self):
        sink = EventSink()
        reg = ToolRegistry()
        reg.register(_BrokenTool())
        loop = _make_loop(sink, registry=reg)

        responses = [
            _tool_resp("broken", {}),
            _text_resp("ok"),
        ]
        loop.client = mock.MagicMock()
        loop.client.chat = mock.MagicMock(side_effect=lambda *a, **kw: responses.pop(0))
        loop.run("do something")

        results = [d for et, d in sink.events if et == "tool_result"]
        assert results[0]["status"] == "error"
        assert results[0]["ok"] is False
        assert "boom" in results[0]["preview"]


class TestThinkingDone:
    def test_thinking_done_fires_before_first_text(self):
        """thinking_done emits between first text_delta."""
        sink = EventSink()
        loop = _make_loop(sink)

        # Stub streaming path
        from strategy_research.foundation.llm.parser import StreamChunk

        def fake_stream(messages, tools=None):
            yield StreamChunk(delta_content="hel")
            yield StreamChunk(delta_content="lo", finish_reason="stop")

        loop._stream_mode = True
        loop.client = mock.MagicMock()
        loop.client.stream = fake_stream

        loop.run("hi")

        events = sink.types()
        assert "thinking_start" in events
        assert "thinking_done" in events
        assert "thinking_end" in events
        assert "text_delta" in events
        start_idx = events.index("thinking_start")
        done_idx = events.index("thinking_done")
        first_delta = events.index("text_delta")
        end_idx = events.index("thinking_end")
        assert start_idx < done_idx < first_delta < end_idx


class TestCompactEvent:
    def test_compact_emits_per_layer(self):
        sink = EventSink()
        loop = _make_loop(sink)

        # Force compression to "apply" — return real dict messages so
        # estimate_tokens can traverse them.
        real_messages = [{"role": "user", "content": "hi"}]
        loop._maybe_compact = mock.MagicMock(
            return_value=(real_messages, ["microcompact", "context_collapse"])
        )

        loop.client = mock.MagicMock()
        loop.client.chat = mock.MagicMock(return_value=_text_resp("ok"))

        loop.run("hi")

        compacts = [d for et, d in sink.events if et == "compact"]
        assert len(compacts) == 2
        assert compacts[0]["layer"] == "microcompact"
        assert compacts[1]["layer"] == "context_collapse"
        assert compacts[0]["iteration"] == 1


class TestToolProgress:
    def test_emit_tool_progress_helper_dispatches(self):
        sink = EventSink()
        loop = _make_loop(sink)

        loop.emit_tool_progress(
            tool="download",
            call_id="c1",
            stage="fetching",
            current=3,
            total=10,
            message="downloading chunk 3",
        )

        events = sink.of_type("tool_progress")
        assert len(events) == 1
        assert events[0]["tool"] == "download"
        assert events[0]["stage"] == "fetching"
        assert events[0]["current"] == 3
        assert events[0]["total"] == 10
        assert events[0]["message"] == "downloading chunk 3"


class TestErrorEvent:
    def test_error_emits_with_message_and_fatal(self):
        sink = EventSink()
        loop = _make_loop(sink)

        from strategy_research.foundation.llm.errors import LLMRateLimitError

        loop.client = mock.MagicMock()
        loop.client.chat = mock.MagicMock(side_effect=LLMRateLimitError("rate"))

        loop.run("hi")

        errs = sink.of_type("error")
        assert len(errs) == 1
        assert "rate" in errs[0]["message"]
        assert errs[0]["fatal"] is True


class TestBackwardCompat:
    def test_old_args_key_no_longer_in_tool_call(self):
        """The legacy 'args' key must be absent so downstream code can rely on 'arguments'."""
        sink = EventSink()
        reg = ToolRegistry()
        reg.register(_ReadTool())
        loop = _make_loop(sink, registry=reg)

        responses = [
            _tool_resp("read", {"k": "v"}),
            _text_resp("ok"),
        ]
        loop.client = mock.MagicMock()
        loop.client.chat = mock.MagicMock(side_effect=lambda *a, **kw: responses.pop(0))
        loop.run("hi")

        tc_payload = sink.of_type("tool_call")[0]
        assert "args" not in tc_payload
        assert "arguments" in tc_payload

    def test_old_ok_key_still_present_in_tool_result(self):
        """The legacy 'ok' key remains in tool_result for backward compat."""
        sink = EventSink()
        reg = ToolRegistry()
        reg.register(_ReadTool())
        loop = _make_loop(sink, registry=reg)

        responses = [
            _tool_resp("read", {}),
            _text_resp("ok"),
        ]
        loop.client = mock.MagicMock()
        loop.client.chat = mock.MagicMock(side_effect=lambda *a, **kw: responses.pop(0))
        loop.run("hi")

        result_payload = sink.of_type("tool_result")[0]
        assert "ok" in result_payload
        assert "status" in result_payload
        # status value is now "done" (was "ok"); ok stays boolean compat
        assert result_payload["ok"] == (result_payload["status"] == "done")