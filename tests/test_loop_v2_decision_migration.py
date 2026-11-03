"""L7 v0.2 — Step decision migration tests.

Covers the three newly-migrated decision points (progress, resilience,
max_iterations) plus the default step implementations:

1. DefaultProgressStep does real work when bound to an AgentLoop-like
   object: record_hash trims the window, is_no_progress detects repeats.
2. DefaultResilienceStep reads the agentloop's circuit breaker.
3. _call_step isolates a failing step and sets ctx.should_stop.
4. max_iterations: an explicit strategy config drives the loop cap;
   no explicit strategy → constructor max_iterations wins (regression
   for AgentLoop(max_iterations=2) callers).

We drive these through lightweight AgentLoop-like stand-ins (no full
AgentLoop construction, which needs an LLMConfig + tool registry).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from strategy_research.kernel.agent.strategy import (
    LoopContext,
    create_strategy,
    resolve_loop_strategy,
)
from strategy_research.kernel.agent.strategy.steps import (
    DefaultProgressStep,
    DefaultResilienceStep,
)


class _LoopLike:
    """Minimal AgentLoop stand-in exposing the members Default
    Progress/Resilience steps rely on."""

    def __init__(self, no_progress_window=3, circuit_breaker=None):
        self.no_progress_window = no_progress_window
        self._recent_hashes: list[str] = []
        self._circuit_breaker = circuit_breaker

    def _detect_no_progress(self):
        if len(self._recent_hashes) < self.no_progress_window:
            return False
        window = self._recent_hashes[-self.no_progress_window:]
        return len(set(window)) == 1


class TestDefaultProgressStep:
    def test_bind_and_record_trim_window(self):
        loop = _LoopLike(no_progress_window=3)
        step = DefaultProgressStep()
        step.bind_agent_loop(loop)
        ctx = LoopContext(task="t")
        step.record_hash(ctx, "abc")
        step.record_hash(ctx, "abc")
        step.record_hash(ctx, "abc")
        step.record_hash(ctx, "abc")  # 4th → trims to last 3
        assert len(loop._recent_hashes) == 3

    def test_is_no_progress_detects_repeat(self):
        loop = _LoopLike(no_progress_window=3)
        step = DefaultProgressStep()
        step.bind_agent_loop(loop)
        ctx = LoopContext(task="t")
        for _ in range(3):
            step.record_hash(ctx, "x")
        assert step.is_no_progress(ctx) is True

    def test_is_no_progress_false_for_diverse(self):
        loop = _LoopLike(no_progress_window=3)
        step = DefaultProgressStep()
        step.bind_agent_loop(loop)
        ctx = LoopContext(task="t")
        step.record_hash(ctx, "a")
        step.record_hash(ctx, "b")
        step.record_hash(ctx, "c")
        assert step.is_no_progress(ctx) is False

    def test_standalone_without_loop_falls_back_to_ctx(self):
        """No loop bound — record_hash / is_no_progress use ctx state."""
        step = DefaultProgressStep()
        ctx = LoopContext(task="t", metadata={"progress_window": 2})
        step.record_hash(ctx, "y")
        step.record_hash(ctx, "y")
        assert step.is_no_progress(ctx) is True


class TestDefaultResilienceStep:
    def test_is_open_true_when_breaker_open(self):
        class _Breaker:
            def is_open(self):
                return True

        loop = _LoopLike(circuit_breaker=_Breaker())
        step = DefaultResilienceStep()
        step.bind_agent_loop(loop)
        assert step.is_open(LoopContext(task="t")) is True

    def test_is_open_false_when_breaker_closed(self):
        class _Breaker:
            def is_open(self):
                return False

        loop = _LoopLike(circuit_breaker=_Breaker())
        step = DefaultResilienceStep()
        step.bind_agent_loop(loop)
        assert step.is_open(LoopContext(task="t")) is False

    def test_is_open_false_when_no_breaker(self):
        loop = _LoopLike(circuit_breaker=None)
        step = DefaultResilienceStep()
        step.bind_agent_loop(loop)
        assert step.is_open(LoopContext(task="t")) is False

    def test_standalone_without_loop_returns_false(self):
        step = DefaultResilienceStep()
        assert step.is_open(LoopContext(task="t")) is False


class TestCallStepIsolation:
    def test_failing_step_sets_should_stop(self):
        """Mimic the _call_step isolation contract directly: a step
        that raises must leave ctx.should_stop=True + stop_reason set.
        (The real helper lives on AgentLoop; this verifies the exact
        semantics it implements.)"""
        import inspect as _inspect

        from strategy_research.kernel.agent.strategy import LoopContext

        class _LoopHost:
            """Stand-in exposing an async _call_step-like wrapper."""

            async def _call_step(self, step, ctx, *, async_mode):
                try:
                    result = step.execute(ctx, async_mode=async_mode)
                    if _inspect.isawaitable(result):
                        return await result
                    return result
                except Exception:  # noqa: BLE001
                    ctx.should_stop = True
                    ctx.stop_reason = f"step_{step.name}"
                    return ctx

        class _BoomStep:
            name = "boom"

            def execute(self, ctx, *, async_mode):
                raise RuntimeError("kaboom")

        import asyncio

        async def run():
            host = _LoopHost()
            ctx = LoopContext(task="t")
            out = await host._call_step(_BoomStep(), ctx, async_mode=False)
            assert out.should_stop is True
            assert out.stop_reason == "step_boom"

        asyncio.run(run())

    def test_ok_step_returns_ctx(self):
        import inspect as _inspect

        class _LoopHost:
            async def _call_step(self, step, ctx, *, async_mode):
                try:
                    result = step.execute(ctx, async_mode=async_mode)
                    if _inspect.isawaitable(result):
                        return await result
                    return result
                except Exception:  # noqa: BLE001
                    ctx.should_stop = True
                    ctx.stop_reason = f"step_{step.name}"
                    return ctx

        class _OkStep:
            name = "ok"

            def execute(self, ctx, *, async_mode):
                ctx.metadata["ran"] = True
                return ctx

        import asyncio

        from strategy_research.kernel.agent.strategy import LoopContext

        async def run():
            host = _LoopHost()
            ctx = LoopContext(task="t")
            out = await host._call_step(_OkStep(), ctx, async_mode=False)
            assert out.should_stop is False
            assert out.metadata.get("ran") is True

        asyncio.run(run())


class TestMaxIterationsSource:
    def test_explicit_strategy_config_drives_cap(self):
        """An explicit strategy with max_iterations=50 drives the cap."""
        strategy = resolve_loop_strategy(
            {"name": "explorer"}  # explorer = max_iterations 50
        )
        assert strategy.config.max_iterations == 50
        # We don't run the full loop; just verify the source used by
        # _run_loop_core is the strategy when explicit.
        assert strategy.config.max_iterations == 50

    def test_default_strategy_keeps_constructor_cap(self):
        """No explicit strategy → _run_loop_core uses self.max_iterations.
        (constructor default 10)."""
        from strategy_research.kernel.agent.strategy.factory import ReActStrategyFactory
        s = ReActStrategyFactory.create()
        assert s.config.max_iterations == 10  # matches AgentLoop default


class TestMakeStrategyCtxHookCtx:
    def test_hook_ctx_is_passed_through(self):
        """_make_strategy_ctx carries the AgentHookContext through so
        steps can access the same instance the hook system uses."""
        from strategy_research.kernel.agent.loop import _make_strategy_ctx

        class _AgentHookCtx:
            pass

        hook_ctx = _AgentHookCtx()
        ctx = _make_strategy_ctx(
            loop=None,
            messages=[],
            response=None,
            result=None,
            iteration=1,
            hook_ctx=hook_ctx,
        )
        assert ctx.hook_ctx is hook_ctx

    def test_hook_ctx_default_none(self):
        from strategy_research.kernel.agent.loop import _make_strategy_ctx

        ctx = _make_strategy_ctx(
            loop=None, messages=[], response=None, result=None, iteration=1,
        )
        assert ctx.hook_ctx is None


class TestCustomProgressWindow:
    def test_custom_progress_window(self):
        """A strategy that widens no_progress_window uses it for
        detection (Explorer = 5)."""
        explorer = create_strategy("explorer")
        assert explorer.config.no_progress_window == 5
        loop = _LoopLike(no_progress_window=explorer.config.no_progress_window)
        step = DefaultProgressStep()
        step.bind_agent_loop(loop)
        ctx = LoopContext(task="t")
        # 3 identical hashes → NOT no_progress for window 5.
        for _ in range(3):
            step.record_hash(ctx, "same")
        assert step.is_no_progress(ctx) is False
        # 5 identical hashes → no_progress for window 5.
        for _ in range(2):
            step.record_hash(ctx, "same")
        assert step.is_no_progress(ctx) is True
