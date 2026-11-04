"""Tests for study/runner.py — AutoresearchRunner AEGIS integration.

Uses behavior stubs to drive the runner without LLM calls.
"""
import asyncio
import os

import pytest

from strategy_research.apps.goal.store import GoalStore
from strategy_research.apps.study.runner import (
    AutoresearchRunner,
    ControlToken,
    ShutdownReason,
    _metric_pass_set,
    meets_metric_targets,
)
from strategy_research.apps.study.store import StudyStore


@pytest.fixture(autouse=True)
def _isolate_db(tmp_path):
    os.environ["QUANTNODES_RESEARCH_GOAL_DB_PATH"] = str(tmp_path / "goals.db")
    yield
    os.environ.pop("QUANTNODES_RESEARCH_GOAL_DB_PATH", None)


@pytest.fixture
def stores(tmp_path):
    study_store = StudyStore(db_path=tmp_path / "goals.db")
    goal_store = GoalStore()
    return study_store, goal_store


@pytest.fixture
def study(stores):
    study_store, goal_store = stores
    # Create a real goal in the DB so FK constraints pass
    goal = goal_store.replace_goal(
        session_id="test-sess",
        objective="test objective",
        criteria=["calmar >= 0.5"],
    )
    return study_store.create_study(
        owner_session_id="test-sess",
        goal_id=goal.goal_id,
        objective="test",
        workspace_path="/tmp/ws",
        strategy_name="demo",
        metric_targets=[
            {"name": "calmar", "op": ">=", "value": 0.5},
            {"name": "sharpe", "op": ">=", "value": 0.3},
        ],
    )


class TestMetricHelpers:
    def test_meets_metric_targets_pass(self):
        assert meets_metric_targets(
            {"calmar": 0.6, "sharpe": 0.4},
            [{"name": "calmar", "op": ">=", "value": 0.5},
             {"name": "sharpe", "op": ">=", "value": 0.3}],
        )

    def test_meets_metric_targets_fail(self):
        assert not meets_metric_targets(
            {"calmar": 0.4, "sharpe": 0.4},
            [{"name": "calmar", "op": ">=", "value": 0.5}],
        )

    def test_metric_pass_set(self):
        passed = _metric_pass_set(
            {"calmar": 0.6, "sharpe": 0.2, "max_dd": -0.1},
            [{"name": "calmar", "op": ">=", "value": 0.5},
             {"name": "sharpe", "op": ">=", "value": 0.3},
             {"name": "max_dd", "op": ">=", "value": -0.15}],
        )
        assert "calmar" in passed
        assert "sharpe" not in passed
        assert "max_dd" in passed


