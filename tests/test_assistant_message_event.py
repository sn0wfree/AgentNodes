"""Tests for Stage A: assistant_message event + non-streaming content delivery.

Verifies that:
1. _handle_stop / _handle_max_iter / _check_no_progress each emit an
   ``assistant_message`` event with the final content BEFORE ``iter_end``.
2. The TUI App's ``_finalize_assistant_message`` writes content into the
   TranscriptView as a foldable StreamingText folder.
3. _run_agent_loop does NOT call ``append_done()`` redundantly
   (the "Done.×2" bug fix).
"""
from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from typing import Any

from strategy_research.core.agent.builtin_tools import build_default_registry
from strategy_research.core.agent.loop import AgentLoop
from strategy_research.foundation.llm import LLMConfig, LLMResponse, ToolCall

# ---------------------------------------------------------------- helpers


class EventSink:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    def __call__(self, event_type: str, data: dict[str, Any]) -> None:
        self.events.append((event_type, dict(data)))

    def types(self) -> list[str]:
        return [e[0] for e in self.events]

    def of_type(self, t: str) -> list[dict[str, Any]]:
        return [d for et, d in self.events if et == t]


class MockLLM:
    def __init__(self, responses):
        self.responses = list(responses)

    def chat(self, messages, **kwargs):
        return self.responses.pop(0)

    async def achat(self, messages, **kwargs):
        return self.responses.pop(0)


def _make_loop(sink, *, max_iterations=1, stream_mode=False, no_progress_window=10):
    cfg = LLMConfig(api_key="sk-test", model="fake-model", temperature=0.7)
    workspace = Path(tempfile.mkdtemp())
    return AgentLoop(
        config=cfg,
        registry=build_default_registry(),
        workspace=workspace,
        on_event=sink,
        max_iterations=max_iterations,
        no_progress_window=no_progress_window,
        stream_mode=stream_mode,
    )


# ---------------------------------------------------------------- Stage A1


class TestAssistantMessageEmission:
    """A1: _handle_stop emits assistant_message BEFORE iter_end."""

    def test_stop_path_emits_assistant_message_with_content(self):
        sink = EventSink()
        loop = _make_loop(sink)
        loop.client.chat = MockLLM([
            LLMResponse(content="策略名称: A股动量策略", tool_calls=[], finish_reason="stop"),
        ]).chat
        result = loop.run("分析A股")
        types = sink.types()
        assert "assistant_message" in types, f"missing assistant_message; got {types}"
        idx_msg = types.index("assistant_message")
        idx_end = types.index("iter_end")
        assert idx_msg < idx_end, "assistant_message must come BEFORE iter_end"
        assert sink.of_type("assistant_message")[0]["content"] == "策略名称: A股动量策略"
        assert result.answer == "策略名称: A股动量策略"
        assert result.finished_reason == "stop"

    def test_max_iter_path_emits_assistant_message(self):
        sink = EventSink()
        loop = _make_loop(sink, max_iterations=2)
        loop.client.chat = MockLLM([
            LLMResponse(content=None, tool_calls=[
                ToolCall(id="c1", name="read", arguments={"path": "x"}),
            ], finish_reason="tool_calls"),
            LLMResponse(content=None, tool_calls=[
                ToolCall(id="c2", name="read", arguments={"path": "y"}),
            ], finish_reason="tool_calls"),
        ]).chat
        result = loop.run("endless")
        types = sink.types()
        assert "assistant_message" in types
        am = sink.of_type("assistant_message")[0]
        assert "max_iterations" in am["content"]
        assert result.finished_reason == "max_iter"

    def test_no_progress_path_emits_assistant_message(self):
        """No-progress gate: repeated identical tool calls trigger the
        HITL approval gate; with reject-on-timeout the loop ends and an
        assistant_message is still emitted (A1 contract: assistant_message
        BEFORE iter_end on every terminal path).

        (Historically this asserted finished_reason == 'no_progress' —
        that reason no longer exists since the no-progress hard stop was
        replaced by the approval gate; timeout+reject is its terminal
        equivalent.)"""
        sink = EventSink()
        loop = _make_loop(sink, max_iterations=10, no_progress_window=3)
        loop._approval_timeout = 0.5
        loop._approval_on_timeout = "reject"
        loop.client.chat = MockLLM([
            LLMResponse(content=None, tool_calls=[
                ToolCall(id=f"c{i}", name="list_history", arguments={}),
            ], finish_reason="tool_calls")
            for i in range(10)
        ]).chat
        result = loop.run("loop")
        types = sink.types()
        assert "assistant_message" in types
        am = sink.of_type("assistant_message")[0]
        assert am["content"] != ""
        idx_msg = types.index("assistant_message")
        idx_end = types.index("iter_end")
        assert idx_msg < idx_end
        assert result.finished_reason == "approval_timeout_rejected"

    def test_assistant_message_emitted_exactly_once_per_turn(self):
        sink = EventSink()
        loop = _make_loop(sink)
        loop.client.chat = MockLLM([
            LLMResponse(content="done", tool_calls=[], finish_reason="stop"),
        ]).chat
        loop.run("once")
        am_events = sink.of_type("assistant_message")
        assert len(am_events) == 1, f"expected 1 assistant_message, got {len(am_events)}"

    def test_async_arun_also_emits_assistant_message(self):
        sink = EventSink()
        loop = _make_loop(sink)
        loop.client.achat = MockLLM([
            LLMResponse(content="async answer", tool_calls=[], finish_reason="stop"),
        ]).achat
        result = asyncio.run(loop.arun("async task"))
        types = sink.types()
        assert "assistant_message" in types
        assert sink.of_type("assistant_message")[0]["content"] == "async answer"
        assert result.answer == "async answer"


# ---------------------------------------------------------------- Stage A2

# (Finalize tests removed: assistant_message now writes Markdown directly
# via TranscriptView.write_assistant_message. See test_markdown_render.py
# TestWriteAssistantMessage for current behaviour.)


# ---------------------------------------------------------------- Stage A3


class TestNoRedundantDone:
    """A3: iter_end is the canonical Done. marker source.

    The original case here asserted that
    ``ChatSession._run_agent_loop`` did not call ``append_done()``. That
    method was deleted in P5-1c ④ (the legacy AgentLoop path is gone), so
    the assertion has no subject. The invariant it protected — "only
    iter_end appends Done." — is still verified below against
    ``route_agent_event``.
    """

    def test_route_agent_event_iter_end_calls_append_done(self):
        """Verify the canonical Done. marker source: route_agent_event iter_end."""
        import inspect

        from strategy_research.cli.tui.app import ResearchApp

        src = inspect.getsource(ResearchApp.route_agent_event)
        # iter_end branch should call tv.append_done()
        iter_end_block = src.split('event_type == "iter_end"')[1].split("elif")[0]
        assert "append_done" in iter_end_block
