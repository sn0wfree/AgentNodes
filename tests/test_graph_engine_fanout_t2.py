"""fanout t2（F4）测试 —— 进程池分支 / 硬杀 / 跨进程预算结算。

对应 docs/archive/2026-10/stage-f4-fanout-t2-design-20261027.md §7 验收。
worker 必须是模块级函数（spawn 池要求可导入）。
"""
from __future__ import annotations

import os
import time

import pytest

from strategy_research.kernel.agent.strategy.budget import BudgetLimits
from strategy_research.kernel.runtimes.executor_bridge import ExecutorBridge
from strategy_research.kernel.runtimes.graph_engine import (
    END,
    GraphEngine,
    INTERRUPT_KEY,
)
from strategy_research.kernel.runtimes.run_budget import RunBudget


# ── 模块级 worker（可 pickle）────────────────────────────


def echo_worker(payload, branch_state):
    return {"value": payload * 2, "pid": os.getpid()}


def failing_worker(payload, branch_state):
    if payload == 2:
        raise ValueError("回测崩了")
    return {"value": payload}


def budget_worker(payload, branch_state):
    budget = branch_state["branch"]["budget"]
    allowed, _ = budget.plan_call()
    assert allowed
    budget.record({"total_tokens": 3})
    return {"value": payload}


def slow_worker(payload, branch_state):
    time.sleep(payload.get("sleep", 0.1))
    return {"done": payload["name"]}


def interrupt_worker(payload, branch_state):
    if payload == "boom":
        from strategy_research.kernel.runtimes.graph_engine import GraphInterrupt

        raise GraphInterrupt("t2_stop", {"where": "child"})
    time.sleep(0.05)
    return {"v": payload}


@pytest.fixture
def bridge():
    b = ExecutorBridge(size=2)
    b.start()
    yield b
    b.shutdown(force=True)


def build_t2_engine(bridge, worker, *, max_parallel=2, cancel_timeout=5.0):
    engine = GraphEngine()
    engine.add_node("split", lambda s: {**s, "branches": s["payloads"]})
    engine.add_node("join", lambda s: {**s, "joined": True})
    engine.add_fanout(
        "split", worker, "join", mode="t2",
        executor=bridge, max_parallel=max_parallel, cancel_timeout=cancel_timeout,
    )
    engine.add_edge("join", END)
    return engine


# ── 声明校验 ─────────────────────────────────────────────


def test_t2_requires_executor():
    engine = GraphEngine()
    engine.add_node("split", lambda s: s)
    engine.add_node("join", lambda s: s)
    with pytest.raises(ValueError, match="需要 executor"):
        engine.add_fanout("split", echo_worker, "join", mode="t2")


def test_t1_rejects_executor(bridge):
    engine = GraphEngine()
    engine.add_node("split", lambda s: s)
    engine.add_node("join", lambda s: s)
    with pytest.raises(ValueError, match="executor 只用于"):
        engine.add_fanout("split", echo_worker, "join", mode="t1", executor=bridge)


def test_invalid_mode_rejected():
    engine = GraphEngine()
    engine.add_node("split", lambda s: s)
    engine.add_node("join", lambda s: s)
    with pytest.raises(ValueError, match="mode 必须是"):
        engine.add_fanout("split", echo_worker, "join", mode="t3")


# ── 正常路径 ─────────────────────────────────────────────


def test_t2_branches_run_in_child_processes(bridge):
    engine = build_t2_engine(bridge, echo_worker)
    out = engine.run({"payloads": [1, 2, 3]})

    assert out["joined"] is True
    results = out["fanout_results"]
    assert [r["status"] for r in results] == ["ok", "ok", "ok"]
    assert [r["result"]["value"] for r in results] == [2, 4, 6]
    # 结果确实来自子进程（PID 与主进程不同）
    pids = {r["result"]["pid"] for r in results}
    assert os.getpid() not in pids


def test_t2_results_ordered_by_ordinal(bridge):
    engine = GraphEngine()
    engine.add_node(
        "split",
        lambda s: {**s, "branches": [{"name": "slow", "sleep": 0.15},
                                     {"name": "fast", "sleep": 0.0}]},
    )
    engine.add_node("join", lambda s: {**s, "joined": True})
    engine.add_fanout("split", slow_worker, "join", mode="t2", executor=bridge)

    out = engine.run({})
    assert [r["result"]["done"] for r in out["fanout_results"]] == ["slow", "fast"]


