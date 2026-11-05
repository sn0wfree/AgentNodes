"""Phase 4 — v0.5.2 TUI tests: WorkflowWorker + Ctrl+G pause + GoalPanel subscription.

TDD stubs for TUI integration of Goal Workflow Engine.
Uses pytest-asyncio (asyncio_mode = "auto") — no raw asyncio.run().

Covers:
  - WorkflowWorker.run() executes runner.start() as asyncio.Task
  - WorkflowWorker.cancel() sets runner.pause()
  - ResearchApp.start_workflow() creates worker + subscribes GoalPanelObserver
  - Ctrl+G pauses active workflow before falling back to continuation toggle
  - GoalPanel.on_workflow_event() is invoked when runner emits events

Reference: docs/phase-4-plan.md §4.2.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

# Ensure pytest-asyncio picks up all async test functions.
pytestmark = pytest.mark.asyncio

from strategy_research.interfaces.cli.tui.workers.workflow_worker import (
    WorkflowWorker,
    WorkflowWorkerState,
)

# ─── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def mock_runner():
    """A mock GoalWorkflowRunner with controllable lifecycle."""
    runner = MagicMock()
    runner.state = MagicMock()
    runner.state.cancelled = False
    runner.state.paused = False
    runner.goal_id = "test_goal_123"
    runner.event_bus = MagicMock()
    runner.event_bus.subscribe = MagicMock()
    runner.event_bus.unsubscribe = MagicMock()

    # runner.start() suspends until _start_event is set
    start_event = asyncio.Event()

    async def _start(objective: str) -> str:
        await start_event.wait()
        return "test_goal_123"

    runner.start = AsyncMock(side_effect=_start)
    runner.pause = MagicMock()
    runner.resume = MagicMock()
    runner.checkpoint = MagicMock()
    runner.get_progress = MagicMock(return_value={
        "status": "running",
        "agents_total": 4,
        "agents_completed": 0,
        "evidence_count": 0,
    })
    runner._start_event = start_event
    return runner


@pytest.fixture
def fresh_db(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("STRATEGY_RESEARCH_GOAL_DB", str(tmp_path / "goals.db"))


# ─── v0.5.2 P0.6 — WorkflowWorker.run / cancel ────────────────────────


class TestWorkflowWorkerRun:
    """Worker.run() executes runner.start() as asyncio.Task."""

    async def test_state_starts_idle(self):
        worker = WorkflowWorker(MagicMock(), MagicMock())
        assert worker.state == WorkflowWorkerState.IDLE
        assert worker.is_running is False

    async def test_run_invokes_runner_start(self, mock_runner, fresh_db):
        app = MagicMock()
        worker = WorkflowWorker(mock_runner, app)
        task = asyncio.create_task(worker.run("test objective"))
        mock_runner._start_event.set()
        goal_id = await asyncio.wait_for(task, timeout=1.0)
        assert goal_id == "test_goal_123"
        assert mock_runner.start.called
        assert mock_runner.start.call_args.args[0] == "test objective"

    async def test_run_emits_progress_events_through_app(self, mock_runner, fresh_db):
        app = MagicMock()
        app.notify = MagicMock()
        worker = WorkflowWorker(mock_runner, app)
        task = asyncio.create_task(worker.run("test"))
        mock_runner._start_event.set()
        await asyncio.wait_for(task, timeout=1.0)
        assert app.notify.call_count >= 2

    async def test_state_transitions(self, mock_runner, fresh_db):
        app = MagicMock()
        worker = WorkflowWorker(mock_runner, app)
        assert worker.state == WorkflowWorkerState.IDLE
        task = asyncio.create_task(worker.run("test"))
        mock_runner._start_event.set()
        await asyncio.wait_for(task, timeout=1.0)
        assert worker.state == WorkflowWorkerState.COMPLETED
        assert worker.goal_id == "test_goal_123"

    async def test_runner_error_marks_state_failed(self, fresh_db):
        runner = MagicMock()

        async def _fail(objective: str) -> str:
            raise RuntimeError("simulated workflow failure")

        runner.start = MagicMock(side_effect=_fail)
        runner.state = MagicMock()
        runner.event_bus = MagicMock()
        worker = WorkflowWorker(runner, MagicMock())

        with pytest.raises(RuntimeError, match="simulated workflow failure"):
            await worker.run("test")
        assert worker.state == WorkflowWorkerState.FAILED
        assert worker.error is not None
        assert "simulated workflow failure" in str(worker.error)


# ─── v0.5.2 P0.4 — Worker.cancel ─────────────────────────────────────


class TestWorkflowWorkerCancel:
    """Worker.cancel() pauses the runner; safe to call when idle."""

    async def test_cancel_invokes_runner_pause(self, mock_runner, fresh_db):
        app = MagicMock()
        worker = WorkflowWorker(mock_runner, app)
        task = asyncio.create_task(worker.run("test"))

        # Let worker reach RUNNING
        for _ in range(50):
            await asyncio.sleep(0.005)
            if worker.state == WorkflowWorkerState.RUNNING:
                break
        assert worker.state == WorkflowWorkerState.RUNNING

        worker.cancel()
        mock_runner.pause.assert_called_once()
        # Release runner so task completes
        mock_runner._start_event.set()
        await asyncio.wait_for(task, timeout=1.0)

    async def test_cancel_when_idle_is_noop(self, mock_runner, fresh_db):
        worker = WorkflowWorker(mock_runner, MagicMock())
        worker.cancel()
        assert not mock_runner.pause.called

    async def test_double_cancel_is_safe(self, mock_runner, fresh_db):
        app = MagicMock()
        worker = WorkflowWorker(mock_runner, app)
        task = asyncio.create_task(worker.run("test"))

        for _ in range(50):
            await asyncio.sleep(0.005)
            if worker.state == WorkflowWorkerState.RUNNING:
                break
        worker.cancel()
        worker.cancel()  # second call no-op
        assert mock_runner.pause.call_count == 1
        mock_runner._start_event.set()
        await asyncio.wait_for(task, timeout=1.0)

    async def test_resume_after_cancel(self, mock_runner, fresh_db):
        app = MagicMock()
        worker = WorkflowWorker(mock_runner, app)
        task = asyncio.create_task(worker.run("test"))

        for _ in range(50):
            await asyncio.sleep(0.005)
            if worker.state == WorkflowWorkerState.RUNNING:
                break
        worker.cancel()
        assert worker.state == WorkflowWorkerState.PAUSED
        mock_runner.resume.assert_not_called()
        worker.resume()
        mock_runner.resume.assert_called_once()
        mock_runner._start_event.set()
        await asyncio.wait_for(task, timeout=1.0)


# ─── v0.5.2 P0.5 — ResearchApp.start_workflow ────────────────────────


class TestResearchAppStartWorkflow:
    """ResearchApp.start_workflow() creates worker + subscribes GoalPanelObserver."""

    async def test_start_workflow_creates_worker(self, fresh_db):
        from strategy_research.interfaces.cli.tui.app import ResearchApp

        app = ResearchApp(skip_resume=True)
        async with app.run_test():
            assert app._workflow_worker is None
            runner = MagicMock()
            runner.event_bus = MagicMock()
            runner.event_bus.subscribe = MagicMock()
            app.start_workflow(runner)
            assert app._workflow_worker is not None
            assert isinstance(app._workflow_worker, WorkflowWorker)

    async def test_start_workflow_subscribes_panel_observer(self, fresh_db):
        from strategy_research.interfaces.cli.tui.app import ResearchApp

        app = ResearchApp(skip_resume=True)
        async with app.run_test():
            runner = MagicMock()
            runner.event_bus = MagicMock()
            runner.event_bus.subscribe = MagicMock()
            app.start_workflow(runner)
            runner.event_bus.subscribe.assert_called_once()
            observer = runner.event_bus.subscribe.call_args.args[0]
            assert hasattr(observer, "on_event")

    async def test_start_workflow_replaces_existing(self, fresh_db):
        from strategy_research.interfaces.cli.tui.app import ResearchApp

        app = ResearchApp(skip_resume=True)
        async with app.run_test():
            runner1 = MagicMock()
            runner1.event_bus = MagicMock()
            runner1.event_bus.subscribe = MagicMock()
            app.start_workflow(runner1)

            runner2 = MagicMock()
            runner2.event_bus = MagicMock()
            runner2.event_bus.subscribe = MagicMock()
            app.start_workflow(runner2)
            # Old observer unsubscribed, new one subscribed
            runner1.event_bus.unsubscribe.assert_called()
            runner2.event_bus.subscribe.assert_called_once()
            assert app._workflow_worker._runner is runner2


# ─── v0.5.2 P0.4 — Ctrl+G pauses workflow ────────────────────────────


class TestCtrlGPausesWorkflow:
    """Ctrl+G pauses the active workflow before falling back to continuation."""

    async def test_ctrl_g_pauses_running_workflow(self, fresh_db):
        from strategy_research.interfaces.cli.tui.app import ResearchApp

        app = ResearchApp(skip_resume=True)
        async with app.run_test():
            runner = MagicMock()
            runner.event_bus = MagicMock()
            runner.event_bus.subscribe = MagicMock()
            runner.state = MagicMock()
            runner.pause = MagicMock()
            app.start_workflow(runner)
            # Force state to RUNNING
            app._workflow_worker._state = WorkflowWorkerState.RUNNING
            app.action_toggle_goal_continuation()
            runner.pause.assert_called_once()

    async def test_ctrl_g_falls_back_to_continuation_when_no_workflow(self, fresh_db):
        from strategy_research.interfaces.cli.tui.app import ResearchApp

        app = ResearchApp(skip_resume=True)
        async with app.run_test():
            assert app._workflow_worker is None
            # Ctrl+G should not crash
            app.action_toggle_goal_continuation()


# ─── v0.5.2 P0.5 — GoalPanel receives events ─────────────────────────


class TestGoalPanelReceivesEvents:
    """GoalPanel.on_workflow_event() is invoked by GoalPanelObserver."""

    async def test_panel_handles_agent_complete_event(self, fresh_db):
        from strategy_research.interfaces.cli.tui.app import ResearchApp

        app = ResearchApp(skip_resume=True)
        async with app.run_test():
            panel = app.query_one("#goal-panel")
            panel.update_goal(
                objective="test", status="active", progress=0.0,
                criteria=[], evidence_count=0,
            )
            panel.on_workflow_event("workflow_start", {"workflow": "x"})

    async def test_panel_handles_workflow_completed_event(self, fresh_db):
        from strategy_research.interfaces.cli.tui.app import ResearchApp

        app = ResearchApp(skip_resume=True)
        async with app.run_test():
            panel = app.query_one("#goal-panel")
            panel.update_goal(
                objective="test", status="active", progress=50.0,
                criteria=[], evidence_count=1,
            )
            panel.on_workflow_event("workflow_completed", {})
            assert panel._status == "complete"
            assert panel._progress == 100.0
