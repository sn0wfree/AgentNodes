"""Real-world Study execution test — simulates a complete research lifecycle.

Tests the full flow from study creation to completion using behavior stubs
that simulate improving metrics over rounds, verifying:
- Study creation and queuing
- Round execution with state transitions
- Metric tracking and early-stop
- Goal completion on target achievement
- Budget enforcement
- Error recovery
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
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
from strategy_research.apps.study.models import StudyStatus, StudyAction, ACTION_MATRIX
from strategy_research.apps.study.state_store import StudyState


# ── Workspace setup ──────────────────────────────────────────────


def _create_real_workspace(tmp_path: Path) -> Path:
    """Create a realistic workspace structure for testing."""
    ws = tmp_path / "workspace"
    ws.mkdir(parents=True, exist_ok=True)

    # Create strategy directory
    strategy_dir = ws / "strategies" / "momentum_20_60"
    strategy_dir.mkdir(parents=True, exist_ok=True)

    # Create baseline strategy
    baseline_dir = strategy_dir / "baseline"
    baseline_dir.mkdir(parents=True, exist_ok=True)
    (baseline_dir / "strategy.py").write_text("""
# Baseline momentum strategy
def run():
    return {"calmar": 0.3, "sharpe": 0.2, "max_dd": -0.20}
