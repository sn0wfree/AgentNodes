"""R-B1 影子验证：WorkflowGraphCompiler vs 旧 WorkflowRunner 逐条对比。

docs/plan-retry-fix-and-workflow-migration-20261031.md §2.3 R-B1：
同一 definition + 同一 scripted loop → 两边 pre_completed / 事件序列 /
终态必须一致。差异即失败（这是 R-B2 切换主路径的前置证据）。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from strategy_research.core.swarm.runtime import AgentStatus
from strategy_research.core.workflow.builtin import load_definition
from strategy_research.core.workflow.executor import WorkflowRunner
from strategy_research.core.workflow.graph_compiler import WorkflowGraphCompiler
from strategy_research.core.workflow.store import WorkflowStore

# Same xdist grouping rationale as test_workflow_segment_loop.py.
pytestmark = pytest.mark.xdist_group("workflow")

PLAN_2 = json.dumps({"plan": [
    {"id": "plan_1", "title": "数据准备", "description": "检查数据质量并确认覆盖",
     "type": "llm_agent", "tools": ["read"], "depends_on": []},
    {"id": "plan_2", "title": "回测", "description": "运行回测验证假设并记录指标",
     "type": "llm_agent", "tools": ["run_backtest"], "depends_on": ["plan_1"]},
]})


class FakeLoop:
    """Scripted loop factory (same shape as the one in segment_loop tests)."""

    def __init__(self, planner=None, evaluator="stop", step="步骤完成") -> None:
        self._planner = planner
        self._evaluator = evaluator
        self._step = step
        self.calls = 0

    def __call__(self, **kwargs) -> str:
        self.calls += 1
        role = kwargs.get("role")
        if role == "planner":
            return self._planner
        if role == "evaluator":
            return self._evaluator
        return self._step


def _definition(name: str, tmp_path: Path):
    definition = load_definition(name, tmp_path)
    assert definition is not None, f"builtin {name} not found"
    return definition


def _run_old(name, tmp_path, *, loop_factory, events):
    store = WorkflowStore(db_path=tmp_path / "old.db")
    runner = WorkflowRunner(
        _definition(name, tmp_path), tmp_path, "研究动量策略",
        store=store, loop_factory=loop_factory,
        emit_event=lambda t, d: events.append(t),
    )
    runner.start()
    return runner


def _run_new(name, tmp_path, *, loop_factory, events):
    definition = _definition(name, tmp_path)
    compiler = WorkflowGraphCompiler(
        definition, workspace=tmp_path, objective="研究动量策略",
        params=definition.params, loop_factory=loop_factory,
        emit_event=lambda t, d: events.append(t),
    )
    return compiler.run()


# ── 对比：无审批、无 replan 的静态流水线 ─────────────────────


def test_shadow_alpha_research_matches(tmp_path):
    """alpha_research：4 个 llm/tool 节点串联，无 planner/evaluator。"""
    old_events: list[str] = []
    old = _run_old("alpha_research", tmp_path, loop_factory=FakeLoop(), events=old_events)

    new_events: list[str] = []
    new = _run_new("alpha_research", tmp_path, loop_factory=FakeLoop(), events=new_events)

    assert sorted(old.pre_completed) == sorted(new.results)
    for node_id in old.pre_completed:
        assert old.pre_completed[node_id].status == new.results[node_id].status
        assert old.pre_completed[node_id].output == new.results[node_id].output
    assert old_events == new_events


def test_shadow_data_quality_audit_matches(tmp_path):
    old_events: list[str] = []
    old = _run_old("data_quality_audit", tmp_path,
                   loop_factory=FakeLoop(), events=old_events)
    new_events: list[str] = []
    new = _run_new("data_quality_audit", tmp_path,
                   loop_factory=FakeLoop(), events=new_events)

    assert sorted(old.pre_completed) == sorted(new.results)
    assert old_events == new_events


def test_shadow_plan_execute_auto_matches(tmp_path):
    """plan_execute_auto：planner 展开 plan_1/plan_2 → evaluator stop。"""
    old_events: list[str] = []
    old = _run_old("plan_execute_auto", tmp_path,
                   loop_factory=FakeLoop(planner=PLAN_2), events=old_events)
    new_events: list[str] = []
    new = _run_new("plan_execute_auto", tmp_path,
                   loop_factory=FakeLoop(planner=PLAN_2), events=new_events)

    assert sorted(old.pre_completed) == sorted(new.results)
    assert sorted(new.results) == ["evaluator", "plan_1", "plan_2", "planner"]
    for node_id in old.pre_completed:
        assert old.pre_completed[node_id].output == new.results[node_id].output
    # 事件序列（含 plan_created）逐条一致
    assert old_events == new_events


def test_shadow_plan_step_failure_matches(tmp_path):
    """plan 步全部失败 → 规则层「连续 2 步失败」终止，两边同结论。"""
    def boom(**kwargs):
        role = kwargs.get("role")
        if role == "planner":
            return PLAN_2
        if role == "evaluator":
            return '{"verdict": "stop"}'
        raise RuntimeError("执行失败")

    old_events: list[str] = []
    old = _run_old("plan_execute_auto", tmp_path,
                   loop_factory=boom, events=old_events)
    new_events: list[str] = []
    new = _run_new("plan_execute_auto", tmp_path,
                   loop_factory=boom, events=new_events)

    assert old.status == "completed"
    assert any("连续 2 步失败" in f for f in old.failures)
    assert any("连续 2 步失败" in f for f in new.failures)
    assert sorted(old.pre_completed) == sorted(new.results)


# ── 审批门：编译器产出 intercept，旧 runner 产出 awaiting ────


def test_shadow_approval_gate_interrupts_at_same_point(tmp_path):
    """plan_execute_approval：planner 后暂停。旧=awaiting；新=图中断。"""
    old_events: list[str] = []
    store = WorkflowStore(db_path=tmp_path / "old.db")
    old = WorkflowRunner(
        _definition("plan_execute_approval", tmp_path), tmp_path, "研究",
        store=store, loop_factory=FakeLoop(planner=PLAN_2),
        emit_event=lambda t, d: old_events.append(t),
    )
    old.start()
    assert old.status == "awaiting"

    new_events: list[str] = []
    new = _run_new("plan_execute_approval", tmp_path,
                   loop_factory=FakeLoop(planner=PLAN_2), events=new_events)

    assert new.stopped_reason == "approval_required"
    # 两边都在 planner + plan 步骤完成后停住，都不含 evaluator
    assert "evaluator" not in old.pre_completed
    assert "evaluator" not in new.results
    assert "planner" in new.results
    assert "awaiting_approval" in new_events
    assert "awaiting_approval" in old_events


def test_shadow_gate_resume_starts_at_next_segment(tmp_path):
    """获批后从下一段继续：编译器用 run(start=下一段入口) 复现旧 approve 路径。"""
    definition = _definition("plan_execute_approval", tmp_path)
    compiler = WorkflowGraphCompiler(
        definition, workspace=tmp_path, objective="研究",
        params=definition.params, loop_factory=FakeLoop(planner=PLAN_2),
    )
    first = compiler.run()
    assert first.stopped_reason == "approval_required"
    assert "evaluator" not in first.results

    # 模拟 approve → 从第二段入口重跑，带 pre_completed
    second = compiler.run(pre_completed=first.results, start="s1_evaluator")
    assert "evaluator" in second.results


# ── 覆盖度对比 ───────────────────────────────────────────────


def test_every_builtin_definition_compiles(tmp_path):
    """4 个出厂模板都能编译（构造期不抛 Unsupported）。"""
    for name in ("alpha_research", "data_quality_audit",
                 "plan_execute_approval", "plan_execute_auto"):
        definition = _definition(name, tmp_path)
        compiler = WorkflowGraphCompiler(
            definition, workspace=tmp_path, objective="x",
            params=definition.params,
        )
        engine = compiler.compile()
        assert engine is not None
