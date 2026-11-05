"""fanout 复合节点（F1）测试 —— 并行执行 / 账本隔离 / 两档失败 / 连坐取消。

对应 docs/archive/2026-10/stage-f1-fanout-design-20261027.md §3 语义契约。
"""
from __future__ import annotations

import threading
import time

import pytest

from strategy_research.kernel.runtimes.graph_engine import (
    END,
    GraphEngine,
    GraphInterrupt,
    INTERRUPT_KEY,
)


class Probe:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def __call__(self, kind: str, data: dict) -> None:
        self.events.append((kind, data))

    def of(self, kind: str) -> list[dict]:
        return [d for k, d in self.events if k == kind]


def double_worker(payload, branch_state):
    return {"value": payload * 2}


def build_fanout_engine(sink=None, worker=double_worker, max_parallel=8):
    engine = GraphEngine(sink=sink)
    engine.add_node(
        "split",
        lambda s: {**s, "branches": list(s.get("payloads", [1, 2, 3]))},
    )
    engine.add_node(
        "join",
        lambda s: {**s, "joined": True, "n_results": len(s["fanout_results"])},
    )
    engine.add_fanout("split", worker, "join", max_parallel=max_parallel)
    return engine


# ── 正常路径 ─────────────────────────────────────────────


def test_fanout_runs_all_branches_and_join_receives_results():
    out = build_fanout_engine().run({"run_id": "t1:r0"})
    assert out["joined"] is True
    assert out["n_results"] == 3
    results = out["fanout_results"]
    assert [r["status"] for r in results] == ["ok", "ok", "ok"]
    assert [r["result"]["value"] for r in results] == [2, 4, 6]


def test_results_ordered_by_ordinal_not_completion():
    def uneven(payload, branch_state):
        time.sleep(payload["delay"])
        return {"who": payload["name"]}

    engine = GraphEngine()
    engine.add_node(
        "split",
        lambda s: {"branches": [
            {"name": "slow", "delay": 0.08},
            {"name": "fast", "delay": 0.0},
        ]},
    )
    engine.add_node("join", lambda s: {**s, "joined": True})
    engine.add_fanout("split", uneven, "join", max_parallel=4)

    out = engine.run({})
    assert [r["payload"]["name"] for r in out["fanout_results"]] == ["slow", "fast"]
    assert out["fanout_results"][1]["result"] == {"who": "fast"}


def test_branch_ids_follow_tree_scheme():
    out = build_fanout_engine().run({"run_id": "turn5:r0"})
    ids = [r["branch_id"] for r in out["fanout_results"]]
    assert ids == ["turn5:r0:1", "turn5:r0:2", "turn5:r0:3"]


def test_worker_receives_branch_context():
    seen = {}

    def spy(payload, branch_state):
        seen.update(branch_state["branch"])
        return {}

    build_fanout_engine(worker=spy).run({"run_id": "r", "payloads": ["x"]})
    # budget 字段由 F2 加入：state 无 run_budget 约定键时为 None
    assert seen == {"id": "r:1", "ordinal": 1, "payload": "x", "budget": None}


def test_branch_state_is_snapshot_not_shared():
    def mutator(payload, branch_state):
        branch_state["tainted"] = True  # 改快照
        return {"got": branch_state.get("base")}

    engine = GraphEngine()
    engine.add_node("split", lambda s: {**s, "branches": [1, 2], "base": "v"})
    engine.add_node("join", lambda s: {**s, "joined": True})
    engine.add_fanout("split", mutator, "join")

    out = engine.run({})
    assert "tainted" not in out  # 分支对快照的变异不回流父账本
    assert out["base"] == "v"
    assert all(r["result"] == {"got": "v"} for r in out["fanout_results"])


def test_zero_branches_join_runs_with_empty():
    engine = GraphEngine()
    engine.add_node("split", lambda s: {**s, "branches": []})
    engine.add_node("join", lambda s: {**s, "n": len(s["fanout_results"])})
    engine.add_fanout("split", double_worker, "join")
    assert engine.run({})["n"] == 0


