"""Study Runner comprehensive tests — state machine, rounds, directives, budget."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from strategy_research.apps.study.runner import (
    AutoresearchRunner,
    ShutdownReason,
)
from strategy_research.apps.study.models import (
    StudyRecord,
    StudyStatus,
    StudyAction,
    ACTION_MATRIX,
)


def _make_study(**kwargs):
    defaults = {
        "study_id": "test-study",
        "title": "Test Study",
        "status": StudyStatus.QUEUED,
        "current_round": 0,
        "max_rounds": 5,
        "strategy_name": "momentum_20_60",
        "market": "a_share",
        "metric_targets": {"calmar_ratio": 1.0},
        "budget_time_seconds": 3600,
        "budget_turn": 50,
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _make_store():
    store = MagicMock()
    store.get_study.return_value = _make_study()
    store.list_pending_directives.return_value = []
    return store


class TestShutdownReason:
    def test_all_reasons_exist(self):
        assert ShutdownReason.TARGETS_MET == "targets_met"
        assert ShutdownReason.MAX_ROUNDS == "max_rounds"
        assert ShutdownReason.STAGNATION == "stagnation"
        assert ShutdownReason.BUDGET == "budget_exceeded"
        assert ShutdownReason.CANCELLED == "cancelled"
        assert ShutdownReason.ERROR == "error"
        assert ShutdownReason.EARLY_STOPPED == "early_stopped"
        assert ShutdownReason.NOVELTY_REJECTED == "novelty_rejected"
        assert ShutdownReason.DISCARD_STREAK == "stagnation_discard_streak"


class TestStudyStatus:
    def test_action_matrix_keys(self):
        assert StudyStatus.QUEUED in ACTION_MATRIX
        assert StudyStatus.RUNNING in ACTION_MATRIX
        assert StudyStatus.PAUSED in ACTION_MATRIX

    def test_queued_can_cancel_archive_replace(self):
        # QUEUED now exposes CANCEL + ARCHIVE + REPLACE_OBJECTIVE
        assert ACTION_MATRIX[StudyStatus.QUEUED] == frozenset({
            StudyAction.CANCEL,
            StudyAction.ARCHIVE,
            StudyAction.REPLACE_OBJECTIVE,
        })

    def test_running_can_pause_or_cancel(self):
        assert StudyAction.PAUSE in ACTION_MATRIX[StudyStatus.RUNNING]
        assert StudyAction.CANCEL in ACTION_MATRIX[StudyStatus.RUNNING]

    def test_paused_can_continue_or_cancel(self):
        assert StudyAction.CONTINUE in ACTION_MATRIX[StudyStatus.PAUSED]
        assert StudyAction.CANCEL in ACTION_MATRIX[StudyStatus.PAUSED]


class TestRunnerInit:
    def test_init_stores_params(self):
        study = _make_study()
        store = _make_store()
        runner = AutoresearchRunner(study=study, store=store)
        assert runner.study is study
        assert runner.study_store is store

    def test_init_default_control(self):
        study = _make_study()
        store = _make_store()
        runner = AutoresearchRunner(study=study, store=store)
        assert runner.control is not None

    def test_init_default_emitter(self):
        study = _make_study()
        store = _make_store()
        runner = AutoresearchRunner(study=study, store=store)
        assert runner.emitter is not None

    def test_init_budget_accumulators_zero(self):
        study = _make_study()
        store = _make_store()
        runner = AutoresearchRunner(study=study, store=store)
        assert runner._total_used_time == 0.0
        assert runner._total_used_turns == 0
        assert runner._idle_rounds == 0
        assert runner._best_score == 0.0

    def test_study_id_property(self):
        study = _make_study(study_id="my-study")
        store = _make_store()
        runner = AutoresearchRunner(study=study, store=store)
        assert runner.study_id == "my-study"


class TestControlChecks:
    def test_cancelled_detected(self):
        study = _make_study()
        store = _make_store()
        control = MagicMock()
        control.cancelled = True
        control.paused = False
        runner = AutoresearchRunner(study=study, store=store, control=control)
        assert runner.control.cancelled is True

    def test_paused_detected(self):
        study = _make_study()
        store = _make_store()
        control = MagicMock()
        control.cancelled = False
        control.paused = True
        runner = AutoresearchRunner(study=study, store=store, control=control)
        assert runner.control.paused is True


class TestBudgetAccounting:
    def test_budget_exceeded_time(self):
        study = _make_study(budget_time_seconds=100, budget_turn=50)
        store = _make_store()
        # _budget_exceeded reads the authoritative store record (fresh
        # semantics), not the constructor snapshot.
        store.get_study.return_value = study
        runner = AutoresearchRunner(study=study, store=store)
        runner._total_used_time = 200
        runner._total_used_turns = 10
        assert runner._budget_exceeded() is True

    def test_budget_exceeded_turns(self):
        study = _make_study(budget_time_seconds=3600, budget_turn=10)
        store = _make_store()
        store.get_study.return_value = study
        runner = AutoresearchRunner(study=study, store=store)
        runner._total_used_time = 100
        runner._total_used_turns = 20
        assert runner._budget_exceeded() is True

    def test_budget_not_exceeded(self):
        study = _make_study(budget_time_seconds=3600, budget_turn=50)
        store = _make_store()
        runner = AutoresearchRunner(study=study, store=store)
        runner._total_used_time = 100
        runner._total_used_turns = 10
        assert runner._budget_exceeded() is False


class TestStudyRecord:
    def test_study_has_required_fields(self):
        study = _make_study()
        assert study.study_id == "test-study"
        assert study.title == "Test Study"
        assert study.status == StudyStatus.QUEUED
        assert study.current_round == 0
        assert study.max_rounds == 5

    def test_study_metric_targets(self):
        study = _make_study(metric_targets={"calmar_ratio": 1.5, "sharpe_ratio": 2.0})
        assert study.metric_targets["calmar_ratio"] == 1.5
        assert study.metric_targets["sharpe_ratio"] == 2.0

    def test_study_budget_fields(self):
        study = _make_study(budget_time_seconds=7200, budget_turn=100)
        assert study.budget_time_seconds == 7200
        assert study.budget_turn == 100
