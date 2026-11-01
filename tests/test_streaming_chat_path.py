"""Tests for Stage B: chat path uses stream_mode=True with achat() fallback.

Verifies that:
1. The chat path uses stream_mode=True (text_delta events fire).
2. _astream_chat errors that are NOT auth/rate-limit fall back to achat().
3. _astream_chat errors that ARE auth/rate-limit propagate immediately.
4. Token-by-token accumulation works end-to-end.
"""
from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from typing import Any

import pytest

from strategy_research.core.agent.builtin_tools import build_default_registry
from strategy_research.core.agent.loop import AgentLoop
from strategy_research.core.llm import LLMConfig, LLMResponse
from strategy_research.core.llm.errors import (
    LLMAuthError,
    LLMConfigError,
    LLMMalformedResponseError,
    LLMRateLimitError,
    LLMServerError,
    LLMTimeoutError,
)

# ---------------------------------------------------------------- helpers


class EventSink:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    def __call__(self, event_type: str, data: dict[str, Any]) -> None:
        self.events.append((event_type, dict(data)))

    def types(self) -> list[str]:
        return [e[0] for e in self.events]


class FakeStreamChunk:
    def __init__(self, content: str = "", *, usage: dict | None = None,
                 finish_reason: str | None = None):
        self.delta_content = content
        self.raw_content = content
        self.delta_thinking = None
        self.raw_thinking = None
        self.delta_tool_calls = []
        self.usage = usage
        self.finish_reason = finish_reason


async def _astream_chunks(chunks):
    for c in chunks:
        yield c


def _make_loop(sink, *, stream_mode=True, max_iterations=1):
    cfg = LLMConfig(api_key="sk-test", model="fake-model")
    workspace = Path(tempfile.mkdtemp())
    return AgentLoop(
        config=cfg,
        registry=build_default_registry(),
        workspace=workspace,
        on_event=sink,
        stream_mode=stream_mode,
        max_iterations=max_iterations,
    )


# ---------------------------------------------------------------- B1


class TestChatPathStreaming:
    """B1: chat path uses stream_mode=True → text_delta events fire."""

    def test_session_uses_stream_mode_true(self):
        """Verify the pipeline is streaming.

        P5-1c ④ deleted ``ChatSession._run_agent_loop`` (the direct
        ``build_chat_agent_loop`` path). The TUI now goes through the
        Gateway, so the streaming invariant must be asserted where the
        loop is actually built: ``build_chat_agent_loop`` still forces
        ``stream_mode=True``, and the react node uses it.
        """
        import inspect

        from strategy_research.core.agent import chat_loop

        loop_src = inspect.getsource(chat_loop.build_chat_agent_loop)
        assert "stream_mode=True" in loop_src

    def test_async_arun_with_streaming_emits_text_deltas(self):
        sink = EventSink()
        loop = _make_loop(sink, stream_mode=True)

        async def fake_astream(messages, **kwargs):
            for chunk in [
                FakeStreamChunk(content="策略"),
                FakeStreamChunk(content="名称"),
                FakeStreamChunk(content=": A股动量"),
                FakeStreamChunk(content="策略", finish_reason="stop"),
            ]:
                yield chunk

        loop.client.astream = fake_astream
        asyncio.run(loop.arun('分析A股'))

        types = sink.types()
        # text_delta events must have fired
        assert "text_delta" in types
        text_events = [d for et, d in sink.events if et == "text_delta"]
        assert len(text_events) == 4
        # Full content assembled correctly
        joined = "".join(d["text"] for d in text_events)
        assert joined == "策略名称: A股动量策略"

    def test_assistant_message_still_fires_after_streaming(self):
        """Streaming path: text_deltas accumulate, then assistant_message finalizes."""
        sink = EventSink()
        loop = _make_loop(sink, stream_mode=True)

        async def fake_astream(messages, **kwargs):
            yield FakeStreamChunk(content="hello", finish_reason="stop")

        loop.client.astream = fake_astream
        asyncio.run(loop.arun("hi"))

        types = sink.types()
        # Order: thinking_start, text_delta(s), assistant_message, iter_end
        assert "text_delta" in types
        assert "assistant_message" in types
        assert "iter_end" in types
        assert types.index("assistant_message") < types.index("iter_end")
        am = [d for et, d in sink.events if et == "assistant_message"][0]
        assert am["content"] == "hello"


# ---------------------------------------------------------------- B2