def test_t2_branch_ids_follow_tree_scheme(bridge):
    engine = build_t2_engine(bridge, echo_worker)
    out = engine.run({"run_id": "turn9:r0", "payloads": [1, 2]})
    assert [r["branch_id"] for r in out["fanout_results"]] == ["turn9:r0:1", "turn9:r0:2"]


# ── 业务失败不连坐 ───────────────────────────────────────


def test_t2_business_error_does_not_cascade(bridge):
    engine = build_t2_engine(bridge, failing_worker)
    out = engine.run({"payloads": [1, 2, 3]})

    statuses = [r["status"] for r in out["fanout_results"]]
    assert statuses == ["ok", "error", "ok"]
    assert "回测崩了" in out["fanout_results"][1]["error"]
    assert out["joined"] is True
    assert INTERRUPT_KEY not in out


# ── 中断连坐与硬杀 ───────────────────────────────────────


def test_t2_interrupt_cascades_and_skips_join(bridge):
    engine = build_t2_engine(bridge, interrupt_worker, max_parallel=1)
    out = engine.run({"payloads": ["boom", "ok"]})

    assert INTERRUPT_KEY in out
    assert out[INTERRUPT_KEY]["reason"] == "t2_stop"
    assert "joined" not in out
    statuses = {r["ordinal"]: r["status"] for r in out["fanout_results"]}
    assert statuses[1] == "interrupted"
    assert statuses[2] == "cancelled"


def test_t2_hard_kill_upgrades_when_cooperative_fails():
    """长跑 job：协作取消超时后 terminate，池自愈重建、后续 job 正常。"""
    from strategy_research.kernel.runtimes.executor_bridge import JobSpec

    b = ExecutorBridge(size=1)
    b.start()
    try:
        b.dispatch(JobSpec(job_id="long", fn=slow_worker,
                           args=({"name": "long", "sleep": 30}, {})))
        time.sleep(0.4)  # 让 job 起跑
        started = time.perf_counter()
        b.cancel(hard_timeout=0.3)   # 协作取消 → 超时 → terminate
        b.discard_pending()
        assert time.perf_counter() - started < 5  # 没有等满 30s

        # 池自愈：ensure_alive 重建后新 job 正常（短超时防测试挂死）
        b.ensure_alive()
        result = b.run(JobSpec(job_id="post-kill", fn=echo_worker, args=(5, {}),
                               timeout=15.0))
        assert result.ok, result.error
        assert result.value["value"] == 10
    finally:
        b.shutdown(force=True)


# ── 跨进程预算 ───────────────────────────────────────────


def test_t2_budget_usage_settles_to_parent(bridge):
    engine = build_t2_engine(bridge, budget_worker)
    parent = RunBudget(limits=BudgetLimits(max_tokens=90))
    out = engine.run({"payloads": [1, 2, 3], "run_budget": parent})

    assert out["joined"] is True
    # 每支记 3 tokens（子进程回传 _budget_usage），3 支 settle 回父
    assert parent.usage.tokens == 9


def test_t2_starved_slice_interrupts_before_dispatch(bridge):
    """父预算 1 切 3 → 全 0 → 第一支预检即中断，不派发任何 job。"""
    engine = build_t2_engine(bridge, budget_worker)
    parent = RunBudget(limits=BudgetLimits(max_tokens=1))
    out = engine.run({"payloads": [1, 2, 3], "run_budget": parent})

    assert INTERRUPT_KEY in out
    assert out[INTERRUPT_KEY]["reason"] == "branch_budget_exhausted"
    statuses = [r["status"] for r in out["fanout_results"]]
    assert statuses == ["interrupted", "cancelled", "cancelled"]
    assert parent.usage.tokens == 0  # 一个 job 都没跑


def test_t2_no_budget_key_is_noop(bridge):
    engine = build_t2_engine(bridge, echo_worker)
    out = engine.run({"payloads": [1]})
    assert out["joined"] is True and out["fanout_results"][0]["status"] == "ok"
