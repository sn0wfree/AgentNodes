"""Tests for ``core.autoresearch.run_research_round`` — the single-round
extraction that the study executor drives.

These tests exercise the stub-backtest path: ``run_backtest_script`` is
patched to return a fixed metrics dict (the round's job up to Step 5 +
decide + summary is what we verify). All agents run in stub mode via
``AUTORESEARCH_BEHAVIOR``. Goal evidence append is driven by passing a
``session_id`` backed by a temp ``goals.db``; this covers the
end-to-end *shape* of a round without needing a real workspace
data.duckdb / prepare.py.

See ``docs/study-longhorizon-plan.md``.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from strategy_research.apps.autoresearch import run_research_round


@pytest.fixture(autouse=True)
def _stub_agent_behavior(monkeypatch):
    """Force every spawn_agent() call into stub mode."""
    monkeypatch.setenv("AUTORESEARCH_BEHAVIOR", "improving")


@pytest.fixture
def goals_db(tmp_path: Path, monkeypatch) -> Path:
    p = tmp_path / "goals.db"
    monkeypatch.setenv("QUANTNODES_RESEARCH_GOAL_DB_PATH", str(p))
    monkeypatch.setenv("QUANTNODES_RESEARCH_HYPOTHESES_PATH", str(tmp_path / "hyp.json"))
    return p


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """Minimal workspace skeleton so read_current_state + run_dir creation work.

    strategy.py / runs/ need not pre-exist; read_current_state tolerates
    missing files. Patching run_backtest_script means we never invoke
    prepare.py / data.duckdb.
    """
    ws = tmp_path / "ws"
    strat = ws / "strategies" / "demo"
    strat.mkdir(parents=True)
    (strat / "strategy.py").write_text("PARAMS = {}\nFACTOR_EXPRS = []\n", encoding="utf-8")
    (ws / "acceptance.yaml").write_text("llm_enabled: false\n", encoding="utf-8")
    return ws


@pytest.fixture
def metrics_fixture():
    #同城回测 stub: metrics 形如 run_backtest_script 的返回
    return {
        "calmar": 0.62,
        "sharpe": 0.45,
        "max_dd": -0.12,
        "ann_return": 0.18,
        "ann_vol": 0.29,
        "turnover": 1.2,
        "trade_count": 120,
        "sortino": 0.55,
    }


@pytest.fixture
def patched_backtest(metrics_fixture):
    """Patch run_backtest_script to return our chosen metrics + a fake run.log."""
    def _fake(workspace_path, strategy_name, action="", description="", run_dir=None, **kw):
        if run_dir is not None:
            (run_dir / "run.log").write_text("ok\n", encoding="utf-8")
        return {
            "success": True,
            "run": run_dir.name if run_dir is not None else "run_t",
            "metrics": metrics_fixture,
            "run_log": "ok\n",
        }

    # run_research_round imports ``run_backtest_script`` lazily from
    # ``core.backtest`` so patch the source module, not the autoresearch module.
    with patch("strategy_research.core.backtest.run_backtest_script", _fake):
        yield _fake


class TestRunResearchRound:
    def test_round_returns_structured_result(self, workspace, patched_backtest):
        r = run_research_round(
            workspace, "demo", round_num=1,
            behavior="improving", inter_agent_sleep=0.0,
        )
        # Structural contract spelled out in docs/study-longhorizon-plan.md
        assert set(r) >= {"round", "run_name", "run_dir", "metrics",
                          "verdict", "decision", "agent_outputs",
                          "summary", "backtest_error"}
        assert r["round"] == 1
        assert r["run_name"].startswith("run_")
        assert isinstance(r["run_dir"], Path)
        assert r["metrics"]["calmar"] == 0.62
        assert r["backtest_error"] is None
        # All 9 agents present
        assert set(r["agent_outputs"]) == {
            "researcher", "data_quality", "factor_analyst", "strategist",
            "portfolio_construction", "risk_controller", "attribution_analyst",
            "anti_overfit_analyst", "backtest_diagnostics",
        }

    def test_round_creates_run_dir_and_summary(self, workspace, patched_backtest):
        r = run_research_round(workspace, "demo", round_num=1, behavior="improving")
        assert r["run_dir"].exists()
        assert (r["run_dir"] / "agents").exists()
        # summary.json written by generate_run_summary + save_run_summary
        assert (r["run_dir"] / "summary.json").exists()
        s = json.loads((r["run_dir"] / "summary.json").read_text(encoding="utf-8"))
        assert s["round"] == 1
        assert "acceptance_decision" in s

    def test_round_appends_goal_evidence_when_session_id_given(
        self, workspace, patched_backtest, goals_db
    ):
        from strategy_research.core.goal import GoalStore
        from strategy_research.core.goal.context import default_goal_criteria

        # Create a goal for the study session + criteria so evidence can
        # attach to criterion_id[0].
        session_id = "sess-study"
        with GoalStore() as gs:
            goal = gs.replace_goal(
                session_id=session_id,
                objective="研究动量因子",
                criteria=default_goal_criteria(),
            )
            goal_id = goal.goal_id

        run_research_round(workspace, 'demo', round_num=1, session_id=session_id, behavior='improving')
        # Evidence should have been appended for criterion[0]
        with GoalStore() as gs:
            ev = gs.list_evidence(goal_id)
        assert any("run_" in (e.run_id or "") for e in ev), ev

    def test_round_without_session_does_not_touch_goal(
        self, workspace, patched_backtest, goals_db, tmp_path
    ):
        # No goal created, no session_id → round still runs, no evidence.
        r = run_research_round(workspace, "demo", round_num=1, behavior="improving")
        assert r["metrics"]["calmar"] == 0.62  # sanity
        from strategy_research.core.goal import GoalStore
        with GoalStore() as gs:
            assert gs.get_current_goal("any-session") is None
