"""Unit tests for A1 three-dimensional run budget (tokens / cost / time)."""
from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from agentnodes.kernel.agent.strategy.budget import (
    BudgetLimits,
    BudgetUsage,
    evaluate_budget,
    record_usage,
    usage_from_response,
)
from agentnodes.kernel.agent.strategy.loop_context import LoopContext
from agentnodes.kernel.agent.strategy.loop_strategy import LoopConfig
from agentnodes.kernel.agent.strategy.steps.stop import BudgetStopStep


class TestUsageFromResponse:
    def test_total_preferred(self):
        assert usage_from_response({"total_tokens": 100, "prompt_tokens": 1, "completion_tokens": 2}) == (100, 1, 2)

    def test_fallback_prompt_plus_completion(self):
        assert usage_from_response({"prompt_tokens": 30, "completion_tokens": 20}) == (50, 30, 20)

    def test_empty_is_zero(self):
        assert usage_from_response({}) == (0, 0, 0)

    def test_none_is_zero(self):
        assert usage_from_response(None) == (0, 0, 0)

    def test_garbage_values_ignored(self):
        assert usage_from_response({"total_tokens": "x", "prompt_tokens": None}) == (0, 0, 0)


class TestRecordUsage:
    def test_accumulates_tokens(self):
        t = BudgetUsage()
        record_usage(t, BudgetLimits(), {"prompt_tokens": 10, "completion_tokens": 5})
        assert t.tokens == 15
        assert t.prompt_tokens == 10
        assert t.completion_tokens == 5

    def test_cost_with_prices(self):
        limits = BudgetLimits(price_per_mtok_input=1.0, price_per_mtok_output=2.0)
        t = BudgetUsage()
        record_usage(t, limits, {"prompt_tokens": 1_000_000, "completion_tokens": 500_000})
        assert t.cost_usd == pytest.approx(1.0 + 1.0)  # 1*1 + 0.5*2

    def test_no_price_means_zero_cost(self):
        t = BudgetUsage()
        record_usage(t, BudgetLimits(), {"prompt_tokens": 1_000_000})
        assert t.cost_usd == 0.0


class TestEvaluateBudget:
    def test_unlimited_never_stops(self):
        t = BudgetUsage(tokens=10**9, cost_usd=10**6)
        ok, reason = evaluate_budget(t, BudgetLimits())
        assert ok is False and reason is None

    def test_zero_limit_is_unlimited(self):
        t = BudgetUsage(tokens=10**6)
        ok, _ = evaluate_budget(t, BudgetLimits(max_tokens=0))
        assert ok is False

    def test_tokens_exceeded(self):
        t = BudgetUsage(tokens=100)
        ok, reason = evaluate_budget(t, BudgetLimits(max_tokens=100))
        assert ok is True and reason == "budget:tokens"

    def test_cost_exceeded(self):
        t = BudgetUsage(cost_usd=1.5)
        ok, reason = evaluate_budget(t, BudgetLimits(max_cost_usd=1.0))
        assert ok is True and reason == "budget:cost"

    def test_time_exceeded(self):
        t = BudgetUsage(started_at=time.perf_counter() - 10)
        ok, reason = evaluate_budget(t, BudgetLimits(max_seconds=1.0))
        assert ok is True and reason == "budget:time"

    def test_time_not_yet(self):
        t = BudgetUsage(started_at=time.perf_counter())
        ok, reason = evaluate_budget(t, BudgetLimits(max_seconds=3600))
        assert ok is False

    def test_priority_tokens_before_cost(self):
        t = BudgetUsage(tokens=100, cost_usd=100)
        _, reason = evaluate_budget(t, BudgetLimits(max_tokens=1, max_cost_usd=1))
        assert reason == "budget:tokens"


class TestBudgetStopStep:
    def test_external_stop_wins(self):
        ctx = LoopContext(task="t", should_stop=True, stop_reason="user")
        stop, reason = BudgetStopStep().evaluate(ctx)
        assert stop is True and reason == "user"

    def test_budget_stop_sets_context(self):
        step = BudgetStopStep(BudgetLimits(max_tokens=10))
        ctx = LoopContext(task="t", tokens_used=50, cost_usd=0.0, t0=time.perf_counter())
        stop, reason = step.evaluate(ctx)
        assert stop is True
        assert reason == "budget:tokens"
        assert ctx.should_stop is True

    def test_no_budget_passes(self):
        step = BudgetStopStep(BudgetLimits())
        ctx = LoopContext(task="t", tokens_used=999999, t0=time.perf_counter())
        stop, reason = step.evaluate(ctx)
        assert stop is False and reason is None

    def test_reads_loop_config_via_bind(self):
        class FakeStrategy:
            config = LoopConfig(budget_max_tokens=5)

        loop = SimpleNamespace(_strategy=FakeStrategy())
        step = BudgetStopStep()
        step.bind_agent_loop(loop)
        ctx = LoopContext(task="t", tokens_used=10, t0=time.perf_counter())
        stop, reason = step.evaluate(ctx)
        assert stop is True and reason == "budget:tokens"


class TestLoopConfigDefaults:
    def test_budget_fields_default_none(self):
        cfg = LoopConfig()
        assert cfg.budget_max_tokens is None
        assert cfg.budget_max_cost_usd is None
        assert cfg.budget_max_seconds is None
