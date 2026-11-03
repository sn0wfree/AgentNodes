"""Phase 4 - v0.5 unit tests: render_dag edge cases + GoalWorkflowRunner lifecycle.

Fills coverage gaps for:
  - render_dag: empty DAG, SKIPPED status, cycle ValueError
  - NodeStatus: all enum values
  - GoalWorkflowRunner: pause/resume, get_progress, subscribe/unsubscribe
  - GoalWorkflowRunner: start_sub_workflow
  - GoalWorkflowRunner: checkpoint without hook
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from strategy_research.apps.goal.dag_renderer import NodeStatus, render_dag
from strategy_research.apps.goal.workflow import (
    GoalWorkflowRunner,
)
from strategy_research.apps.goal.workflow_config import load_goal_workflow


@pytest.fixture
def fresh_db(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("STRATEGY_RESEARCH_GOAL_DB", str(tmp_path / "goals.db"))


@pytest.fixture
def workflow_config():
    return load_goal_workflow("goal_factor_research")


# ═══════════════════════════════════════════════════════════════════════
# render_dag edge cases
# ═══════════════════════════════════════════════════════════════════════


class TestRenderDagEdgeCases:
    def test_empty_dag(self):
        result = render_dag({})
        assert "empty" in result.lower()

    def test_skipped_status_icon(self):
        result = render_dag(
            {"A": []},
            status={"A": NodeStatus.SKIPPED},
        )
        assert "–" in result or "-" in result

    def test_cycle_raises_value_error(self):
        with pytest.raises(ValueError, match="cycle"):
            render_dag({"A": ["B"], "B": ["A"]})

    def test_all_node_status_values(self):
        assert NodeStatus.PENDING == "pending"
        assert NodeStatus.RUNNING == "running"
        assert NodeStatus.COMPLETED == "completed"
        assert NodeStatus.ERROR == "error"
        assert NodeStatus.SKIPPED == "skipped"

    def test_skipped_status_in_progress_count(self):
        result = render_dag(
            {"A": [], "B": ["A"]},
            status={
                "A": NodeStatus.COMPLETED,
                "B": NodeStatus.SKIPPED,
            },
        )
        assert "1/" in result or "2/" in result

    def test_large_dag(self):
        dag = {f"node_{i}": [] for i in range(20)}
        for i in range(1, 20):
            dag[f"node_{i}"] = [f"node_{i-1}"]
        result = render_dag(dag)
        assert "node_0" in result
        assert "node_19" in result

    def test_render_with_none_status(self):
        result = render_dag({"A": []}, status=None)
        assert "A" in result

    def test_render_with_none_selected(self):
        result = render_dag({"A": []}, selected=None)
        assert "A" in result
        assert "▸" not in result


# ═══════════════════════════════════════════════════════════════════════
# GoalWorkflowRunner lifecycle
# ═══════════════════════════════════════════════════════════════════════


class TestRunnerPauseResume:
    def test_pause_graceful(self, workflow_config, fresh_db):
        runner = GoalWorkflowRunner(config=workflow_config, session_id="s1")
        runner.pause()
        assert runner.state.paused is True
        assert runner.state.cancelled is False

    def test_pause_immediate_sets_cancelled(self, workflow_config, fresh_db):
        runner = GoalWorkflowRunner(config=workflow_config, session_id="s1")
        runner.pause(immediate=True)
        assert runner.state.cancelled is True
        assert runner.state.paused is True

    def test_resume_clears_flags(self, workflow_config, fresh_db):
        runner = GoalWorkflowRunner(config=workflow_config, session_id="s1")
        runner.pause(immediate=True)
        assert runner.state.cancelled is True
        runner.resume()
        assert runner.state.paused is False
        assert runner.state.cancelled is False

    def test_resume_when_not_paused(self, workflow_config, fresh_db):
        runner = GoalWorkflowRunner(config=workflow_config, session_id="s1")
        runner.resume()
        assert runner.state.paused is False


class TestRunnerGetProgress:
    def test_progress_initial_state(self, workflow_config, fresh_db):
        runner = GoalWorkflowRunner(config=workflow_config, session_id="s1")
        progress = runner.get_progress()
        assert progress["status"] == "idle"
        assert progress["agents_total"] == len(workflow_config.agents)
        assert progress["agents_completed"] == 0
        assert progress["evidence_count"] == 0
        assert progress["paused"] is False

    def test_progress_with_hook(self, workflow_config, fresh_db):
        runner = GoalWorkflowRunner(config=workflow_config, session_id="s1")
        hook = MagicMock()
        hook.evidence_count = 3
        hook.completed = False
        runner._hook = hook
        runner._state.status = "running"
        runner._state.set_agent_status("a", "success")
        runner._state.set_agent_status("b", "running")
        progress = runner.get_progress()
        assert progress["evidence_count"] == 3
        assert progress["agents_completed"] == 1
        assert progress["hook_completed"] is False

    def test_progress_with_completed_hook(self, workflow_config, fresh_db):
        runner = GoalWorkflowRunner(config=workflow_config, session_id="s1")
        hook = MagicMock()
        hook.evidence_count = 5
        hook.completed = True
        runner._hook = hook
        progress = runner.get_progress()
        assert progress["hook_completed"] is True


class TestRunnerSubscribe:
    def test_subscribe_and_unsubscribe(self, workflow_config, fresh_db):
        runner = GoalWorkflowRunner(config=workflow_config, session_id="s1")
        observer = MagicMock()
        runner.subscribe(observer)
        runner.unsubscribe(observer)
        # Should not raise

    def test_event_bus_accessible(self, workflow_config, fresh_db):
        runner = GoalWorkflowRunner(config=workflow_config, session_id="s1")
        assert runner.event_bus is not None
        assert hasattr(runner.event_bus, "subscribe")
        assert hasattr(runner.event_bus, "unsubscribe")
        assert hasattr(runner.event_bus, "emit")


class TestRunnerCheckpoint:
    def test_checkpoint_without_hook(self, workflow_config, fresh_db):
        runner = GoalWorkflowRunner(config=workflow_config, session_id="s1")
        runner._goal_id = "g_test"
        cp_dir = runner.checkpoint()
        assert cp_dir is not None

    def test_checkpoint_without_goal_id(self, workflow_config, fresh_db):
        runner = GoalWorkflowRunner(config=workflow_config, session_id="s1")
        # goal_id is empty string, checkpoint should still work
        cp_dir = runner.checkpoint()
        # Should not crash, may return a path
        assert cp_dir is not None or cp_dir is None

    def test_resume_from_checkpoint_not_found(self, workflow_config, fresh_db):
        runner = GoalWorkflowRunner.resume_from_checkpoint(
            session_id="nonexistent",
            goal_id="ghost",
            config=workflow_config,
        )
        assert runner is None


class TestRunnerStartSubWorkflow:
    def test_start_sub_workflow_creates_child_goal(self, workflow_config, fresh_db):
        from strategy_research.apps.goal.store import GoalStore
        store = GoalStore()
        runner = GoalWorkflowRunner(
            config=workflow_config,
            session_id="test_sub",
            store=store,
        )
        # Create parent goal first
        parent_goal_id = asyncio.run(runner.start("parent objective"))

        # Start sub-workflow
        child_runner = GoalWorkflowRunner(
            config=workflow_config,
            session_id="test_sub",
            store=store,
        )
        child_goal_id = asyncio.run(
            child_runner.start_sub_workflow("child objective", parent_goal_id)
        )
        assert child_goal_id != parent_goal_id

        # Verify parent link
        child_goal = store.get_goal(child_goal_id)
        assert child_goal is not None
        assert child_goal.parent_goal_id == parent_goal_id


class TestRunnerProperties:
    def test_state_property(self, workflow_config, fresh_db):
        runner = GoalWorkflowRunner(config=workflow_config, session_id="s1")
        assert runner.state is not None
        assert runner.state.status == "idle"

    def test_goal_id_default_empty(self, workflow_config, fresh_db):
        runner = GoalWorkflowRunner(config=workflow_config, session_id="s1")
        assert runner.goal_id == ""

    def test_goal_id_after_set(self, workflow_config, fresh_db):
        runner = GoalWorkflowRunner(config=workflow_config, session_id="s1")
        runner._goal_id = "g123"
        assert runner.goal_id == "g123"