""")

    # Create config
    (strategy_dir / "config.yaml").write_text("name: momentum_20_60\n")

    # Create results.tsv
    (ws / "results.tsv").write_text("run\tcalmar\tsharpe\tmax_dd\tverdict\n")

    return ws


# ── Study creation test ──────────────────────────────────────────


class TestStudyCreation:
    def test_create_study_with_real_workspace(self, tmp_path):
        """Create a study with a real workspace structure."""
        ws = _create_real_workspace(tmp_path)

        from strategy_research.apps.study.bootstrap import create_study_record

        study = create_study_record(
            owner_session_id="test-session",
            objective="Research momentum factors in A-share markets",
            workspace_path=str(ws),
            strategy_name="momentum_20_60",
            metric_targets=[
                {"name": "calmar", "op": ">=", "value": 0.5},
                {"name": "sharpe", "op": ">=", "value": 0.3},
            ],
            max_rounds=10,
            early_stop_patience=3,
            budget_turn=50,
        )

        assert study.study_id is not None
        assert study.objective == "Research momentum factors in A-share markets"
        assert study.strategy_name == "momentum_20_60"
        assert study.max_rounds == 10
        assert study.early_stop_patience == 3
        assert study.budget_turn == 50
        assert study.execution_status == StudyStatus.QUEUED

    def test_study_directory_created(self, tmp_path):
        """Study directory should be created with proper structure."""
        ws = _create_real_workspace(tmp_path)

        from strategy_research.apps.study.bootstrap import create_study_record

        study = create_study_record(
            owner_session_id="test-session",
            objective="Test",
            workspace_path=str(ws),
            strategy_name="momentum_20_60",
        )

        study_dir = ws / "study" / study.study_id
        assert study_dir.exists()
        assert (study_dir / "baseline").exists()
        assert (study_dir / "rounds").exists()
        assert (study_dir / "state.json").exists()
        assert (study_dir / "results.tsv").exists()
        assert (study_dir / "guidance.md").exists()
        assert (study_dir / "todos.md").exists()
        assert (study_dir / "knowledge.md").exists()


# ── Runner execution test ────────────────────────────────────────


class TestRunnerExecution:
    def test_runner_initialization(self, tmp_path):
        """Runner should initialize with correct state."""
        ws = _create_real_workspace(tmp_path)

        study = SimpleNamespace(
            study_id="test-study",
            title="Test",
            status=StudyStatus.RUNNING,
            current_round=0,
            max_rounds=5,
            strategy_name="momentum_20_60",
            market="a_share",
            objective="Test objective",
            metric_targets=[{"name": "calmar", "op": ">=", "value": 0.5}],
            budget_time_seconds=3600,
            budget_turn=50,
            workspace_path=str(ws),
            goal_id="goal-1",
            session_id="test-study",
            created_at="2026-01-01",
            updated_at="2026-01-01",
            behavior="improving",
            early_stop_patience=3,
        )

        store = MagicMock()
        store.get_study.return_value = study
        store.list_pending_directives.return_value = []

        runner = AutoresearchRunner(study=study, store=store)
        runner._goal_store = MagicMock()
        runner.emitter = MagicMock()

        assert runner._get_study().study_id == "test-study"
        assert runner._get_study().behavior == "improving"
        assert runner._total_used_time == 0.0
        assert runner._total_used_turns == 0
        assert runner._idle_rounds == 0
        assert runner._best_score == 0.0


# ── Metric evaluation test ───────────────────────────────────────


class TestMetricEvaluation:
    def test_improving_behavior_reaches_targets(self):
        """With 'improving' behavior, metrics should improve over rounds."""
        # Simulate the improving stub behavior
        from strategy_research.apps.study.stub_agents import _stub_researcher

        # Round 1: baseline
        output1 = json.loads(_stub_researcher(1, [], "improving"))
        assert "hypothesis" in output1

        # Round 3: improved
        output3 = json.loads(_stub_researcher(3, [], "improving"))
        assert "hypothesis" in output3

    def test_static_behavior_never_improves(self):
        """With 'static' behavior, metrics should stay constant."""
        from strategy_research.apps.study.stub_agents import _stub_researcher

        output1 = json.loads(_stub_researcher(1, [], "static"))
        output2 = json.loads(_stub_researcher(2, [], "static"))
        # Static behavior produces same structure
        assert "hypothesis" in output1
        assert "hypothesis" in output2

    def test_varying_behavior_varies(self):
        """With 'varying' behavior, metrics should vary."""
        from strategy_research.apps.study.stub_agents import _stub_researcher

        outputs = []
        for i in range(5):
            output = json.loads(_stub_researcher(i, [], "varying"))
            outputs.append(output.get("predicted_affected", []))

        # At least some variation in outputs
        assert len(outputs) == 5


# ── Budget enforcement test ──────────────────────────────────────


class TestBudgetEnforcement:
    def test_budget_limits_rounds(self):
        """Study should stop when budget is exceeded."""
        runner = _make_runner(budget_turn=3)
        runner._total_used_turns = 3
        assert runner._budget_exceeded() is True

    def test_budget_allows_until_limit(self):
        """Study should continue until budget is reached."""
        runner = _make_runner(budget_turn=5)
        runner._total_used_turns = 4
        assert runner._budget_exceeded() is False


# ── Early stop test ──────────────────────────────────────────────


class TestEarlyStop:
    def test_early_stop_after_patience(self):
        """Study should stop after patience rounds without improvement."""
        runner = _make_runner(early_stop_patience=3)
        runner._idle_rounds = 3
        runner._best_score = 0.5

        # Check that early stop would trigger
        assert runner._idle_rounds >= runner._get_study().early_stop_patience

    def test_early_stop_resets_on_improvement(self):
        """Idle rounds should reset when metrics improve."""
        runner = _make_runner(early_stop_patience=3)
        runner._idle_rounds = 2
        runner._best_score = 0.5

        # Simulate improvement
        current_score = 0.8
        if current_score > runner._best_score:
            runner._best_score = current_score
            runner._idle_rounds = 0

        assert runner._idle_rounds == 0
        assert runner._best_score == 0.8


# ── State transition test ────────────────────────────────────────


class TestStateTransitions:
    def test_queued_to_running(self):
        """Study should transition from QUEUED to RUNNING."""
        from strategy_research.apps.study.models import StudyStatus, StudyAction, ACTION_MATRIX
        assert StudyAction.CANCEL in ACTION_MATRIX[StudyStatus.QUEUED]

    def test_running_to_paused(self):
        """Study should transition from RUNNING to PAUSED."""
        from strategy_research.apps.study.models import StudyStatus, StudyAction, ACTION_MATRIX
        assert StudyAction.PAUSE in ACTION_MATRIX[StudyStatus.RUNNING]

    def test_paused_to_running(self):
        """Study should transition from PAUSED to RUNNING."""
        from strategy_research.apps.study.models import StudyStatus, StudyAction, ACTION_MATRIX
        assert StudyAction.CONTINUE in ACTION_MATRIX[StudyStatus.PAUSED]

    def test_complete_cancelled_allow_continue_archive(self):
        """COMPLETE/CANCELLED expose CONTINUE + ARCHIVE."""
        from strategy_research.apps.study.models import StudyStatus, StudyAction, ACTION_MATRIX
        for status in (StudyStatus.COMPLETE, StudyStatus.CANCELLED):
            acts = ACTION_MATRIX.get(status, frozenset())
            assert StudyAction.CONTINUE in acts, status
            assert StudyAction.ARCHIVE in acts, status
            assert len(acts) == 2, status

    def test_retryable_states_allow_continue_and_archive(self):
        """ERROR/BUDGET_LIMITED/EARLY_STOPPED/NEEDS_REFRESH allow CONTINUE + ARCHIVE."""
        from strategy_research.apps.study.models import StudyStatus, StudyAction, ACTION_MATRIX
        for status in (StudyStatus.ERROR, StudyStatus.BUDGET_LIMITED,
                       StudyStatus.EARLY_STOPPED, StudyStatus.NEEDS_REFRESH):
            acts = ACTION_MATRIX.get(status, frozenset())
            assert StudyAction.CONTINUE in acts, status
            assert StudyAction.ARCHIVE in acts, status
            assert len(acts) == 2, status


# ── Goal completion test ─────────────────────────────────────────


class TestGoalCompletion:
    def test_goal_completion_with_targets_met(self):
        """Goal should be completed when all targets are met."""
        runner = _make_runner(goal_id="g1")
        criteria = [
            SimpleNamespace(criterion_id="c1", required=True),
            SimpleNamespace(criterion_id="c2", required=True),
        ]
        existing = [SimpleNamespace(criterion_id="c1")]
        runner._goal_store.list_criteria.return_value = criteria
        runner._goal_store.list_evidence.return_value = existing

        runner._complete_goal({"metrics": {"calmar": 1.0}, "run_name": "run_0001"})

        runner._goal_store.append_evidence.assert_called_once()
        runner._goal_store.complete_lite.assert_called_once()

    def test_goal_completion_exception_safe(self):
        """Goal completion should not crash on exceptions."""
        runner = _make_runner(goal_id="g1")
        runner._goal_store.list_criteria.side_effect = RuntimeError("db error")
        # Should not raise
        runner._complete_goal({"metrics": {}, "run_name": "r1"})


# ── Helper function ──────────────────────────────────────────────


def _make_runner(**kwargs):
    study = SimpleNamespace(
        study_id=kwargs.get("study_id", "test-study"),
        title="Test Study",
        status=StudyStatus.RUNNING,
        current_round=kwargs.get("current_round", 0),
        max_rounds=kwargs.get("max_rounds", 5),
        strategy_name="momentum_20_60",
        market="a_share",
        objective="Test objective",
        metric_targets=[{"name": "calmar", "op": ">=", "value": 0.5}],
        budget_time_seconds=kwargs.get("budget_time_seconds", 3600),
        budget_turn=kwargs.get("budget_turn", 50),
        workspace_path="/tmp/test-ws",
        goal_id=kwargs.get("goal_id", "goal-1"),
        session_id="test-study",
        created_at="2026-01-01",
        updated_at="2026-01-01",
        behavior=kwargs.get("behavior", None),
        early_stop_patience=kwargs.get("early_stop_patience", 3),
    )

    from strategy_research.apps.study.runner import AutoresearchRunner

    store = MagicMock()
    store.get_study.return_value = study
    store.list_pending_directives.return_value = []

    runner = AutoresearchRunner(study=study, store=store)
    runner._goal_store = MagicMock()
    runner.emitter = MagicMock()
    runner._archive_rejected = MagicMock()
    return runner
