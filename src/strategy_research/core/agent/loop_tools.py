"""W-split: tool kwargs + batch/heartbeat execution (from AgentLoop)."""
from __future__ import annotations

from typing import Any

import asyncio
import json
import logging
import time
from typing import TYPE_CHECKING, Any

logger = logging.getLogger(__name__)

from concurrent.futures import ThreadPoolExecutor, as_completed

from .progress import HeartbeatTimer
from .tools import TRANSIENT_TOOL_ERRORS, ToolContext

_TOOL_MAX_RETRIES = 2
_TOOL_RETRY_DELAY = 2.0
_TOOL_RESULT_HISTORY_MAX = 30000
from .runtime.sync_bridge import _run_coro_in_sync

if TYPE_CHECKING:
    from strategy_research.foundation.llm.parser import ToolCall
    from .loop import LoopResult

class LoopToolsMixin:
# ── Host surface (provided by AgentLoop / AutoresearchRunner) ──
    _build_data_store: Any
    _build_sandbox: Any
    _circuit_breaker: Any
    _current_iter: Any
    _current_message_id: Any
    _emit: Any
    _permission_evaluator: Any
    _permission_gateway: Any
    _recent_hashes: Any
    _subagent_count: Any
    _trace: Any
    _trace_and_emit: Any
    heartbeat_interval: Any
    no_progress_window: Any
    read_roots: Any
    registry: Any
    results_tsv: Any
    runs_dir: Any
    session_id: Any
    strategy_dir: Any
    workspace: Any
    write_roots: Any

    def _build_tool_kwargs(self, tc: ToolCall) -> dict[str, Any]:
        """W5: assemble ToolContext + injected kwargs for one tool call."""
        def _progress_callback(steps: list[str]) -> None:
            self._emit("tool_progress", {"id": tc.id, "steps": steps})

        kwargs = dict(tc.arguments) if isinstance(tc.arguments, dict) else {}
        if "workspace" not in kwargs and self.workspace is not None:
            kwargs["workspace"] = self.workspace
        if "session_id" not in kwargs and self.session_id is not None:
            kwargs["session_id"] = self.session_id
        kwargs["_progress_callback"] = _progress_callback
        kwargs["ctx"] = ToolContext(
            workspace=self.workspace,
            session_id=self.session_id,
            strategy_dir=self.strategy_dir,
            runs_dir=self.runs_dir,
            results_tsv=self.results_tsv,
            write_roots=self.write_roots,
            read_roots=self.read_roots,
            emit_progress=_progress_callback,
            emit_event=self._emit,
            message_id=getattr(self, "_current_message_id", None),
            permission_evaluator=getattr(self, "_permission_evaluator", None),
            permission_gateway=getattr(self, "_permission_gateway", None),
            tool_call_id=tc.id,
            data_store=self._build_data_store(),
            sandbox=self._build_sandbox(),
        )
        if tc.name == "task":
            kwargs["emit_event"] = self._emit
            kwargs["message_id"] = getattr(self, "_current_message_id", None)
            kwargs["_subagent_count_ref"] = self._subagent_count
            kwargs["_parent_registry"] = self.registry
        if tc.name == "todowrite":
            kwargs["emit_event"] = self._emit
        return kwargs

    async def _execute_tool_call_core(
        self, tc: ToolCall, result: LoopResult, *, async_mode: bool
    ) -> dict[str, Any]:
        """Shared tool-call core (single source for both modes).

        ``async_mode`` selects ``tool.ainvoke`` (permission-gated, awaited
        on the loop) vs ``tool.invoke`` (direct sync call) and
        ``asyncio.sleep`` vs ``time.sleep`` for the transient-retry delay.
        The sync entry runs this core via ``_run_coro_in_sync`` (from a
        pool thread there is no running loop, so no nested thread).
        """
        from .runtime.tool_exec import (
            guard_denied_payload,
            missing_tool_payload,
        )

        tool = self.registry.get(tc.name)
        if tool is None:
            logger.warning("tool '%s' not in registry", tc.name)
            self._trace_and_emit("tool_error", {"tool": tc.name, "error": "not in registry"})
            event, msg = missing_tool_payload(tc)
            self._emit("tool_result", event)
            return msg

        # DSH-inspired: check monotonic deny guards before execution
        guard_reason = self.registry.check_guards(tc.name, tc.arguments)
        if guard_reason:
            logger.info("tool '%s' denied by guard: %s", tc.name, guard_reason)
            self._trace_and_emit("tool_error", {"tool": tc.name, "error": f"guard: {guard_reason}"})
            event, msg = guard_denied_payload(tc, guard_reason)
            self._emit("tool_result", event)
            return msg

        # Emit tool_call event
        self._emit("tool_call", {
            "tool": tc.name,
            "name": tc.name,            # frontend reads data.name
            "id": tc.id,                # frontend reads data.id
            "arguments": json.dumps(tc.arguments, ensure_ascii=False)
            if not isinstance(tc.arguments, str)
            else tc.arguments,
            "call_id": tc.id,           # backward compat
            "iter": getattr(self, "_current_iter", 0),
        })

        kwargs = self._build_tool_kwargs(tc)

        t0 = time.perf_counter()
        # Count only calls that actually reached execution — the old
        # top-of-function increment also counted missing tools and
        # guard-denied calls, inflating the metric.
        result.tool_calls_made += 1
        # ── Tool-level auto-retry for transient errors ──────────────
        last_exc = None
        for _attempt in range(_TOOL_MAX_RETRIES):
            try:
                if async_mode:
                    output = await tool.ainvoke(kwargs, kwargs.get("ctx"))
                else:
                    output = tool.invoke(kwargs)
                last_exc = None
                break
            except TRANSIENT_TOOL_ERRORS as exc:
                last_exc = exc
                logger.warning("tool %s raised %s (attempt %d/%d): %s",
                               tc.name, type(exc).__name__, _attempt + 1,
                               _TOOL_MAX_RETRIES, exc)
                if _attempt < _TOOL_MAX_RETRIES - 1:
                    if async_mode:
                        await asyncio.sleep(_TOOL_RETRY_DELAY)
                    else:
                        time.sleep(_TOOL_RETRY_DELAY)
            except Exception as exc:                    # noqa: BLE001
                logger.exception("tool %s raised", tc.name)
                output = json.dumps(
                    {"status": "error", "error": f"{type(exc).__name__}: {exc}"},
                    ensure_ascii=False,
                )
                last_exc = None
                break
        else:
            # All retries exhausted for transient errors
            output = json.dumps(
                {"status": "error", "error": f"{type(last_exc).__name__}: {last_exc}",
                 "hint": "tool failed after retries; check input parameters or data quality"},
                ensure_ascii=False,
            )
        elapsed_ms = int((time.perf_counter() - t0) * 1000)

        # Emit tool_result event + circuit breaker + A3 history view.
        from .runtime.tool_exec import (
            classify_tool_output,
            render_history_output,
        )

        classified = classify_tool_output(output)
        is_error = classified["is_error"]
        status_str = "error" if is_error else "done"
        if self._circuit_breaker is not None:
            if is_error:
                self._circuit_breaker.record_failure(tc.name)
            else:
                self._circuit_breaker.record_success(tc.name)

        self._emit("tool_result", {
            "tool": tc.name,
            "id": tc.id,
            "call_id": tc.id,
            "status": status_str,
            "ok": not is_error,
            "result": classified["full"],
            "preview": classified["preview"],
            "elapsed_ms": elapsed_ms,
        })
        self._trace({
            "type": "tool_result",
            "tool": tc.name,
            "call_id": tc.id,
            "status": status_str,
            "iteration": getattr(self, "_current_iter", 0),
            "elapsed_ms": elapsed_ms,
            "output_preview": classified["preview"],
        })
        history_output = render_history_output(
            output, max_chars=_TOOL_RESULT_HISTORY_MAX or 0
        )
        return {
            "role": "tool",
            "tool_call_id": tc.id,
            "content": history_output,
        }

    def _execute_tool_call(
        self, tc: ToolCall, result: LoopResult
    ) -> dict[str, Any]:
        """Execute one tool_call via the registry; return tool-result message.

        Sync entry over the shared core (see ``_execute_tool_call_core``).
        """
        return _run_coro_in_sync(
            self._execute_tool_call_core(tc, result, async_mode=False)
        )

    async def _aexecute_tool_call(
        self, tc: ToolCall, result: LoopResult
    ) -> dict[str, Any]:
        """Async version of _execute_tool_call (permission-gated ainvoke)."""
        return await self._execute_tool_call_core(tc, result, async_mode=True)

    async def _execute_tool_with_heartbeat_core(
        self, tc: ToolCall, result: LoopResult, *, async_mode: bool
    ) -> dict[str, Any]:
        """Shared heartbeat wrapper (single source for both modes)."""
        def _heartbeat_tick(payload: dict) -> None:
            self._trace({"type": "heartbeat", **payload})
            self._emit("tool_heartbeat", {
                "tool": tc.name,
                "call_id": tc.id,
                "elapsed_s": payload.get("elapsed_s", 0.0),
            })

        with HeartbeatTimer(
            tool_name=tc.name,
            interval=self.heartbeat_interval,
            emit=_heartbeat_tick,
        ):
            return await self._execute_tool_call_core(tc, result, async_mode=async_mode)

    def _execute_tool_with_heartbeat(
        self, tc: ToolCall, result: LoopResult
    ) -> dict[str, Any]:
        """Execute tool_call with HeartbeatTimer for long-running tools.

        Sync entry over the shared core (see
        ``_execute_tool_with_heartbeat_core``).
        """
        return _run_coro_in_sync(
            self._execute_tool_with_heartbeat_core(tc, result, async_mode=False)
        )

    async def _aexecute_tool_with_heartbeat(
        self, tc: ToolCall, result: LoopResult
    ) -> dict[str, Any]:
        """Async version of _execute_tool_with_heartbeat."""
        return await self._execute_tool_with_heartbeat_core(tc, result, async_mode=True)

    def _execute_tool_batch(
        self, tool_calls: list[ToolCall], result: LoopResult
    ) -> list[dict[str, Any]]:
        """Execute tool_calls with read-only parallelism.

        Read-only tools are dispatched in parallel via ThreadPoolExecutor.
        Write tools (is_readonly=False) run serially to prevent races.
        Results are returned in the same order as tool_calls.
        """
        if not tool_calls:
            return []

        # Single tool: no batching overhead
        if len(tool_calls) == 1:
            return [self._execute_tool_with_heartbeat(tool_calls[0], result)]

        # Classify each tool_call as readonly or write
        readonly_indices: list[int] = []
        write_indices: list[int] = []
        for i, tc in enumerate(tool_calls):
            tool = self.registry.get(tc.name)
            if tool is not None and not getattr(tool, "is_readonly", True):
                write_indices.append(i)
            else:
                readonly_indices.append(i)

        # Prepare result slots (preserves order)
        results: list[dict[str, Any] | None] = [None] * len(tool_calls)

        # Dispatch readonly tools in parallel
        if readonly_indices:
            max_workers = min(len(readonly_indices), 4)
            with ThreadPoolExecutor(max_workers=max_workers) as pool:
                futures = {
                    pool.submit(self._execute_tool_with_heartbeat, tool_calls[i], result): i
                    for i in readonly_indices
                }
                for future in as_completed(futures):
                    idx = futures[future]
                    try:
                        results[idx] = future.result()
                    except Exception as exc:  # noqa: BLE001
                        logger.exception("parallel tool %s failed", tool_calls[idx].name)
                        results[idx] = {
                            "role": "tool",
                            "tool_call_id": tool_calls[idx].id,
                            "content": json.dumps(
                                {"status": "error", "error": f"{type(exc).__name__}: {exc}"},
                                ensure_ascii=False,
                            ),
                        }

        # Dispatch write tools serially
        for i in write_indices:
            results[i] = self._execute_tool_with_heartbeat(tool_calls[i], result)

        return [r for r in results if r is not None]

    def _detect_no_progress(self) -> bool:
        """Return True if last N tool_calls all have the same hash."""
        if len(self._recent_hashes) < self.no_progress_window:
            return False
        window = self._recent_hashes[-self.no_progress_window:]
        return len(set(window)) == 1

    # ── Async tool execution ─────────────────────

    async def _aexecute_tool_batch(
        self, tool_calls: list[ToolCall], result: LoopResult
    ) -> list[dict[str, Any]]:
        """Async version of _execute_tool_batch using asyncio.gather for readonly."""
        if not tool_calls:
            return []

        if len(tool_calls) == 1:
            return [await self._aexecute_tool_with_heartbeat(tool_calls[0], result)]

        readonly_indices: list[int] = []
        write_indices: list[int] = []
        for i, tc in enumerate(tool_calls):
            tool = self.registry.get(tc.name)
            if tool is not None and not getattr(tool, "is_readonly", True):
                write_indices.append(i)
            else:
                readonly_indices.append(i)

        results: list[dict[str, Any] | None] = [None] * len(tool_calls)

        if readonly_indices:
            coros = [
                self._aexecute_tool_with_heartbeat(tool_calls[i], result)
                for i in readonly_indices
            ]
            gathered = await asyncio.gather(*coros, return_exceptions=True)
            for idx, res in zip(readonly_indices, gathered):
                if isinstance(res, Exception):
                    logger.exception("parallel tool %s failed", tool_calls[idx].name)
                    results[idx] = {
                        "role": "tool",
                        "tool_call_id": tool_calls[idx].id,
                        "content": json.dumps(
                            {"status": "error", "error": f"{type(res).__name__}: {res}"},
                            ensure_ascii=False,
                        ),
                    }
                else:
                    results[idx] = res

        for i in write_indices:
            results[i] = await self._aexecute_tool_with_heartbeat(tool_calls[i], result)

        return [r for r in results if r is not None]

    # ── Context compression ─────────────────────────
