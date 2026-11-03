"""P1-1 — LoopStrategy abstraction tests.

Covers: LoopContext defaults, 9 Step Protocols (runtime_checkable),
Default Step implementations, LoopStrategy composition, ReAct factory,
StrategyFactory lifecycle, CustomStrategy override semantics.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from strategy_research.kernel.agent.strategy import (
    CompactionStep,
    ContinuationStep,
    CustomStrategy,
    FinalizationStep,
    LLMCallStep,
    LoopConfig,
    LoopContext,
    PreRunStep,
    ProgressStep,
    ReActStrategyFactory,
    ResilienceStep,
    StopStep,
    StrategyFactory,
    ToolExecutionStep,
    create_strategy,
)
from strategy_research.kernel.agent.strategy.steps import (
    DefaultCompactionStep,
    DefaultContinuationStep,
    DefaultFinalizationStep,
    DefaultLLMCallStep,
    DefaultPreRunStep,
    DefaultProgressStep,
    DefaultResilienceStep,
    DefaultStopStep,
    DefaultToolExecutionStep,
)


class TestLoopContext:
    def test_defaults(self):
        ctx = LoopContext(task="hello", context="ctx", history=[])
        assert ctx.task == "hello"
        assert ctx.iteration == 0
        assert ctx.messages == []
        assert ctx.should_stop is False
        assert ctx.recent_hashes == []
        assert ctx.tool_calls_made == 0
        assert ctx.previous_summary is None
        assert ctx.metadata == {}

    def test_context_independence(self):
        """Two contexts don't share default mutable state."""
        a = LoopContext(task="a")
        b = LoopContext(task="b")
        a.messages.append({"role": "user", "content": "x"})
        assert b.messages == []

    def test_progress_window_metadata_round_trip(self):
        ctx = LoopContext(task="t", metadata={"progress_window": 5})
        assert ctx.metadata["progress_window"] == 5


class TestStepProtocols:
    def test_default_steps_satisfy_protocols(self):
        """Each default implementation must pass its matching Protocol."""
        pairs = [
            (DefaultPreRunStep(), PreRunStep),
            (DefaultLLMCallStep(), LLMCallStep),
            (DefaultCompactionStep(), CompactionStep),
            (DefaultStopStep(), StopStep),
            (DefaultContinuationStep(), ContinuationStep),
            (DefaultProgressStep(), ProgressStep),
            (DefaultResilienceStep(), ResilienceStep),
            (DefaultToolExecutionStep(), ToolExecutionStep),
            (DefaultFinalizationStep(), FinalizationStep),
        ]
        for step, proto in pairs:
            assert isinstance(step, proto), f"{type(step).__name__} should satisfy {proto.__name__}"

    def test_all_steps_have_name(self):
        for step in (
            DefaultPreRunStep(), DefaultLLMCallStep(), DefaultCompactionStep(),
            DefaultStopStep(), DefaultContinuationStep(), DefaultProgressStep(),
            DefaultResilienceStep(), DefaultToolExecutionStep(),
            DefaultFinalizationStep(),
        ):
            assert isinstance(step.name, str) and len(step.name) > 0


class TestLoopStrategy:
    def test_should_continue_default_true(self):
        s = ReActStrategyFactory.create()
        ctx = LoopContext(task="x")
        assert s.should_continue(ctx) is True

    def test_should_continue_respects_should_stop(self):
        s = ReActStrategyFactory.create()
        ctx = LoopContext(task="x")
        ctx.should_stop = True
        ctx.stop_reason = "manual"
        cont, reason = (s.should_continue(ctx), ctx.stop_reason)
        assert cont is False
        assert reason == "manual"

    def test_loop_config_defaults(self):
        cfg = LoopConfig()
        assert cfg.max_iterations == 10
        assert cfg.no_progress_window == 3
        assert cfg.parallel_tool_execution is True