class TestStreamFallback:
    """B2: stream failures fall back to achat() for non-auth errors."""

    def test_malformed_response_error_triggers_fallback(self):
        """MalformedResponseError → stream fails → achat() succeeds."""
        sink = EventSink()
        loop = _make_loop(sink, stream_mode=True)

        call_count = {"astream": 0, "achat": 0}

        async def fake_astream(messages, **kwargs):
            call_count["astream"] += 1
            # Yield one chunk then raise (valid generator)
            yield FakeStreamChunk(content="partial ")
            raise LLMMalformedResponseError("unexpected chunk format")

        async def fake_achat(messages, **kwargs):
            call_count["achat"] += 1
            return LLMResponse(content="fallback answer", tool_calls=[], finish_reason="stop")

        loop.client.astream = fake_astream
        loop.client.achat = fake_achat

        result = asyncio.run(loop.arun("test"))

        assert call_count["astream"] == 1
        assert call_count["achat"] == 1
        assert result.answer == "fallback answer"
        assert result.finished_reason == "stop"

    def test_timeout_error_triggers_fallback(self):
        sink = EventSink()
        loop = _make_loop(sink, stream_mode=True)

        async def fake_astream(messages, **kwargs):
            yield FakeStreamChunk(content="")
            raise LLMTimeoutError("stream timeout")

        async def fake_achat(messages, **kwargs):
            return LLMResponse(content="timeout fallback", tool_calls=[], finish_reason="stop")

        loop.client.astream = fake_astream
        loop.client.achat = fake_achat

        result = asyncio.run(loop.arun("test"))
        assert result.answer == "timeout fallback"

    def test_server_error_triggers_fallback(self):
        sink = EventSink()
        loop = _make_loop(sink, stream_mode=True)

        async def fake_astream(messages, **kwargs):
            yield FakeStreamChunk(content="")
            raise LLMServerError("500 internal error")

        async def fake_achat(messages, **kwargs):
            return LLMResponse(content="server fallback", tool_calls=[], finish_reason="stop")

        loop.client.astream = fake_astream
        loop.client.achat = fake_achat

        result = asyncio.run(loop.arun("test"))
        assert result.answer == "server fallback"


class TestStreamRequiredErrors:
    """Auth/rate-limit/config errors do NOT trigger fallback."""

    @pytest.mark.parametrize("error_class", [LLMAuthError, LLMRateLimitError, LLMConfigError])
    def test_required_error_propagates_without_fallback(self, error_class):
        sink = EventSink()
        loop = _make_loop(sink, stream_mode=True)

        call_count = {"astream": 0, "achat": 0}

        async def fake_astream(messages, **kwargs):
            call_count["astream"] += 1
            yield FakeStreamChunk(content="")
            raise error_class("auth/rate/config error")

        async def fake_achat(messages, **kwargs):
            call_count["achat"] += 1
            return LLMResponse(content="should not reach", tool_calls=[], finish_reason="stop")

        loop.client.astream = fake_astream
        loop.client.achat = fake_achat

        result = asyncio.run(loop.arun("test"))

        assert call_count["astream"] == 1
        assert call_count["achat"] == 0, f"{error_class.__name__} should not trigger fallback"
        assert result.finished_reason == "error"
        assert "auth/rate/config error" in result.error

    def test_is_stream_required_error_classification(self):
        """Unit test the helper directly."""
        assert AgentLoop._is_stream_required_error(LLMAuthError("x")) is True
        assert AgentLoop._is_stream_required_error(LLMRateLimitError("x")) is True
        assert AgentLoop._is_stream_required_error(LLMConfigError("x")) is True
        # Other errors → NOT required → fallback eligible
        assert AgentLoop._is_stream_required_error(LLMTimeoutError("x")) is False
        assert AgentLoop._is_stream_required_error(LLMServerError("x")) is False
        assert AgentLoop._is_stream_required_error(LLMMalformedResponseError("x")) is False
        assert AgentLoop._is_stream_required_error(Exception("generic")) is False

    def test_fallback_failure_propagates(self):
        """If both stream and achat fail, the error event fires."""
        sink = EventSink()
        loop = _make_loop(sink, stream_mode=True)

        async def fake_astream(messages, **kwargs):
            yield FakeStreamChunk(content="")
            raise LLMServerError("first")

        async def fake_achat(messages, **kwargs):
            raise LLMServerError("second")

        loop.client.astream = fake_astream
        loop.client.achat = fake_achat

        result = asyncio.run(loop.arun("test"))
        assert result.finished_reason == "error"
        assert "second" in result.error
        assert "error" in sink.types()


# ---------------------------------------------------------------- B: source-level


class TestRunSyncAlsoHasFallback:
    """The sync run() path also has stream→chat fallback."""

    def test_sync_run_stream_failure_falls_back_to_chat(self):
        sink = EventSink()
        cfg = LLMConfig(api_key="sk-test", model="fake-model")
        workspace = Path(tempfile.mkdtemp())
        loop = AgentLoop(
            config=cfg, registry=build_default_registry(),
            workspace=workspace, on_event=sink,
            stream_mode=True, max_iterations=1,
        )

        def fake_stream(messages, iteration):
            raise LLMServerError("sync stream failed")

        def fake_chat(messages, **kwargs):
            return LLMResponse(content="sync fallback", tool_calls=[], finish_reason="stop")

        loop._stream_chat = fake_stream
        loop.client.chat = fake_chat

        result = loop.run("sync test")
        assert result.answer == "sync fallback"
        assert result.finished_reason == "stop"

    def test_sync_run_auth_error_propagates(self):
        sink = EventSink()
        cfg = LLMConfig(api_key="sk-test", model="fake-model")
        workspace = Path(tempfile.mkdtemp())
        loop = AgentLoop(
            config=cfg, registry=build_default_registry(),
            workspace=workspace, on_event=sink,
            stream_mode=True, max_iterations=1,
        )

        def fake_stream(messages, iteration):
            raise LLMAuthError("bad key")

        def fake_chat(messages, **kwargs):
            raise AssertionError("should not reach")

        loop._stream_chat = fake_stream
        loop.client.chat = fake_chat

        result = loop.run("test")
        assert result.finished_reason == "error"
