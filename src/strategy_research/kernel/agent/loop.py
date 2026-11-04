"""AgentLoop: ReAct-style agent execution loop.

Minimal version (PR6-c2):
    - Builds initial messages via ContextBuilder
    - Calls LLM (OpenAICompatClient.chat)
    - Executes tool_calls in order
    - Returns LoopResult when LLM stops or max_iterations reached
    - Detects "no_progress" (last 3 tool_calls hashes identical)

Extended version (PR6-c3):
    - 3-layer context compression (microcompact + context_collapse)
    - HeartbeatTimer for long tool calls
    - TraceWriter integration (JSONL trace events)
    - git commit after run

NOT in this PR (PR7):
    - Tool dispatch optimizations
    - Cancellation tokens
    - Checkpointing

Exception handling policy:
    Agent loop and builtin tools use `except Exception` (BLE001) because
    any uncaught error in a tool or trace/memory helper would abort the
    loop. Failures are logged + traced + converted to error responses
    for the LLM. This is intentional and required for agent resilience.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import logging
import os
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from strategy_research.foundation.git import git_commit
from strategy_research.kernel.hooks.composite import CompositeHook
from strategy_research.kernel.hooks.context import AgentHookContext
from strategy_research.foundation.llm import LLMConfig, LLMResponse, OpenAICompatClient, ToolCall
from strategy_research.foundation.llm.errors import LLMError
from strategy_research.kernel.memory.persistent import PersistentMemory
from strategy_research.foundation.utils.ids import SessionId
from .circuit_breaker import RetryPolicy, ToolLoopCircuitBreaker
from .compact import CompactConfig
from .context import ContextBuilder, estimate_tokens
from .tools import ToolRegistry
from .trace import TraceWriter

logger = logging.getLogger(__name__)


# ── Shared persistent hook loop ────────────────────────────────────
# One process-wide daemon loop for sync→async hook adapters. Creating
# and tearing down an event loop per _fire_hooks call dominated the
# adapter cost when hooks fired per iteration/event.
_HOOK_LOOP: asyncio.AbstractEventLoop | None = None
_HOOK_LOOP_LOCK = threading.Lock()


def _get_hook_loop() -> asyncio.AbstractEventLoop:
    global _HOOK_LOOP
    with _HOOK_LOOP_LOCK:
        if _HOOK_LOOP is None or _HOOK_LOOP.is_closed():
            _HOOK_LOOP = asyncio.new_event_loop()
            threading.Thread(
                target=_HOOK_LOOP.run_forever,
                daemon=True,
                name="agent-hooks-loop",
            ).start()
        return _HOOK_LOOP


def _run_coro_in_sync(coro: Any) -> Any:
    """Run a coroutine to completion from synchronous code.

    Works both outside AND inside a running event loop (``AgentLoop.run``
    is invoked from async contexts by ``role_factory``): when a loop is
    already running, the coroutine executes on a background thread with
    its own loop and the caller blocks until it finishes.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    result: dict[str, Any] = {}

    def _runner() -> None:
        try:
            result["value"] = asyncio.run(coro)
        except BaseException as exc:  # noqa: BLE001 — re-raised below
            result["error"] = exc

    thread = threading.Thread(target=_runner, daemon=True)
    thread.start()
    thread.join()
    if "error" in result:
        raise result["error"]
    return result["value"]


# ── Tool-level auto-retry (transient errors only) ───────────────────
# Shared by sync (_execute_tool_call) and async (_aexecute_tool_call).
# Transient error types live in tools.py (single source, used by
# BaseTool.invoke to re-raise them to this retry loop).
_TOOL_MAX_RETRIES = 2
_TOOL_RETRY_DELAY = 2.0

# SSE tool_result 的 result 字段大小上限（字符）。完整 result 供前端解析
# （run_backtest metrics → 右侧面板）；超大输出（如 read_file 大文件）截断
# 防撑爆 SSE/DB。preview 字段始终为 200 字符截断（兼容旧消费方）。
_TOOL_RESULT_MAX = 50_000

# Tool result 入 LLM history 前的统一截断上限（字符）。
# 超长输出在中间截断，保留头尾并标记 [truncated]。
# 环境变量 SR_TOOL_RESULT_MAX_CHARS 可覆盖（0 = 不截断）。
_TOOL_RESULT_HISTORY_MAX = int(os.environ.get("SR_TOOL_RESULT_MAX_CHARS", "30000"))


# ── Cached GoalStore (goal-snapshot injection) ─────────────────────
# Constructing a GoalStore per loop iteration leaked one SQLite
# connection each (the store had no close() before F1-2); a single
# long-lived store per db path avoids the churn while keeping test
# isolation when QUANTNODES_RESEARCH_GOAL_DB_PATH is overridden.
_goal_store_cache: dict[Path, Any] = {}


def _get_goal_store() -> Any:
    """Return the cached GoalStore for the default goal DB path."""
    from strategy_research.apps.goal.store import GoalStore
    store = GoalStore()
    key = store.db_path
    cached = _goal_store_cache.get(key)
    if cached is None:
        _goal_store_cache[key] = store
        return store
    store.close()
    return cached


# ── Compaction persistence registration ─────────────────────────────
#
# Moved to ``compact.py`` in W10-1 (2026-11-04): the only reader was
# ``loop_compaction.py``, while this module imports that mixin at top
# level — a genuine eager cycle. ``compact.py`` is imported by both
# sides, so the registration (and its global) now lives there, and the
# names are re-exported here so existing
# ``from ...loop import register_compaction_persister`` callers
# (``interfaces/api/app.py``, tests) keep working.
from .compact import (  # noqa: E402  (kept here to preserve import order)
    compaction_persister_registered,
    get_compaction_persister,
    register_compaction_persister,
)


# ── L7: LoopStrategy context helper ────────────────────────────────────


