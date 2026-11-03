"""Progress emission channel for long-running tools.

The structured-progress half (``emit_progress`` / ``_set_emitter`` /
``ProgressEvent``) was removed in P1.5-c (B3):
the agent loop injects progress via ``loop.py._progress_callback`` and
nothing consumed the thread-local emitter. What remains is the
tool-level heartbeat: a background timer that fires keepalive events
every N seconds while a tool runs, so the UI never looks frozen.
Driven by the agent loop, opaque to the tool. Tests: test_progress.py.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)


class HeartbeatTimer:
    """Background thread that emits keepalive ticks while a tool runs.

    Use as a context manager around a single tool invocation:

        with HeartbeatTimer(tool_name="run_backtest", interval=3.0, emit=fn):
            result = registry.execute(...)

    The timer wakes every ``interval`` seconds and calls ``emit`` with a
    dict containing ``tool`` and ``elapsed_s``. Stops cleanly on context
    exit; ``join`` is bounded so a hung emitter can't deadlock the loop.
    """

    def __init__(
        self,
        tool_name: str,
        interval: float,
        emit: Callable[[Dict[str, Any]], None],
    ) -> None:
        """Initialize the heartbeat timer (not started until ``__enter__``).

        Args:
            tool_name: Tool name surfaced in each tick payload.
            interval: Seconds between ticks. Values <0.5 are clamped.
            emit: Callback invoked with each tick payload.
        """
        self._tool_name = tool_name
        requested_interval = float(interval)
        self._interval = max(0.5, requested_interval)
        if requested_interval < 0.5:
            logger.warning(
                "HeartbeatTimer interval %s clamped to 0.5s", interval
            )
        self._emit = emit
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._t0 = 0.0

    def __enter__(self) -> "HeartbeatTimer":
        self._t0 = time.perf_counter()
        self._thread = threading.Thread(
            target=self._run,
            name=f"heartbeat-{self._tool_name}",
            daemon=True,
        )
        self._thread.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)

    def _run(self) -> None:
        """Tick loop: wait + emit until the stop event fires."""
        while not self._stop_event.wait(self._interval):
            elapsed = time.perf_counter() - self._t0
            try:
                self._emit({"tool": self._tool_name, "elapsed_s": round(elapsed, 2)})
            except Exception:
                # A failing callback must not crash the heartbeat thread.
                pass
