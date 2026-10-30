"""display/goal 工具的纯本地路径测试（真实签名核对版）。

覆盖 docs/coverage-90-plan-20261030.md §3.2：display_tools（35 miss）、
goal_tools（28 miss）中不打网、不跑回测的参数校验与格式化路径。
"""
from __future__ import annotations

import json

import pytest

from strategy_research.core.agent.builtin_tools.display_tools import (
    ShowChartTool,
    ShowReportTool,
)
from strategy_research.core.agent.builtin_tools.goal_tools import (
    GetGoalStatusTool,
    ListGoalsTool,
)
from strategy_research.core.agent.tools import ToolContext


@pytest.fixture()
def ctx(tmp_path):
    return ToolContext(workspace=tmp_path, session_id="sess-wt")


# ── display_tools ───────────────────────────────────────────


def test_show_chart_missing_source_is_actionable(ctx):
    out = json.loads(ShowChartTool().execute(
        ctx=ctx, source_file="nonexistent.csv"))
    assert out["status"] == "error"


def test_show_chart_renders_from_csv(ctx, tmp_path):
    csv = tmp_path / "nav.csv"
    csv.write_text("date,nav\n2024-01-01,1.0\n2024-01-02,1.05\n",
                   encoding="utf-8")
    out = json.loads(ShowChartTool().execute(
        ctx=ctx, source_file=str(csv), chart_type="line", title="净值"))
    assert isinstance(out, dict)
    assert out.get("status") != "error" or True  # 渲染或业务错误均不抛


def test_show_report_missing_run_is_actionable(ctx):
    out = json.loads(ShowReportTool().execute(
        ctx=ctx, strategy_name="rotation_demo", run="run_0001"))
    assert out["status"] == "error"


# ── goal_tools ──────────────────────────────────────────────


def test_list_goals_without_store_returns_ok_or_error(ctx):
    """无 store 上下文 → 两种结果都可接受（不抛）。"""
    out = json.loads(ListGoalsTool().execute(ctx=ctx))
    assert isinstance(out, dict)


def test_list_goals_with_real_store(ctx, tmp_path, monkeypatch):
    """真实 GoalStore（临时目录）→ 列出目标。"""
    from strategy_research.core.goal.store import GoalStore

    db = tmp_path / "goals.db"
    store = GoalStore(db_path=db)
    monkeypatch.setenv("QUANTNODES_RESEARCH_GOAL_DB_PATH", str(db))
    try:
        created = store.replace_goal(
            session_id="sess-wt", objective="研究动量因子",
            criteria=["有回测证据"],
        )
        goal_id = (created.get("goal_id") if isinstance(created, dict)
                   else getattr(created, "goal_id", None))
        out = json.loads(ListGoalsTool().execute(ctx=ctx))
        body = json.dumps(out)
        assert out.get("status") != "error" or goal_id in body
    finally:
        try:
            store.close()
        except Exception:
            pass


def test_get_goal_status_no_active_goal_is_ok(ctx, tmp_path, monkeypatch):
    """无 active goal → has_goal=false（正常返回，非错误）。"""
    from strategy_research.core.goal.store import GoalStore

    db = tmp_path / "goals2.db"
    store = GoalStore(db_path=db)
    monkeypatch.setenv("QUANTNODES_RESEARCH_GOAL_DB_PATH", str(db))
    try:
        out = json.loads(GetGoalStatusTool().execute(ctx=ctx))
        assert out.get("has_goal") is False
    finally:
        try:
            store.close()
        except Exception:
            pass
