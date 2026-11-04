"""Study concurrency tests — verifying multi-study parallel execution."""

from __future__ import annotations

import asyncio
import sys
import threading
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


def _make_study(study_id: str = "test-study", **kwargs):
    defaults = {
        "study_id": study_id,
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


def _make_runner(study_id: str = "test-study", **kwargs):
    study = _make_study(study_id=study_id, **kwargs)
    store = MagicMock()
    store.get_study.return_value = study
    store.list_pending_directives.return_value = []
    # Pass goal_store at construction — __init__ otherwise opens a REAL
    # goal DB connection per runner (own_goal_store=True) that nothing
    # closes. 10 concurrent runners = 10 leaked SQLite connections and
    # the classic 'database is locked' flake in the suite after this one.
    runner = AutoresearchRunner(study=study, store=store, goal_store=MagicMock())
    runner.emitter = MagicMock()
    runner._archive_rejected = MagicMock()
    return runner


# ── Runner independence tests ────────────────────────────────────


class TestRunnerIndependence:
    def test_runners_are_independent(self):
        """Each runner instance should be completely independent."""
        r1 = _make_runner(study_id="s1")
        r2 = _make_runner(study_id="s2")
        
        r1._total_used_time = 100
        r1._total_used_turns = 10
        r1._idle_rounds = 2
        r1._best_score = 1.5
        
        # r2 should be unaffected
        assert r2._total_used_time == 0
        assert r2._total_used_turns == 0
        assert r2._idle_rounds == 0
        assert r2._best_score == 0.0

    def test_runners_have_separate_study_data(self):
        """Each runner should have its own study data."""
        r1 = _make_runner(study_id="s1", objective="Objective 1")
        r2 = _make_runner(study_id="s2", objective="Objective 2")
        
        assert r1._get_study().objective == "Objective 1"
        assert r2._get_study().objective == "Objective 2"

    def test_runners_have_separate_goal_stores(self):
        """Each runner should have its own goal store mock."""
        r1 = _make_runner(study_id="s1")
        r2 = _make_runner(study_id="s2")
        
        r1._goal_store.list_criteria.return_value = [SimpleNamespace(criterion_id="c1")]
        # r2 should be unaffected (MagicMock returns empty list by default)
        result = r2._goal_store.list_criteria()
        # MagicMock returns a new MagicMock, not an empty list
        # The key point is that r1's mock is different from r2's mock
        assert r1._goal_store is not r2._goal_store


# ── Thread safety tests ─────────────────────────────────────────


class TestThreadSafety:
    def test_concurrent_runner_creation(self):
        """Multiple runners can be created concurrently."""
        runners = []
        errors = []
        
        def create_runner(study_id):
            try:
                r = _make_runner(study_id=study_id)
                runners.append(r)
            except Exception as e:
                errors.append(e)
        
        threads = [threading.Thread(target=create_runner, args=(f"s{i}",)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        
        assert len(errors) == 0
        assert len(runners) == 10

    def test_concurrent_budget_updates(self):
        """Budget updates should be thread-safe (dict assignment)."""
        runner = _make_runner()
        errors = []
        
        def update_budget():
            try:
                for _ in range(100):
                    runner._total_used_time += 0.1
                    runner._total_used_turns += 1
            except Exception as e:
                errors.append(e)
        
        threads = [threading.Thread(target=update_budget) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        
        assert len(errors) == 0
        # Final value should be 400 (4 threads * 100 iterations)
        assert runner._total_used_turns == 400


# ── State isolation tests ────────────────────────────────────────


class TestStateIsolation:
    def test_study_state_per_runner(self):
        """Each runner should have its own study state."""
        r1 = _make_runner(study_id="s1")
        r2 = _make_runner(study_id="s2")
        
        # Modify r1's study
        r1._get_study().current_round = 5
        r2._get_study().current_round = 10
        
        assert r1._get_study().current_round == 5
        assert r2._get_study().current_round == 10

    def test_emitter_per_runner(self):
        """Each runner should have its own emitter."""
        r1 = _make_runner(study_id="s1")
        r2 = _make_runner(study_id="s2")
        
        r1.emitter.emit("s1", "test_event", {"key": "value"})
        # r2's emitter should not have been called
        r2.emitter.emit.assert_not_called()


# ── Budget isolation tests ───────────────────────────────────────


class TestBudgetIsolation:
    def test_separate_budgets(self):
        """Each runner should have independent budget tracking."""
        r1 = _make_runner(budget_turn=10)
        r2 = _make_runner(budget_turn=20)
        
        r1._total_used_turns = 10
        r2._total_used_turns = 15
        
        # r1 exceeded, r2 not
        assert r1._budget_exceeded() is True
        assert r2._budget_exceeded() is False

    def test_budget_reset_independence(self):
        """Resetting one runner's budget shouldn't affect another."""
        r1 = _make_runner()
        r2 = _make_runner()
        
        r1._total_used_time = 100
        r2._total_used_time = 200
        
        r1._total_used_time = 0
        
        assert r1._total_used_time == 0
        assert r2._total_used_time == 200
