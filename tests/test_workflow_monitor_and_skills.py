"""workflow monitor 块 + workspace skills 测试（B/C 档收尾缺口）。

覆盖 docs/coverage-90-plan-20261030.md §3.2：
- goal/workflow.py 365-395（Phase 1.5 monitor：启动/漂移检测/异常容忍）
- workspace_tools.py 297-332 / 382-415（ListSkills / LoadSkill 主体）
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from strategy_research.core.agent.builtin_tools.workspace_tools import (
    ListSkillsTool,
    LoadSkillTool,
)
from strategy_research.core.agent.tools import ToolContext
from strategy_research.core.goal.workflow import (
    CompletionConfig,
    GoalAgentConfig,
    GoalWorkflowConfig,
    GoalWorkflowRunner,
)


# ── workspace skills（重补——早期重写时丢失）──────────────


@pytest.fixture()
def skill_ctx(tmp_path):
    skills = tmp_path / ".skills"
    skills.mkdir()
    (skills / "factor-research.md").write_text(
        "---\nname: factor-research\ncategory: research\n"
        "description: 因子研究技能\n---\n技能正文内容",
        encoding="utf-8",
    )
    return ToolContext(workspace=tmp_path, session_id="s-sk")


def test_list_skills_body(skill_ctx):
    out = json.loads(ListSkillsTool().execute(ctx=skill_ctx))
    assert out["n_skills"] >= 1
    assert any(s["name"] == "factor-research" for s in out["skills"])
    assert "research" in out["categories"]


def test_list_skills_category_filter(skill_ctx):
    out = json.loads(ListSkillsTool().execute(ctx=skill_ctx, category="research"))
    assert out["n_skills"] >= 1
    out_empty = json.loads(ListSkillsTool().execute(
        ctx=skill_ctx, category="nonexistent"))
    assert out_empty["n_skills"] == 0


def test_load_skill_body(skill_ctx):
    out = json.loads(LoadSkillTool().execute(
        ctx=skill_ctx, name="factor-research"))
    assert out["name"] == "factor-research"
    assert "技能正文" in out["content"]


# ── goal/workflow monitor 块（365-395）────────────────────


def _wf_runner(tmp_path, monitor_interval):
    cfg = GoalWorkflowConfig(
        name="mon",
        description="",
        agents=[GoalAgentConfig(id="backtest", prompt_file="x",
                                executor_type="llm")],
        dag={"backtest": []},
        completion=CompletionConfig(
            mode="auto", monitor_interval_seconds=monitor_interval,
            metric_targets=[{"name": "sharpe", "op": ">=", "value": 1.0}],
        ),
    )
    return GoalWorkflowRunner(cfg, "sess-mon", workspace=tmp_path)


def test_launch_monitor_without_interval_is_noop(tmp_path):
    runner = _wf_runner(tmp_path, monitor_interval=None)
    runner.launch_monitor()
    assert runner._monitor_task is None  # 未配置 → 不启动


def test_monitor_background_drift_detection(tmp_path, monkeypatch):
    """监测发现指标不达标 → status=needs_refresh + drift 事件。"""
    runner = _wf_runner(tmp_path, monitor_interval=1)
    runner._goal_id = "g-mon"
    runner._state.status = "completed"
    # backtest agent 名单命中 → strategy_name = "backtest"
    monkeypatch.setattr(runner, "_run_monitor_check",
                        lambda strategy_name: {"sharpe": 0.2})
    asyncio.run(runner._monitor_background())
    assert runner._state.status == "needs_refresh"


def test_monitor_background_metrics_ok_keeps_running(tmp_path, monkeypatch):
    """指标达标 → 不触发 drift，循环继续（一轮后取消退出）。"""
    runner = _wf_runner(tmp_path, monitor_interval=1)
    runner._goal_id = "g-mon"
    runner._state.status = "completed"
    monkeypatch.setattr(runner, "_run_monitor_check",
                        lambda strategy_name: {"sharpe": 2.0})

    async def run_two_ticks():
        task = asyncio.create_task(runner._monitor_background())
        await asyncio.sleep(1.5)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(run_two_ticks())
    assert runner._state.status == "completed"  # 未变 needs_refresh


def test_monitor_background_no_targets_returns_immediately(tmp_path):
    runner = _wf_runner(tmp_path, monitor_interval=1)
    runner._config.completion.metric_targets = None
    # 无 metric_targets → 立即返回（不挂起）
    asyncio.run(runner._monitor_background())


def test_monitor_background_backtest_exception_tolerated(tmp_path, monkeypatch):
    """_run_monitor_check 抛错 → 记警告继续（一轮后取消）。"""
    runner = _wf_runner(tmp_path, monitor_interval=1)
    runner._goal_id = "g-mon"

    def boom(strategy_name):
        raise RuntimeError("backtest down")

    monkeypatch.setattr(runner, "_run_monitor_check", boom)

    async def run_one_tick():
        task = asyncio.create_task(runner._monitor_background())
        await asyncio.sleep(1.2)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(run_one_tick())


def test_run_monitor_check_backtest_failure_returns_none(tmp_path, monkeypatch):
    runner = _wf_runner(tmp_path, monitor_interval=None)

    def fail_result(**kw):
        return {"success": False}

    import strategy_research.apps.backtest as bt
    monkeypatch.setattr(bt, "run_backtest_script", fail_result)
    assert runner._run_monitor_check("backtest") is None


def test_run_monitor_check_success_returns_metrics(tmp_path, monkeypatch):
    runner = _wf_runner(tmp_path, monitor_interval=None)

    def ok_result(**kw):
        return {"success": True, "metrics": {"sharpe": 1.5}}

    import strategy_research.apps.backtest as bt
    monkeypatch.setattr(bt, "run_backtest_script", ok_result)
    assert runner._run_monitor_check("backtest") == {"sharpe": 1.5}