class TestRunnerAEGIS:
    def test_runner_completes_with_met_targets(self, stores, study, monkeypatch):
        """Runner should complete when metric targets are met."""
        study_store, goal_store = stores
        runner = AutoresearchRunner(study, study_store, goal_store=goal_store)

        def _round(self_runner, round_num, prev_summary, directive_text):
            return {
                "round": round_num, "run_name": f"run_{round_num:04d}",
                "metrics": {"calmar": 0.6, "sharpe": 0.4},
                "verdict": "keep",
                "e2_passed": True,
                "decision": {"stagnation_triggered": False},
                "agent_outputs": {},
                "summary": {"round": round_num, "metrics": {"calmar": 0.6}},
                "backtest_error": None,
                "passed_now": {"calmar", "sharpe"},
            }

        monkeypatch.setattr(AutoresearchRunner, "_run_one_round", _round)
        monkeypatch.setattr(AutoresearchRunner, "_round_cooldown", lambda self: 0.0)
        monkeypatch.setattr(AutoresearchRunner, "_maybe_load_previous_summary", lambda self, s: None)

        reason = asyncio.run(runner.run())
        assert reason == ShutdownReason.TARGETS_MET

    def test_runner_stops_at_max_rounds(self, stores, study, monkeypatch):
        """Runner should stop when max_rounds is reached."""
        study_store, goal_store = stores
        # Create study with max_rounds=2
        with StudyStore() as s:
            study2 = s.create_study(
                owner_session_id="test-sess2", goal_id="goal2",
                objective="test", workspace_path="/tmp/ws",
                strategy_name="demo",
                metric_targets=[{"name": "calmar", "op": ">=", "value": 99.0}],
                max_rounds=2,
            )
        runner = AutoresearchRunner(study2, study_store, goal_store=goal_store)

        def _round(self_runner, round_num, prev_summary, directive_text):
            return {
                "round": round_num, "run_name": f"run_{round_num:04d}",
                "metrics": {"calmar": 0.1}, "verdict": "discard",
                "decision": {"stagnation_triggered": False},
                "agent_outputs": {}, "summary": None,
                "backtest_error": None, "passed_now": set(),
            }

        monkeypatch.setattr(AutoresearchRunner, "_run_one_round", _round)
        monkeypatch.setattr(AutoresearchRunner, "_round_cooldown", lambda self: 0.0)
        monkeypatch.setattr(AutoresearchRunner, "_maybe_load_previous_summary", lambda self, s: None)

        reason = asyncio.run(runner.run())
        assert reason == ShutdownReason.MAX_ROUNDS

    def test_runner_early_stop(self, stores, study, monkeypatch):
        """Runner should early-stop after 3 idle rounds (when max_rounds set)."""
        study_store, goal_store = stores
        with StudyStore() as s:
            study2 = s.create_study(
                owner_session_id="test-sess3", goal_id="goal3",
                objective="test", workspace_path="/tmp/ws",
                strategy_name="demo",
                metric_targets=[{"name": "calmar", "op": ">=", "value": 99.0}],
                max_rounds=10,
            )
        runner = AutoresearchRunner(study2, study_store, goal_store=goal_store)

        call_count = {"n": 0}

        def _round(self_runner, round_num, prev_summary, directive_text):
            call_count["n"] += 1
            return {
                "round": round_num, "run_name": f"run_{round_num:04d}",
                "metrics": {"calmar": 0.1}, "verdict": "discard",
                "decision": {"stagnation_triggered": False},
                "agent_outputs": {}, "summary": None,
                "backtest_error": None, "passed_now": set(),
            }

        monkeypatch.setattr(AutoresearchRunner, "_run_one_round", _round)
        monkeypatch.setattr(AutoresearchRunner, "_round_cooldown", lambda self: 0.0)
        monkeypatch.setattr(AutoresearchRunner, "_maybe_load_previous_summary", lambda self, s: None)

        reason = asyncio.run(runner.run())
        assert reason == ShutdownReason.EARLY_STOPPED
        assert call_count["n"] == 4  # 3 idle + 1 triggered

    def test_runner_cancel(self, stores, study, monkeypatch):
        """Runner should stop when cancelled."""
        study_store, goal_store = stores
        control = ControlToken()
        runner = AutoresearchRunner(study, study_store, goal_store=goal_store, control=control)

        def _round(self_runner, round_num, prev_summary, directive_text):
            if round_num == 1:
                control.cancelled = True
            return {
                "round": round_num, "run_name": f"run_{round_num:04d}",
                "metrics": {"calmar": 0.1}, "verdict": "discard",
                "decision": {"stagnation_triggered": False},
                "agent_outputs": {}, "summary": None,
                "backtest_error": None, "passed_now": set(),
            }

        monkeypatch.setattr(AutoresearchRunner, "_run_one_round", _round)
        monkeypatch.setattr(AutoresearchRunner, "_round_cooldown", lambda self: 0.0)
        monkeypatch.setattr(AutoresearchRunner, "_maybe_load_previous_summary", lambda self, s: None)

        reason = asyncio.run(runner.run())
        assert reason == ShutdownReason.CANCELLED

    def test_runner_novelty_gate(self, stores, study, monkeypatch):
        """Runner should skip aborted rounds and continue."""
        study_store, goal_store = stores
        runner = AutoresearchRunner(study, study_store, goal_store=goal_store)

        call_count = {"n": 0}

        def _round(self_runner, round_num, prev_summary, directive_text):
            call_count["n"] += 1
            # First round: simulate abort (novelty rejected)
            if call_count["n"] == 1:
                return {"round": round_num, "run_name": f"run_{round_num:04d}",
                        "aborted": True, "reason": "novelty_rejected"}
            # Subsequent rounds: targets met
            return {
                "round": round_num, "run_name": f"run_{round_num:04d}",
                "metrics": {"calmar": 0.6, "sharpe": 0.4}, "verdict": "keep",
                "e2_passed": True,
                "decision": {"stagnation_triggered": False},
                "agent_outputs": {}, "summary": {"round": round_num},
                "backtest_error": None, "passed_now": {"calmar", "sharpe"},
            }

        monkeypatch.setattr(AutoresearchRunner, "_run_one_round", _round)
        monkeypatch.setattr(AutoresearchRunner, "_round_cooldown", lambda self: 0.0)
        monkeypatch.setattr(AutoresearchRunner, "_maybe_load_previous_summary", lambda self, s: None)

        reason = asyncio.run(runner.run())
        assert reason == ShutdownReason.TARGETS_MET
        assert call_count["n"] == 2  # 1 aborted + 1 completed