class TestProgressStep:
    def test_record_and_no_progress(self):
        s = DefaultProgressStep()
        ctx = LoopContext(task="t", metadata={"progress_window": 3})
        for h in ("abc", "abc", "abc"):
            s.record_hash(ctx, h)
        assert s.is_no_progress(ctx) is True

    def test_progress_window_breaker(self):
        s = DefaultProgressStep()
        ctx = LoopContext(task="t", metadata={"progress_window": 3})
        s.record_hash(ctx, "x")
        s.record_hash(ctx, "y")
        assert s.is_no_progress(ctx) is False  # window not full


class TestResilienceStep:
    def test_default_is_open_false(self):
        s = DefaultResilienceStep()
        ctx = LoopContext(task="t")
        assert s.is_open(ctx) is False

    def test_record_does_not_raise(self):
        s = DefaultResilienceStep()
        ctx = LoopContext(task="t")
        s.record_success(ctx, "x")
        s.record_failure(ctx, "x")


class TestFactory:
    def test_default_is_react(self):
        # P1-2/3/4 added explorer/validator/minimal; P1-1's strict
        # expectation is relaxed to a set membership check.
        available = set(StrategyFactory.available())
        assert {"react", "explorer", "validator", "minimal"}.issubset(available)
        s = create_strategy()
        assert s.name == "react"

    def test_unknown_raises(self):
        with pytest.raises(KeyError) as ei:
            create_strategy("nope")
        assert "nope" in str(ei.value)

    def test_register_unregister(self):
        class FakeStrategy:
            pass

        def factory(config=None):
            return FakeStrategy()

        StrategyFactory.register("fake", factory)
        assert "fake" in StrategyFactory.available()
        assert isinstance(create_strategy("fake"), FakeStrategy)
        StrategyFactory.unregister("fake")
        assert "fake" not in StrategyFactory.available()

    def test_react_with_custom_config(self):
        cfg = LoopConfig(max_iterations=42)
        s = create_strategy("react", cfg)
        assert s.config.max_iterations == 42

    def test_create_strategy_by_name(self):
        for name in ("react", "explorer", "validator", "minimal"):
            s = create_strategy(name)
            assert s.name == name


class TestCustomStrategy:
    def test_override_single_step(self):
        class MyStop:
            @property
            def name(self):
                return "stop"

            def evaluate(self, ctx):
                return True, "always"

        custom = CustomStrategy(
            name="my_strategy",
            stop=MyStop(),
        )
        ctx = LoopContext(task="t")
        # CustomStrategy uses MyStop for the stop slot.
        assert isinstance(custom.stop, MyStop)
        stop_now, reason = custom.stop.evaluate(ctx)
        assert stop_now is True
        assert reason == "always"
        # Other slots come from the default ReAct base.
        assert isinstance(custom.pre_run, DefaultPreRunStep)
        assert isinstance(custom.tool_execution, DefaultToolExecutionStep)

    def test_custom_strategy_should_continue_via_ctx_should_stop(self):
        """``should_continue`` reads ``ctx.should_stop`` (set by StopStep
        elsewhere in the loop body). Verify the wiring works."""
        s = CustomStrategy(name="wire_test")
        ctx = LoopContext(task="t")
        # Default: keep going.
        assert s.should_continue(ctx) is True
        # After a StopStep signals stop: break.
        ctx.should_stop = True
        ctx.stop_reason = "manual"
        assert s.should_continue(ctx) is False

    def test_inherits_base_when_no_override(self):
        custom = CustomStrategy(name="inherit")
        s = ReActStrategyFactory.create()
        # Every slot is the same default instance type.
        assert type(custom.pre_run) is type(s.pre_run)
        assert type(custom.llm_call) is type(s.llm_call)
        assert custom.name == "inherit"


class TestExports:
    def test_module_top_level_exports(self):
        from strategy_research.kernel.agent import strategy
        names = {
            "LoopContext", "LoopStrategy", "LoopConfig",
            "StrategyFactory", "create_strategy", "register_strategy",
            "CustomStrategy", "ReActStrategyFactory",
            "Step", "PreRunStep", "LLMCallStep", "CompactionStep",
            "StopStep", "ContinuationStep", "ProgressStep",
            "ResilienceStep", "ToolExecutionStep", "FinalizationStep",
        }
        assert names.issubset(set(strategy.__all__))