def _make_strategy_ctx(
    loop: Any,
    messages: list[dict[str, Any]],
    response: Any,
    result: Any,
    iteration: int,
    hook_ctx: Any = None,
) -> Any:
    """Build a transient ``LoopContext`` for consulting Stop /
    Continuation / Progress / Resilience steps without disturbing the
    legacy hard-coded control flow. v0.2 keeps the strategy steps
    read-only — the legacy path still drives everything; a custom
    step can only flip ``ctx.should_stop`` (the loop body honours it).

    ``hook_ctx`` is carried through so a step that wants AgentHookContext
    access (e.g. to read usage) gets the same instance the hook system
    uses — zero extra synchronisation.
    """
    from strategy_research.kernel.agent.strategy.loop_context import LoopContext

    return LoopContext(
        task="",
        messages=list(messages),
        iteration=iteration,
        response=response,
        response_was_tool_call=bool(getattr(response, "tool_calls", None)),
        response_content=getattr(response, "content", "") or "",
        result=result,
        hook_ctx=hook_ctx,
        tokens_used=getattr(loop, "_budget_tokens", 0) or 0,
        cost_usd=getattr(loop, "_budget_cost_usd", 0.0) or 0.0,
        t0=getattr(result, "started_at", 0.0) or 0.0,
    )


def _inject_agent_loop(strategy: Any, agent_loop: Any) -> None:
    """L7 — bind ``agent_loop`` to every step on ``strategy`` that opts in.

    Steps that need the loop (e.g. DefaultPreRunStep, DefaultLLMCallStep)
    define a ``bind_agent_loop`` method; custom or no-op steps simply
    ignore the call. Walks the 9 known step slots.
    """
    slots = (
        "pre_run", "llm_call", "compaction", "stop", "continuation",
        "progress", "resilience", "tool_execution", "finalization",
    )
    for slot in slots:
        step = getattr(strategy, slot, None)
        if step is None:
            continue
        bind = getattr(step, "bind_agent_loop", None)
        if callable(bind):
            bind(agent_loop)


# ── Result dataclass ────────────────────────────────────────────────


@dataclass
class LoopResult:
    """Result of an AgentLoop run."""

    answer: str = ""
    iterations: int = 0
    tool_calls_made: int = 0
    finished_reason: str = "stop"     # stop | max_iter | no_progress | error | timeout | budget
    stop_reason: str | None = None
    started_at: float = 0.0
    error: str | None = None
    messages: list[dict[str, Any]] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    compression_applied: list[str] = field(default_factory=list)
    trace_path: str | None = None

    @property
    def success(self) -> bool:
        return self.finished_reason in ("stop", "max_iter") and bool(self.answer)


# ── Helpers ──────────────────────────────────────────────────────────


# SwarmWorker parity (unified engine): wrap-up nudge fires once at 80%
# of max_iterations to push the model toward a final text answer.
WRAP_UP_RATIO = 0.8
WRAP_UP_NUDGE_TEXT = (
    "Wrap-up: 2 sentences max. If you must write a file, "
    "do so now via write_file (if available). End your reply "
    "with a clear final summary."
)


def _tool_call_hash(tc: ToolCall) -> str:
    """Stable hash for tool_call to detect no_progress."""
    payload = json.dumps({"name": tc.name, "arguments": tc.arguments},
                          sort_keys=True, ensure_ascii=False)
    return hashlib.md5(payload.encode("utf-8")).hexdigest()[:12]


# ── AgentLoop ────────────────────────────────────────────────────────


from .loop_compaction import LoopCompactionMixin
from .loop_lifecycle import LoopLifecycleMixin
from .loop_progress import LoopProgressMixin
from .loop_stream import LoopStreamMixin
from .loop_telemetry import LoopTelemetryMixin
from .loop_tools import LoopToolsMixin