def test_fanout_inside_linear_graph_full_order():
    probe = Probe()
    engine = GraphEngine(sink=probe)
    engine.add_node("a", lambda s: {**s, "ran": s.get("ran", []) + ["a"]})
    engine.add_node("split", lambda s: {**s, "branches": [1], "ran": s["ran"] + ["split"]})
    engine.add_node("join", lambda s: {**s, "ran": s["ran"] + ["join"]})
    engine.add_node("c", lambda s: {**s, "ran": s["ran"] + ["c"]})
    engine.add_fanout("split", double_worker, "join")
    engine.add_edge("join", "c")

    out = engine.run({})
    assert out["ran"] == ["a", "split", "join", "c"]


# ── 业务失败：不连坐 ─────────────────────────────────────


def test_business_error_does_not_stop_siblings():
    def flaky(payload, branch_state):
        if payload == 2:
            raise ValueError("回测崩了")
        return {"v": payload}

    probe = Probe()
    out = build_fanout_engine(sink=probe, worker=flaky).run({})

    statuses = [r["status"] for r in out["fanout_results"]]
    assert statuses == ["ok", "error", "ok"]
    assert out["fanout_results"][1]["error"] == "回测崩了"
    assert out["joined"] is True  # 汇合照常——策略（abort_turn）属 preset 层
    assert INTERRUPT_KEY not in out


# ── 系统中断：连坐 ───────────────────────────────────────


def test_interrupt_cancels_queued_branches_and_skips_join():
    calls: list[int] = []

    def budget_bomb(payload, branch_state):
        calls.append(payload)
        if payload == 1:
            raise GraphInterrupt("budget_exhausted")
        return {}

    probe = Probe()
    out = build_fanout_engine(sink=probe, worker=budget_bomb, max_parallel=1).run({})

    statuses = [r["status"] for r in out["fanout_results"]]
    assert statuses == ["interrupted", "cancelled", "cancelled"]
    assert calls == [1]  # 后续分支从未起跑
    assert "joined" not in out  # join 不执行，路由守卫送 END
    assert out[INTERRUPT_KEY]["reason"] == "budget_exhausted"
    # state 无 run_id：分支 id 退化为 {节点名}:b{n}
    assert out[INTERRUPT_KEY]["data"]["branch_id"] == "split:b1"
    kinds = [k for k, _ in probe.events]
    assert kinds.count("graph.interrupted") == 1


def test_interrupt_state_carries_partial_results():
    def bomb_on_third(payload, branch_state):
        if payload == 3:
            raise GraphInterrupt("hitl_pause")
        return {"v": payload}

    engine = GraphEngine()
    engine.add_node("split", lambda s: {"branches": [1, 2, 3]})
    engine.add_node("join", lambda s: {**s, "joined": True})
    engine.add_fanout("split", bomb_on_third, "join", max_parallel=1)

    out = engine.run({})
    assert out[INTERRUPT_KEY]["reason"] == "hitl_pause"
    statuses = [r["status"] for r in out["fanout_results"]]
    assert statuses == ["ok", "ok", "interrupted"]  # 已完成的结果保留


# ── 并发度与事件 ─────────────────────────────────────────


def test_max_parallel_bound_respected():
    lock = threading.Lock()
    active = {"n": 0, "peak": 0}

    def slow(payload, branch_state):
        with lock:
            active["n"] += 1
            active["peak"] = max(active["peak"], active["n"])
        time.sleep(0.05)
        with lock:
            active["n"] -= 1
        return {}

    build_fanout_engine(worker=slow, max_parallel=2).run({"payloads": [1, 2, 3, 4]})
    assert active["peak"] == 2


def test_events_all_from_parent_thread_in_order():
    probe = Probe()
    out = build_fanout_engine(sink=probe).run({})
    kinds = [k for k, _ in probe.events]
    assert kinds == [
        "node.started",       # split
        "graph.fanout",
        "branch.settled", "branch.settled", "branch.settled",
        "node.completed",     # split
        "node.started",       # join
        "node.completed",     # join
    ]
    settled = probe.of("branch.settled")
    # 事件按落定顺序（as_completed），不保证序号序；结果列表才按 ordinal 保序
    assert sorted(s["ordinal"] for s in settled) == [1, 2, 3]
    assert all(s["node"] == "split" for s in settled)
    assert out["fanout_results"][0]["seconds"] >= 0.0


