"""GraphEngine 单元测试 —— 节点顺序 / 事件 / state 传递 / 异常。"""
from __future__ import annotations

import pytest

from agentnodes.kernel.runtimes.graph_engine import GraphEngine


class Probe:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def __call__(self, kind: str, data: dict) -> None:
        self.events.append((kind, data))

    def kinds(self) -> list[str]:
        return [k for k, _ in self.events]


def test_nodes_run_in_registration_order():
    order: list[str] = []
    engine = GraphEngine()
    engine.add_node("a", lambda s: {**s, "order": s.get("order", []) + ["a"]})
    engine.add_node("b", lambda s: {**s, "order": s.get("order", []) + ["b"]})
    engine.add_node("c", lambda s: {**s, "order": s.get("order", []) + ["c"]})
    out = engine.run({})
    assert out["order"] == ["a", "b", "c"]


def test_state_flows_between_nodes():
    engine = GraphEngine()
    engine.add_node("first", lambda s: {**s, "value": 1})
    engine.add_node("second", lambda s: {**s, "doubled": s["value"] * 2})
    out = engine.run({})
    assert out["doubled"] == 2 and out["value"] == 1


def test_single_node_works():
    engine = GraphEngine()
    engine.add_node("only", lambda s: {**s, "done": True})
    assert engine.run({})["done"] is True


def test_events_emitted_per_node_in_order():
    probe = Probe()
    engine = GraphEngine(sink=probe)
    engine.add_node("x", lambda s: s)
    engine.add_node("y", lambda s: s)
    engine.run({})
    assert probe.kinds() == [
        "node.started", "node.completed",
        "node.started", "node.completed",
    ]
    assert [d["node"] for k, d in probe.events if k == "node.started"] == ["x", "y"]


def test_completed_event_carries_seconds():
    probe = Probe()
    engine = GraphEngine(sink=probe)
    engine.add_node("n", lambda s: s)
    engine.run({})
    completed = [d for k, d in probe.events if k == "node.completed"]
    assert len(completed) == 1
    assert isinstance(completed[0]["seconds"], float)
    assert completed[0]["seconds"] >= 0.0


def test_no_nodes_raises():
    with pytest.raises(ValueError, match="至少需要一个节点"):
        GraphEngine().run({})


def test_node_exception_propagates_and_stops():
    probe = Probe()
    engine = GraphEngine(sink=probe)

    def boom(state: dict) -> dict:
        raise RuntimeError("node failed")

    engine.add_node("ok", lambda s: s)
    engine.add_node("bad", boom)
    engine.add_node("never", lambda s: {**s, "reached": True})

    with pytest.raises(RuntimeError, match="node failed"):
        engine.run({})
    # bad 的 started 已发，completed 未发；never 从未开始
    started = [d["node"] for k, d in probe.events if k == "node.started"]
    completed = [d["node"] for k, d in probe.events if k == "node.completed"]
    assert started == ["ok", "bad"]
    assert completed == ["ok"]


def test_input_state_not_mutated_by_engine():
    original = {"messages": [{"role": "user", "content": "q"}], "n": 1}
    snapshot = {"messages": [dict(m) for m in original["messages"]], "n": 1}
    engine = GraphEngine()
    engine.add_node("mutate", lambda s: {**s, "n": 999})
    engine.run(original)
    assert original == snapshot


def test_engine_reusable_across_runs():
    counter = {"n": 0}
    engine = GraphEngine()

    def inc(state: dict) -> dict:
        counter["n"] += 1
        return {**state, "n": counter["n"]}

    engine.add_node("inc", inc)
    assert engine.run({})["n"] == 1
    assert engine.run({})["n"] == 2  # 同一引擎可重复执行
