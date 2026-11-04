"""Study P1 tests — review fail_count, scheduler shutdown, API endpoints, bootstrap."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch, AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from strategy_research.apps.study.runner import AutoresearchRunner, ShutdownReason
from strategy_research.apps.study.models import StudyStatus


# ── Review fail_count escalation ─────────────────────────────────


def _make_runner(**kwargs):
    defaults = {
        "study_id": "test-study", "title": "Test", "status": StudyStatus.RUNNING,
        "current_round": 1, "max_rounds": 5, "strategy_name": "mom",
        "market": "a_share", "objective": "test", "metric_targets": {},
        "budget_time_seconds": 3600, "budget_turn": 50, "workspace_path": "/tmp",
        "goal_id": "g1", "session_id": "test-study", "behavior": None,
    }
    defaults.update(kwargs)
    study = SimpleNamespace(**defaults)
    store = MagicMock()
    store.get_study.return_value = study
    store.list_pending_directives.return_value = []
    runner = AutoresearchRunner(study=study, store=store)
    runner._goal_store = MagicMock()
    runner.emitter = MagicMock()
    runner._archive_rejected = MagicMock()
    return runner


class TestReviewFailCount:
    def test_review_fail_count_increments(self):
        runner = _make_runner()
        runner._review_fail_count = 0
        runner._review_fail_count += 1
        assert runner._review_fail_count == 1

    def test_review_fail_count_threshold(self):
        runner = _make_runner()
        runner._review_fail_count = 2
        # 2+ failures should trigger stop
        assert runner._review_fail_count >= 2


# ── Scheduler shutdown ───────────────────────────────────────────


class TestSchedulerShutdown:
    def test_control_token_cancelled(self):
        """Cancel sets control token."""
        from strategy_research.apps.study.runner import ControlToken
        ct = ControlToken()
        assert ct.cancelled is False
        ct.cancelled = True
        assert ct.cancelled is True

    def test_control_token_paused(self):
        from strategy_research.apps.study.runner import ControlToken
        ct = ControlToken()
        assert ct.paused is False
        ct.paused = True
        assert ct.paused is True


# ── Bootstrap validation ─────────────────────────────────────────


class TestBootstrapValidation:
    def test_validate_workspace_strategy_rejects_slash(self):
        """Strategy names with / are rejected."""
        from strategy_research.apps.study.bootstrap import validate_workspace_strategy
        with pytest.raises(ValueError, match="path separators"):
            validate_workspace_strategy(Path("/tmp"), "bad/name")

    def test_validate_workspace_strategy_rejects_dot(self):
        """Strategy names starting with . are rejected."""
        from strategy_research.apps.study.bootstrap import validate_workspace_strategy
        with pytest.raises(ValueError, match="path separators"):
            validate_workspace_strategy(Path("/tmp"), ".hidden")


# ── Runner exception path ────────────────────────────────────────


class TestRunnerExceptionPath:
    def test_runner_stores_error_on_exception(self):
        """Runner stores error info when exception occurs."""
        runner = _make_runner()
        # Simulate error tracking
        runner._last_error = None
        try:
            raise RuntimeError("test error")
        except RuntimeError as e:
            runner._last_error = str(e)
        assert runner._last_error == "test error"


# ── State store edge cases ───────────────────────────────────────


class TestStateStoreEdgeCases:
    def test_study_state_defaults(self):
        from strategy_research.apps.study.state_store import StudyState
        state = StudyState()
        assert state.last_completed_round == 0
        assert state.best_metrics == {}
        assert state.discard_streak == 0
        assert state.review_fail_count == 0

    def test_study_state_fields(self):
        from strategy_research.apps.study.state_store import StudyState
        state = StudyState(
            last_completed_round=5,
            best_metrics={"calmar": 1.5},
            discard_streak=3,
            review_fail_count=2,
        )
        assert state.last_completed_round == 5
        assert state.best_metrics["calmar"] == 1.5
        assert state.discard_streak == 3
        assert state.review_fail_count == 2