# ── 声明期与运行期校验 ───────────────────────────────────


def test_add_fanout_validation_errors():
    engine = GraphEngine()
    engine.add_node("split", lambda s: s)
    engine.add_node("join", lambda s: s)

    with pytest.raises(ValueError, match="add_fanout join.*ghost"):
        engine.add_fanout("split", double_worker, "ghost")
    with pytest.raises(ValueError, match="同一节点"):
        engine.add_fanout("split", double_worker, "split")
    with pytest.raises(ValueError, match="t2.*需要 executor"):
        engine.add_fanout("split", double_worker, "join", mode="t2")
    with pytest.raises(ValueError, match="max_parallel"):
        engine.add_fanout("split", double_worker, "join", max_parallel=0)
    with pytest.raises(TypeError, match="worker"):
        engine.add_fanout("split", "not_callable", "join")

    engine.add_fanout("split", double_worker, "join")
    with pytest.raises(ValueError, match="已声明 fanout"):
        engine.add_fanout("split", double_worker, "join")


def test_split_returning_non_list_fails_loud():
    engine = GraphEngine()
    engine.add_node("split", lambda s: {**s, "branches": "not_a_list"})
    engine.add_node("join", lambda s: s)
    engine.add_fanout("split", double_worker, "join")

    with pytest.raises(ValueError, match="branches.*list"):
        engine.run({})


def test_fanout_forces_source_outedge_to_join():
    """声明即覆盖：split 的默认直线边被 add_fanout 改指 join。"""
    engine = GraphEngine()
    engine.add_node("split", lambda s: {**s, "branches": []})
    engine.add_node("bypass", lambda s: {**s, "bypassed": True})
    engine.add_node("join", lambda s: {**s, "joined": True})
    engine.add_fanout("split", double_worker, "join")
    engine.add_edge("join", END)

    out = engine.run({})
    assert "bypassed" not in out
    assert out["joined"] is True


# ── 补充：防御路径与组合行为 ─────────────────────────────


def test_worker_returning_interrupt_marker_treated_as_interrupt():
    """防御路径：worker 用返回值夹带 __interrupt__，按中断处理（不炸线程）。"""

    def sneaky(payload, branch_state):
        if payload == 1:
            return {INTERRUPT_KEY: {"reason": " smuggled"}}
        return {}

    out = build_fanout_engine(worker=sneaky, max_parallel=1).run({})
    statuses = [r["status"] for r in out["fanout_results"]]
    assert statuses == ["interrupted", "cancelled", "cancelled"]
    assert "joined" not in out


def test_resume_after_fanout_interrupt_from_join():
    """恢复模式：中断后 pop 标记，start=join 直接消费已保留的部分结果。"""

    def bomb_on_two(payload, branch_state):
        if payload == 2:
            raise GraphInterrupt("hitl_pause")
        return {"v": payload * 10}

    engine = GraphEngine()
    engine.add_node("split", lambda s: {**s, "branches": [1, 2, 3]})
    engine.add_node("join", lambda s: {**s, "joined": True})
    engine.add_fanout("split", bomb_on_two, "join", max_parallel=1)

    first = engine.run({})
    assert INTERRUPT_KEY in first
    partial = dict(first)
    partial.pop(INTERRUPT_KEY)

    resumed = engine.run(partial, start="join")
    assert resumed["joined"] is True
    statuses = [r["status"] for r in resumed["fanout_results"]]
    assert statuses == ["ok", "interrupted", "cancelled"]  # 历史条目原样保留


def test_two_fanouts_in_one_graph():
    def tag(payload, branch_state):
        return {"stage": branch_state["branch"]["id"], "v": payload}

    engine = GraphEngine()
    engine.add_node("split1", lambda s: {**s, "branches": [1, 2]})
    engine.add_node("join1", lambda s: {**s, "n1": len(s["fanout_results"])})
    engine.add_node("split2", lambda s: {**s, "branches": [10, 20, 30]})
    engine.add_node("join2", lambda s: {**s, "n2": len(s["fanout_results"])})
    engine.add_fanout("split1", tag, "join1")
    engine.add_fanout("split2", tag, "join2")

    out = engine.run({})
    assert out["n1"] == 2 and out["n2"] == 3  # join1 默认直线边 → split2
    # state 无 run_id：分支 id 退化 {节点名}:b{n}
    assert out["fanout_results"][0]["branch_id"] == "split2:b1"


