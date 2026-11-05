"""P1-2/3/4 — three strategies tests.

Covers:
- ExplorerStrategy: high max_iterations (50), relaxed progress (5).
- ValidatorStrategy: low max_iterations (5), strict progress (2),
  ClaimValidationFinalizationStep attached.
- MinimalStrategy: max_iterations=1, NoOpToolExecutionStep attached.
- CustomStep helpers: ClaimValidationFinalizationStep marks
  ctx.metadata["claim_validation_ran"]; NoOpToolExecutionStep
  records ctx.metadata["tool_execution_skipped"] when response has
  tool calls.
- create_strategy(name) returns the matching factory result.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agentnodes.kernel.agent.strategy import (
    ClaimValidationFinalizationStep,
    ExplorerStrategy,
    ExplorerStrategyFactory,
    LoopConfig,
    LoopContext,
    MinimalStrategy,
    MinimalStrategyFactory,
    NoOpToolExecutionStep,
    StrategyFactory,
    ValidatorStrategy,
    ValidatorStrategyFactory,
    create_strategy,
)
from agentnodes.kernel.agent.strategy.factory import ReActStrategyFactory


class TestExplorerStrategy:
    def test_high_max_iterations(self):
        s = create_strategy("explorer")
        assert s.config.max_iterations == 50

    def test_relaxed_progress_window(self):
        s = create_strategy("explorer")
        assert s.config.no_progress_window == 5

    def test_inherits_default_steps(self):
        s = create_strategy("explorer")
        react = create_strategy("react")
        # Same step types except the finalization (which the validator
        # also overrides). For explorer: every step matches the ReAct
        # base because explorer only changes LoopConfig.
        assert type(s.pre_run) is type(react.pre_run)
        assert type(s.llm_call) is type(react.llm_call)
        assert type(s.stop) is type(react.stop)
        assert type(s.tool_execution) is type(react.tool_execution)

    def test_explorer_factory_with_custom_config(self):
        cfg = LoopConfig(max_iterations=100, no_progress_window=10)
        s = ExplorerStrategyFactory.create(config=cfg)
        assert s.config.max_iterations == 100
        assert s.config.no_progress_window == 10

    def test_explorer_direct_construction(self):
        e = ExplorerStrategy()
        assert e.name == "explorer"
        assert e.config.max_iterations == 50


class TestValidatorStrategy:
    def test_low_max_iterations(self):
        s = create_strategy("validator")
        assert s.config.max_iterations == 5

    def test_strict_progress_window(self):
        s = create_strategy("validator")
        assert s.config.no_progress_window == 2

    def test_uses_claim_validation_finalization(self):
        s = create_strategy("validator")
        assert isinstance(s.finalization, ClaimValidationFinalizationStep)

    def test_inherits_other_default_steps(self):
        s = create_strategy("validator")
        react = create_strategy("react")
        assert type(s.pre_run) is type(react.pre_run)
        assert type(s.stop) is type(react.stop)
        assert type(s.tool_execution) is type(react.tool_execution)

    def test_validator_factory_with_custom_config(self):
        cfg = LoopConfig(max_iterations=2, no_progress_window=1)
        s = ValidatorStrategyFactory.create(config=cfg)
        assert s.config.max_iterations == 2

    def test_validator_direct_construction(self):
        v = ValidatorStrategy()
        assert v.name == "validator"
        assert isinstance(v.finalization, ClaimValidationFinalizationStep)


class TestMinimalStrategy:
    def test_single_iteration(self):
        s = create_strategy("minimal")
        assert s.config.max_iterations == 1

    def test_uses_noop_tool_execution(self):
        s = create_strategy("minimal")
        assert isinstance(s.tool_execution, NoOpToolExecutionStep)

    def test_inherits_other_default_steps(self):
        s = create_strategy("minimal")
        react = create_strategy("react")
        assert type(s.pre_run) is type(react.pre_run)
        assert type(s.stop) is type(react.stop)
        assert type(s.finalization) is type(react.finalization)

    def test_minimal_factory_with_custom_config(self):
        cfg = LoopConfig(max_iterations=3)  # ignored for minimal unless requested
        s = MinimalStrategyFactory.create(config=cfg)
        assert s.name == "minimal"
        assert isinstance(s.tool_execution, NoOpToolExecutionStep)

    def test_minimal_direct_construction(self):
        m = MinimalStrategy()
        assert m.name == "minimal"
        assert m.config.max_iterations == 1
        assert isinstance(m.tool_execution, NoOpToolExecutionStep)


class TestCustomSteps:
    def test_claim_validation_step_marks_metadata(self):
        step = ClaimValidationFinalizationStep()
        ctx = LoopContext(task="t")
        assert "claim_validation_ran" not in ctx.metadata
        step.execute(ctx, async_mode=False)
        assert ctx.metadata.get("claim_validation_ran") is True

    def test_noop_tool_execution_passes_context(self):
        step = NoOpToolExecutionStep()
        ctx = LoopContext(task="t")
        result = step.execute(ctx, async_mode=False)
        assert result is ctx
        assert "tool_execution_skipped" not in ctx.metadata

    def test_noop_tool_execution_records_skip_with_tool_calls(self):
        step = NoOpToolExecutionStep()
        # Fake response with a ``tool_calls`` attribute; we don't need
        # the real LLM response shape, just the attribute presence.
        class FakeResp:
            tool_calls = [{"name": "x"}]

        ctx = LoopContext(task="t", response=FakeResp())
        step.execute(ctx, async_mode=False)
        assert ctx.metadata.get("tool_execution_skipped") is True


class TestFactoryRegistration:
    def test_all_four_default_strategies_registered(self):
        available = set(StrategyFactory.available())
        assert {"react", "explorer", "validator", "minimal"}.issubset(available)

    def test_create_by_name(self):
        for name in ("react", "explorer", "validator", "minimal"):
            s = create_strategy(name)
            assert s.name == name

    def test_unknown_raises(self):
        with pytest.raises(KeyError) as ei:
            create_strategy("nope")
        assert "nope" in str(ei.value)


class TestReActUnchanged:
    """Ensure P1-2/3/4 don't perturb the ReAct strategy."""

    def test_react_unchanged(self):
        s = create_strategy("react")
        # Same as factory.create() with no overrides.
        react_direct = ReActStrategyFactory.create()
        assert s.config == react_direct.config
        assert s.name == react_direct.name
