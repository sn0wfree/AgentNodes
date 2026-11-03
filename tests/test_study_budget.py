"""Study budget enforcement tests — verifying resource limits work correctly."""

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
from strategy_research.apps.study.models import StudyStatus


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
        "metric_targets": [{"name": "calmar", "op": ">=", "value": 0.5}],
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


# ── Time budget tests ────────────────────────────────────────────


class TestTimeBudget:
    def test_time_budget_not_set(self):
        """No time budget means no time limit."""
        runner = _make_runner(budget_time_seconds=None)
        runner._total_used_time = 999999
        assert runner._budget_exceeded() is False

    def test_time_budget_not_exceeded(self):
        runner = _make_runner(budget_time_seconds=3600)
        runner._total_used_time = 1000
        assert runner._budget_exceeded() is False

    def test_time_budget_exceeded(self):
        runner = _make_runner(budget_time_seconds=100)
        runner._total_used_time = 150
        assert runner._budget_exceeded() is True

    def test_time_budget_exact_limit(self):
        runner = _make_runner(budget_time_seconds=100)
        runner._total_used_time = 100
        assert runner._budget_exceeded() is True

    def test_time_budget_zero(self):
        """Zero time budget is treated as a valid limit (0 >= 0 is True)."""
        runner = _make_runner(budget_time_seconds=0)
        runner._total_used_time = 0
        assert runner._budget_exceeded() is True  # 0 >= 0 is True


# ── Turn budget tests ────────────────────────────────────────────


class TestTurnBudget:
    def test_turn_budget_not_set(self):
        """No turn budget means no turn limit."""
        runner = _make_runner(budget_turn=None)
        runner._total_used_turns = 999999
        assert runner._budget_exceeded() is False

    def test_turn_budget_not_exceeded(self):
        runner = _make_runner(budget_turn=50)
        runner._total_used_turns = 30
        assert runner._budget_exceeded() is False

    def test_turn_budget_exceeded(self):
        runner = _make_runner(budget_turn=10)
        runner._total_used_turns = 15
        assert runner._budget_exceeded() is True

    def test_turn_budget_exact_limit(self):
        runner = _make_runner(budget_turn=10)
        runner._total_used_turns = 10
        assert runner._budget_exceeded() is True

    def test_turn_budget_zero(self):
        """Zero turn budget is treated as a valid limit (0 >= 0 is True)."""
        runner = _make_runner(budget_turn=0)
        runner._total_used_turns = 0
        assert runner._budget_exceeded() is True  # 0 >= 0 is True


# ── Combined budget tests ────────────────────────────────────────


class TestCombinedBudget:
    def test_both_budgets_not_exceeded(self):
        runner = _make_runner(budget_time_seconds=3600, budget_turn=50)
        runner._total_used_time = 1000
        runner._total_used_turns = 20
        assert runner._budget_exceeded() is False

    def test_time_exceeded_turn_ok(self):
        runner = _make_runner(budget_time_seconds=100, budget_turn=50)
        runner._total_used_time = 200
        runner._total_used_turns = 10
        assert runner._budget_exceeded() is True

    def test_time_ok_turn_exceeded(self):
        runner = _make_runner(budget_time_seconds=3600, budget_turn=10)
        runner._total_used_time = 100
        runner._total_used_turns = 20
        assert runner._budget_exceeded() is True

    def test_both_exceeded(self):
        runner = _make_runner(budget_time_seconds=100, budget_turn=10)
        runner._total_used_time = 200
        runner._total_used_turns = 20
        assert runner._budget_exceeded() is True


# ── Budget summary tests ─────────────────────────────────────────


class TestBudgetSummary:
    def test_budget_summary_format(self):
        runner = _make_runner()
        runner._total_used_time = 123.456
        runner._total_used_turns = 10
        summary = runner._budget_summary()
        assert "turns_used=10" in summary
        assert "time_used=123.5s" in summary


# ── Early stop patience tests ────────────────────────────────────


class TestEarlyStopPatience:
    def test_default_patience(self):
        runner = _make_runner()
        assert runner._get_study().early_stop_patience == 3

    def test_custom_patience(self):
        runner = _make_runner(early_stop_patience=5)
        assert runner._get_study().early_stop_patience == 5

    def test_patience_independent_of_rounds(self):
        runner = _make_runner(early_stop_patience=10, max_rounds=20)
        assert runner._get_study().early_stop_patience == 10
        assert runner._get_study().max_rounds == 20
