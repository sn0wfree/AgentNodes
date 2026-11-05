"""Tests for AgentLoop.arun() - the async execution path.

Mirrors the sync test_agent_loop.py tests but exercises arun() with
async mock LLM clients (achat / astream).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from agentnodes.kernel.agent.builtin_tools import build_default_registry
from agentnodes.kernel.agent.loop import AgentLoop
from agentnodes.foundation.llm import LLMConfig, LLMResponse, ToolCall
from agentnodes.foundation.llm.errors import LLMError
from agentnodes.foundation.llm.parser import StreamChunk

# ── Helpers ──────────────────────────────────────────────────────────


class AsyncMockLLM:
    """Mock that returns queued LLMResponse objects from async achat()."""

    def __init__(self, responses: list[LLMResponse]):
        self.responses = list(responses)
        self.calls: list[int] = []

    async def achat(self, messages, **kwargs):
        self.calls.append(len(messages))
        if not self.responses:
            raise RuntimeError("AsyncMockLLM exhausted; no more responses queued")
        return self.responses.pop(0)


class AsyncMockStreamLLM:
    """Mock that yields StreamChunk objects from async astream()."""

    def __init__(self, chunk_lists: list[list[StreamChunk]]):
        self.chunk_lists = list(chunk_lists)
        self.call_count = 0

    async def astream(self, messages, **kwargs):
        self.call_count += 1
        if not self.chunk_lists:
            raise RuntimeError("AsyncMockStreamLLM exhausted")
        chunks = self.chunk_lists.pop(0)
        for chunk in chunks:
            yield chunk


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    (tmp_path / "strategies" / "foo").mkdir(parents=True)
    return tmp_path


def text_resp(content: str, **kwargs) -> LLMResponse:
    return LLMResponse(content=content, tool_calls=[], finish_reason="stop", **kwargs)


def tool_resp(tool_calls: list[ToolCall], content: str | None = None, **kwargs) -> LLMResponse:
    return LLMResponse(
        content=content, tool_calls=tool_calls,
        finish_reason="tool_calls", **kwargs,
    )


def _text_chunks(content: str) -> list[StreamChunk]:
    """Split a string into StreamChunks (one per word + a finish chunk)."""
    words = content.split()
    chunks = [StreamChunk(delta_content=w + " ") for w in words]
    chunks.append(StreamChunk(finish_reason="stop"))
    return chunks


# ── Basic arun ───────────────────────────────────────────────────────


class TestAruncBasic:
    @pytest.mark.asyncio
    async def test_arun_single_iteration_stop(self, workspace):
        mock = AsyncMockLLM([text_resp("the answer")])
        loop = AgentLoop(
            config=LLMConfig(api_key="sk-test"),
            registry=build_default_registry(),
            workspace=workspace,
            max_iterations=5,
            stream_mode=False,
        )
        loop.client.achat = mock.achat
        r = await loop.arun("hello")
        assert r.iterations == 1
        assert r.answer == "the answer"
        assert r.finished_reason == "stop"
        assert r.success
        assert r.tool_calls_made == 0

    @pytest.mark.asyncio
    async def test_arun_tool_call_then_answer(self, workspace):
        mock = AsyncMockLLM([
            tool_resp([ToolCall(id="c1", name="list_history", arguments={})]),
            text_resp("done"),
        ])
        loop = AgentLoop(
            config=LLMConfig(api_key="sk-test"),
            registry=build_default_registry(),
            workspace=workspace,
            stream_mode=False,
        )
        loop.client.achat = mock.achat
        r = await loop.arun("improve")
        assert r.iterations == 2
        assert r.tool_calls_made == 1
        assert r.answer == "done"
        assert r.finished_reason == "stop"

    @pytest.mark.asyncio
    async def test_arun_multiple_tool_calls_one_response(self, workspace):
        mock = AsyncMockLLM([
            tool_resp([
                ToolCall(id="c1", name="read", arguments={"path": "README.md"}),
                ToolCall(id="c2", name="list_history", arguments={}),
            ]),
            text_resp("got both"),
        ])
        loop = AgentLoop(
            config=LLMConfig(api_key="sk-test"),
            registry=build_default_registry(),
            workspace=workspace,
            stream_mode=False,
        )
        loop.client.achat = mock.achat
        r = await loop.arun("multi")
        assert r.iterations == 2
        assert r.tool_calls_made == 2
        assert r.answer == "got both"


# ── arun with stream_mode ────────────────────────────────────────────


class TestAruncStreamMode:
    @pytest.mark.asyncio
    async def test_arun_stream_mode_single_iteration(self, workspace):
        mock = AsyncMockStreamLLM([_text_chunks("streamed answer")])
        loop = AgentLoop(
            config=LLMConfig(api_key="sk-test"),
            registry=build_default_registry(),
            workspace=workspace,
            max_iterations=5,
            stream_mode=True,
        )
        loop.client.astream = mock.astream
        r = await loop.arun("hello")
        assert r.iterations == 1
        assert "streamed answer" in r.answer
        assert r.finished_reason == "stop"
        assert mock.call_count == 1

    @pytest.mark.asyncio
    async def test_arun_stream_mode_emits_text_delta_events(self, workspace):
        events: list[tuple[str, dict]] = []
        mock = AsyncMockStreamLLM([_text_chunks("hello world")])
        loop = AgentLoop(
            config=LLMConfig(api_key="sk-test"),
            registry=build_default_registry(),
            workspace=workspace,
            stream_mode=True,
            on_event=lambda et, data: events.append((et, data)),
        )
        loop.client.astream = mock.astream
        await loop.arun("test")
        text_deltas = [e for e in events if e[0] == "text_delta"]
        assert len(text_deltas) >= 2  # at least "hello" and "world"
        thinking_start = [e for e in events if e[0] == "thinking_start"]
        assert len(thinking_start) == 1
        thinking_done = [e for e in events if e[0] == "thinking_done"]
        assert len(thinking_done) == 1


# ── arun max iterations ──────────────────────────────────────────────


class TestAruncMaxIterations:
    @pytest.mark.asyncio
    async def test_arun_max_iterations_reached(self, workspace):
        mock = AsyncMockLLM([
            tool_resp([ToolCall(id=f"c{i}", name="read",
                                arguments={"path": f"file_{i}.txt"})])
            for i in range(5)
        ])
        loop = AgentLoop(
            config=LLMConfig(api_key="sk-test"),
            registry=build_default_registry(),
            workspace=workspace,
            max_iterations=3,
            no_progress_window=10,
            stream_mode=False,
        )
        loop.client.achat = mock.achat
        r = await loop.arun("endless")
        assert r.iterations == 3
        assert r.finished_reason == "max_iter"
        assert r.tool_calls_made == 3


# ── arun no-progress detection ───────────────────────────────────────


class TestAruncNoProgress:
    @pytest.mark.asyncio
    async def test_arun_same_tool_call_triggers_no_progress(self, workspace):
        mock = AsyncMockLLM([
            tool_resp([ToolCall(id=f"c{i}", name="list_history", arguments={})])
            for i in range(5)
        ])
        loop = AgentLoop(
            config=LLMConfig(api_key="sk-test"),
            registry=build_default_registry(),
            workspace=workspace,
            max_iterations=10,
            no_progress_window=3,
            stream_mode=False,
            approval_timeout=0.1,
            approval_on_timeout="reject",
        )
        loop.client.achat = mock.achat
        r = await loop.arun("loop")
        assert r.finished_reason == "approval_timeout_rejected"
        assert "no-progress approval timed out" in r.answer


# ── arun error handling ──────────────────────────────────────────────


class TestAruncError:
    @pytest.mark.asyncio
    async def test_arun_llm_error_emits_error_event(self, workspace):
        class ErrorMockLLM:
            async def achat(self, messages, **kwargs):
                raise LLMError("API key invalid")

        events: list[tuple[str, dict]] = []
        loop = AgentLoop(
            config=LLMConfig(api_key="sk-test"),
            registry=build_default_registry(),
            workspace=workspace,
            max_iterations=3,
            stream_mode=False,
            on_event=lambda et, data: events.append((et, data)),
        )
        loop.client.achat = ErrorMockLLM().achat
        r = await loop.arun("test")
        assert r.finished_reason == "error"
        assert "API key invalid" in (r.error or "")
        error_events = [e for e in events if e[0] == "error"]
        assert len(error_events) == 1


# ── arun event sequence ──────────────────────────────────────────────


class TestAruncEvents:
    @pytest.mark.asyncio
    async def test_arun_emits_iter_start_and_end(self, workspace):
        mock = AsyncMockLLM([text_resp("answer")])
        events: list[tuple[str, dict]] = []
        loop = AgentLoop(
            config=LLMConfig(api_key="sk-test"),
            registry=build_default_registry(),
            workspace=workspace,
            max_iterations=3,
            stream_mode=False,
            on_event=lambda et, data: events.append((et, data)),
        )
        loop.client.achat = mock.achat
        await loop.arun("test")
        iter_starts = [e for e in events if e[0] == "iter_start"]
        iter_ends = [e for e in events if e[0] == "iter_end"]
        assert len(iter_starts) == 1
        assert iter_starts[0][1]["iteration"] == 1
        assert len(iter_ends) == 1
        assert iter_ends[0][1]["finish_reason"] == "stop"

    @pytest.mark.asyncio
    async def test_arun_tool_call_emits_tool_events(self, workspace):
        mock = AsyncMockLLM([
            tool_resp([ToolCall(id="c1", name="list_history", arguments={})]),
            text_resp("done"),
        ])
        events: list[tuple[str, dict]] = []
        loop = AgentLoop(
            config=LLMConfig(api_key="sk-test"),
            registry=build_default_registry(),
            workspace=workspace,
            max_iterations=5,
            stream_mode=False,
            on_event=lambda et, data: events.append((et, data)),
        )
        loop.client.achat = mock.achat
        await loop.arun("test")
        tool_calls = [e for e in events if e[0] == "tool_call"]
        tool_results = [e for e in events if e[0] == "tool_result"]
        assert len(tool_calls) == 1
        assert tool_calls[0][1]["tool"] == "list_history"
        assert len(tool_results) == 1
        assert tool_results[0][1]["status"] == "done"


# ── arun with async hooks ────────────────────────────────────────────


class TestAruncHooks:
    @pytest.mark.asyncio
    async def test_arun_hooks_are_awaited(self, workspace):
        mock = AsyncMockLLM([text_resp("answer")])
        hook_calls: list[str] = []

        class AsyncHooks:
            async def before_run(self, ctx):
                hook_calls.append("before_run")
            async def after_run(self, ctx, result):
                hook_calls.append("after_run")

        loop = AgentLoop(
            config=LLMConfig(api_key="sk-test"),
            registry=build_default_registry(),
            workspace=workspace,
            max_iterations=3,
            stream_mode=False,
            hooks=AsyncHooks(),
        )
        loop.client.achat = mock.achat
        await loop.arun("test")
        assert "before_run" in hook_calls
        assert "after_run" in hook_calls
