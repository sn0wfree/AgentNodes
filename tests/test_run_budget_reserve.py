"""预算预留树（F2）测试 —— 均分数学 / 零剩余预标 / settle 归还 / fanout 接线。

对应 docs/stage-f2-budget-tree-design-20261027.md §5 验收。
"""
from __future__ import annotations

import pytest

from strategy_research.kernel.agent.strategy.budget import BudgetLimits
from strategy_research.kernel.runtimes.graph_engine import (
    GraphEngine,
    GraphInterrupt,
    INTERRUPT_KEY,
)
from strategy_research.kernel.runtimes.run_budget import RunBudget


def parent_budget(**limits) -> RunBudget:
    return RunBudget(limits=BudgetLimits(**limits))


# ── 均分数学 ─────────────────────────────────────────────


def test_reserve_even_split_floor():
    slices = parent_budget(max_tokens=10).reserve(3)
    assert [s.limits.max_tokens for s in slices] == [3, 3, 3]
    assert all(not s.exhausted for s in slices)


def test_remainder_and_parent_untouched():
    b = parent_budget(max_tokens=10)
    b.reserve(3)
    assert b.limits.max_tokens == 10  # 父上限不动（未预留余量仍归父）
    assert b.usage.tokens == 0


def test_reserve_accounts_existing_usage():
    b = parent_budget(max_tokens=10)
    b.record({"total_tokens": 4})
    slices = b.reserve(3)
    assert [s.limits.max_tokens for s in slices] == [2, 2, 2]


def test_inactive_dims_inherit_unlimited():
    slices = parent_budget(max_tokens=10).reserve(2)  # cost/time 未启用
    for s in slices:
        assert s.limits.max_cost_usd is None
        assert s.limits.max_seconds is None
        assert not s.exhausted


def test_price_fields_passthrough():
    b = parent_budget(
        max_tokens=100, price_per_mtok_input=1.0, price_per_mtok_output=2.0,
    )
    slices = b.reserve(2)
    for s in slices:
        assert s.limits.price_per_mtok_input == 1.0
        assert s.limits.price_per_mtok_output == 2.0
    # 切片内成本记账照常工作
    slices[0].record({"prompt_tokens": 1_000_000, "completion_tokens": 0})
    assert abs(slices[0].usage.cost_usd - 1.0) < 1e-9


# ── 零剩余：预标 exhausted（0-上限漏洞）──────────────────


def test_starved_slice_plan_call_refuses():
    b = parent_budget(max_tokens=10)
    b.record({"total_tokens": 10})
    slices = b.reserve(3)
    for s in slices:
        assert s.exhausted is True
        assert s.reason == "budget:reserve_starved"
        allowed, reason = s.plan_call()
        assert allowed is False and reason == "budget:reserve_starved"


def test_floor_to_zero_starved():
    slices = parent_budget(max_tokens=2).reserve(3)
    assert all(s.exhausted for s in slices)


def test_starved_time_dimension():
    b = parent_budget(max_seconds=10.0)
    b.usage.started_at -= 10.0  # 已耗尽墙钟
    slices = b.reserve(2)
    assert all(s.exhausted for s in slices)


def test_reserve_n_lt_1_raises():
    with pytest.raises(ValueError, match="至少切 1 份"):
        parent_budget(max_tokens=10).reserve(0)


# ── 切片独立与 settle 归还 ───────────────────────────────


def test_slices_are_independent():
    b = parent_budget(max_tokens=10)
    a_slice, b_slice = b.reserve(2)
    a_slice.record({"total_tokens": 3})
    assert b_slice.usage.tokens == 0
    assert b.usage.tokens == 0  # fanout 期间父 usage 也不动


