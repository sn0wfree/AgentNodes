"""WorkflowWorker — asyncio.Task wrapper for GoalWorkflowRunner (Phase 4 v0.5.2).

Runs ``GoalWorkflowRunner.start()`` as a background task in the Textual
event loop, with cooperative pause/resume. Designed to coexist with
Textual's message loop:

* The TUI remains responsive while the workflow executes.
* ``Ctrl+G`` calls ``worker.cancel()`` which sets the runner's pause
  flag via ``runner.pause()``.
* Exceptions in the runner propagate to the TUI's notification system
  (``app.notify(severity="error")``).
* The worker subscribes its own ``WorkflowEventBus`` to a
  ``GoalPanelObserver`` so the GoalPanel widget updates in real time.

Lifecycle::

    IDLE  ──run()──▶  RUNNING  ──complete──▶  COMPLETED
                       │   │
                       │   └─error──▶ FAILED
                       │
                       ├─cancel()──▶ PAUSED  ──resume()──▶ RUNNING
                       │
                       └─cancel(immediate=True)──▶ CANCELLED

Reference: docs/phase-4-plan.md §4.2.
"""
from __future__ import annotations

import asyncio
import logging
from enum import Enum
from typing import Any, Optional

logger = logging.getLogger(__name__)


class WorkflowWorkerState(str, Enum):
    """State machine for the workflow worker."""

    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class WorkflowWorker:
    """asyncio-safe wrapper around ``GoalWorkflowRunner``.

    Args:
        runner: A ``GoalWorkflowRunner`` instance.
        app: The Textual ``ResearchApp`` (used for notifications and
            querying the GoalPanel widget). May be a stub in tests.
    """

    def __init__(self, runner: Any, app: Any) -> None:
        self._runner = runner
        self._app = app
        self._state: WorkflowWorkerState = WorkflowWorkerState.IDLE
        self._goal_id: str = ""
        self._error: Optional[BaseException] = None
        self._task: Optional[asyncio.Task] = None
        self._observer: Any = None
        self._paused_event = asyncio.Event()
        self._paused_event.set()  # initially "not paused"

    # ── Public properties ──────────────────────────────────────

    @property
    def state(self) -> WorkflowWorkerState:
        return self._state

    @property
    def goal_id(self) -> str:
        return self._goal_id

    @property
    def error(self) -> Optional[BaseException]:
        return self._error

    @property
    def is_running(self) -> bool:
        """True when the worker is actively executing (not paused, completed, or failed)."""
        return self._state == WorkflowWorkerState.RUNNING

    # ── Lifecycle ──────────────────────────────────────────────

    async def run(self, objective: str) -> str:
        """Execute the workflow asynchronously.

        Returns:
            The goal_id from the runner.

        Raises:
            Any exception from the runner — also captured in self.error.
        """
        if self._state == WorkflowWorkerState.RUNNING:
            raise RuntimeError("workflow worker is already running")

        self._state = WorkflowWorkerState.RUNNING
        self._error = None
        self._subscribe_panel_observer()
        self._notify(
            f"Workflow '{self._runner._config.name}' started",
            severity="information",
        )

        try:
            self._goal_id = await self._runner.start(objective)
            self._state = WorkflowWorkerState.COMPLETED
            progress = self._safe_progress()
            self._notify(
                f"Workflow finished: "
                f"status={progress.get('status', '?')} "
                f"evidence={progress.get('evidence_count', 0)}",
                severity="information",
            )
            return self._goal_id
        except asyncio.CancelledError:
            self._state = WorkflowWorkerState.CANCELLED
            self._notify("Workflow cancelled", severity="warning")
            raise
        except Exception as exc:  # noqa: BLE001
            self._state = WorkflowWorkerState.FAILED
            self._error = exc
            self._notify(
                f"Workflow failed: {exc}",
                severity="error",
            )
            logger.exception("Workflow worker failed")
            raise
        finally:
            self._unsubscribe_panel_observer()

    def cancel(self, *, immediate: bool = False) -> None:
        """Pause / cancel the workflow.

        Args:
            immediate: If True, set the runner's ``cancelled`` flag for
                hard-stop (P3.4). If False (default), set ``paused`` for
                graceful stop after the current agent.

        Synchronous — delegates to ``runner.pause()`` which sets a flag
        that the runner checks on the next layer boundary. Safe to call
        from any context (Textual action handler, sync code, etc.).
        """
        if self._state != WorkflowWorkerState.RUNNING:
            logger.debug("WorkflowWorker.cancel() called when state=%s", self._state)
            return

        try:
            self._runner.pause(immediate=immediate)
            self._state = WorkflowWorkerState.PAUSED
            mode = "cancelled" if immediate else "paused"
            self._notify(f"Workflow {mode}", severity="warning")
        except Exception as exc:  # noqa: BLE001
            logger.warning("Worker.cancel() pause failed: %s", exc)

    def resume(self) -> None:
        """Resume from paused state."""
        if self._state != WorkflowWorkerState.PAUSED:
            return
        try:
            self._runner.resume()
            self._state = WorkflowWorkerState.RUNNING
            self._notify("Workflow resumed", severity="information")
        except Exception as exc:  # noqa: BLE001
            logger.warning("Worker.resume() failed: %s", exc)

    # ── Internals ──────────────────────────────────────────────

    def _subscribe_panel_observer(self) -> None:
        """Wire the GoalPanel widget as an event observer."""
        try:
            panel = self._app.query_one("#goal-panel")
        except Exception:
            panel = None
        if panel is None:
            return
        from agentnodes.apps.goal.event_bus import GoalPanelObserver
        self._observer = GoalPanelObserver(panel)
        try:
            self._runner.event_bus.subscribe(self._observer)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to subscribe GoalPanelObserver: %s", exc)

    def _unsubscribe_panel_observer(self) -> None:
        if self._observer is None:
            return
        try:
            self._runner.event_bus.unsubscribe(self._observer)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to unsubscribe GoalPanelObserver: %s", exc)
        self._observer = None

    def _notify(self, message: str, *, severity: str = "information") -> None:
        """Push a notification into the TUI (best-effort)."""
        try:
            notify = getattr(self._app, "notify", None)
            if notify is not None:
                notify(message, severity=severity)
        except Exception:  # noqa: BLE001
            logger.debug("app.notify failed for: %s", message)

    def _safe_progress(self) -> dict[str, Any]:
        try:
            return self._runner.get_progress() or {}
        except Exception:  # noqa: BLE001
            return {}


__all__ = ["WorkflowWorker", "WorkflowWorkerState"]
