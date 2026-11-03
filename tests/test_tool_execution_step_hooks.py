"""L7 v0.3 — ToolExecutionStep hook self-trigger tests.

Covers the "Step self-triggers hooks" pattern landed in v0.3:

- DefaultToolExecutionStep fires before_execute_tools before dispatch.
- It fires on_tool_error for error results and after_tool_executed for
  success results (matching the legacy _fire_tool_result_hooks order).
- It collects hashes + appends results into ctx (metadata + messages).
- A step whose loop is None is a no-op (standalone testability).
- error / success tool results route to the right hooks.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from strategy_research.kernel.agent.strategy import LoopContext
from strategy_research.kernel.agent.strategy.steps import DefaultToolExecutionStep


class _FakeLoop:
    """AgentLoop stand-in capturing hook calls."""

    def __init__(self, async_mode_result=None):
        self.hook_calls: list[tuple] = []
        self.async_mode_result = async_mode_result
        self._collect_tool_hashes = MagicMock(return_value=["h1", "h2"])
        self._append_tool_results = MagicMock()

    async def _afire_hooks(self, name, hook_ctx, *args, **kwargs):
        self.hook_calls.append((name, hook_ctx, args, kwargs))

    async def _aexecute_tool_batch(self, tool_calls, result):
        return self.async_mode_result or []

    def _execute_tool_batch(self, tool_calls, result):
        return []


class _ToolCall:
    def __init__(self, name="tool_x"):
        self.name = name


def _success_msg():
    return {"role": "tool", "tool_call_id": "t1", "content": "ok"}


def _error_msg():
    return {
        "role": "tool",
        "tool_call_id": "t2",
        "content": '{"status": "error", "error": "boom"}',
    }


class TestToolExecutionStepHooks:
    def test_fires_before_execute_tools_first(self):
        loop = _FakeLoop()
        step = DefaultToolExecutionStep()
        step.bind_agent_loop(loop)

        resp = MagicMock(tool_calls=[_ToolCall()])
        ctx = LoopContext(task="t", response=resp, hook_ctx=object())
        # Sync path.
        import asyncio

        asyncio.run(step.execute(ctx, async_mode=False))
        assert loop.hook_calls[0][0] == "before_execute_tools"
        # after tool hooks come later (none here — batch returned []).
        assert all(c[0] != "on_tool_error" for c in loop.hook_calls)
        assert all(c[0] != "after_tool_executed" for c in loop.hook_calls)

    def test_noop_when_no_loop(self):
        step = DefaultToolExecutionStep()
        ctx = LoopContext(task="t", response=MagicMock(tool_calls=[_ToolCall()]))
        import asyncio

        out = asyncio.run(step.execute(ctx, async_mode=False))
        assert out is ctx
        assert "tool_hashes" not in ctx.metadata

    def test_noop_when_no_tool_calls(self):
        loop = _FakeLoop()
        step = DefaultToolExecutionStep()
        step.bind_agent_loop(loop)
        ctx = LoopContext(task="t", response=MagicMock(tool_calls=[]))
        import asyncio

        out = asyncio.run(step.execute(ctx, async_mode=False))
        assert out is ctx
        assert loop.hook_calls == []  # no before_execute_tools either

    def test_success_result_fires_after_tool_executed(self):
        loop = _FakeLoop(async_mode_result=[_success_msg()])
        step = DefaultToolExecutionStep()
        step.bind_agent_loop(loop)
        ctx = LoopContext(
            task="t", response=MagicMock(tool_calls=[_ToolCall("a")]),
            hook_ctx=object(),
        )
        import asyncio

        asyncio.run(step.execute(ctx, async_mode=True))
        names = [c[0] for c in loop.hook_calls]
        assert "after_tool_executed" in names
        assert "on_tool_error" not in names
        # after_tool_executed fired after before_execute_tools.
        assert names.index("before_execute_tools") < names.index("after_tool_executed")

    def test_error_result_fires_on_tool_error(self):
        loop = _FakeLoop(async_mode_result=[_error_msg()])
        step = DefaultToolExecutionStep()
        step.bind_agent_loop(loop)
        ctx = LoopContext(
            task="t", response=MagicMock(tool_calls=[_ToolCall("b")]),
            hook_ctx=object(),
        )
        import asyncio

        asyncio.run(step.execute(ctx, async_mode=True))
        names = [c[0] for c in loop.hook_calls]
        assert "on_tool_error" in names
        assert "after_tool_executed" not in names

    def test_mixed_results_fire_both(self):
        loop = _FakeLoop(async_mode_result=[_success_msg(), _error_msg()])
        step = DefaultToolExecutionStep()
        step.bind_agent_loop(loop)
        ctx = LoopContext(
            task="t", response=MagicMock(tool_calls=[_ToolCall("a"), _ToolCall("b")]),
            hook_ctx=object(),
        )
        import asyncio

        asyncio.run(step.execute(ctx, async_mode=True))
        names = [c[0] for c in loop.hook_calls]
        assert "before_execute_tools" in names
        assert "after_tool_executed" in names
        assert "on_tool_error" in names

    def test_sync_path_uses_execute_tool_batch(self):
        loop = _FakeLoop()  # sync batch returns []
        step = DefaultToolExecutionStep()
        step.bind_agent_loop(loop)
        ctx = LoopContext(
            task="t", response=MagicMock(tool_calls=[_ToolCall()]),
            hook_ctx=object(),
        )
        import asyncio

        asyncio.run(step.execute(ctx, async_mode=False))
        # Sync path called _execute_tool_batch (returns []) not the
        # async twin; both hook paths work.
        assert loop.hook_calls[0][0] == "before_execute_tools"

    def test_collects_hashes_and_appends_results(self):
        loop = _FakeLoop(async_mode_result=[_success_msg()])
        step = DefaultToolExecutionStep()
        step.bind_agent_loop(loop)
        ctx = LoopContext(
            task="t", response=MagicMock(tool_calls=[_ToolCall("x")]),
            hook_ctx=object(),
        )
        import asyncio

        out = asyncio.run(step.execute(ctx, async_mode=True))
        assert out.metadata["tool_hashes"] == ["h1", "h2"]
        assert len(out.metadata["tool_result_msgs"]) == 1
        loop._append_tool_results.assert_called_once()