def test_absorb_sums_usage():
    b = parent_budget(max_tokens=100, price_per_mtok_input=1.0)
    s1, s2 = b.reserve(2)
    s1.record({"total_tokens": 30, "prompt_tokens": 30})
    s2.record({"total_tokens": 50, "prompt_tokens": 50})
    b.absorb(s1, s2)
    assert b.usage.tokens == 80
    assert b.usage.prompt_tokens == 80
    assert b.limits.max_tokens == 100


def test_absorb_does_not_inherit_exhaustion_or_time():
    import time as _t

    b = parent_budget(max_tokens=10)
    started = b.usage.started_at
    s1, s2 = b.reserve(2)
    s1.record({"total_tokens": 5})
    s1.exhausted = True  # 切片耗尽（5 ≥ 5? 不会自动——手动标记模拟）
    s1.reason = "budget:tokens"
    _t.sleep(0.01)
    b.absorb(s1, s2)
    assert b.exhausted is False  # 切份额耗尽 ≠ 父预算耗尽
    assert b.usage.started_at == started  # 时间不吸收


# ── fanout 接线 ──────────────────────────────────────────


def test_fanout_reserves_and_settles():
    captured: list = []

    def worker(payload, branch_state):
        budget = branch_state["branch"]["budget"]
        captured.append(budget.limits.max_tokens)
        allowed, _ = budget.plan_call()
        assert allowed
        budget.record({"total_tokens": 2})
        return {"v": payload}

    engine = GraphEngine()
    engine.add_node("split", lambda s: {**s, "branches": [1, 2, 3]})
    engine.add_node("join", lambda s: {**s, "joined": True})
    engine.add_fanout("split", worker, "join")

    parent = parent_budget(max_tokens=9)
    out = engine.run({"run_budget": parent})

    assert captured == [3, 3, 3]  # 9 切 3 → 每支 3
    assert out["joined"] is True
    assert parent.usage.tokens == 6  # settle：Σ子消耗 2×3 回到父
    assert not parent.exhausted


def test_fanout_without_budget_key_is_noop():
    seen: list = []

    def worker(payload, branch_state):
        seen.append(branch_state["branch"]["budget"])
        return {}

    engine = GraphEngine()
    engine.add_node("split", lambda s: {"branches": [1, 2]})
    engine.add_node("join", lambda s: {**s, "joined": True})
    engine.add_fanout("split", worker, "join")

    out = engine.run({})
    assert seen == [None, None]
    assert out["joined"] is True


def test_slice_exhaustion_stops_branch_not_parent():
    def hungry(payload, branch_state):
        budget = branch_state["branch"]["budget"]
        results = []
        for _ in range(10):
            allowed, _ = budget.plan_call()
            if not allowed:
                results.append("blocked")
                break
            budget.record({"total_tokens": 1})
            results.append("ok")
        return {"calls": results}

    engine = GraphEngine()
    engine.add_node("split", lambda s: {**s, "branches": ["only"]})
    engine.add_node("join", lambda s: {**s, "joined": True})
    engine.add_fanout("split", hungry, "join")

    parent = parent_budget(max_tokens=3)
    out = engine.run({"run_budget": parent})

    assert out["fanout_results"][0]["result"]["calls"] == ["ok", "ok", "ok", "blocked"]
    assert out["joined"] is True
    assert parent.usage.tokens == 3
    assert not parent.exhausted  # 切片耗尽不传染父


# ── 补充：维度数学与边界 ─────────────────────────────────


def test_reserve_cost_dimension():
    b = parent_budget(max_cost_usd=0.30)
    slices = b.reserve(3)
    for s in slices:
        assert abs(s.limits.max_cost_usd - 0.1) < 1e-9


def test_reserve_time_dimension():
    slices = parent_budget(max_seconds=9.0).reserve(3)
    for s in slices:
        # reserve 前有微秒级流逝（started_at 到调用点的间隔），放宽容差
        assert 2.99 < s.limits.max_seconds <= 3.0


def test_reserve_single_slice_gets_full_remaining():
    b = parent_budget(max_tokens=10)
    b.record({"total_tokens": 4})
    (s,) = b.reserve(1)
    assert s.limits.max_tokens == 6 and not s.exhausted


