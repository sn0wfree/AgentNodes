"""W-split: hooks + emit/trace (from AgentLoop)."""
from __future__ import annotations

from typing import Any

import asyncio
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass

from agentnodes.kernel.agent.runtime.sync_bridge import _get_hook_loop

logger = logging.getLogger(__name__)

class LoopTelemetryMixin:
# ── Host surface (provided by AgentLoop / AutoresearchRunner) ──
    _hooks: Any
    _on_event: Any
    _trace: Any

    def _fire_hooks(self, method_name: str, *args: Any, **kwargs: Any) -> None:
        """Sync adapter for async CompositeHook methods.

        The coroutine runs on the shared persistent hook loop (see
        ``_get_hook_loop``) via ``run_coroutine_threadsafe`` — safe from
        any thread, and no per-call loop construction/teardown.
        """
        if self._hooks is None:
            return
        try:
            method = getattr(self._hooks, method_name)
            coro = method(*args, **kwargs)
            if asyncio.iscoroutine(coro):
                fut = asyncio.run_coroutine_threadsafe(coro, _get_hook_loop())
                fut.result()
        except Exception:  # noqa: BLE001
            logger.warning("Hook %s failed", method_name, exc_info=True)

    def _emit(self, event_type: str, data: dict | None = None) -> None:
        """Emit an event to the on_event callback (if set)."""
        if self._on_event is not None:
            try:
                self._on_event(event_type, data or {})
            except Exception:  # noqa: BLE001
                logger.warning("on_event callback failed for %s", event_type, exc_info=True)

    def _trace_and_emit(self, event_type: str, data: dict | None = None) -> None:
        """Write a trace entry AND emit it onto the event bus.

        Keeps ``trace.jsonl`` (backward-compat) and the event_log in sync
        for the same logical event, so the Trajectory View can be derived
        from the event_log alone (single source of truth).
        """
        self._trace({"type": event_type, **(data or {})})
        self._emit(event_type, data or {})

    def emit_tool_progress(
        self,
        *,
        tool: str,
        call_id: str,
        stage: str,
        current: int | None = None,
        total: int | None = None,
        message: str = "",
    ) -> None:
        """Emit a tool_progress event from inside a tool execution.

        Tools can call this to report in-flight progress (e.g. "downloaded
        chunk 3/10").  Stage names are tool-specific (e.g. "fetching",
        "parsing", "validating").
        """
        self._emit("tool_progress", {
            "tool": tool,
            "call_id": call_id,
            "stage": stage,
            "current": current,
            "total": total,
            "message": message,
        })
    async def _afire_hooks(self, method_name: str, *args: Any, **kwargs: Any) -> None:
        """Async version of _fire_hooks - awaits coroutine hooks directly."""
        if self._hooks is None:
            return
        try:
            method = getattr(self._hooks, method_name)
            result = method(*args, **kwargs)
            if asyncio.iscoroutine(result):
                await result
        except Exception:  # noqa: BLE001
            logger.warning("Hook %s failed", method_name, exc_info=True)
