"""L7 v0.4 — LLMCallStep + FinalizationStep execution migration tests.

Covers:

1. DefaultLLMCallStep drives the LLM call (await _get_response), fires
   before_iteration before the call and on_error after _handle_llm_error.
2. DefaultFinalizationStep runs metrics + claim validation + fires
   after_run (normal-end path).
3. Step error isolation still works (a failing step sets should_stop).
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agentnodes.kernel.agent.strategy import LoopContext
from agentnodes.kernel.agent.strategy.steps import (
    DefaultFinalizationStep,
    DefaultLLMCallStep,
)


class _LLMError(Exception):
    pass


class _FakeLoop:
    """AgentLoop stand-in with configurable _get_response."""

    def __init__(self):
        self.hook_calls: list[tuple] = []
        self.response_obj = None
        self.response_error = None
        self.handle_error_calls = []
        self.metrics_calls = []
        self.claim_calls = []
        self.append_calls = []

    def _build_hook_context(self, iteration, messages):
        return object()

    async def _afire_hooks(self, name, hook_ctx, *args, **kwargs):
        self.hook_calls.append((name, hook_ctx, args, kwargs))

    async def _get_response(self, messages, iteration, async_mode, hook_ctx, result):
        if self.response_error is not None:
            raise self.response_error
        return self.response_obj

    def _handle_llm_error(self, exc, iteration, result):
        self.handle_error_calls.append((exc, iteration))

    def _append_assistant_msg(self, response, messages, result, iteration):
        self.append_calls.append((response, iteration))

    def _finalize_metrics(self, result, messages, t0):
        self.metrics_calls.append((result, messages, t0))

    def _run_claim_validation(self, result, messages):
        self.claim_calls.append((result, messages))


class _Resp:
    def __init__(self, content="", tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls or []

    def has_tool_calls(self):
        return bool(self.tool_calls)


class TestLLMCallStep:
    def test_before_iteration_fires_first(self):
        loop = _FakeLoop()
        loop.response_obj = _Resp(content="hi")
        step = DefaultLLMCallStep()
        step.bind_agent_loop(loop)
        ctx = LoopContext(task="t", iteration=2, messages=[], hook_ctx=object())
        import asyncio

        out = asyncio.run(step.execute(ctx, async_mode=True))
        names = [c[0] for c in loop.hook_calls]
        assert names[0] == "before_iteration"
        assert "on_error" not in names
        assert out.response.content == "hi"
        assert out.response_was_tool_call is False

    def test_response_populates_ctx(self):
        loop = _FakeLoop()
        loop.response_obj = _Resp(content="answer", tool_calls=[{"name": "x"}])
        step = DefaultLLMCallStep()
        step.bind_agent_loop(loop)
        ctx = LoopContext(task="t", iteration=1, messages=[], hook_ctx=object())
        import asyncio

        out = asyncio.run(step.execute(ctx, async_mode=True))
        assert out.response_was_tool_call is True
        assert out.response_content == "answer"
        assert len(loop.append_calls) == 1

    def test_error_fires_on_error_and_handle(self):
        loop = _FakeLoop()
        loop.response_error = _LLMError("boom")
        step = DefaultLLMCallStep()
        step.bind_agent_loop(loop)
        ctx = LoopContext(task="t", iteration=1, messages=[], hook_ctx=object())
        import asyncio

        out = asyncio.run(step.execute(ctx, async_mode=True))
        names = [c[0] for c in loop.hook_calls]
        assert "before_iteration" in names
        assert "on_error" in names
        assert names.index("before_iteration") < names.index("on_error")
        assert len(loop.handle_error_calls) == 1
        assert out.should_stop is True
        assert out.stop_reason == "error"

    def test_none_response_sets_stop(self):
        loop = _FakeLoop()
        loop.response_obj = None
        step = DefaultLLMCallStep()
        step.bind_agent_loop(loop)
        ctx = LoopContext(task="t", iteration=1, messages=[], hook_ctx=object())
        import asyncio

        out = asyncio.run(step.execute(ctx, async_mode=True))
        assert out.should_stop is True
        assert out.stop_reason == "llm_none"

    def test_noop_when_no_loop(self):
        step = DefaultLLMCallStep()
        ctx = LoopContext(task="t", iteration=1, messages=[])
        import asyncio

        out = asyncio.run(step.execute(ctx, async_mode=True))
        assert out is ctx


class TestFinalizationStep:
    def test_runs_metrics_and_claim_and_after_run(self):
        loop = _FakeLoop()
        step = DefaultFinalizationStep()
        step.bind_agent_loop(loop)
        result = MagicMock()
        ctx = LoopContext(
            task="t", iteration=3, messages=[], result=result, t0=1.0,
            hook_ctx=object(),
        )
        import asyncio

        asyncio.run(step.execute(ctx, async_mode=False))
        assert len(loop.metrics_calls) == 1
        assert loop.metrics_calls[0][2] == 1.0  # t0 passed
        assert len(loop.claim_calls) == 1
        names = [c[0] for c in loop.hook_calls]
        assert "after_run" in names

    def test_noop_when_no_loop(self):
        step = DefaultFinalizationStep()
        ctx = LoopContext(task="t", result=MagicMock(), messages=[], t0=0.0)
        import asyncio

        out = asyncio.run(step.execute(ctx, async_mode=False))
        assert out is ctx
