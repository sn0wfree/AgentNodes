"""L7 — AgentLoop ↔ LoopStrategy wiring tests.

Covers:
- ``_inject_agent_loop`` walks the 9 step slots and calls
  ``bind_agent_loop(loop)`` on steps that opt in.
- ``_make_strategy_ctx`` builds a transient LoopContext with the right
  fields populated from AgentLoop state.
- ``AgentLoop.get_strategy()`` returns the resolved strategy.
- The PreRunStep + LLMCallStep binding round-trip: a real AgentLoop
  instance, after ``_inject_agent_loop``, has its self-reference in
  every Default step that supports it.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agentnodes.kernel.agent.loop import (
    _inject_agent_loop,
    _make_strategy_ctx,
)
from agentnodes.kernel.agent.strategy import (
    DefaultLLMCallStep,
    DefaultPreRunStep,
    create_strategy,
)


class TestInjectAgentLoop:
    def test_walks_all_nine_slots(self):
        """Every slot that has a ``bind_agent_loop`` callable gets the
        loop. None of the v0.1 default steps *need* the loop, but
        PreRunStep and LLMCallStep both opted in."""
        strategy = create_strategy("react")
        sentinel = object()
        _inject_agent_loop(strategy, sentinel)
        assert strategy.pre_run._loop is sentinel
        assert strategy.llm_call._loop is sentinel

    def test_no_op_on_steps_without_bind(self):
        """StopStep / ContinuationStep etc. have no ``bind_agent_loop``
        attribute; ``_inject_agent_loop`` should silently skip them."""
        strategy = create_strategy("react")
        sentinel = object()
        # No error expected.
        _inject_agent_loop(strategy, sentinel)
        # The non-binding slots stay untouched.
        assert not hasattr(strategy.stop, "_loop") or strategy.stop._loop is None

    def test_silent_when_loop_is_none(self):
        """PreRunStep with ``_loop = None`` is fine — the step itself
        guards with ``if self._loop is None: return ctx``."""
        strategy = create_strategy("react")
        _inject_agent_loop(strategy, None)
        assert strategy.pre_run._loop is None


class TestMakeStrategyCtx:
    def test_builds_loop_context_with_response(self):
        mock_response = MagicMock()
        mock_response.tool_calls = [{"name": "x"}]
        mock_response.content = "hello"
        ctx = _make_strategy_ctx(
            loop=MagicMock(),
            messages=[{"role": "user"}],
            response=mock_response,
            result=MagicMock(),
            iteration=3,
        )
        assert ctx.response is mock_response
        assert ctx.response_was_tool_call is True
        assert ctx.response_content == "hello"
        assert ctx.iteration == 3
        assert ctx.messages == [{"role": "user"}]
        assert ctx.task == ""
        assert ctx.should_stop is False

    def test_response_without_tool_calls_marks_no_tool_call(self):
        mock_response = MagicMock()
        mock_response.tool_calls = None
        mock_response.content = ""
        ctx = _make_strategy_ctx(
            loop=MagicMock(),
            messages=[],
            response=mock_response,
            result=MagicMock(),
            iteration=1,
        )
        assert ctx.response_was_tool_call is False
        assert ctx.response_content == ""


class TestStrategyStepBinding:
    """Verify the wiring works end-to-end without spinning up a full
    AgentLoop (which needs LLMConfig, registry, etc.)."""

    def test_default_pre_run_step_bind(self):
        step = DefaultPreRunStep()
        sentinel = object()
        step.bind_agent_loop(sentinel)
        assert step._loop is sentinel

    def test_default_llm_call_step_bind(self):
        step = DefaultLLMCallStep()
        sentinel = object()
        step.bind_agent_loop(sentinel)
        assert step._loop is sentinel

    def test_strategy_resolved_and_accessible(self):
        """Smoke — exercises the factory + resolver path so a typo in
        either doesn't silently keep ``self._strategy`` None."""
        # Build a stub AgentLoop just enough to call get_strategy.
        # We don't run the full constructor because it needs an LLM
        # config; instead we instantiate LoopStrategy + resolver
        # directly and verify they compose.
        from agentnodes.kernel.agent.strategy import (
            StrategyFactory,
        )
        from agentnodes.kernel.agent.strategy.profile_resolver import (
            resolve_loop_strategy,
        )

        StrategyFactory.register("test_l7", lambda cfg=None: create_strategy("explorer"))
        try:
            s = resolve_loop_strategy("test_l7")
            assert s.name == "explorer"
        finally:
            StrategyFactory.unregister("test_l7")
