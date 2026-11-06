"""R-B1 影子验证：WorkflowGraphCompiler vs 旧 WorkflowRunner 逐条对比。

docs/archive/2026-10/plan-retry-fix-and-workflow-migration-20261031.md §2.3 R-B1：
同一 definition + 同一 scripted loop → 两边 pre_completed / 事件序列 /
终态必须一致。差异即失败（这是 R-B2 切换主路径的前置证据）。
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from agentnodes.kernel.swarm.runtime import AgentStatus
from agentnodes.kernel.workflow.builtin import load_definition
from agentnodes.kernel.workflow.executor import WorkflowRunner
from agentnodes.kernel.workflow.graph_compiler import WorkflowGraphCompiler
from agentnodes.kernel.workflow.store import WorkflowStore

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
    """Reference implementation of the legacy segment-loop semantics.

    R-B3 deleted ``WorkflowRunner._run_loop_legacy``, so the shadow
    comparison can no longer call it. Instead this keeps an explicit,
    minimal re-statement of the old rules (layer-by-layer serial
    execution, planner expansion, 2-failure stop, approval before the
    gated segment) and asserts the compiler matches it. It is a
    regression statement about the *contract*, not a live second
    implementation.
    """
    from agentnodes.kernel.workflow import node_types
    from agentnodes.kernel.workflow.node_types import (
        NodeContext,
        register_builtin_tool_executors,
    )

    register_builtin_tool_executors()  # same as the runner's _ensure_executors

    definition = _definition(name, tmp_path)
    segments = definition.segment_cut()
    results: dict = {}
    failures: list[str] = []
    plan: list[dict] = []
    processed_segments: set[int] = set()

    def dispatch(node):
        ctx = NodeContext(
            workspace=tmp_path, strategy_name="s", objective="o",
            session_id="", upstream=dict(results), params=definition.params,
            llm_config=None, loop_factory=loop_factory, emit_event=None,
        )
        return node_types.dispatch_node(node, ctx)

    for seg in segments:
        events.append("segment_started")
        ordered = sorted(seg.node_ids,
                         key=lambda n: definition.ordered_ids().index(n))
        for nid in ordered:
            node = next(n for n in definition.nodes if n.id == nid)
            res = dispatch(node)
            results[nid] = res
            events.append("node_completed")
            if getattr(res.status, "value", "") == "error":
                failures.append(f"{nid}: {res.error}")
            if node.type == "planner" and (res.artifacts or {}).get("plan"):
                plan[:] = list(res.artifacts["plan"])
                events.append("plan_created")
                from agentnodes.kernel.workflow.dag import topological_layers
                steps = {}
                for s in plan:
                    sid = s.get("id") or ""
                    sid = sid if sid.startswith("plan_") else f"plan_{sid}"
                    s["id"] = sid
                    steps[sid] = [
                        (d if str(d).startswith("plan_") else f"plan_{d}")
                        for d in (s.get("depends_on") or [])
                    ]
                from agentnodes.kernel.workflow.definition import WorkflowNode
                for layer in topological_layers(steps):
                    for sid in layer:
                        if sid in results:
                            continue
                        snode = WorkflowNode(
                            id=sid, type="llm_agent",
                            config={"role": "researcher",
                                    "prompt_text": next(
                                        (s.get("description", "") for s in plan
                                         if s["id"] == sid), "")},
                        )
                        res = dispatch(snode)
                        results[sid] = res
                        events.append("node_completed")
                        if getattr(res.status, "value", "") == "error":
                            failures.append(f"{sid}: {res.error}")
        events.append("segment_completed")
        processed_segments.add(seg.index)
        if len([f for f in failures if f.split(":")[0] in set(seg.node_ids)
                or f.startswith("plan_")]) >= 2:
            failures.append("连续 2 步失败，规则层判定停止")
            events.append("run_completed")
            break
        # approval gate guarding the NEXT segment
        nxt = next((s for s in segments if s.index == seg.index + 1), None)
        if nxt is not None and nxt.approval_after:
            events.append("awaiting_approval")
            break
    else:
        events.append("run_completed")

    return SimpleNamespace(pre_completed=results, failures=failures,
                           status="completed" if "run_completed" in events else "awaiting")


def _run_new(name, tmp_path, *, loop_factory, events):
    definition = _definition(name, tmp_path)
    compiler = WorkflowGraphCompiler(
        definition, workspace=tmp_path, objective="研究动量策略",
        params=definition.params, loop_factory=loop_factory,
        emit_event=lambda t, d: events.append(t),
    )
    return compiler.run()


# ── 对比：无审批、无 replan 的静态流水线 ─────────────────────

#: 这两个 builtin definition 的 tool 节点用的是 ``check_data`` / ``run_backtest``，
#: 其实现模块（apps/builtin_tools/data_tools.py、backtest_tools.py）已被
#: 2026-11-05 批次 1 直删且不留档案（依赖数据域/回测域同时删除）。
#: ``register_builtin_tool_executors`` 因此注册 0 个执行器，节点必然
#: NodeDispatchError —— 这是永久不可达，不是「等 A 流恢复 apps/goal」。
#: 保留用例本体与 skip 原因，待数据域回归时一并解除。
_DATA_DEPENDENT_REASON = (
    "builtin template uses check_data/run_backtest; "
    "apps/builtin_tools/{data,backtest}_tools.py deleted in batch 1 (no archive)"
)


@pytest.mark.skip(reason=_DATA_DEPENDENT_REASON)
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


@pytest.mark.skip(reason=_DATA_DEPENDENT_REASON)
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
    """plan_execute_auto：planner 展开 plan_1/plan_2 → evaluator stop。

    事件序列在 replan 环上有意不比（参照实现只重述静态段语义）；
    环本身的语义由 test_workflow_segment_loop.py 的真实用例锁定。
    """
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
    # 静态部分的事件前缀一致（plan_created 必然出现）
    assert "plan_created" in new_events
    assert new_events[:old_events.index("run_completed")]
    assert old_events[:4] == new_events[:4]


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
