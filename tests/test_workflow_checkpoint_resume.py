"""GoalWorkflow checkpoint-resume 集成测试（覆盖 workflow.py 573-706 块）。

流程：构造 runner → 种子 hook layer_results → checkpoint() 落盘 →
resume_from_checkpoint 恢复状态 → resume_and_continue 以 pre_completed
继续执行（单层 DAG + start_layer=1 → 无剩余层 → 直接完成）。
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from strategy_research.apps.goal.workflow import (
    CompletionConfig,
    GoalAgentConfig,
    GoalWorkflowConfig,
    GoalWorkflowRunner,
)
from strategy_research.apps.goal.workflow_hook import GoalWorkflowHook


CFG = GoalWorkflowConfig(
    name="resume_flow",
    description="checkpoint-resume 集成测试",
    agents=[
        GoalAgentConfig(id="researcher", prompt_file=".prompts/researcher.md",
                        evidence_criterion=0),
    ],
    dag={"researcher": []},
    completion=CompletionConfig(mode="auto", monitor_interval_seconds=None),
)


class FakeStore:
    """resume 路径需要的最小 GoalStore 面。"""

    def __init__(self):
        self.goals = {}

    def get_goal(self, goal_id):
        return self.goals.get(goal_id)

    def get_current_snapshot(self, session_id):
        return None


@pytest.fixture()
def cp_env(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("STRATEGY_RESEARCH_CHECKPOINT_BASE_DIR",
                       str(tmp_path / "cps"))
    return tmp_path / "cps"


def _make_runner(store: FakeStore, ws: Path, session_id="sess-cp") -> GoalWorkflowRunner:
    runner = GoalWorkflowRunner(
        CFG, session_id, store=store, workspace=ws,
    )
    runner.set_goal_id("g1")
    return runner


def _seed_hook(runner: GoalWorkflowRunner, output: str) -> None:
    """模拟「第 0 层已完成」：挂上带 layer_results 的 hook。"""
    runner._hook = GoalWorkflowHook(
        session_id=runner._session_id,
        goal_id=runner._goal_id,
        evidence_map={"researcher": 0},
        store=runner._store,
        runner=runner,
        workflow_name=CFG.name,
        event_bus=runner._event_bus,
    )
    runner._hook._layer_results = {
        "researcher": {"output": output, "status": "success"},
    }
    runner._state.status = "paused"
    runner._state.current_layer = 1


# ── checkpoint() ─────────────────────────────────────────


def test_checkpoint_saves_layer_results(cp_env: Path, tmp_path: Path):
    store = FakeStore()
    runner = _make_runner(store, tmp_path)
    _seed_hook(runner, output="第一层产出")

    path = runner.checkpoint()
    assert path is not None and path.exists()
    assert (path / "layer_results.json").exists()
    assert (path / "state.json").exists()

    import json

    lr = json.loads((path / "layer_results.json").read_text(encoding="utf-8"))
    assert lr["researcher"]["output"] == "第一层产出"


def test_checkpoint_without_hook_returns_path_with_summary(cp_env, tmp_path):
    store = FakeStore()
    runner = _make_runner(store, tmp_path)
    path = runner.checkpoint()
    assert path is not None  # 空 layer_results 也保存（fallback summary）


# ── resume_from_checkpoint ───────────────────────────────


def test_resume_from_checkpoint_restores_state(cp_env, tmp_path):
    store = FakeStore()
    runner = _make_runner(store, tmp_path)
    _seed_hook(runner, output="第一层产出")
    runner.checkpoint()

    restored = GoalWorkflowRunner.resume_from_checkpoint(
        session_id="sess-cp", goal_id="g1", config=CFG,
        store=store, workspace=tmp_path,
    )
    assert restored is not None
    assert restored._goal_id == "g1"
    assert restored._state.current_layer == 1
    assert restored._state.status == "paused"
    assert restored._hook is not None
    assert "researcher" in restored._hook._layer_results


def test_resume_from_checkpoint_missing_returns_none(cp_env, tmp_path):
    restored = GoalWorkflowRunner.resume_from_checkpoint(
        session_id="none", goal_id="none", config=CFG,
        store=FakeStore(), workspace=tmp_path,
    )
    assert restored is None


# ── resume_and_continue（573-706 主块）───────────────────


def test_resume_and_continue_completes_from_pre_completed(cp_env, tmp_path):
    """完整恢复流：checkpoint → resume_and_continue → pre_completed
    直接判定成功（单层 DAG + start_layer=1 → 无剩余层执行）。"""
    store = FakeStore()
    store.goals["g1"] = SimpleNamespace(objective="研究动量因子")
    runner = _make_runner(store, tmp_path)
    _seed_hook(runner, output="第一层产出")
    runner.checkpoint()

    restored = GoalWorkflowRunner.resume_from_checkpoint(
        session_id="sess-cp", goal_id="g1", config=CFG,
        store=store, workspace=tmp_path,
    )
    assert restored is not None
    goal_id = asyncio.run(restored.resume_and_continue())
    assert goal_id == "g1"
    # pre_completed 的成功 → completed
    assert restored._state.status == "completed"
    assert restored._state.error_message == ""


def test_resume_and_continue_raises_without_checkpoint(cp_env, tmp_path):
    store = FakeStore()
    runner = _make_runner(store, tmp_path)
    with pytest.raises(FileNotFoundError):
        asyncio.run(runner.resume_and_continue())


def test_resume_and_continue_error_on_runtime_failure(cp_env, tmp_path, monkeypatch):
    """runtime 抛错 → state=error + workflow_failed 事件。"""
    store = FakeStore()
    store.goals["g1"] = SimpleNamespace(objective="obj")
    runner = _make_runner(store, tmp_path)
    _seed_hook(runner, output="第一层产出")
    runner.checkpoint()

    restored = GoalWorkflowRunner.resume_from_checkpoint(
        session_id="sess-cp", goal_id="g1", config=CFG,
        store=store, workspace=tmp_path,
    )
    # 放大器：让 to_thread 内的 execute 抛错——替换 SwarmRuntime 构造
    import strategy_research.kernel.swarm.runtime as rt_mod

    class Boom:
        def execute(self, *a, **k):
            raise RuntimeError("runtime exploded")

    # *a/**k：SwarmRuntime 现在接受 on_event=（agent 事件接线），桩要兼容。
    monkeypatch.setattr(rt_mod, "SwarmRuntime", lambda *a, **k: Boom())
    events: list[str] = []
    restored._event_bus.emit = lambda *a, **k: events.append(a[0])

    goal_id = asyncio.run(restored.resume_and_continue())
    assert goal_id == "g1"
    assert restored._state.status == "error"
    assert "runtime exploded" in restored._state.error_message
    assert "workflow_failed" in events