def test_absorb_no_children_is_noop():
    b = parent_budget(max_tokens=10)
    b.absorb()
    assert b.usage.tokens == 0


def test_exhaustion_reason_idempotent():
    b = parent_budget(max_tokens=5)
    b.record({"total_tokens": 5})
    first = b.plan_call()
    second = b.plan_call()
    assert first == second == (False, "budget:tokens")  # 老路径行为不变


def test_parent_exhausts_naturally_after_absorb():
    b = parent_budget(max_tokens=10)
    (s,) = b.reserve(1)
    s.record({"total_tokens": 10})  # 切片内合法用满
    b.absorb(s)
    assert b.plan_call() == (False, "budget:tokens")  # 父按自身限额自然判定


def test_plan_call_refuses_repeatedly_after_short_circuit():
    b = parent_budget(max_tokens=1)
    b.record({"total_tokens": 1})
    b.plan_call()
    assert b.exhausted
    assert b.plan_call() == (False, "budget:tokens")  # 短路后 reason 不丢


# ── 补充：fanout × 预算交互 ──────────────────────────────


def test_cancelled_branches_consume_nothing():
    def one_token(payload, branch_state):
        budget = branch_state["branch"]["budget"]
        budget.plan_call()
        budget.record({"total_tokens": 1})
        if payload == 1:
            raise GraphInterrupt("stop")
        return {}

    engine = GraphEngine()
    engine.add_node("split", lambda s: {**s, "branches": [1, 2, 3]})
    engine.add_node("join", lambda s: s)
    engine.add_fanout("split", one_token, "join", max_parallel=1)

    parent = parent_budget(max_tokens=100)
    engine.run({"run_budget": parent})
    assert parent.usage.tokens == 1  # 被取消的分支从未消费


def test_settle_happens_even_when_interrupted():
    """中断路径同样归还：已跑分支的消耗进父账（absorb 先于中断返回）。"""

    def worker(payload, branch_state):
        budget = branch_state["branch"]["budget"]
        budget.record({"total_tokens": 2})
        if payload == 1:
            raise GraphInterrupt("stop")
        return {}

    engine = GraphEngine()
    engine.add_node("split", lambda s: {**s, "branches": [1, 2]})
    engine.add_node("join", lambda s: s)
    engine.add_fanout("split", worker, "join", max_parallel=1)

    parent = parent_budget(max_tokens=100)
    out = engine.run({"run_budget": parent})
    assert out[INTERRUPT_KEY]["reason"] == "stop"
    assert parent.usage.tokens == 2


def test_branch_worker_plugs_real_llm_node_with_slice():
    """切片直接喂 make_llm_node：INV-9 路径在并行分支里原样成立。"""
    from strategy_research.kernel.runtimes import make_llm_node

    class FakeResp:
        content = "hi"
        usage = {"total_tokens": 2, "prompt_tokens": 1, "completion_tokens": 1}

    class FakeClient:
        def chat(self, messages, **kwargs):
            return FakeResp()

    def worker(payload, branch_state):
        node = make_llm_node(FakeClient(), budget=branch_state["branch"]["budget"])
        result = node({"messages": [{"role": "user", "content": str(payload)}]})
        return {"answer": result["answer"], "tokens": result["budget"]["total_tokens"]}

    engine = GraphEngine()
    engine.add_node("split", lambda s: {**s, "branches": ["q1", "q2", "q3"]})
    engine.add_node("join", lambda s: {**s, "joined": True})
    engine.add_fanout("split", worker, "join")

    parent = parent_budget(max_tokens=9)
    out = engine.run({"run_budget": parent})

    assert all(r["result"] == {"answer": "hi", "tokens": 2} for r in out["fanout_results"])
    assert parent.usage.tokens == 6  # 3 支 × 2 tokens，settle 回父
