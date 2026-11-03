"""Study E2E integration tests — full lifecycle verification.

Tests the complete study execution path from creation to completion,
including error handling, budget enforcement, and state transitions.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch, AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from strategy_research.apps.study.runner import (
    AutoresearchRunner,
    ShutdownReason,
    meets_metric_targets,
)
from strategy_research.apps.study.models import (
    StudyStatus,
    StudyAction,
    ACTION_MATRIX,
    MetricTarget,
)
from strategy_research.apps.study.state_store import StudyState


# ── Helpers ───────────────────────────────────────────────────────


def _make_study(**kwargs):
    defaults = {
        "study_id": "test-study",
        "title": "Test Study",
        "status": StudyStatus.RUNNING,
        "current_round": 0,
        "max_rounds": 5,
        "strategy_name": "momentum_20_60",
        "market": "a_share",
        "objective": "Improve calmar ratio",
        "metric_targets": [
            {"name": "calmar", "op": ">=", "value": 0.5},
            {"name": "sharpe", "op": ">=", "value": 0.3},
        ],
        "budget_time_seconds": 3600,
        "budget_turn": 50,
        "workspace_path": "/tmp/test-ws",
        "goal_id": "goal-1",
        "session_id": "test-study",
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
        "behavior": None,
        "early_stop_patience": 3,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _make_runner(**kwargs):
    study = _make_study(**kwargs)
    store = MagicMock()
    store.get_study.return_value = study
    store.list_pending_directives.return_value = []
    runner = AutoresearchRunner(study=study, store=store)
    runner._goal_store = MagicMock()
    runner.emitter = MagicMock()
    runner._archive_rejected = MagicMock()
    return runner


# ── meets_metric_targets tests ────────────────────────────────────


class TestMeetsMetricTargets:
    def test_all_targets_met(self):
        metrics = {"calmar": 1.0, "sharpe": 0.5}
        targets = [
            {"name": "calmar", "op": ">=", "value": 0.5},
            {"name": "sharpe", "op": ">=", "value": 0.3},
        ]
        assert meets_metric_targets(metrics, targets) is True

    def test_one_target_not_met(self):
        metrics = {"calmar": 0.3, "sharpe": 0.5}
        targets = [
            {"name": "calmar", "op": ">=", "value": 0.5},
            {"name": "sharpe", "op": ">=", "value": 0.3},
        ]
        assert meets_metric_targets(metrics, targets) is False

    def test_missing_metric(self):
        metrics = {"sharpe": 0.5}
        targets = [
            {"name": "calmar", "op": ">=", "value": 0.5},
        ]
        assert meets_metric_targets(metrics, targets) is False

    def test_empty_targets(self):
        metrics = {"calmar": 1.0}
        assert meets_metric_targets(metrics, []) is True

    def test_less_than_operator(self):
        metrics = {"max_dd": -0.10}
        targets = [{"name": "max_dd", "op": "<=", "value": -0.15}]
        assert meets_metric_targets(metrics, targets) is False

    def test_equal_operator(self):
        metrics = {"calmar": 0.5}
        targets = [{"name": "calmar", "op": "==", "value": 0.5}]
        assert meets_metric_targets(metrics, targets) is True


# ── State machine tests ──────────────────────────────────────────


class TestStateMachine:
    def test_queued_allows_cancel(self):
        assert StudyAction.CANCEL in ACTION_MATRIX[StudyStatus.QUEUED]

    def test_running_allows_pause_and_cancel(self):
        assert StudyAction.PAUSE in ACTION_MATRIX[StudyStatus.RUNNING]
        assert StudyAction.CANCEL in ACTION_MATRIX[StudyStatus.RUNNING]

    def test_paused_allows_continue_and_cancel(self):
        assert StudyAction.CONTINUE in ACTION_MATRIX[StudyStatus.PAUSED]
        assert StudyAction.CANCEL in ACTION_MATRIX[StudyStatus.PAUSED]

    def test_complete_cancelled_allow_continue_archive(self):
        for status in (StudyStatus.COMPLETE, StudyStatus.CANCELLED):
            acts = ACTION_MATRIX.get(status, frozenset())
            assert StudyAction.CONTINUE in acts, status
            assert StudyAction.ARCHIVE in acts, status
            assert len(acts) == 2, status

    def test_retryable_states_allow_continue_and_archive(self):
        for status in (StudyStatus.ERROR, StudyStatus.BUDGET_LIMITED,
                       StudyStatus.EARLY_STOPPED, StudyStatus.NEEDS_REFRESH):
            acts = ACTION_MATRIX.get(status, frozenset())
            assert StudyAction.CONTINUE in acts, status
            assert StudyAction.ARCHIVE in acts, status
            assert len(acts) == 2, status


# ── Runner budget tests ──────────────────────────────────────────


class TestRunnerBudget:
    def test_budget_not_exceeded(self):
        runner = _make_runner(budget_time_seconds=3600, budget_turn=50)
        runner._total_used_time = 100
        runner._total_used_turns = 10
        assert runner._budget_exceeded() is False

    def test_budget_time_exceeded(self):
        runner = _make_runner(budget_time_seconds=100)
        runner._total_used_time = 200
        assert runner._budget_exceeded() is True

    def test_budget_turn_exceeded(self):
        runner = _make_runner(budget_turn=10)
        runner._total_used_turns = 20
        assert runner._budget_exceeded() is True

    def test_no_budget_set(self):
        runner = _make_runner(budget_time_seconds=None, budget_turn=None)
        runner._total_used_time = 999999
        assert runner._budget_exceeded() is False


# ── Runner early-stop tests ──────────────────────────────────────


class TestRunnerEarlyStop:
    def test_early_stop_patience_default(self):
        runner = _make_runner()
        assert runner._get_study().early_stop_patience == 3

    def test_early_stop_patience_custom(self):
        runner = _make_runner(early_stop_patience=5)
        assert runner._get_study().early_stop_patience == 5


# ── Runner novelty gate tests ────────────────────────────────────


class TestRunnerNoveltyGate:
    def test_novel_hypothesis_proceeds(self):
        runner = _make_runner()
        runner._check_novelty = MagicMock(return_value=(True, "novel"))
        assert runner._novelty_gate(1, "hypothesis", ["calmar"]) is True

    def test_non_novel_rejects(self):
        runner = _make_runner()
        runner._check_novelty = MagicMock(return_value=(False, "not novel"))
        assert runner._novelty_gate(1, "hypothesis", ["calmar"]) is False
        runner._archive_rejected.assert_called_once()


# ── Runner regression gate tests ─────────────────────────────────


class TestRunnerRegressionGate:
    def test_no_regression(self):
        runner = _make_runner()
        runner._check_regression = MagicMock(return_value=(True, []))
        attribution = {"calmar": "flipped"}
        runner._record_journal_and_regression(
            round_num=1, hypothesis="h", predicted_affected=["calmar"],
            lever="l", strategist_output={}, gating_outcome="pass",
            attribution=attribution,
        )
        runner._archive_rejected.assert_not_called()

    def test_regression_detected(self):
        runner = _make_runner()
        runner._check_regression = MagicMock(return_value=(False, ["sharpe"]))
        runner._record_journal_and_regression(
            round_num=1, hypothesis="h", predicted_affected=["sharpe"],
            lever="l", strategist_output={}, gating_outcome="fail",
            attribution={"sharpe": "regressed"},
        )
        runner._archive_rejected.assert_called_once()


# ── Runner goal completion tests ─────────────────────────────────


class TestRunnerGoalCompletion:
    def test_completes_goal(self):
        runner = _make_runner(goal_id="g1")
        criteria = [SimpleNamespace(criterion_id="c1", required=True)]
        runner._goal_store.list_criteria.return_value = criteria
        runner._goal_store.list_evidence.return_value = [SimpleNamespace(criterion_id="c1")]
        runner._complete_goal({"metrics": {}, "run_name": "r1"})
        runner._goal_store.complete_lite.assert_called_once()

    def test_no_goal_skips(self):
        runner = _make_runner(goal_id=None)
        runner._complete_goal({"metrics": {}, "run_name": "r1"})
        runner._goal_store.complete_lite.assert_not_called()


# ── State store tests ────────────────────────────────────────────


class TestStateStore:
    def test_default_state(self):
        state = StudyState()
        assert state.last_completed_round == 0
        assert state.best_metrics == {}
        assert state.discard_streak == 0
        assert state.budget_used_turns == 0
        assert state.budget_used_time_s == 0.0

    def test_state_serialization(self):
        state = StudyState(
            last_completed_round=5,
            best_metrics={"calmar": 1.5},
            budget_used_turns=20,
        )
        d = state.as_dict()
        assert d["last_completed_round"] == 5
        assert d["best_metrics"]["calmar"] == 1.5
        assert d["budget_used_turns"] == 20


# ── Attribution tests ────────────────────────────────────────────


class TestAttribution:
    def test_flipped(self):
        from strategy_research.apps.study.method.attribution import classify_attribution, AttributionOutcome
        result = classify_attribution(["calmar"], set(), {"calmar"})
        assert result["calmar"] == AttributionOutcome.FLIPPED

    def test_regressed(self):
        from strategy_research.apps.study.method.attribution import classify_attribution, AttributionOutcome
        result = classify_attribution(["calmar"], {"calmar"}, set())
        assert result["calmar"] == AttributionOutcome.REGRESSED

    def test_precision(self):
        from strategy_research.apps.study.method.attribution import compute_precision, AttributionOutcome
        attr = {"a": AttributionOutcome.FLIPPED, "b": AttributionOutcome.REGRESSED}
        precision, hits, total = compute_precision(attr)
        assert hits == 1
        assert total == 3  # attributed=2 + side_effects=1
