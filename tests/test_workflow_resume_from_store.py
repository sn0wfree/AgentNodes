"""R-B2: resume_from_store — 进程重启后审批续跑（此前永久卡死）。

docs/plan-retry-fix-and-workflow-migration-20261031.md §决定 B：
`/approve` 原先依赖内存里的 runner 实例（registry.get → None 即 404），
重启后 awaiting 的 run 永远无法推进。R-B0 补齐 output 列后，本刀从
store 重建 pre_completed，用新实例续跑。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from strategy_research.kernel.swarm.runtime import AgentStatus
from strategy_research.core.workflow.builtin import load_definition
from strategy_research.core.workflow.executor import WorkflowRunner
from strategy_research.core.workflow.store import WorkflowStore

pytestmark = pytest.mark.xdist_group("workflow")

PLAN_2 = json.dumps({"plan": [
    {"id": "plan_1", "title": "数据准备", "description": "d",
     "type": "llm_agent", "tools": ["read"], "depends_on": []},
    {"id": "plan_2", "title": "回测", "description": "d",
     "type": "llm_agent", "tools": ["run_backtest"], "depends_on": ["plan_1"]},
]})


class FakeLoop:
    def __init__(self, planner=None, evaluator="stop"):
        self._planner, self._evaluator = planner, evaluator
        self.calls = 0

    def __call__(self, **kwargs):
        self.calls += 1
        role = kwargs.get("role")
        if role == "planner":
            return self._planner
        if role == "evaluator":
            return self._evaluator
        return "步骤完成"


def _definition(name: str, tmp_path: Path):
    definition = load_definition(name, tmp_path)
    assert definition is not None
    return definition


def test_node_outputs_persisted_with_full_output(tmp_path):
    """跑一遍后，node_outputs 每行都有 output 列（R-B0 列接线生效）。

    alpha_research 的 tool 节点在本机无数据源时回退为空输出，所以这里
    只断言「列存在且被写入」，不要求每行非空——那是数据依赖，不是
    本刀契约。
    """
    store = WorkflowStore(db_path=tmp_path / "w.db")
    runner = WorkflowRunner(
        _definition("alpha_research", tmp_path), tmp_path, "目标",
        store=store, loop_factory=FakeLoop(),
    )
    run_id = runner.start()
    rows = store.list_node_outputs(run_id)
    assert rows
    assert all("output" in r for r in rows)
    # 至少一个 LLM 节点落了真实文本
    assert any(r["output"] for r in rows), "LLM 节点应落完整 output"


def test_resume_from_store_returns_none_for_unknown_run(tmp_path):
    store = WorkflowStore(db_path=tmp_path / "w.db")
    runner = WorkflowRunner.resume_from_store(
        "no-such-run", _definition("alpha_research", tmp_path), tmp_path, "o",
        store=store,
    )
    assert runner is None


def test_resume_from_store_returns_none_for_terminal_run(tmp_path):
    store = WorkflowStore(db_path=tmp_path / "w.db")
    first = WorkflowRunner(
        _definition("alpha_research", tmp_path), tmp_path, "目标",
        store=store, loop_factory=FakeLoop(),
    )
    run_id = first.start()
    assert first.status == "completed"

    resumed = WorkflowRunner.resume_from_store(
        run_id, _definition("alpha_research", tmp_path), tmp_path, "目标",
        store=store,
    )
    assert resumed is None   # 终态不再续跑


def test_resume_from_store_rebuilds_pre_completed(tmp_path):
    """模拟「跑到一半进程挂了」：手工写库，新实例应能重建已完成节点。"""
    store = WorkflowStore(db_path=tmp_path / "w.db")
    definition = _definition("alpha_research", tmp_path)
    store.create_run("r-crash", definition.name, "s", "目标", {})

    from strategy_research.kernel.swarm.runtime import AgentResult
    store.save_node_output("r-crash", 0, AgentResult(
        agent_id="hypothesis", status=AgentStatus.SUCCESS,
        output="hypothesis body", summary="h",
    ))
    store.update_run("r-crash", status="running", segment_idx=0)

    runner = WorkflowRunner.resume_from_store(
        "r-crash", definition, tmp_path, "目标", store=store,
        loop_factory=FakeLoop(),
    )
    assert runner is not None
    assert "hypothesis" in runner.pre_completed
    assert runner.pre_completed["hypothesis"].output == "hypothesis body"
    # 中断事件已发出
    events = [e["event_type"] for e in store.list_events("r-crash")]
    assert "run_resumed" in events


def test_resume_from_store_continues_and_completes(tmp_path):
    """重建后继续跑：已完成节点不重跑，其余补齐。"""
    store = WorkflowStore(db_path=tmp_path / "w.db")
    definition = _definition("data_quality_audit", tmp_path)
    store.create_run("r1", definition.name, "s", "目标", {})

    from strategy_research.kernel.swarm.runtime import AgentResult
    store.save_node_output("r1", 0, AgentResult(
        agent_id="check", status=AgentStatus.SUCCESS,
        output="checked", summary="ok",
    ))
    store.update_run("r1", status="running")

    runner = WorkflowRunner.resume_from_store(
        "r1", definition, tmp_path, "目标", store=store,
        loop_factory=FakeLoop(),
    )
    assert runner is not None
    runner._run_loop()

    assert runner.status == "completed"
    # check 保留旧输出（未重跑），diagnose 已执行
    assert runner.pre_completed["check"].output == "checked"
    assert "diagnose" in runner.pre_completed


def test_awaiting_run_survives_instance_loss(tmp_path):
    """核心回归：审批暂停 → 丢实例 → resume_from_store → 新实例可 approve。"""
    store = WorkflowStore(db_path=tmp_path / "w.db")
    definition = _definition("plan_execute_approval", tmp_path)
    first = WorkflowRunner(
        definition, tmp_path, "研究", store=store,
        loop_factory=FakeLoop(planner=PLAN_2),
    )
    run_id = first.start()
    assert first.status == "awaiting"
    gate = first._current_gate()
    assert gate is not None
    assert store.get_approval(run_id, gate.id)["status"] == "awaiting"

    # 模拟进程重启：丢掉 first，只用 store 重建
    del first
    resumed = WorkflowRunner.resume_from_store(
        run_id, definition, tmp_path, "研究", store=store,
        loop_factory=FakeLoop(planner=PLAN_2),
    )
    assert resumed is not None
    # 续跑会重新在 gate 处停下（gate 语义保留）
    resumed._run_loop()
    assert resumed.status == "awaiting"
    assert "evaluator" not in resumed.pre_completed

    # 现在可以正常审批并推进
    assert resumed.approve(True) is True
    assert "evaluator" in resumed.pre_completed
