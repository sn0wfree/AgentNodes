"""Study Runner integration tests — novelty gate, regression, goal completion, evidence."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from strategy_research.apps.study.runner import (
    AutoresearchRunner,
    ShutdownReason,
)
from strategy_research.apps.study.models import StudyStatus


def _make_study(**kwargs):
    defaults = {
        "study_id": "test-study",
        "title": "Test Study",
        "status": StudyStatus.RUNNING,
        "current_round": 1,
        "max_rounds": 5,
        "strategy_name": "momentum_20_60",
        "market": "a_share",
        "objective": "Improve calmar ratio",
        "metric_targets": {"calmar_ratio": 1.0},
        "budget_time_seconds": 3600,
        "budget_turn": 50,
        "workspace_path": "/tmp/test-ws",
        "goal_id": "goal-1",
        "session_id": "test-study",
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
        "behavior": None,
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
    # Mock _archive_rejected to track calls
    runner._archive_rejected = MagicMock()
    return runner


# ── _novelty_gate ────────────────────────────────────────────────


class TestNoveltyGate:
    def test_novel_hypothesis_proceeds(self):
        runner = _make_runner()
        runner._check_novelty = MagicMock(return_value=(True, "novel approach"))
        result = runner._novelty_gate(1, "new momentum variant", ["calmar"])
        assert result is True
        runner._archive_rejected.assert_not_called()

    def test_non_novel_hypothesis_rejected(self):
        runner = _make_runner()
        runner._check_novelty = MagicMock(return_value=(False, "already tried"))
        result = runner._novelty_gate(1, "duplicate hypothesis", ["calmar"])
        assert result is False
        runner._archive_rejected.assert_called_once_with(
            1, "duplicate hypothesis", "novelty", "already tried"
        )

    def test_novelty_gate_emits_rejection(self):
        runner = _make_runner()
        runner._check_novelty = MagicMock(return_value=(False, "not novel"))
        runner._novelty_gate(2, "test hyp", ["sharpe"])
        emit_calls = [c for c in runner.emitter.emit.call_args_list]
        rejection_events = [c for c in emit_calls if len(c[0]) >= 2 and c[0][1] == "study_round_rejected"]
        assert len(rejection_events) >= 1


# ── _record_journal_and_regression ───────────────────────────────


class TestRecordJournalAndRegression:
    def test_appends_journal_entry(self):
        runner = _make_runner()
        runner._check_regression = MagicMock(return_value=(True, []))
        runner._record_journal_and_regression(
            round_num=1, hypothesis="test hypothesis",
            predicted_affected=["calmar"], lever="momentum_window",
            strategist_output={"changes": "window 20->30"},
            gating_outcome="pass", attribution={"calmar": "flipped"},
        )
        runner._goal_store.append_journal_entry.assert_called_once()

    def test_fills_attribution(self):
        runner = _make_runner()
        runner._check_regression = MagicMock(return_value=(True, []))
        runner._record_journal_and_regression(
            round_num=1, hypothesis="h", predicted_affected=["calmar"],
            lever="lever", strategist_output={}, gating_outcome="pass",
            attribution={"calmar": "flipped"},
        )
        runner._goal_store.fill_journal_attribution.assert_called_once()

    def test_regression_detected_archives(self):
        runner = _make_runner()
        runner._check_regression = MagicMock(return_value=(False, ["sharpe"]))
        runner._record_journal_and_regression(
            round_num=3, hypothesis="bad change", predicted_affected=["sharpe"],
            lever="leverage", strategist_output={}, gating_outcome="fail",
            attribution={"sharpe": "regressed"},
        )
        runner._archive_rejected.assert_called_once_with(
            3, "bad change", "regression", "['sharpe']"
        )

    def test_no_regression_no_archive(self):
        runner = _make_runner()
        runner._check_regression = MagicMock(return_value=(True, []))
        runner._record_journal_and_regression(
            round_num=1, hypothesis="h", predicted_affected=[],
            lever="l", strategist_output={}, gating_outcome="pass",
            attribution={},
        )
        runner._archive_rejected.assert_not_called()


# ── _complete_goal ────────────────────────────────────────────────


class TestCompleteGoal:
    def test_completes_goal_with_evidence(self):
        runner = _make_runner(goal_id="g1")
        criteria = [
            SimpleNamespace(criterion_id="c1", required=True),
            SimpleNamespace(criterion_id="c2", required=True),
        ]
        existing_evidence = [SimpleNamespace(criterion_id="c1")]
        runner._goal_store.list_criteria.return_value = criteria
        runner._goal_store.list_evidence.return_value = existing_evidence
        runner._complete_goal({"metrics": {"calmar": 1.5}, "run_name": "run_0001"})
        runner._goal_store.append_evidence.assert_called_once()
        runner._goal_store.complete_lite.assert_called_once()

    def test_no_goal_id_skips(self):
        runner = _make_runner(goal_id=None)
        runner._complete_goal({"metrics": {}, "run_name": "run_0001"})
        runner._goal_store.complete_lite.assert_not_called()

    def test_all_criteria_already_covered(self):
        runner = _make_runner(goal_id="g1")
        criteria = [SimpleNamespace(criterion_id="c1", required=True)]
        existing = [SimpleNamespace(criterion_id="c1")]
        runner._goal_store.list_criteria.return_value = criteria
        runner._goal_store.list_evidence.return_value = existing
        runner._complete_goal({"metrics": {}, "run_name": "r1"})
        runner._goal_store.append_evidence.assert_not_called()
        runner._goal_store.complete_lite.assert_called_once()

    def test_exception_does_not_crash(self):
        runner = _make_runner(goal_id="g1")
        runner._goal_store.list_criteria.side_effect = RuntimeError("db error")
        runner._complete_goal({"metrics": {}, "run_name": "r1"})


# ── _record_keep_evidence ────────────────────────────────────────


class TestRecordKeepEvidence:
    def test_appends_evidence(self):
        runner = _make_runner(goal_id="g1")
        evidence = SimpleNamespace(evidence_id="e1")
        runner._goal_store.append_evidence.return_value = evidence
        runner._goal_store.list_criteria.return_value = []
        runner._goal_store.list_evidence.return_value = []
        runner._record_keep_evidence(1, "run_0001", {"calmar": 1.5, "sharpe": 2.0})
        runner._goal_store.append_evidence.assert_called_once()

    def test_emits_study_evidence_event(self):
        runner = _make_runner(goal_id="g1")
        evidence = SimpleNamespace(evidence_id="e1")
        runner._goal_store.append_evidence.return_value = evidence
        runner._goal_store.list_criteria.return_value = []
        runner._goal_store.list_evidence.return_value = []
        runner._record_keep_evidence(1, "run_0001", {"calmar": 1.5})
        emit_calls = [c for c in runner.emitter.emit.call_args_list]
        evidence_events = [c for c in emit_calls if len(c[0]) >= 2 and c[0][1] == "study_evidence"]
        assert len(evidence_events) >= 1

    def test_emits_study_progress_event(self):
        runner = _make_runner(goal_id="g1")
        evidence = SimpleNamespace(evidence_id="e1")
        runner._goal_store.append_evidence.return_value = evidence
        criteria = [SimpleNamespace(criterion_id="c1"), SimpleNamespace(criterion_id="c2")]
        runner._goal_store.list_criteria.return_value = criteria
        runner._goal_store.list_evidence.return_value = [SimpleNamespace(criterion_id="c1")]
        runner._record_keep_evidence(1, "run_0001", {"calmar": 1.5})
        emit_calls = [c for c in runner.emitter.emit.call_args_list]
        progress_events = [c for c in emit_calls if len(c[0]) >= 2 and c[0][1] == "study_progress"]
        assert len(progress_events) >= 1
        progress_data = progress_events[0][0][2]
        assert progress_data["covered"] == 1
        assert progress_data["total"] == 2

    def test_exception_does_not_crash(self):
        runner = _make_runner(goal_id="g1")
        runner._goal_store.append_evidence.side_effect = RuntimeError("db error")
        runner._record_keep_evidence(1, "run_0001", {"calmar": 1.5})


# ── _check_stop_conditions ───────────────────────────────────────


class TestCheckStopConditions:
    def test_budget_exceeded(self):
        runner = _make_runner(budget_time_seconds=100)
        runner._total_used_time = 200
        runner._total_used_turns = 0
        reason = runner._check_stop_conditions(
            result={}, metrics={}, verdict="keep", round_num=1,
            session="test-study", sid="test-study",
        )
        assert reason == ShutdownReason.BUDGET

    def test_no_stop(self):
        runner = _make_runner()
        reason = runner._check_stop_conditions(
            result={}, metrics={}, verdict="keep", round_num=1,
            session="test-study", sid="test-study",
        )
        assert reason is None


# ── _budget_exceeded ─────────────────────────────────────────────


class TestBudgetExceeded:
    def test_time_exceeded(self):
        runner = _make_runner(budget_time_seconds=100)
        runner._total_used_time = 200
        assert runner._budget_exceeded() is True

    def test_turns_exceeded(self):
        runner = _make_runner(budget_turn=10)
        runner._total_used_turns = 20
        assert runner._budget_exceeded() is True

    def test_not_exceeded(self):
        runner = _make_runner(budget_time_seconds=3600, budget_turn=50)
        runner._total_used_time = 100
        runner._total_used_turns = 10
        assert runner._budget_exceeded() is False

    def test_no_budget(self):
        runner = _make_runner(budget_time_seconds=None, budget_turn=None)
        runner._total_used_time = 999999
        assert runner._budget_exceeded() is False
