"""GraphEngine 阶段图测试 —— 条件边 / 环 / 协作中断 / max_hops / 恢复。

对应 docs/archive/2026-10/stage-b1a-graph-engine-design-20261027.md §6 验收。
线性语义 / 事件顺序 / 异常冒出等基础行为由 test_graph_engine.py 覆盖。
"""
from __future__ import annotations

import pytest

from agentnodes.kernel.runtimes.graph_engine import (
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

    def kinds(self) -> list[str]:
        return [k for k, _ in self.events]

    def started(self) -> list[str]:
        return [d["node"] for k, d in self.events if k == "node.started"]

    def of(self, kind: str) -> list[dict]:
        return [d for k, d in self.events if k == kind]


# ── 条件边 ────────────────────────────────────────────────


def test_conditional_route_takes_branch():
    engine = GraphEngine()
    engine.add_node("gate", lambda s: {**s, "flag": "b"})
    engine.add_node("branch_a", lambda s: {**s, "a": True})
    engine.add_node("branch_b", lambda s: {**s, "b": True})

    engine.add_conditional_edge("gate", lambda s: s["flag"], {"a": "branch_a", "b": "branch_b"})
    engine.add_edge("branch_a", END)
    engine.add_edge("branch_b", END)

    out = engine.run({})
    assert out.get("b") is True and "a" not in out


def test_conditional_route_to_end_skips_downstream():
    """novelty 形态：gate 判 dup → END，下游节点从未执行。"""
    probe = Probe()
    engine = GraphEngine(sink=probe)
    engine.add_node("gate", lambda s: {**s, "novel": False})
    engine.add_node("eval", lambda s: {**s, "evaluated": True})

    engine.add_conditional_edge(
        "gate", lambda s: "novel" if s["novel"] else "dup",
        {"novel": "eval", "dup": END},
    )

    out = engine.run({})
    assert "evaluated" not in out
    assert probe.started() == ["gate"]


def test_router_without_mapping_returns_node_directly():
    engine = GraphEngine()
    engine.add_node("pick", lambda s: {**s, "choice": "second"})
    engine.add_node("first", lambda s: {**s, "got": "first"})
    engine.add_node("second", lambda s: {**s, "got": "second"})

    engine.add_conditional_edge("pick", lambda s: s["choice"])
    engine.add_edge("first", END)
    engine.add_edge("second", END)

    assert engine.run({})["got"] == "second"


def test_router_unknown_key_fails_loud():
    engine = GraphEngine()
    engine.add_node("gate", lambda s: s)
    engine.add_node("next", lambda s: s)
    engine.add_conditional_edge("gate", lambda s: "unexpected", {"expected": "next"})

    with pytest.raises(ValueError, match="未知 key.*unexpected"):
        engine.run({})


def test_edge_to_unregistered_node_rejected_at_declaration():
    engine = GraphEngine()
    engine.add_node("a", lambda s: s)
    with pytest.raises(ValueError, match="add_edge target.*ghost"):
        engine.add_edge("a", "ghost")


def test_conditional_mapping_bad_target_rejected_at_declaration():
    engine = GraphEngine()
    engine.add_node("a", lambda s: s)
    with pytest.raises(ValueError, match="mapping.*ghost"):
        engine.add_conditional_edge("a", lambda s: "k", {"k": "ghost"})


def test_declared_edge_overrides_default_line():
    """默认直线是 a→b→c；声明 b→c 被覆盖为 b→END 后，c 不再默认可达。"""
    probe = Probe()
    engine = GraphEngine(sink=probe)
    engine.add_node("a", lambda s: s)
    engine.add_node("b", lambda s: s)
    engine.add_node("c", lambda s: {**s, "c_ran": True})
    engine.add_edge("b", END)

    out = engine.run({})
    assert "c_ran" not in out
    assert probe.started() == ["a", "b"]


# ── 环（两阶段图验收）─────────────────────────────────────


def test_two_stage_graph_with_retry_loop():
    """验收用例：gen → judge 条件路由，judge 判 retry 回 gen（环），第二次通过。"""
    probe = Probe()
    engine = GraphEngine(sink=probe)

    def gen(state: dict) -> dict:
        attempts = state.get("attempts", 0) + 1
        return {**state, "attempts": attempts, "hypothesis": f"h{attempts}"}

    def judge(state: dict) -> str:
        return "ok" if state["attempts"] >= 2 else "retry"

    engine.add_node("gen", gen)
    engine.add_node("judge", lambda s: s)
    # judge 默认边是 END（末节点），只有 retry 分支需要显式声明
    engine.add_conditional_edge("gen", judge, {"ok": "judge", "retry": "gen"})

    out = engine.run({})
    assert out["attempts"] == 2
    assert out["hypothesis"] == "h2"
    assert probe.started().count("gen") == 2
    assert probe.started().count("judge") == 1


def test_cycle_state_accumulates_across_loops():
    engine = GraphEngine()
    engine.add_node("work", lambda s: {**s, "log": s.get("log", []) + [s.get("i", 0)]})
    engine.add_node(
        "incr",
        lambda s: {**s, "i": s.get("i", 0) + 1},
    )
    engine.add_conditional_edge("incr", lambda s: "work" if s["i"] < 3 else END)
    engine.add_edge("work", "incr")

    out = engine.run({})
    assert out["i"] == 3
    assert out["log"] == [0, 1, 2]


# ── 协作中断 ─────────────────────────────────────────────


def test_interrupt_stops_at_boundary_and_preserves_state():
    probe = Probe()
    engine = GraphEngine(sink=probe)

    def bad(state: dict) -> dict:
        raise GraphInterrupt("novelty_rejected")

    engine.add_node("good", lambda s: {**s, "value": 1})
    engine.add_node("bad", bad)
    engine.add_node("never", lambda s: {**s, "never": True})
    # 全部固定边：证明守卫对固定边同样生效
    engine.add_edge("good", "bad")
    engine.add_edge("bad", "never")
    engine.add_edge("never", END)

    out = engine.run({})
    assert out["value"] == 1  # 已完成节点的结果保留
    assert "never" not in out  # 中断后下游不跑
    assert out[INTERRUPT_KEY] == {"node": "bad", "reason": "novelty_rejected"}


def test_interrupt_event_semantics():
    """中断节点：started 有、completed 无、graph.interrupted 发出。"""
    probe = Probe()
    engine = GraphEngine(sink=probe)

    def stopper(state: dict) -> dict:
        raise GraphInterrupt("budget_exhausted")

    engine.add_node("a", lambda s: s)
    engine.add_node("stopper", stopper)
    engine.add_edge("a", "stopper")
    engine.add_edge("stopper", END)

    engine.run({})
    assert probe.started() == ["a", "stopper"]
    assert probe.of("node.completed") and probe.of("node.completed")[0]["node"] == "a"
    interrupted = probe.of("graph.interrupted")
    assert len(interrupted) == 1
    assert interrupted[0] == {"node": "stopper", "reason": "budget_exhausted"}


def test_interrupt_carries_data_payload():
    engine = GraphEngine()

    def stopper(state: dict) -> dict:
        raise GraphInterrupt("paused_for_approval", {"awaiting": "human"})

    engine.add_node("stopper", stopper)
    engine.add_edge("stopper", END)

    out = engine.run({})
    assert out[INTERRUPT_KEY]["data"] == {"awaiting": "human"}


def test_interrupted_state_stays_interrupted_on_rerun():
    """带 __interrupt__ 的 state 重跑：首跳即 END，不静默恢复。"""
    probe = Probe()
    engine = GraphEngine(sink=probe)
    engine.add_node("a", lambda s: {**s, "ran": True})
    engine.add_edge("a", END)

    out = engine.run({INTERRUPT_KEY: {"node": "x", "reason": "prev"}})
    assert "ran" not in out
    assert probe.started() == []


def test_plain_exception_still_propagates():
    def boom(state: dict) -> dict:
        raise RuntimeError("node failed")

    engine = GraphEngine()
    engine.add_node("boom", boom)
    engine.add_edge("boom", END)

    with pytest.raises(RuntimeError, match="node failed"):
        engine.run({})


# ── max_hops 防环失控 ────────────────────────────────────


def test_max_hops_cooperative_stop():
    probe = Probe()
    engine = GraphEngine(sink=probe, max_hops=5)
    engine.add_node("spin", lambda s: {**s, "n": s.get("n", 0) + 1})
    engine.add_edge("spin", "spin")  # 自环

    out = engine.run({})
    assert out[INTERRUPT_KEY]["reason"] == "max_hops"
    assert out["n"] == 5  # 前 5 次正常执行，第 6 跃点被拦
    assert probe.started().count("spin") == 5
    assert probe.of("graph.interrupted")[0]["node"] == "spin"


def test_max_hops_default_allows_research_loops():
    engine = GraphEngine()  # 默认 50
    engine.add_node("spin", lambda s: {**s, "n": s.get("n", 0) + 1})
    engine.add_conditional_edge("spin", lambda s: "spin" if s["n"] < 30 else END)

    out = engine.run({})
    assert out["n"] == 30
    assert INTERRUPT_KEY not in out


# ── 入口与恢复 ───────────────────────────────────────────


def test_set_entry_changes_starting_node():
    probe = Probe()
    engine = GraphEngine(sink=probe)
    engine.add_node("first", lambda s: {**s, "first_ran": True})
    engine.add_node("second", lambda s: {**s, "second_ran": True})
    engine.set_entry("second")

    out = engine.run({})
    assert out.get("second_ran") is True
    assert "first_ran" not in out
    assert probe.started() == ["second"]


def test_resume_from_node_with_start():
    """恢复：pop 中断标记后用 start= 从指定节点续跑，跳过上游。"""
    probe = Probe()
    engine = GraphEngine(sink=probe)

    def gate(state: dict) -> dict:
        if not state.get("approved"):
            raise GraphInterrupt("paused_for_approval")
        return state

    engine.add_node("gate", gate)
    engine.add_node("act", lambda s: {**s, "acted": True})
    engine.add_edge("gate", "act")
    engine.add_edge("act", END)

    state = {"approved": False}
    first = engine.run(state)
    assert first[INTERRUPT_KEY]["reason"] == "paused_for_approval"

    # 恢复：解除标记 + 补上人工输入，从 act 直接续跑
    resumed = dict(first)
    resumed.pop(INTERRUPT_KEY)
    resumed["approved"] = True
    second = engine.run(resumed, start="act")
    assert second["acted"] is True
    assert INTERRUPT_KEY not in second


# ── 组合与回归 ───────────────────────────────────────────


def test_input_state_not_mutated_by_stage_graph():
    original = {"flag": "b", "log": [1]}
    snapshot = {"flag": "b", "log": [1]}
    engine = GraphEngine()
    engine.add_node("gate", lambda s: {**s, "touched": True})
    engine.add_node("branch_a", lambda s: {**s, "log": s["log"] + ["a"]})
    engine.add_node("branch_b", lambda s: {**s, "log": s["log"] + ["b"]})
    engine.add_conditional_edge("gate", lambda s: s["flag"], {"a": "branch_a", "b": "branch_b"})
    engine.add_edge("branch_a", END)
    engine.add_edge("branch_b", END)

    out = engine.run(original)
    assert original == snapshot
    assert out["log"] == [1, "b"]


def test_engine_reusable_across_runs_with_loops():
    engine = GraphEngine()

    def gen(state: dict) -> dict:
        return {**state, "n": state.get("n", 0) + 1}

    engine.add_node("gen", gen)
    engine.add_conditional_edge("gen", lambda s: "gen" if s["n"] < 2 else END)

    assert engine.run({})["n"] == 2
    assert engine.run({})["n"] == 2  # 跃点计数按次运行重置


def test_end_sentinel_is_string_and_usable_in_mapping():
    from agentnodes.kernel.runtimes import END as PKG_END

    assert PKG_END == END == "__end__"


def test_interrupt_key_reserved_via_duplicate_node_guard():
    """注册守卫：END 不能当节点名；重复注册报错（构图期 fail-fast）。"""
    engine = GraphEngine()
    engine.add_node("a", lambda s: s)
    with pytest.raises(ValueError, match="已注册"):
        engine.add_node("a", lambda s: s)
    with pytest.raises(ValueError, match="END"):
        engine.add_node(END, lambda s: s)
