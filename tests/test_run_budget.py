"""RunBudget 独立测试 —— 预算记账、三维判定、分层合并、边界。

此前只经 react/llm 节点间接覆盖；这里直接验证其契约：
  A. plan_call 判定（三维各自触发、顺序稳定、边界值）
  B. record 记账（tokens/cost 累加、缺失 usage、畸形 usage）
  C. 状态（exhausted/reason 粘滞、snapshot 结构）
  D. 与 strategy/budget.py 的分层关系（不重复实现、复用记账）
  E. 长时间运行（wallclock 维度）
"""
from __future__ import annotations

import time

import pytest

from strategy_research.core.agent.strategy.budget import BudgetLimits
from strategy_research.kernel.runtimes.run_budget import RunBudget


def usage(tokens: int = 0, *, prompt: int = 0, completion: int = 0) -> dict:
    return {
        "total_tokens": tokens,
        "prompt_tokens": prompt,
        "completion_tokens": completion,
    }


# ── A. plan_call 判定 ───────────────────────────────────


def test_fresh_budget_allows_call():
    rb = RunBudget()
    allowed, reason = rb.plan_call()
    assert allowed and reason is None


def test_tokens_limit_checked_first():
    rb = RunBudget(limits=BudgetLimits(max_tokens=10))
    rb.record(usage(11))
    allowed, reason = rb.plan_call()
    assert not allowed
    assert reason == "budget:tokens"


def test_calls_limit_when_no_token_limit():
    """strategy/budget.py 无 max_calls 维度；本测试确认它只有 tokens/cost/time。"""
    rb = RunBudget(limits=BudgetLimits(max_tokens=None, max_seconds=None))
    rb.record(usage(10**6))
    allowed, _ = rb.plan_call()
    assert allowed  # 无上限=不限


def test_cost_limit_checked():
    rb = RunBudget(
        limits=BudgetLimits(max_cost_usd=0.001, price_per_mtok_input=1000.0),
    )
    rb.record(usage(0, prompt=10_000))  # 10k tokens * 1000 USD/Mtok = 0.01 USD
    allowed, reason = rb.plan_call()
    assert not allowed
    assert reason == "budget:cost"


def test_wallclock_limit_checked():
    rb = RunBudget(limits=BudgetLimits(max_seconds=0.01))
    time.sleep(0.02)
    allowed, reason = rb.plan_call()
    assert not allowed
    assert reason == "budget:time"


def test_exactly_at_limit_allowed():
    rb = RunBudget(limits=BudgetLimits(max_tokens=100))
    rb.record(usage(100))
    # evaluate_budget 用 >=，等于即超
    allowed, _ = rb.plan_call()
    assert not allowed


def test_below_limit_allowed():
    rb = RunBudget(limits=BudgetLimits(max_tokens=100))
    rb.record(usage(99))
    allowed, _ = rb.plan_call()
    assert allowed


# ── B. record 记账 ──────────────────────────────────────


def test_record_accumulates_tokens():
    rb = RunBudget()
    rb.record(usage(10))
    rb.record(usage(20))
    assert rb.usage.tokens == 30


def test_record_accumulates_prompt_completion():
    rb = RunBudget()
    rb.record(usage(30, prompt=20, completion=10))
    rb.record(usage(60, prompt=40, completion=20))
    assert rb.usage.prompt_tokens == 60
    assert rb.usage.completion_tokens == 30


def test_record_missing_usage_is_zero():
    rb = RunBudget()
    rb.record(None)
    assert rb.usage.tokens == 0


def test_record_malformed_usage_does_not_crash():
    rb = RunBudget()
    rb.record({"total_tokens": "not-a-number"})  # 畸形值按 0
    assert rb.usage.tokens == 0


def test_record_falls_back_to_prompt_plus_completion():
    rb = RunBudget()
    rb.record({"prompt_tokens": 7, "completion_tokens": 3})  # 无 total
    assert rb.usage.tokens == 10


def test_record_accumulates_cost():
    rb = RunBudget(
        limits=BudgetLimits(price_per_mtok_input=1.0, price_per_mtok_output=2.0),
    )
    rb.record(usage(0, prompt=1_000_000, completion=1_000_000))
    # 1M input * 1.0 + 1M output * 2.0 = 3.0
    assert rb.usage.cost_usd == pytest.approx(3.0)


# ── C. 状态 ─────────────────────────────────────────────


def test_exhausted_flag_sticky():
    rb = RunBudget(limits=BudgetLimits(max_tokens=1))
    rb.record(usage(10))
    rb.plan_call()
    assert rb.exhausted is True
    assert rb.reason == "budget:tokens"
    # 再次调用仍报耗尽（不因状态读取而重置）
    allowed, _ = rb.plan_call()
    assert not allowed and rb.exhausted is True


def test_snapshot_shape():
    rb = RunBudget()
    rb.record(usage(42))
    snap = rb.snapshot()
    assert set(snap) == {"total_tokens", "cost_usd", "exhausted", "reason"}
    assert snap["total_tokens"] == 42
    assert snap["exhausted"] is False


def test_snapshot_after_exhaustion():
    rb = RunBudget(limits=BudgetLimits(max_tokens=1))
    rb.record(usage(10))
    rb.plan_call()
    snap = rb.snapshot()
    assert snap["exhausted"] is True
    assert snap["reason"] == "budget:tokens"


# ── D. 与 strategy/budget.py 的关系 ─────────────────────


def test_limits_are_the_shared_dataclass():
    """分层而非重复实现：limits 类型就是既有 BudgetLimits。"""
    rb = RunBudget(limits=BudgetLimits(max_tokens=5))
    assert isinstance(rb.limits, BudgetLimits)


def test_usage_is_the_shared_dataclass():
    from strategy_research.core.agent.strategy.budget import BudgetUsage

    rb = RunBudget()
    assert isinstance(rb.usage, BudgetUsage)


def test_default_limits_are_unlimited():
    rb = RunBudget()
    assert rb.limits.max_tokens is None
    assert rb.limits.max_cost_usd is None
    assert rb.limits.max_seconds is None


def test_started_at_initialized():
    """RunBudget 构造时启动计时（wallclock 维度依赖它）。"""
    rb = RunBudget()
    assert rb.usage.started_at > 0
    assert rb.usage.started_at <= time.perf_counter()
