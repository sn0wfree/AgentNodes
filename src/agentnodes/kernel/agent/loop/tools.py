"""W-split: tool kwargs + batch/heartbeat execution (from AgentLoop)."""
from __future__ import annotations

from typing import Any

import asyncio
import json
import logging
import time
from functools import partial
from typing import TYPE_CHECKING, Any

logger = logging.getLogger(__name__)

from concurrent.futures import ThreadPoolExecutor, as_completed

from agentnodes.kernel.agent.loop.errors import AttemptTimeoutError
from agentnodes.kernel.agent.signals.progress import HeartbeatTimer
from agentnodes.kernel.agent.tools.core import (
    TIMEOUT_CAPABILITY_UNSTOPPABLE,
    TRANSIENT_TOOL_ERRORS,
    ToolContext,
)

_TOOL_MAX_RETRIES = 2
_TOOL_RETRY_DELAY = 2.0
_TOOL_RESULT_HISTORY_MAX = 30000
from agentnodes.kernel.agent.runtime.sync_bridge import _run_coro_in_sync

if TYPE_CHECKING:
    from agentnodes.foundation.llm.parser import ToolCall
    from agentnodes.kernel.agent.loop.core import LoopResult

class _ExecHost:
    """IsolatedToolExecutor 的宿主能力面（第二轮候选 3）。

    把 loop self 收成执行器实际需要的 4 个能力——执行器不再读私有数据字段
    （_attempt_deadline / _current_iter），seam 落在能力上而非内部状态上。
    finalize_timeout 刻意吞 AttemptTimeoutError：终态重注入只需事件副作用，
    异常由外层 check 边界处理（与执行器原注释语义一致）。
    """

    def __init__(self, loop: Any) -> None:
        self._loop = loop

    @property
    def registry(self) -> Any:
        return self._loop.registry

    def trace_and_emit(self, kind: str, payload: dict) -> None:
        self._loop._trace_and_emit(kind, payload)

    def record_timeout_outcome(self, kind: str, name: str, declared: str, state: str) -> None:
        self._loop._record_timeout_outcome(kind, name, declared, state)

    def finalize_timeout(self) -> None:
        """终态重注入（只产事件副作用，不抛 AttemptTimeoutError）。"""
        from agentnodes.kernel.agent.loop.errors import AttemptTimeoutError

        check = getattr(self._loop, "_check_attempt_timeout", None)
        if not callable(check):
            return
        try:
            check(getattr(self._loop, "_current_iter", 0), None, finalize=True)
        except AttemptTimeoutError:
            pass  # 刻意吞：终态重注入只需要 finalize 事件，不需要抛


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
        from agentnodes.kernel.agent.runtime.tool_exec import (
            guard_denied_payload,
            missing_tool_payload,
        )

        check = getattr(self, "_check_attempt_timeout", None)
        if async_mode and callable(check):
            check(getattr(self, "_current_iter", 0), result, finalize=False)

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

        # B2.4 深化：唯一隔离决策/执行入口 IsolatedToolExecutor——决策、
        # 规则闸、执行、E5 outcome、观测事件全在深模块内；开关默认关时
        # run/arun 返回 None，下方执行分支逐字不变。sync/async/retry 共用。
        from agentnodes.kernel.agent.loop._isolated_exec import IsolatedToolExecutor

        _executor = IsolatedToolExecutor(_ExecHost(self))

        kwargs = self._build_tool_kwargs(tc)
        if async_mode and callable(check):
            kwargs["ctx"].before_execute = partial(
                check, getattr(self, "_current_iter", 0), result, finalize=False,
            )

        t0 = time.perf_counter()
        # Count only calls that actually reached execution — the old
        # top-of-function increment also counted missing tools and
        # guard-denied calls, inflating the metric.
        result.tool_calls_made += 1
        # ── Tool-level auto-retry for transient errors ──────────────
        # 批次 13 附录 §3：优先读**显式注入**的 ``_retry_policy``——旧实现
        # 只用模块常量，``AgentLoop(retry_policy=RetryPolicy(max_retries=5))``
        # 完全无效（该参数在 __init__ 赋值后零读取点）。
        #
        # ``max_retries`` 语义是「总尝试次数」，与 ``range(n)`` 一致。
        # **只在调用方显式传了 policy 时生效**：``AgentLoop.__init__`` 在
        # 未传时构造默认 ``RetryPolicy()``（max_retries=3），若无条件采用
        # 会把既有行为从 2 次改成 3 次——这不是本批要改变的东西。
        _policy = getattr(self, "_retry_policy", None)
        _explicit = getattr(self, "_retry_policy_explicit", False)
        max_attempts = (
            max(1, int(getattr(_policy, "max_retries", _TOOL_MAX_RETRIES)))
            if _explicit else _TOOL_MAX_RETRIES
        )
        last_exc = None
        for _attempt in range(max_attempts):
            if _attempt and async_mode and callable(check):
                check(getattr(self, "_current_iter", 0), result, finalize=False)
            try:
                _deadline = getattr(self, "_attempt_deadline", None)
                _remaining = (
                    max(0.0, _deadline - time.monotonic())
                    if _deadline is not None else None
                )
                _outcome = (
                    await _executor.arun(
                        tc, tool, tool_ctx=kwargs.get("ctx"),
                        remaining_budget_s=_remaining,
                        approval_decider=lambda req: "deny",  # 规则已过；ASK fail-closed
                    ) if async_mode else _executor.run(
                        tc, tool, tool_ctx=kwargs.get("ctx"),
                        remaining_budget_s=_remaining,
                        approval_decider=lambda req: "deny",
                    )
                )
                if _outcome is not None:
                    output = _outcome.payload
                    last_exc = None
                    break
                if async_mode:
                    output = await tool.ainvoke(kwargs, kwargs.get("ctx"))
                else:
                    # **批次 13 附录 P0**：sync 路径原先直调 ``tool.invoke``，
                    # 而权限闸只在 ``ainvoke`` 里 → ``AgentLoop.run()`` 完全
                    # 绕过权限（实测：evaluator 明确 deny shell，
                    # sync 仍执行了 ``echo hi``，exit_code=0）。
                    # ``enforce_permission_sync`` 是既有实现（原只被
                    # ``ToolRegistry.execute`` 使用），语义与 async 侧的
                    # 规则判定一致；ASK 在同步路径无提问通道 → fail-closed。
                    tool.enforce_permission_sync(
                        dict(tc.arguments) if isinstance(tc.arguments, dict) else {},
                        kwargs.get("ctx"),
                    )
                    output = tool.invoke(kwargs)
                # 批 4a（§10.5）：截止后发现工具已自然返回（含失败返回）→
                # 实际状态"returned"（结果拒收）。同步工具常阻塞到自然
                # 返回后才被发现超时——按实际观察记录，不得记"仍在运行"。
                # 并发调用各在自身完成点记录（_deadline 已在分支前统一取值）。
                if _deadline is not None and time.monotonic() >= _deadline:
                    # B2.4 4c：declared 取准入过滤后的 registry 值（修正裸
                    # getattr 绕过 B2.1 回落的偏差）；state 仍是实际观测。
                    _declared = (
                        self.registry.timeout_capability(tc.name)
                        if self.registry is not None
                        else getattr(tool, "timeout_capability",
                                    TIMEOUT_CAPABILITY_UNSTOPPABLE)
                    )
                    self._record_timeout_outcome(
                        "tool", tc.name, _declared, "returned",
                    )
                last_exc = None
                break
            except AttemptTimeoutError:
                raise
            except TRANSIENT_TOOL_ERRORS as exc:
                last_exc = exc
                logger.warning("tool %s raised %s (attempt %d/%d): %s",
                               tc.name, type(exc).__name__, _attempt + 1,
                               max_attempts, exc)
                if _attempt < max_attempts - 1:
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
        from agentnodes.kernel.agent.runtime.tool_exec import (
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
            for res in gathered:
                if isinstance(res, AttemptTimeoutError):
                    raise res
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
