"""R-B4: uncovered compiler/executor branches.

Coverage gap closed after the migration (C 能力层 fell to 89.1% because
graph_compiler.py is new and executor.py still carried branches the old
tests used to reach). Targets, per coverage report:
- graph_compiler: node dispatch error → GraphInterrupt; generic exception
  → ERROR envelope; empty-plan early return; approval inside a segment
  guard; replan router's END branches
- executor: node_dispatch_error → failed status; resume with corrupt
  findings JSON
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from strategy_research.kernel.swarm.runtime import AgentResult, AgentStatus
from strategy_research.kernel.workflow.definition import (
    WorkflowDefinition,
    WorkflowEdge,
    WorkflowNode,
)
from strategy_research.kernel.workflow.executor import WorkflowRunner
from strategy_research.kernel.workflow.graph_compiler import (
    CompiledRunState,
    WorkflowGraphCompiler,
)
from strategy_research.kernel.workflow.store import WorkflowStore

pytestmark = pytest.mark.xdist_group("workflow")


class FakeLoop:
    def __init__(self, planner=None, evaluator="stop"):
        self._planner, self._evaluator = planner, evaluator

    def __call__(self, **kwargs):
        role = kwargs.get("role")
        if role == "planner":
            return self._planner
        if role == "evaluator":
            return self._evaluator
        return "ok"


def _def(nodes, edges=(), params=None) -> WorkflowDefinition:
    d = WorkflowDefinition(
        name="t",
        description="",
        nodes=nodes,
        edges=[WorkflowEdge(source=s, target=t) for s, t in edges],
    )
    if params:
        d.params = params
    return d


# ── compiler: dispatch error paths ────────────────────────────


def test_unknown_node_type_raises_dispatch_interrupt(tmp_path):
    """未知节点类型 → NodeDispatchError → GraphInterrupt，不炸整个 run。"""
    d = _def([WorkflowNode(id="bad", type="not_a_type")])
    compiler = WorkflowGraphCompiler(d, workspace=tmp_path, objective="o")
    state = compiler.run()
    assert state.stopped_reason == "node_dispatch_error"
    assert "bad" not in state.results


def test_generic_exception_becomes_error_envelope(tmp_path, monkeypatch):
    """dispatch 抛普通异常 → ERROR 信封（失败入账，流程继续）。"""
    d = _def([WorkflowNode(id="n", type="llm_agent", config={"role": "r"})])
    compiler = WorkflowGraphCompiler(d, workspace=tmp_path, objective="o",
                                     loop_factory=FakeLoop())

    def boom(*a, **kw):
        raise RuntimeError("executor exploded")

    monkeypatch.setattr(compiler, "_dispatch_with_retry", boom)
    state = compiler.run()
    assert state.results["n"].status == AgentStatus.ERROR
    assert "exploded" in state.results["n"].error


def test_empty_plan_returns_early(tmp_path):
    """planner 结果无 plan 键 → plan 展开节点直接返回（不展开步骤）。"""
    d = _def([WorkflowNode(id="planner", type="planner", config={})])
    compiler = WorkflowGraphCompiler(d, workspace=tmp_path, objective="o",
                                     loop_factory=FakeLoop())
    # 直接注入一个无 plan 的 planner 结果（模拟未产出计划的路径），
    # 避开 _parse_plan 的 fallback 兜底（那属另一条既有语义）。
    compiler._run_state = CompiledRunState(results={
        "planner": AgentResult(agent_id="planner", status=AgentStatus.SUCCESS,
                               output="no plan here", summary="s", artifacts={}),
    })
    engine = compiler.compile(pre_completed=compiler._run_state.results)
    state = {"run": compiler._run_state}
    from strategy_research.kernel.workflow.graph_compiler import CompiledRunState as S
    # 直接调用 plan 节点 fn
    out = compiler._make_plan_fn(d.nodes[0])(state)
    assert out["run"].plan == []
    assert isinstance(compiler._run_state, S)


def test_approval_node_inside_segment_is_rejected(tmp_path):
    """畸形定义：approval 落在段内 → 明确抛 Unsupported（fail loud）。"""
    from strategy_research.kernel.workflow.definition import WorkflowSegment

    d = _def([WorkflowNode(id="a", type="llm_agent", config={"role": "r"}),
              WorkflowNode(id="gate", type="approval")])
    compiler = WorkflowGraphCompiler(d, workspace=tmp_path, objective="o")
    seg = WorkflowSegment(index=0, nodes=list(d.nodes), edges=[])
    engine = compiler.compile()
    with pytest.raises(Exception):
        compiler._compile_segment(engine, seg)


# ── compiler: replan router branches ──────────────────────────


def _router(compiler, state):
    return compiler._make_replan_router()(state)


def test_replan_router_stops_on_interrupt(tmp_path):
    from strategy_research.kernel.runtimes import INTERRUPT_KEY

    d = _def([WorkflowNode(id="p", type="planner", config={})])
    c = WorkflowGraphCompiler(d, workspace=tmp_path, objective="o")
    run = CompiledRunState()
    assert _router(c, {"run": run, INTERRUPT_KEY: {"x": 1}}) == "__end__"


def test_replan_router_stops_when_stopped(tmp_path):
    d = _def([WorkflowNode(id="p", type="planner", config={})])
    c = WorkflowGraphCompiler(d, workspace=tmp_path, objective="o")
    run = CompiledRunState(stopped_reason="completed")
    assert _router(c, {"run": run}) == "__end__"


def test_replan_router_stops_when_verdict_is_not_replan(tmp_path):
    d = _def([WorkflowNode(id="p", type="planner", config={})])
    c = WorkflowGraphCompiler(d, workspace=tmp_path, objective="o")
    run = CompiledRunState()
    run.results["e"] = AgentResult(
        agent_id="e", status=AgentStatus.SUCCESS, output="",
        artifacts={"decision": {"verdict": "stop"}}, summary="s",
    )
    assert _router(c, {"run": run}) == "__end__"


def test_replan_router_loops_when_under_cap(tmp_path):
    d = _def([WorkflowNode(id="p", type="planner", config={})])
    c = WorkflowGraphCompiler(d, workspace=tmp_path, objective="o",
                              params={"exec": {"max_segments": 3}})
    run = CompiledRunState(replan_count=0)
    run.results["e"] = AgentResult(
        agent_id="e", status=AgentStatus.SUCCESS, output="",
        artifacts={"decision": {"verdict": "replan"}}, summary="s",
    )
    assert _router(c, {"run": run}) == "__replan_planner"


def test_replan_router_ends_at_cap(tmp_path):
    d = _def([WorkflowNode(id="p", type="planner", config={})])
    c = WorkflowGraphCompiler(d, workspace=tmp_path, objective="o",
                              params={"exec": {"max_segments": 1}})
    run = CompiledRunState(replan_count=1)
    run.results["e"] = AgentResult(
        agent_id="e", status=AgentStatus.SUCCESS, output="",
        artifacts={"decision": {"verdict": "replan"}}, summary="s",
    )
    assert _router(c, {"run": run}) == "__end__"


def test_last_verdict_ignores_non_dict_decision(tmp_path):
    d = _def([WorkflowNode(id="p", type="planner", config={})])
    c = WorkflowGraphCompiler(d, workspace=tmp_path, objective="o")
    run = CompiledRunState()
    run.results["e"] = AgentResult(
        agent_id="e", status=AgentStatus.SUCCESS, output="",
        artifacts={"decision": "not-a-dict"}, summary="s",
    )
    assert c._last_verdict(run) == "continue"
    assert c._last_decision_reason(run) == ""


# ── executor: failure status mapping + corrupt findings ───────


def test_dispatch_error_marks_run_failed(tmp_path):
    """节点配置错误 → runner.status == 'failed' + run_failed 事件。"""
    d = _def([WorkflowNode(id="bad", type="nope")])
    store = WorkflowStore(db_path=tmp_path / "w.db")
    events = []
    runner = WorkflowRunner(d, tmp_path, "o", store=store,
                            emit_event=lambda t, _d: events.append(t))
    runner.start()
    assert runner.status == "failed"
    assert "run_failed" in events


def test_resume_tolerates_corrupt_findings_json(tmp_path):
    """store 里的 findings/failures 是坏 JSON → 忽略，不炸。"""
    store = WorkflowStore(db_path=tmp_path / "w.db")
    d = _def([WorkflowNode(id="n", type="llm_agent", config={"role": "r"})])
    store.create_run("r1", d.name, "s", "o", {})
    store.update_run("r1", status="running")
    store._ensure_conn().execute(
        "UPDATE runs SET findings='{broken', failures='[1' WHERE run_id='r1'"
    )
    store._ensure_conn().commit()

    runner = WorkflowRunner.resume_from_store(
        "r1", d, tmp_path, "o", store=store, loop_factory=FakeLoop(),
    )
    assert runner is not None
    assert runner.findings == []
    assert runner.failures == []