class AgentLoop(
    LoopStreamMixin,
    LoopTelemetryMixin,
    LoopProgressMixin,
    LoopLifecycleMixin,
    LoopToolsMixin,
    LoopCompactionMixin,
):
    """ReAct agent loop.

    Usage:
        loop = AgentLoop(config=cfg, registry=registry, workspace=ws)
        result = loop.run("improve momentum_20_60")
        print(result.answer)
    """

    def __init__(
        self,
        config: LLMConfig,
        registry: ToolRegistry,
        memory: PersistentMemory | None = None,
        workspace: Path | None = None,
        max_iterations: int = 10,
        no_progress_window: int = 3,
        threshold_tokens: int | None = None,
        heartbeat_interval: float = 15.0,
        trace_dir: Path | None = None,
        auto_git_commit: bool = False,
        system_prompt: str | None = None,
        allowed_tools: list[str] | None = None,
        readonly: bool = False,
        session_id: SessionId | None = None,
        strategy_name: str | None = None,
        # ── v2 path parameterization (study scenario) ────────────────
        strategy_dir: Path | None = None,
        runs_dir: Path | None = None,
        results_tsv: Path | None = None,
        write_roots: tuple[str, ...] | None = None,
        read_roots: tuple[str, ...] | None = None,
        enable_goal_injection: bool = True,
        enable_memory_injection: bool = True,
        enable_hypothesis_auto_create: bool = True,
        hooks: CompositeHook | None = None,
        session_manager: Any | None = None,
        on_event: Any | None = None,
        stream_mode: bool = True,
        compact_config: CompactConfig | None = None,
        event_bus: Any | None = None,
        circuit_breaker: ToolLoopCircuitBreaker | None = None,
        retry_policy: RetryPolicy | None = None,
        enable_claim_validation: bool = False,
        strict_claim_validation: bool = False,
        # ── P1-5: LoopStrategy integration ──────────────────────
        # v0.1: accept the strategy spec; build / resolve into a
        # ``LoopStrategy`` and store on self. The actual rewrite of
        # ``_run_loop_core`` to drive the strategy lands in L7.
        # Accepts: ``LoopStrategy`` / str / dict / None.
        strategy: Any | None = None,
        # ── DSH-inspired: pluggable context injectors ──────────
        injectors: list[Any] | None = None,
        # ── Approval gate (no-progress interruption) ──────────────
        # When the LLM calls the same tool N times in a row, ask the
        # user to approve continuation instead of silently bailing out.
        # ``approval_timeout`` is how long to wait (default 30 min);
        # ``approval_on_timeout`` decides what to do if the user is
        # offline (default "continue" — less disruptive than failing).
        approval_timeout: float = 1800.0,
        approval_on_timeout: str = "continue",  # "continue" | "reject"
        role: str | None = None,  # agent name (used in approval event)
        # ── SwarmWorker parity (unified engine) ─────────────────────
        # Per-iteration wall-clock timeout for the LLM call. Async mode
        # cancels via asyncio.wait_for; sync mode post-checks elapsed
        # (matching SwarmWorker semantics). None = disabled (default).
        iteration_timeout_s: float | None = None,
        # One-shot wrap-up nudge system message at 0.8×max_iterations.
        wrap_up_nudge: bool = False,
        # Final iteration passes tools=None so the model must answer
        # in text instead of issuing more tool calls.
        force_final_text: bool = False,
    ):
        self.config = config
        self.memory = memory
        self.workspace = workspace
        self.max_iterations = max_iterations
        self.no_progress_window = no_progress_window
        self.threshold_tokens = threshold_tokens
        self.heartbeat_interval = heartbeat_interval
        self.auto_git_commit = auto_git_commit
        self.session_id = session_id
        self.strategy_name = strategy_name
        self.strategy_dir = strategy_dir
        self.runs_dir = runs_dir
        self.results_tsv = results_tsv
        self.write_roots = write_roots
        self.read_roots = read_roots
        self.enable_goal_injection = enable_goal_injection
        self.enable_memory_injection = enable_memory_injection
        self.enable_hypothesis_auto_create = enable_hypothesis_auto_create
        self._hooks = hooks
        self._session_manager = session_manager
        self._on_event = on_event
        self._stream_mode = stream_mode
        self._event_bus = event_bus
        self._circuit_breaker = circuit_breaker
        self._retry_policy = retry_policy or RetryPolicy()
        # P1-5: build / resolve the LoopStrategy from the spec.
        # v0.1 stores it on self; ``_run_loop_core`` does not yet
        # consult it (that's L7's job).
        from strategy_research.kernel.agent.strategy.profile_resolver import resolve_loop_strategy
        self._strategy = resolve_loop_strategy(strategy)
        # L7 v0.2: when the caller passed an explicit strategy, its
        # config drives the loop (max_iterations etc.); otherwise we
        # fall back to the constructor kwargs. This keeps existing
        # ``AgentLoop(max_iterations=2)`` tests working while letting
        # an explicit profile/strategy override the cap.
        self._strategy_explicit = strategy is not None
        if self._strategy_explicit:
            cfg = self._strategy.config
            self.no_progress_window = getattr(cfg, "no_progress_window", self.no_progress_window)
            self._wrap_up_ratio = getattr(cfg, "wrap_up_ratio", WRAP_UP_RATIO)
        # L7: inject self into every Default*Step so steps that opt
        # in (currently DefaultPreRunStep + DefaultLLMCallStep) can
        # call AgentLoop methods. Custom steps ignore the binding.
        _inject_agent_loop(self._strategy, self)
        self._enable_claim_validation = enable_claim_validation
        self._strict_claim_validation = strict_claim_validation
        self.cc = compact_config or config.compact_config or CompactConfig()
        self._previous_summary: str | None = None
        # DSH-inspired: pluggable context injectors (sorted by order).
        if injectors is not None:
            self._injectors = sorted(injectors, key=lambda i: getattr(i, "order", 0))
        else:
            from .context_injector import build_default_injectors
            self._injectors = build_default_injectors()
        # P5-4: pre-run 上下文（goal/memory）经 ContextHub 收集——唯一组装点。
        # per-iteration / post-response injector 仍走 self._injectors 原通路
        # （设计：docs/stage-p54-context-hub-w0-design-20261027.md §1）。
        from strategy_research.kernel.context import ContextHub
        from .context_providers import build_prerun_context_provider

        self.context_hub = ContextHub()
        self.context_hub.register(build_prerun_context_provider(self, self._injectors))
        # 注意：provider 在构造时对 order<0 成员做了一次性快照——
        # self._injectors 构造后视为冻结（运行期增删 pre-run injector
        # 不会反映到桥接；当前无消费者这么做）。
        # DSH-inspired: steering inbox for mid-loop message injection.
        self._inbox: asyncio.Queue = asyncio.Queue()

        # Tool filtering: allowed_tools > readonly > all
        if allowed_tools is not None:
            filtered = ToolRegistry()
            for name in allowed_tools:
                tool = registry.get(name)
                if tool is not None:
                    filtered.register(tool)
            self.registry = filtered
        elif readonly:
            filtered = ToolRegistry()
            for name, tool in registry._tools.items():
                if getattr(tool, "is_readonly", True):
                    filtered.register(tool)
            self.registry = filtered
        else:
            self.registry = registry

        self.context_builder = ContextBuilder(
            config=config, registry=self.registry,
            memory=memory, workspace=workspace,
            system_prompt=system_prompt,
        )
        self.client = OpenAICompatClient(config)
        # Track tool_calls per iteration for no_progress detection
        self._recent_hashes: list[str] = []
        # ── Approval gate state (no-progress interruption) ───────
        self._approval_timeout = approval_timeout
        self._approval_on_timeout = approval_on_timeout
        self._role = role
        self._iteration_timeout_s = iteration_timeout_s
        self._wrap_up_nudge = wrap_up_nudge
        self._force_final_text = force_final_text
        self._approval_event = threading.Event()
        self._approval_response: str | None = None
        self._approval_thread: threading.Thread | None = None
        # Subagent delegation counter (reset per arun())
        self._subagent_count: list[int] = [0]  # mutable ref for SubAgentTool
        # Trace writer (optional)
        self._trace_writer: TraceWriter | None = None
        if trace_dir is not None:
            self._trace_writer = TraceWriter(trace_dir)

    # ── Hook sync adapter ─────────────────────────

    def _build_hook_context(
        self, iteration: int, messages: list[dict[str, Any]],
    ) -> AgentHookContext:
        """Build AgentHookContext for the current iteration."""
        return AgentHookContext(iteration=iteration, messages=messages)

    # ── P0-2 D: capability seam builders ────────────────────────

    def _build_data_store(self):
        """Return the default DataStore for this AgentLoop instance.

        v0.1 always returns ``get_store("duckdb")`` — the registry's
        default provider. Tests / callers can override by setting
        ``self._data_store_override`` before ainvoke runs.
        """
        override = getattr(self, "_data_store_override", None)
        if override is not None:
            return override
        from strategy_research.foundation.storage import get_store
        return get_store()

    def _build_sandbox(self):
        """Return the default ExecutionSandbox for this AgentLoop instance.

        v0.1 always returns a fresh ``StaticSandbox`` rooted at
        ``self.workspace``. Overridable via
        ``self._sandbox_override``.
        """
        override = getattr(self, "_sandbox_override", None)
        if override is not None:
            return override
        from strategy_research.kernel.agent.sandbox import StaticSandbox
        workspace = self.workspace or getattr(self, "_fallback_workspace", None)
        if workspace is None:
            # StaticSandbox needs a workspace; in tests where workspace
            # isn't set we still return a sandbox bound to cwd. Tools
            # that call resolve_write/read without a real workspace
            # already raise PathValidationError, so this is safe.
            from pathlib import Path
            workspace = Path.cwd()
        return StaticSandbox(workspace)

    # ── P1-5: LoopStrategy accessor ──────────────────────────────

    def get_strategy(self):
        """Return the ``LoopStrategy`` resolved from the ``strategy=``
        constructor arg (or the default ReAct strategy when none was
        supplied). v0.1 stores the strategy on ``self._strategy`` but
        ``_run_loop_core`` does not yet consult it (L7 work)."""
        return self._strategy

    # ── L7 v0.2: step execution with uniform error isolation ──────

    async def _call_step(self, step: Any, ctx: Any, *, async_mode: bool) -> Any:
        """Execute a strategy Step with try/except isolation.

        A failing Step must not crash the agent loop: we log the error,
        set ``ctx.should_stop`` so the caller can break safely, and
        return the (possibly partially-mutated) context. Sync steps
        (evaluate / is_no_progress / is_open) are handled transparently
        via ``inspect.isawaitable``.
        """
        try:
            result = step.execute(ctx, async_mode=async_mode)
            if inspect.isawaitable(result):
                return await result
            return result
        except Exception as exc:  # noqa: BLE001 — isolated per-step
            logger.warning("step %s failed: %s", step.name, exc)
            ctx.should_stop = True
            ctx.stop_reason = f"step_{step.name}"
            return ctx

    # ── Shared logic (sync-safe: pure logic + trace + emit, no I/O) ──

    def _prepare_run(
        self, task: str, context: str | None,
        history: list[dict[str, Any]] | None = None,
    ) -> tuple[str, LoopResult, list[dict[str, Any]], float]:
        """Assemble full_task, init result/messages, emit loop_start trace."""
        self._maybe_auto_create_hypothesis(task)
        full_task = task
        if context:
            full_task = context + "\n\n" + task
        # P5-4: pre-run 上下文经 ContextHub 收集（原 injector 循环迁入；
        # 整链桥接保持检索 query 的叠加语义，full_task 逐字节等价——
        # docs/stage-p54-context-hub-w0-design-20261027.md §2）。
        from strategy_research.kernel.context.protocol import ContextQuery

        bundle = self.context_hub.build(
            ContextQuery(task_text=full_task, session_id=self.session_id),
        )
        if bundle.items:
            full_task = (
                "\n\n".join(item.content for item in bundle.items)
                + "\n\n" + full_task
            )
        result = LoopResult()
        messages = self.context_builder.build_initial_messages(full_task, history=history)
        result.messages = list(messages)
        t0 = time.perf_counter()
        result.started_at = t0
        # A1 budget accounting — reset per run.
        self._budget_tokens = 0
        self._budget_prompt_tokens = 0
        self._budget_completion_tokens = 0
        self._budget_cost_usd = 0.0
        self._trace_and_emit("loop_start", {
            "task": task,
            "max_iterations": self.max_iterations,
            "tokens": estimate_tokens(messages),
        })
        return full_task, result, messages, t0

    # ── A1: three-dimensional run budget ────────────────────────────

    def _record_budget_usage(self, response: Any, t0: float) -> None:
        """Accumulate one LLM response's usage into the run budget."""
        from strategy_research.kernel.agent.strategy.budget import usage_from_response

        usage = getattr(response, "usage", None) or {}
        total, prompt, completion = usage_from_response(usage)
        self._budget_tokens = getattr(self, "_budget_tokens", 0) + total
        self._budget_prompt_tokens = getattr(self, "_budget_prompt_tokens", 0) + prompt
        self._budget_completion_tokens = getattr(self, "_budget_completion_tokens", 0) + completion

        cfg = getattr(self._strategy, "config", None)
        price_in = getattr(cfg, "price_per_mtok_input", None) if cfg else None
        price_out = getattr(cfg, "price_per_mtok_output", None) if cfg else None
        cost = getattr(self, "_budget_cost_usd", 0.0)
        if price_in:
            cost += (prompt / 1_000_000.0) * float(price_in)
        if price_out:
            cost += (completion / 1_000_000.0) * float(price_out)
        self._budget_cost_usd = cost

    def _check_budget(self, t0: float) -> tuple[bool, str | None]:
        """Return (exceeded, reason) against LoopConfig budget limits (A5)."""
        from strategy_research.kernel.agent.runtime.policy import StopPolicy, limits_from_config

        cfg = getattr(self._strategy, "config", None)
        if cfg is None:
            return False, None
        policy = getattr(self, "_stop_policy", None)
        if policy is None or policy.limits != limits_from_config(cfg):
            policy = StopPolicy(limits_from_config(cfg))
            self._stop_policy = policy
        decision = policy.decide(
            tokens=getattr(self, "_budget_tokens", 0),
            cost_usd=getattr(self, "_budget_cost_usd", 0.0),
            started_at=t0,
        )
        return decision.should_stop, decision.reason

    def _emit_compaction(
        self, applied: list[str], iteration: int, result: LoopResult,
    ) -> None:
        """Extend compression_applied, emit compression trace + compact events."""
        result.compression_applied.extend(applied)
        self._trace_and_emit("compression", {"applied": applied, "iteration": iteration})
        for layer in applied:
            self._emit("compact", {
                "layer": layer,
                "iteration": iteration,
                "summary": f"Context compression: {layer}",
            })

    def _emit_iter_start(self, iteration: int, messages: list[dict[str, Any]]) -> None:
        """Emit iter_start trace + event, set _current_iter."""
        self._trace_and_emit("iter_start", {
            "iteration": iteration,
            "tokens": estimate_tokens(messages),
            "max_iterations": self.max_iterations,
        })
        self._current_iter = iteration

    def _effective_max_iterations(self) -> int:
        """Effective iteration cap (explicit strategy config wins)."""
        if self._strategy_explicit:
            return getattr(
                self._strategy.config, "max_iterations", self.max_iterations,
            )
        return self.max_iterations

    def _is_final_iteration(self, iteration: int) -> bool:
        return iteration >= self._effective_max_iterations()

    def _effective_tools(self, iteration: int) -> list[Any] | None:
        """Tool definitions for this iteration.

        With ``force_final_text`` the final iteration passes
        ``tools=None`` so the model must answer in text.
        """
        if self._force_final_text and self._is_final_iteration(iteration):
            return None
        return self.registry.get_definitions() or None

    async def _with_iteration_timeout(
        self, coro: Any, iteration: int, result: LoopResult,
    ) -> Any:
        """Await ``coro`` with the per-iteration wall-clock timeout.

        On timeout the result is finalized with
        ``finished_reason="timeout"`` and ``None`` is returned (the
        caller breaks the loop on a None response).
        """
        if self._iteration_timeout_s is None:
            return await coro
        try:
            return await asyncio.wait_for(coro, timeout=self._iteration_timeout_s)
        except (asyncio.TimeoutError, TimeoutError):
            self._handle_iteration_timeout(result, iteration)
            return None

    def _with_iteration_timeout_sync(
        self, fn: Callable[[], Any], iteration: int, result: LoopResult,
    ) -> Any:
        """Blocking variant: post-call elapsed check (SwarmWorker parity).

        A blocking ``client.chat`` cannot be cancelled safely from the
        same thread; like SwarmWorker we measure the wall clock after
        the call returns and terminate when it exceeded the budget.
        """
        t0 = time.perf_counter()
        out = fn()
        if (
            self._iteration_timeout_s is not None
            and (time.perf_counter() - t0) > self._iteration_timeout_s
        ):
            self._handle_iteration_timeout(result, iteration)
            return None
        return out

    def run(
        self,
        task: str,
        *,
        context: str | None = None,
        history: list[dict[str, Any]] | None = None,
    ) -> LoopResult:
        """Run the loop until done (sync path).

        Args:
            task: User task description.
            context: Optional context to prepend to task (e.g., current_state).
            history: Optional prior conversation turns in OpenAI
                ``{"role": ..., "content": ...}`` format. Inserted between
                the system prompt and the current user message so the LLM
                has full conversation context.

        Returns:
            LoopResult with answer, iterations, tool_calls_made, finished_reason.
        """
        logger.info("[AGENT] run start task_len=%d history_len=%d",
                   len(task), len(history) if history else 0)

        # Log compaction messages in history
        if history:
            compaction_count = sum(1 for h in history
                                  if h.get("role") == "user"
                                  and "<conversation-checkpoint>" in h.get("content", ""))
            logger.info("[AGENT] compaction_in_history=%d", compaction_count)

        return _run_coro_in_sync(
            self._run_loop_core(task, context, history, async_mode=False)
        )

    # ── Async public API ──────────────────────────

    async def arun(
        self,
        task: str,
        *,
        context: str | None = None,
        history: list[dict[str, Any]] | None = None,
    ) -> LoopResult:
        """Async version of run() - all I/O points use await.

        Uses ``client.astream()`` / ``client.achat()`` for LLM calls,
        ``asyncio.to_thread()`` for tool execution, and ``asyncio.gather()``
        for parallel readonly tools.

        Args:
            task: The user's task description.
            context: Optional goal-context prefix prepended to the task.
            history: Optional prior conversation turns in OpenAI
                ``{"role": ..., "content": ...}`` format. Inserted between
                the system prompt and the current user message so the LLM
                has full conversation context.

        Returns:
            LoopResult with answer, iterations, tool_calls_made, finished_reason.
        """
        return await self._run_loop_core(task, context, history, async_mode=True)

    # ── DSH-inspired: steering inbox ──────────────────────────────

    def inject(self, message: dict[str, Any]) -> None:
        """Queue a message for injection at the next pre-step boundary.

        The message is drained at the start of each iteration (after
        compaction, before LLM call).  This enables mid-loop steering
        — e.g. injecting user corrections or external context while the
        agent is still running.

        Non-blocking: always succeeds.  Messages are FIFO.
        """
        self._inbox.put_nowait(message)

    async def ainject(self, message: dict[str, Any]) -> None:
        """Async variant of inject()."""
        await self._inbox.put(message)

    def _drain_inbox(self, messages: list[dict[str, Any]]) -> None:
        """Drain all queued inbox messages into the conversation."""
        while not self._inbox.empty():
            try:
                msg = self._inbox.get_nowait()
                messages.append(msg)
            except asyncio.QueueEmpty:
                break

    async def _run_loop_core(  # noqa: C901
        self,
        task: str,
        context: str | None,
        history: list[dict[str, Any]] | None,
        *,
        async_mode: bool,
    ) -> LoopResult:
        """Shared run-loop core (single source for ``run``/``arun``).

        ``async_mode`` selects the await-based I/O (achat/astream/ainvoke/
        gather/afire_hooks) vs the synchronous equivalents (chat/stream/
        invoke/ThreadPoolExecutor/fire_hooks). Both paths share the same
        iteration/hook/compact/stop/no-progress semantics.
        """
        from strategy_research.foundation.observability.trace import _session_id, _trace_id

        if not _trace_id.get():
            _trace_id.set(uuid.uuid4().hex[:12])
        if self.session_id and not _session_id.get():
            _session_id.set(self.session_id)
        full_task, result, messages, t0 = self._prepare_run(task, context, history)
        response = None  # L7 v0.4: set by LLMCallStep each iteration
        self._subagent_count[0] = 0  # reset per-turn delegation counter
        hook_ctx = self._build_hook_context(0, messages)

        async def _fire(name: str, ctx: Any, *args: Any) -> None:
            # Both modes await hook coroutines on the current loop: the
            # sync adapter (_fire_hooks) runs them on a throwaway loop,
            # which raises in 3.10 when the coroutine was created inside
            # a running loop (the sync `run` bridge thread).
            await self._afire_hooks(name, ctx, *args)

        await _fire("before_run", hook_ctx)

        # Phase 5-guardrails v1: observe injection signatures in the user
        # task (docs/phase5-guardrails-design.md §2.3). Emits only — never
        # alters the message path; see extension-points.md hooks red line.
        try:
            from .guardrails import scan_user_input

            for finding in scan_user_input(task, source_id=self.session_id or ""):
                self._emit("guardrail.finding", finding.to_dict())
                await _fire("on_guardrail_finding", hook_ctx, finding)
        except Exception:  # noqa: BLE001 — guardrails never break the loop
            logger.debug("guardrail scan failed (user input)", exc_info=True)

        # L7 v0.2: iteration cap — an explicit strategy config drives
        # it (a profile said max_iterations=N); otherwise fall back to
        # the constructor ``self.max_iterations`` so existing tests /
        # callers that pass max_iterations=2 keep working unchanged.
        max_iter = self._effective_max_iterations()
        for iteration in range(1, max_iter + 1):
            result.iterations = iteration
            hook_ctx = self._build_hook_context(iteration, messages)

            # SwarmWorker parity: one-shot wrap-up nudge at configurable
            # ratio (default 0.8) to push the model toward a final text answer.
            if (
                self._wrap_up_nudge
                and iteration == max(1, int(max_iter * getattr(self, "_wrap_up_ratio", WRAP_UP_RATIO)))
            ):
                messages.append({
                    "role": "system",
                    "content": WRAP_UP_NUDGE_TEXT,
                })

            # L7 v0.5: compaction + per-iteration observability delegated
            # to the strategy's CompactionStep. The step runs the
            # compaction engine, emits _emit_compaction, _emit_iter_start.
            comp_ctx = _make_strategy_ctx(
                self, messages, None, result, iteration, hook_ctx,
            )
            comp_ctx = await self._call_step(
                self._strategy.compaction, comp_ctx, async_mode=async_mode,
            )
            messages = comp_ctx.messages

            # DSH-inspired: drain steering inbox (mid-loop message injection)
            self._drain_inbox(messages)

            # DSH-inspired: run per-iteration injectors (order=0, e.g. TodosInjector)
            for injector in self._injectors:
                if getattr(injector, "order", 0) == 0:
                    try:
                        injector.inject_per_iteration(self, messages)
                    except Exception:  # noqa: BLE001
                        pass  # injectors must not break the loop

            # L7 v0.4: LLM call + before_iteration + on_error delegated
            # to the strategy's LLMCallStep. The step fires
            # before_iteration before _get_response and on_error after
            # _handle_llm_error.
            llm_ctx = _make_strategy_ctx(
                self, messages, None, result, iteration, hook_ctx,
            )
            llm_ctx = await self._call_step(
                self._strategy.llm_call, llm_ctx, async_mode=async_mode,
            )
            if llm_ctx.should_stop:
                break
            response = llm_ctx.response
            if response is None:
                break
            messages = llm_ctx.messages

            # A1: accumulate LLM usage into the run budget, then stop
            # if tokens/cost/time are exhausted before doing more work.
            self._record_budget_usage(response, t0)
            budget_stop, budget_reason = self._check_budget(t0)
            if budget_stop:
                result.finished_reason = "budget"
                result.stop_reason = budget_reason
                self._trace_and_emit("budget_exhausted", {
                    "reason": budget_reason,
                    "tokens_used": self._budget_tokens,
                    "cost_usd": round(self._budget_cost_usd, 6),
                    "elapsed": round(time.perf_counter() - t0, 3),
                })
                await _fire("after_iteration", hook_ctx)
                break

            if not response.has_tool_calls():
                # L7: consult the strategy's ContinuationStep before
                # the goal check. DefaultContinuationStep is a no-op
                # (returns False); the legacy path still runs.
                should_continue, _ = self._strategy.continuation.evaluate(
                    _make_strategy_ctx(self, messages, response, result, iteration, hook_ctx)
                )
                if should_continue:
                    continue
                # DSH-inspired: run post-response injectors (order>=100, e.g. GoalContinuationInjector)
                injector_continued = False
                for injector in self._injectors:
                    if getattr(injector, "order", 0) >= 100:
                        try:
                            if injector.inject_post_response(self, response, messages, result, iteration):
                                injector_continued = True
                                break
                        except Exception:  # noqa: BLE001
                            pass  # injectors must not break the loop
                if injector_continued:
                    continue
                # L7: consult the strategy's StopStep before the legacy
                # _handle_stop. DefaultStopStep is a no-op; the legacy
                # path still runs.
                should_stop, _stop_reason = self._strategy.stop.evaluate(
                    _make_strategy_ctx(self, messages, response, result, iteration, hook_ctx)
                )
                if should_stop:
                    await _fire("after_iteration", hook_ctx)
                    break
                self._handle_stop(response, result, iteration)
                await _fire("after_iteration", hook_ctx)
                break

            # L7 v0.2 decision point 3: circuit-breaker gate delegated to
            # the strategy's ResilienceStep. DefaultResilienceStep reads
            # ``self._circuit_breaker`` directly, so behaviour is
            # identical unless a custom strategy overrides is_open().
            if self._strategy.resilience.is_open(
                _make_strategy_ctx(self, messages, response, result, iteration, hook_ctx)
            ):
                tool_result_msgs = self._breaker_open_messages(response.tool_calls)
                self._append_tool_results(response.tool_calls, tool_result_msgs, messages, result)
                await _fire("after_iteration", hook_ctx)
                continue

            # L7 v0.3: tool execution + tool lifecycle hooks delegated
            # to the strategy's ToolExecutionStep. The step fires
            # before_execute_tools / on_tool_error / after_tool_executed
            # and collects hashes into ctx.metadata["tool_hashes"].
            tool_ctx = _make_strategy_ctx(
                self, messages, response, result, iteration, hook_ctx
            )
            tool_ctx = await self._call_step(
                self._strategy.tool_execution, tool_ctx, async_mode=async_mode,
            )
            if tool_ctx.should_stop:
                await _fire("after_iteration", hook_ctx)
                break
            tool_result_msgs = tool_ctx.metadata.get("tool_result_msgs") or []
            tool_hashes = tool_ctx.metadata.get("tool_hashes") or []
            # The step already appended tool results into ctx.messages
            # via _append_tool_results; messages = tool_ctx.messages to
            # pick them up.
            messages = tool_ctx.messages
            await _fire("after_iteration", hook_ctx)

            # L7 v0.2 decision point 2: no-progress detection delegated
            # to the strategy's ProgressStep. We record the hashes into
            # the step's window, then ask it whether the window shows
            # no progress. DefaultProgressStep mirrors AgentLoop's
            # legacy _recent_hashes / _detect_no_progress, so behaviour
            # is identical unless a custom strategy overrides it.
            progress_ctx = _make_strategy_ctx(
                self, messages, response, result, iteration, hook_ctx
            )
            for h in tool_hashes:
                self._strategy.progress.record_hash(progress_ctx, h)
            if self._strategy.progress.is_no_progress(progress_ctx):
                # Keep the legacy side-effect path (record_event,
                # circuit_breaker.record_no_progress, emit) intact.
                # hashes_pre_recorded=True avoids double-counting the
                # window (record_hash already did the extend above).
                #
                # Honour the return value: True = exit (user rejected /
                # timeout-reject), False = user approved continuation —
                # fall through to the next iteration instead of leaving.
                # (Previously the value was discarded, so an approved
                # continuation still exited — the approval gate was
                # unreachable.)
                if self._check_no_progress(
                    tool_hashes, response, result, iteration,
                    hashes_pre_recorded=True,
                ):
                    await _fire("after_run", hook_ctx, result)
                    if self._trace_writer is not None:
                        result.trace_path = str(self._trace_writer.path)
                        self._trace_writer.close()
                    return result
        else:
            self._handle_max_iter(result)

        # L7 v0.4: finalization (metrics + claim validation) + the
        # normal-end ``after_run`` hook delegated to the strategy's
        # FinalizationStep. The no-progress early-return path fires
        # after_run in the skeleton above (it returns before reaching
        # this Step) — that call is intentionally kept.
        fin_ctx = _make_strategy_ctx(
            self, messages, response, result, result.iterations, hook_ctx,
        )
        fin_ctx.t0 = t0
        fin_ctx = await self._call_step(
            self._strategy.finalization, fin_ctx, async_mode=async_mode,
        )
        if async_mode:
            await asyncio.to_thread(self._git_commit, full_task, result)
        else:
            self._git_commit(full_task, result)

        if self._trace_writer is not None:
            result.trace_path = str(self._trace_writer.path)
            self._trace_writer.close()

        return result

    async def _get_response(
        self,
        messages: list[dict[str, Any]],
        iteration: int,
        async_mode: bool,
        hook_ctx: Any,
        result: LoopResult,
    ) -> "LLMResponse | None":
        """Chat/stream call with the non-streaming fallback.

        Returns the response, or ``None`` when the stream-fallback error
        path already fired ``on_error`` (caller breaks the loop).
        Raises ``LLMError`` when the error was NOT stream-fallback-eligible
        (caller handles + fires ``on_error``).
        """
        try:
            if self._stream_mode:
                # Trace LLM request envelope before streaming
                self._trace_llm_request(messages, iteration, tools=None)
                if async_mode:
                    return await self._with_iteration_timeout(
                        self._astream_chat(messages, iteration), iteration, result,
                    )
                return self._with_iteration_timeout_sync(
                    lambda: self._stream_chat(messages, iteration), iteration, result,
                )
            tools = self._effective_tools(iteration)
            # Trace LLM request envelope before non-streaming call
            self._trace_llm_request(messages, iteration, tools=tools)
            if async_mode:
                return await self._with_iteration_timeout(
                    self.client.achat(messages, tools=tools), iteration, result,
                )
            return self._with_iteration_timeout_sync(
                lambda: self.client.chat(messages, tools=tools), iteration, result,
            )
        except LLMError as exc:
            if not (self._stream_mode and not self._is_stream_required_error(exc)):
                raise
            # Streaming failed for non-streaming-required reasons
            # (e.g. provider doesn't support SSE, parsing error on a
            # partial chunk). Fall back to non-streaming chat().
            try:
                tools = self._effective_tools(iteration)
                # Trace fallback LLM request
                self._trace_llm_request(messages, iteration, tools=tools)
                if async_mode:
                    return await self._with_iteration_timeout(
                        self.client.achat(messages, tools=tools), iteration, result,
                    )
                return self._with_iteration_timeout_sync(
                    lambda: self.client.chat(messages, tools=tools), iteration, result,
                )
            except LLMError as exc2:
                self._handle_llm_error(exc2, iteration, result)
                await self._afire_hooks("on_error", hook_ctx, exc2)
                return None

    @staticmethod
    def _response_to_assistant_msg(response: LLMResponse) -> dict[str, Any]:
        """Convert LLMResponse to an assistant message dict."""
        msg: dict[str, Any] = {
            "role": "assistant",
            "content": response.content,
        }
        if response.has_tool_calls():
            msg["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.name,
                        "arguments": json.dumps(tc.arguments, ensure_ascii=False),
                    },
                }
                for tc in response.tool_calls
            ]
        return msg


    def _trace(self, entry: dict[str, Any]) -> None:
        """Write a trace entry if trace writer is active."""
        if self._trace_writer is not None:
            try:
                self._trace_writer.write(entry)
            except Exception:                       # noqa: BLE001
                pass  # trace failures should never break the loop

    def _trace_llm_request(
        self,
        messages: list[dict[str, Any]],
        iteration: int,
        tools: list[dict[str, Any]] | None = None,
    ) -> None:
        """Log full LLM request envelope (DSH request/header pattern).

        Records system prompt, tools schema, and history metadata for each
        LLM call.  Large fields (system prompt, tools JSON) are offloaded to
        sidecar files when they exceed the threshold.

        Two sinks:
        1. ``llm_request`` on_event → the B4 forwarder (service.py) offloads
           large fields and persists to event_log (single source of truth).
        2. trace.jsonl (optional, backward-compat) if a trace_writer is set.

        This provides the foundation for Trajectory View / Trace Viewer.
        """
        try:
            import json as _json

            # Extract system prompt from first message
            system_prompt = ""
            if messages and messages[0].get("role") == "system":
                system_prompt = messages[0].get("content", "")

            # Tools schema as JSON
            tools_json = _json.dumps(tools, ensure_ascii=False) if tools else "[]"

            # History metadata (don't log full content — too large)
            history_meta = []
            for m in messages[1:]:  # skip system prompt
                history_meta.append({
                    "role": m.get("role", "?"),
                    "content_len": len(m.get("content", "")),
                    "has_tool_calls": bool(m.get("tool_calls")),
                })

            # DSH-inspired: hash fields for fast diff detection
            system_prompt_hash = hashlib.sha256(system_prompt.encode()).hexdigest()[:16]
            tools_hash = hashlib.sha256(tools_json.encode()).hexdigest()[:16]
            # History hash: content-length fingerprint (cheap, no full-text hash)
            history_fingerprint = "|".join(
                f"{m.get('role', '?')}:{len(m.get('content', ''))}"
                for m in messages[1:]
            )
            history_hash = hashlib.sha256(history_fingerprint.encode()).hexdigest()[:16]

            entry: dict[str, Any] = {
                "type": "llm_request",
                "iteration": iteration,
                "session_id": self.session_id or "",
                "history_count": len(messages),
                "history_meta": history_meta,
                "tools_count": len(tools) if tools else 0,
                "system_prompt_len": len(system_prompt),
                "system_prompt": system_prompt,
                "tools_schema": tools_json,
                # DSH-inspired: hash fields
                "system_prompt_hash": system_prompt_hash,
                "tools_hash": tools_hash,
                "history_hash": history_hash,
                "estimated_tokens": estimate_tokens(messages),
            }

            # Sink 1: event_log via the on_event forwarder (offloads large
            # fields itself). Best-effort — never break the loop.
            try:
                self._emit("llm_request", dict(entry))
            except Exception:  # noqa: BLE001
                logger.debug("llm_request emit failed", exc_info=True)

            # Sink 2: trace.jsonl (optional, backward-compat).
            if self._trace_writer is not None:
                try:
                    # Use sidecar offload for large fields
                    self._trace_writer.write_text_entry(
                        dict(entry),
                        field="system_prompt",
                        value=system_prompt,
                        offload_kind=f"llm-request-system-{iteration}",
                    )
                    # Tools schema goes inline (usually small) or offloaded
                    self._trace_writer.write_text_entry(
                        dict(entry),
                        field="tools_schema",
                        value=tools_json,
                        offload_kind=f"llm-request-tools-{iteration}",
                        threshold=10_000,  # tools schema usually fits inline
                    )
                except Exception:  # noqa: BLE001
                    pass  # trace failures should never break the loop
        except Exception:                       # noqa: BLE001
            pass  # trace failures should never break the loop

    # ── P3-d: Goal + Hypothesis integration ────────

    def _maybe_auto_create_hypothesis(self, task: str) -> None:
        """Auto-create an exploring hypothesis per (strategy, market) on first run.

        Per the P3-b user decision, this fires only when:
          - enable_hypothesis_auto_create is True (default)
          - session_id is set
          - strategy_name is set
          - registry has no matching (strategy, market) hypothesis yet

        Failures are swallowed (logged at most) to avoid breaking the loop.
        """
        if not self.enable_hypothesis_auto_create:
            return
        if not self.session_id or not self.strategy_name:
            return
        try:
            from strategy_research.apps.hypothesis import HypothesisAutoCreator
            creator = HypothesisAutoCreator()
            hyp = creator.maybe_auto_create(
                session_id=self.session_id,
                strategy_name=self.strategy_name,
                initial_thesis=task,
                # FIXME: hardcoded market. The unified market detection
                # (core/utils/market_detection.py) was standardized in
                # Phase 4-3; this call site should resolve the market
                # from the detected asset universe instead of assuming
                # A-shares.
                market="a_share",
            )
            if hyp is not None:
                self._trace({
                    "type": "hypothesis_auto_created",
                    "hypothesis_id": hyp.hypothesis_id,
                    "title": hyp.title,
                })
        except Exception as exc:                     # noqa: BLE001
            # Never let hypothesis machinery break the agent loop
            self._trace({
                "type": "hypothesis_auto_create_failed",
                "error": str(exc),
            })

    def _get_goal_snapshot(self) -> dict[str, Any] | None:
        """Return the raw goal snapshot for continuation checks.

        Returns None when no active goal exists or on failure.
        """
        if not self.enable_goal_injection:
            return None
        if not self.session_id:
            return None
        try:
            return _get_goal_store().get_current_snapshot(self.session_id)
        except Exception:  # noqa: BLE001
            return None

    # ── Git commit after run ──────────────────────

    def _git_commit(self, task: str, result: LoopResult) -> None:
        """Auto-commit workspace changes after run."""
        if not self.auto_git_commit or self.workspace is None:
            return
        try:
            msg = f"agent: {result.finished_reason} | {task[:80]}"
            ok = git_commit(self.workspace, msg)
            if ok:
                self._trace({"type": "git_commit", "message": msg})
        except Exception as exc:                    # noqa: BLE001
            logger.warning("git commit failed: %s", exc)
            self._trace({"type": "git_commit_error", "error": str(exc)})


__all__ = ["AgentLoop", "LoopResult"]
