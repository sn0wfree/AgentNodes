"""深度统一 B2 的验收测试：层间自动存档 + CLI 真续跑。

docs/deep-unification-graph-engine-design-20261031.md 决定 E：
1. GoalWorkflowHook.on_layer_complete → runner.checkpoint()（每层自动落盘，
   失败静默降级，无 goal_id 跳过）；
2. ``/goal checkpoint resume`` 改调 resume_from_checkpoint +
   resume_and_continue —— pre_completed + 引擎原语 run(start=layer_k)
   从断点层续跑；workflow 装载失败回退检视模式。
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from rich.console import Console

from strategy_research.core.goal.checkpoint_store import CheckpointStore
from strategy_research.core.goal.workflow import (
    CompletionConfig,
    GoalAgentConfig,
    GoalWorkflowConfig,
    GoalWorkflowRunner,
)
from strategy_research.core.goal.workflow_hook import GoalWorkflowHook


# ── _auto_checkpoint 单元语义 ────────────────────────────────────


def _hook(runner) -> GoalWorkflowHook:
    return GoalWorkflowHook(
        session_id="s", goal_id="g1", evidence_map={},
        store=SimpleNamespace(get_current_snapshot=lambda sid: None),
        runner=runner, workflow_name="wf",
    )


def test_auto_checkpoint_fires_per_layer_complete():
    calls: list[str] = []
    runner = SimpleNamespace(_goal_id="g1", checkpoint=lambda: calls.append("cp"))
    _hook(runner).on_layer_complete(0, ["a"], {"a": SimpleNamespace(output="x")})
    _hook(runner).on_layer_complete(1, ["b"], {})
    assert calls == ["cp", "cp"]


def test_auto_checkpoint_failure_is_swallowed():
    def _boom():
        raise OSError("disk full")

    runner = SimpleNamespace(_goal_id="g1", checkpoint=_boom)
    # 不抛——存档是恢复优化，不是执行依赖
    _hook(runner).on_layer_complete(0, ["a"], {})


def test_auto_checkpoint_skipped_without_goal_id():
    calls: list[str] = []
    runner = SimpleNamespace(_goal_id="", checkpoint=lambda: calls.append("cp"))
    _hook(runner).on_layer_complete(0, ["a"], {})
    _hook(None).on_layer_complete(0, ["a"], {})
    assert calls == []


# ── 集成：真实 runner 跑两层 DAG，逐层自动存档 ───────────────────


CFG = GoalWorkflowConfig(
    name="auto_cp_flow",
    description="auto-checkpoint 集成测试",
    agents=[
        GoalAgentConfig(id="researcher", prompt_file=".prompts/researcher.md",
                        evidence_criterion=0),
    ],
    dag={"researcher": []},
    completion=CompletionConfig(mode="auto", monitor_interval_seconds=None),
)


class FakeStore:
    def __init__(self):
        self.goals = {}

    def get_goal(self, goal_id):
        return self.goals.get(goal_id)

    def get_current_goal(self, session_id):
        return None

    def get_current_snapshot(self, session_id):
        return None


def test_workflow_run_auto_checkpoints_layers(tmp_path, monkeypatch):
    """跑一次 start()：层完成后 checkpoint 已在盘上（含 layer_results）。"""
    monkeypatch.setenv("STRATEGY_RESEARCH_CHECKPOINT_BASE_DIR",
                       str(tmp_path / "cps"))
    runner = GoalWorkflowRunner(CFG, "sess-auto", store=FakeStore(),
                                workspace=tmp_path)
    runner.set_goal_id("g-auto")
    asyncio.run(runner.start("objective"))

    data = CheckpointStore().load("sess-auto", "g-auto")
    assert data is not None
    # agent 执行失败（无 LLM）不妨碍层完成存档：layer_results 有条目
    assert "researcher" in data["layer_results"]
    assert data["meta"]["workflow_name"] == CFG.name


# ── CLI 端到端：/goal checkpoint resume 真续跑 ───────────────────


def _write_workflow_yaml(path: Path) -> None:
    path.write_text(
        """
goal:
  risk_tier: research_general
agents:
  - id: layer_a
    evidence_criterion: 0
  - id: layer_b
    input_from: [layer_a]
    evidence_criterion: 0
dag:
  layer_a: []
  layer_b: [layer_a]
completion:
  mode: auto
""",
        encoding="utf-8",
    )


def test_cli_resume_continues_from_checkpoint(tmp_path, monkeypatch):
    """手写一个「第 0 层已完成」的 checkpoint → CLI resume 续跑第 1 层：
    退出码 0、输出含 Resumed、checkpoint 里新增第 1 层 agent 的条目。"""
    cps = str(tmp_path / "cps")
    # 两个变量都要设：core 用 _BASE_DIR，CLI 的 _checkpoint_base_dir 用 _DIR
    monkeypatch.setenv("STRATEGY_RESEARCH_CHECKPOINT_BASE_DIR", cps)
    monkeypatch.setenv("STRATEGY_RESEARCH_CHECKPOINT_DIR", cps)
    yaml_path = tmp_path / "wf_b2.yaml"
    _write_workflow_yaml(yaml_path)

    from strategy_research.core.goal.models import RiskTier
    from strategy_research.core.goal.store import GoalStore

    sid, gid = "cli-b2", "g-b2"
    store = GoalStore()
    goal = store.replace_goal(
        session_id=sid,
        objective="obj",
        criteria=["c1"],
        source="workflow",
        protocol="wf_b2",
        risk_tier=RiskTier("research_general"),
        workflow_id="wf_b2",
    )
    assert goal.goal_id == gid or gid == "g-b2"

    CheckpointStore().save(
        session_id=sid, goal_id=gid,
        state={"goal_status": "running", "current_layer": 1,
               "evidence_count": 0, "agent_statuses": {"layer_a": "success"}},
        layer_results={"layer_a": {"output": "layer0-done"}},
        workflow_name=str(yaml_path),
    )

    from strategy_research.cli.commands.slash_goal import _cmd_checkpoint_resume

    console = Console(record=True)
    rc = _cmd_checkpoint_resume(console, sid, gid)
    assert rc == 0
    out = console.export_text()
    assert "Resuming workflow" in out
    assert "Resumed + finished" in out

    # 第 1 层 agent 也进了 checkpoint（auto-checkpoint 生效）
    data = CheckpointStore().load(sid, gid)
    assert "layer_b" in data["layer_results"]
    assert data["layer_results"]["layer_a"]["output"] == "layer0-done"


def test_cli_resume_falls_back_to_inspect_without_workflow_name(
    tmp_path, monkeypatch, capsys,
):
    """老 checkpoint（无 workflow 名）→ 检视模式，退出码 0。"""
    cps = str(tmp_path / "cps")
    monkeypatch.setenv("STRATEGY_RESEARCH_CHECKPOINT_BASE_DIR", cps)
    monkeypatch.setenv("STRATEGY_RESEARCH_CHECKPOINT_DIR", cps)
    sid, gid = "cli-old", "g-old"
    CheckpointStore().save(
        session_id=sid, goal_id=gid,
        state={"goal_status": "completed"},
        layer_results={},
        workflow_name="",
    )
    from strategy_research.cli.commands.slash_goal import _cmd_checkpoint_resume

    console = Console(record=True)
    rc = _cmd_checkpoint_resume(console, sid, gid)
    assert rc == 0
    assert "无法续跑" in console.export_text()