def test_fanout_loop_participates_in_max_hops():
    """split→join 回边成环：每圈 2 跃点，max_hops 到顶协作停止。"""
    engine = GraphEngine(max_hops=6)
    engine.add_node(
        "split",
        lambda s: {**s, "laps": s.get("laps", 0) + 1, "branches": [1]},
    )
    engine.add_node("join", lambda s: s)
    engine.add_fanout("split", double_worker, "join")
    engine.add_edge("join", "split")  # 回边

    out = engine.run({})
    assert out[INTERRUPT_KEY]["reason"] == "max_hops"
    assert out["laps"] == 3  # 6 跃点 = 3 圈整


def test_conditional_edge_before_fanout_is_overridden():
    engine = GraphEngine()
    engine.add_node("split", lambda s: {**s, "branches": [], "pick": "join"})
    engine.add_node("join", lambda s: {**s, "joined": True})
    engine.add_node("other", lambda s: {**s, "othered": True})
    engine.add_conditional_edge(
        "split", lambda s: s["pick"], {"join": "join", "other": "other"}
    )
    engine.add_fanout("split", double_worker, "join")  # 后声明，赢
    engine.add_edge("join", END)  # join 的默认直线边否则会流到 other
    engine.add_edge("other", END)

    out = engine.run({})
    assert out["joined"] is True and "othered" not in out


def test_conditional_edge_after_fanout_overrides_fanout_routing():
    """最后声明者赢：fanout 照跑（结果保留），但路由让位给后声明的条件边。"""
    engine = GraphEngine()
    engine.add_node("split", lambda s: {**s, "branches": [1], "go": "END"})
    engine.add_node("join", lambda s: {**s, "joined": True})
    engine.add_fanout("split", double_worker, "join")
    engine.add_conditional_edge(
        "split", lambda s: s["go"], {"join": "join", "END": END}
    )

    out = engine.run({})
    assert "joined" not in out  # 路由被覆盖：没去 join
    assert out["fanout_results"][0]["status"] == "ok"  # 分支照跑，结果在


def test_cancelled_entries_carry_payload_echo():
    def bomb_first(payload, branch_state):
        if payload == "a":
            raise GraphInterrupt("stop")
        return {}

    engine = GraphEngine()
    engine.add_node("split", lambda s: {**s, "branches": ["a", "b", "c"]})
    engine.add_node("join", lambda s: s)
    engine.add_fanout("split", bomb_first, "join", max_parallel=1)

    out = engine.run({})
    cancelled = [r for r in out["fanout_results"] if r["status"] == "cancelled"]
    assert [r["payload"] for r in cancelled] == ["b", "c"]  # 回显载荷供 join 判断


def test_fanout_event_payloads_complete():
    probe = Probe()
    build_fanout_engine(sink=probe, max_parallel=2).run({"payloads": [1, 2, 3]})
    fanout_ev = probe.of("graph.fanout")
    assert fanout_ev == [{"node": "split", "branches": 3, "max_parallel": 2}]
    for s in probe.of("branch.settled"):
        assert s["node"] == "split" and s["seconds"] >= 0.0
        assert s["status"] == "ok" and s["branch_id"].startswith("split:b")


def test_max_parallel_one_runs_strictly_sequentially():
    order: list[str] = []

    def sequenced(payload, branch_state):
        order.append(f"start{payload}")
        order.append(f"end{payload}")
        return {}

    build_fanout_engine(worker=sequenced, max_parallel=1).run({})
    assert order == ["start1", "end1", "start2", "end2", "start3", "end3"]


def test_interrupted_entry_carries_seconds():
    def bomb(payload, branch_state):
        raise GraphInterrupt("x")

    out = build_fanout_engine(worker=bomb, max_parallel=1).run({})
    entry = out["fanout_results"][0]
    assert entry["status"] == "interrupted" and entry["seconds"] >= 0.0
