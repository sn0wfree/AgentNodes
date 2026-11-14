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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator, Callable

from agentnodes.foundation.git import git_commit
from agentnodes.foundation import settings as _settings
from agentnodes.kernel.hooks.composite import CompositeHook
from agentnodes.kernel.hooks.context import AgentHookContext
from agentnodes.foundation.llm import LLMConfig, LLMResponse, OpenAICompatClient, ToolCall
from agentnodes.foundation.llm.errors import LLMError
from agentnodes.kernel.memory.persistent import PersistentMemory
from agentnodes.foundation.utils.ids import SessionId
from ._core_types import StepResult, maybe_await
from .circuit_breaker import RetryPolicy, ToolLoopCircuitBreaker
from .compact import CompactConfig
from . import agent_telemetry as _tel
from .context import ContextBuilder, estimate_tokens
from .runtime.sync_bridge import _get_hook_loop as _get_hook_loop  # noqa: F401
from .runtime.sync_bridge import _run_coro_in_sync
from .tools import ToolRegistry
from .trace import TraceWriter

logger = logging.getLogger(__name__)


# ── Shared persistent hook loop / sync↔async bridge ───────────────
# W11 (2026-11-04): ``_get_hook_loop`` / ``_run_coro_in_sync`` used to be
# defined here as well as in ``runtime/sync_bridge.py`` — two copies, each
# with its own ``_HOOK_LOOP`` global and lock. ``loop_telemetry.py`` imported
# the sync_bridge copy while this module used its own, so a process could end
# up with two daemon hook event loops. ``sync_bridge`` is now the single
# authority; the names are imported above so existing importers keep working.


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
# 环境变量 AGENTNODES_TOOL_RESULT_MAX_CHARS 可覆盖（0 = 不截断）。
_TOOL_RESULT_HISTORY_MAX = int(_settings.get_str("AGENTNODES_TOOL_RESULT_MAX_CHARS"))


# ── Cached GoalStore (goal-snapshot injection) ─────────────────────
# Constructing a GoalStore per loop iteration leaked one SQLite
# connection each (the store had no close() before F1-2); a single
# long-lived store per db path avoids the churn while keeping test
# isolation when AGENTNODES_GOAL_DB_PATH is overridden.
_goal_store_cache: dict[Path, Any] = {}


def _get_goal_store() -> Any:
    """Return the cached GoalStore for the default goal DB path."""
    from agentnodes.kernel.agent.capability import resolve

    GoalStore = resolve("goal.store")
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
    from agentnodes.kernel.agent.strategy.loop_context import LoopContext

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


def _merge_spec(explicit: Any, folded: Any) -> Any:
    """合并显式传入的 LoopSpec 与旧 kwargs 折叠结果（批次 12 S2）。

    **显式 spec 优先**：旧 kwargs 只在「非默认值」时覆盖——这样
    ``AgentLoop(config, registry, spec=my_spec)`` 不会被旧参数的
    默认值（10 iterations / stream_mode=True 等）意外冲掉。
    """
    from dataclasses import fields as _fields

    from .loop_spec import LoopSpec as _LoopSpec

    merged = _LoopSpec()
    defaults = _LoopSpec.fold_legacy_kwargs({})

    for group_name in ("budget", "runtime", "capability", "context",
                       "permission", "scope"):
        exp_group = getattr(explicit, group_name)
        fold_group = getattr(folded, group_name)
        default_group = getattr(defaults, group_name)
        cls = type(exp_group)
        values: dict[str, Any] = {}
        for f in _fields(exp_group):
            exp_val = getattr(exp_group, f.name)
            fold_val = getattr(fold_group, f.name)
            def_val = getattr(default_group, f.name)
            # 旧 kwargs 非默认 → 覆盖；否则用显式 spec 的值
            values[f.name] = fold_val if fold_val != def_val else exp_val
        object.__setattr__(merged, group_name, cls(**values))

    merged.on_event = folded.on_event if folded.on_event is not None else explicit.on_event
    return merged


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
    #: 当前状态的上下文指纹（统一引擎 U6）：每轮更新，反映「模型最近一次
    #: 调用看到的上下文」。格式 ``trace_id_system_hash_tools_hash_c_digest``。
    #: 详见 docs/design/ctx-hash-id-design-20261112.md。
    ctx_hash_id: str = ""

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
        # ── 批次 V3-a：tier-1 tool_set 裁剪签名 ──────────────────
        # 空 = 不裁剪（``get_definitions_for("")`` 与 ``get_definitions()``
        # 完全等价），所以默认路径零变化。非空时按签名裁掉不在该
        # harness 里的工具——原路径 B（make_react_node，V3-c 已删）曾有此能力。
        task_signature: str = "",
        # **批次 13 附录 §4**：默认改为 False——``hypothesis.auto_creator``
        # capability 随 hypothesis 子系统归档在 testcase/golden/，未注册；
        # 旧默认 True 让这个开关**永不生效**（resolve 抛异常被吞）。
        # 恢复注册后再翻回 True。
        enable_hypothesis_auto_create: bool = False,
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
        # ── 批次 12 S2: LoopSpec 聚合入口 ──────────────────────────
        # **不删任何旧参数**（阶段 3 才删）——200+ 调用点零改动的实现是
        # 本参数缺省时走旧签名，之后 ``spec`` 的显式字段覆盖旧参数。
        spec: Any | None = None,
    ):
        # 批次 12 S2：把旧式参数折叠进 LoopSpec（单一事实源
        # ``loop_spec._LEGACY_MAP``）。显式传 spec 时以其为准，
        # 旧参数作为**覆盖层**（便于渐进迁移：先传 spec，再逐项删旧参）。
        from .loop_spec import LoopSpec as _LoopSpec

        _folded = _LoopSpec.fold_legacy_kwargs({
            # Scope（**workspace 曾漏传 → 工具拿不到 ctx.workspace，
            # 全部报 "missing workspace context"；批次 12 S2 自测抓出**）
            "workspace": workspace,
            # Capability（memory 同理曾漏传）
            "memory": memory,
            "max_iterations": max_iterations,
            "no_progress_window": no_progress_window,
            "threshold_tokens": threshold_tokens,
            "heartbeat_interval": heartbeat_interval,
            "trace_dir": trace_dir,
            "auto_git_commit": auto_git_commit,
            "system_prompt": system_prompt,
            "allowed_tools": allowed_tools,
            "readonly": readonly,
            "session_id": session_id,
            "strategy_name": strategy_name,
            "strategy_dir": strategy_dir,
            "runs_dir": runs_dir,
            "results_tsv": results_tsv,
            "write_roots": write_roots,
            "read_roots": read_roots,
            "enable_goal_injection": enable_goal_injection,
            "enable_memory_injection": enable_memory_injection,
            "task_signature": task_signature,
            "enable_hypothesis_auto_create": enable_hypothesis_auto_create,
            "hooks": hooks,
            "session_manager": session_manager,
            "on_event": on_event,
            "stream_mode": stream_mode,
            "compact_config": compact_config,
            "event_bus": event_bus,
            "circuit_breaker": circuit_breaker,
            "retry_policy": retry_policy,
            "enable_claim_validation": enable_claim_validation,
            "strict_claim_validation": strict_claim_validation,
            "strategy": strategy,
            "injectors": injectors,
            "approval_timeout": approval_timeout,
            "approval_on_timeout": approval_on_timeout,
            "role": role,
            "iteration_timeout_s": iteration_timeout_s,
            "wrap_up_nudge": wrap_up_nudge,
            "force_final_text": force_final_text,
        })
        if spec is not None:
            # 显式 spec 优先；旧参数作为覆盖层（非默认值才覆盖）
            _folded = _merge_spec(spec, _folded)
        self._spec = _folded

        # ── 以下赋值全部改从 spec 读（旧参数已在上面折叠）──────────
        self.config = config
        self.memory = _folded.capability.memory
        self.workspace = _folded.scope.workspace
        self.max_iterations = _folded.budget.max_iterations
        self.no_progress_window = _folded.budget.no_progress_window
        self.threshold_tokens = _folded.budget.threshold_tokens
        self.heartbeat_interval = _folded.runtime.heartbeat_interval
        self.auto_git_commit = _folded.runtime.auto_git_commit
        self.session_id = _folded.scope.session_id
        self.strategy_name = _folded.context.strategy_name
        self.strategy_dir = _folded.scope.strategy_dir
        self.runs_dir = _folded.scope.runs_dir
        self.results_tsv = _folded.scope.results_tsv
        self.write_roots = _folded.scope.write_roots
        self.read_roots = _folded.scope.read_roots
        self.enable_goal_injection = _folded.context.enable_goal_injection
        self.enable_memory_injection = _folded.context.enable_memory_injection
        self._task_signature = _folded.context.task_signature
        self.enable_hypothesis_auto_create = (
            _folded.context.enable_hypothesis_auto_create
        )
        self._hooks = _folded.capability.hooks
        self._session_manager = _folded.capability.session_manager
        self._on_event = _folded.on_event
        self._stream_mode = _folded.runtime.stream_mode
        self._event_bus = _folded.capability.event_bus
        self._circuit_breaker = _folded.capability.circuit_breaker
        self._retry_policy = _folded.capability.retry_policy or RetryPolicy()
        # 批次 13 附录 §3：旧实现只赋值不读——现在 loop_tools 会消费它，
        # 但**仅当调用方显式传入**（默认 RetryPolicy() 的 3 次不应悄悄
        # 覆盖既有的 2 次重试行为）。
        self._retry_policy_explicit = _folded.capability.retry_policy is not None
        # P1-5: build / resolve the LoopStrategy from the spec.
        # v0.1 stores it on self; ``_run_loop_core`` does not yet
        # consult it (that's L7's job).
        from agentnodes.kernel.agent.strategy.profile_resolver import resolve_loop_strategy
        self._strategy = resolve_loop_strategy(_folded.context.strategy)
        # L7 v0.2: when the caller passed an explicit strategy, its
        # config drives the loop (max_iterations etc.); otherwise we
        # fall back to the constructor kwargs. This keeps existing
        # ``AgentLoop(max_iterations=2)`` tests working while letting
        # an explicit profile/strategy override the cap.
        self._strategy_explicit = _folded.context.strategy is not None
        if self._strategy_explicit:
            cfg = self._strategy.config
            self.no_progress_window = getattr(cfg, "no_progress_window", self.no_progress_window)
            self._wrap_up_ratio = getattr(cfg, "wrap_up_ratio", WRAP_UP_RATIO)
        # L7: inject self into every Default*Step so steps that opt
        # in (currently DefaultPreRunStep + DefaultLLMCallStep) can
        # call AgentLoop methods. Custom steps ignore the binding.
        _inject_agent_loop(self._strategy, self)
        self._enable_claim_validation = _folded.context.enable_claim_validation
        self._strict_claim_validation = _folded.context.strict_claim_validation
        self.cc = (
            _folded.capability.compact_config
            or config.compact_config or CompactConfig()
        )
        self._previous_summary: str | None = None
        # DSH-inspired: pluggable context injectors (sorted by order).
        _injectors = _folded.capability.injectors
        if _injectors is not None:
            self._injectors = sorted(_injectors, key=lambda i: getattr(i, "order", 0))
        else:
            from .context_injector import build_default_injectors
            self._injectors = build_default_injectors()
        # P5-4: pre-run 上下文（goal/memory）经 ContextHub 收集——唯一组装点。
        # per-iteration / post-response injector 仍走 self._injectors 原通路
        # （设计：docs/archive/2026-10/stage-p54-context-hub-w0-design-20261027.md §1）。
        from agentnodes.kernel.context import ContextHub
        from .context_providers import build_prerun_context_provider

        self.context_hub = ContextHub()
        self.context_hub.register(build_prerun_context_provider(self, self._injectors))
        # 注意：provider 在构造时对 order<0 成员做了一次性快照——
        # self._injectors 构造后视为冻结（运行期增删 pre-run injector
        # 不会反映到桥接；当前无消费者这么做）。
        # DSH-inspired: steering inbox for mid-loop message injection.
        self._inbox: asyncio.Queue = asyncio.Queue()

        # Tool filtering: allowed_tools > readonly > all
        # （批次 12 S2：读 spec 而非旧局部变量）
        _allowed_tools = _folded.context.allowed_tools
        _readonly = _folded.runtime.readonly
        if _allowed_tools is not None:
            filtered = ToolRegistry()
            for name in _allowed_tools:
                tool = registry.get(name)
                if tool is not None:
                    filtered.register(tool)
            self.registry = filtered
        elif _readonly:
            filtered = ToolRegistry()
            for name, tool in registry._tools.items():
                if getattr(tool, "is_readonly", True):
                    filtered.register(tool)
            self.registry = filtered
        else:
            self.registry = registry

        self.context_builder = ContextBuilder(
            config=config, registry=self.registry,
            memory=_folded.capability.memory, workspace=_folded.scope.workspace,
            system_prompt=_folded.context.system_prompt,
        )
        self.client = OpenAICompatClient(config)
        # Track tool_calls per iteration for no_progress detection
        self._recent_hashes: list[str] = []
        # ── Approval gate state (no-progress interruption) ───────
        self._approval_timeout = _folded.budget.approval_timeout
        self._approval_on_timeout = _folded.budget.approval_on_timeout
        self._role = _folded.context.role
        self._iteration_timeout_s = _folded.budget.iteration_timeout_s
        self._wrap_up_nudge = _folded.budget.wrap_up_nudge
        self._force_final_text = _folded.budget.force_final_text
        self._approval_event = threading.Event()
        self._approval_response: str | None = None
        self._approval_thread: threading.Thread | None = None
        # ── 权限闸（批次 V2）────────────────────────────────────
        #
        # **实测背景**（2026-11-09 V2）：真实模型执行 `sudo echo ...` 时
        # hard_deny 没拦住——因为直接构造 `AgentLoop` 时 evaluator 是 None，
        # 工具裸执行。`chat_loop.build_chat_agent_loop` 会 setattr 挂上，
        # 但 evals / scheduled / 脚本 / 测试都绕过了它。
        #
        # **为什么默认仍是 None 而不是自动接线**：`AgentLoop` 是**低级构造器**，
        # 大量既有调用方（含 27 个测试）依赖「无 evaluator = 无权限检查」的
        # 旧行为；在构造器里静默加安全策略会让它们全部 fail-closed 拒绝
        # （实测：27 个测试红）。安全策略应由**工厂层**决定。
        #
        # 因此：
        #   - `spec.permission.*` 显式给了 -> 用它（新入口，供工厂/调用方）
        #   - 否则保持 None -> 由 `chat_loop` / 未来其它工厂 setattr 挂载
        # 生产安全由 `chat_loop` 的默认接线 + `AGENTNODES_PERMISSION_MODE` 保证；
        # 直接构造 AgentLoop 的场景（评测/脚本）需要调用方显式传 permission spec。
        _perm = _folded.permission
        self._permission_evaluator = _perm.permission_evaluator
        self._permission_gateway = _perm.permission_gateway
        # Subagent delegation counter (reset per arun())
        self._subagent_count: list[int] = [0]  # mutable ref for SubAgentTool
        # Trace writer (optional)
        self._trace_writer: TraceWriter | None = None
        if _folded.runtime.trace_dir is not None:
            self._trace_writer = TraceWriter(_folded.runtime.trace_dir)

    # ── Hook sync adapter ─────────────────────────

    def _build_hook_context(
        self, iteration: int, messages: list[dict[str, Any]],
    ) -> AgentHookContext:
        """Build AgentHookContext for the current iteration."""
        return AgentHookContext(iteration=iteration, messages=messages)

    # ── P0-2 D: capability seam builders ────────────────────────

    def _build_data_store(self):
        """Return the default DataStore for this AgentLoop instance, or None.

        Resolution order:
        1. ``self._data_store_override`` (tests / callers that inject one).
        2. The L4 injection slot filled by a process entry point via
           ``register_default_data_store``.
        3. ``None`` — ``ToolContext.data_store`` is Optional, so tools that
           need it fail with their own clear message instead of the whole
           loop refusing to run. A one-time warning makes the gap visible.

        W10-3 (2026-11-04): this used to read the L2 registry directly
        (``from ...foundation.storage import get_store``), which broke when
        the registry moved to ``apps.data_store_registry`` during W7 — the
        import raised ImportError on every tool batch and was logged as
        "step tool_execution failed" while the loop carried on. Importing L2
        from here would fix the crash but add an upward dependency the layer
        gate rejects (L3>L2 went 46 → 47), so the slot lives in L4 and is
        filled by the entry point.
        """
        override = getattr(self, "_data_store_override", None)
        if override is not None:
            return override
        from agentnodes.foundation.storage import get_default_data_store

        store = get_default_data_store()
        if store is None and not getattr(self, "_warned_no_data_store", False):
            self._warned_no_data_store = True
            logger.warning(
                "no default DataStore registered; ToolContext.data_store will "
                "be None. Call register_default_data_store(get_store()) in the "
                "process entry point, or set _data_store_override."
            )
        return store

    def _build_sandbox(self):
        """Return the default ExecutionSandbox for this AgentLoop instance.

        v0.1 always returns a fresh ``StaticSandbox`` rooted at
        ``self.workspace``. Overridable via
        ``self._sandbox_override``.
        """
        override = getattr(self, "_sandbox_override", None)
        if override is not None:
            return override
        from agentnodes.kernel.agent.sandbox import StaticSandbox
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

    async def _call_step(self, step: Any, ctx: Any) -> StepResult:
        """Execute a strategy Step（handle 协议唯一通道，B2-① T11 定稿）.

        全部 step 已迁 ``handle(input, ctx) → StepResult``；缺 handle 的
        传入对象按 AttributeError 走错误隔离（不静默兜底旧形状）。

        错误隔离：异常 → ``StepResult.fail(f"{step.name}: {exc}")``。
        **不写 ``ctx.should_stop``**——停止语义由读 stop 通道的调用点
        显式回填。见 step-protocol-migration-20261112.md §4。
        """
        try:
            return await maybe_await(step.handle(None, ctx))
        except Exception as exc:  # noqa: BLE001 — isolated per-step
            sname = getattr(step, "name", type(step).__name__)
            logger.warning("step %s failed: %s", sname, exc)
            return StepResult.fail(f"{sname}: {exc}")

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
        # docs/archive/2026-10/stage-p54-context-hub-w0-design-20261027.md §2）。
        from agentnodes.kernel.context.protocol import ContextQuery

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
        from agentnodes.kernel.agent.strategy.budget import usage_from_response

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

        **批次 V3-a**：按 ``task_signature`` 做 tier-1 tool_set 裁剪
        （``get_definitions_for`` 在 signature 为空时与 ``get_definitions()``
        **完全等价**——``tools.py:881-882``），所以默认路径零变化。
        这是路径 A 相对路径 B 唯一缺的能力，补齐后两条路径的能力集一致。
        """
        if self._force_final_text and self._is_final_iteration(iteration):
            return None
        if self._task_signature:
            return self.registry.get_definitions_for(self._task_signature) or None
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
            self.arun(task, context=context, history=history)
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
        self._ensure_trace_context()
        return await self._run_via_skeleton(task, context, history)

    def _ensure_trace_context(self) -> None:
        """确保本次运行有 trace_id / session_id（ContextVar 兜底）。

        **为什么在入口而不是循环体**：原兜底在 ``_run_loop_core``（legacy
        引擎）内部——统一引擎（``LoopEngine``）走不到，于是**生产主路径的
        日志与事件没有 request 级标识**（``event_store.py:167`` 注入时拿到
        None）。走 API 的路径由 ``session/service.py:601`` 先设了
        ``attempt_id``，所以此前没被发现；直接用 ``AgentLoop`` 的调用则缺失。

        U5.5 之后 ``run()``/``arun()`` 是唯一入口，兜底放这里两条路径都覆盖。
        与 legacy 的旧行为一致：**只在未设置时补**（不覆盖调用方已设的值——
        API 层设的 attempt_id 优先）。
        """
        from agentnodes.foundation.observability.trace import (
            _session_id,
            _trace_id,
        )

        if not _trace_id.get():
            _trace_id.set(uuid.uuid4().hex[:12])
        if self.session_id and not _session_id.get():
            _session_id.set(self.session_id)

    async def _run_via_skeleton(
        self,
        task: str,
        context: str | None,
        history: list[dict[str, Any]] | None,
    ) -> LoopResult:
        """B3 阶段 1 影子路径：LoopEngine 消费 deciders 的状态机。

        handlers（LoopReasoner/LoopActor/LoopPrepare/LoopHooks/LoopFinalize）
        绑定 self，复用全部既有能力——引擎本体不知道预算怎么算、工具怎么执行。

        **U5.5**：装配逻辑已提取为 :meth:`_build_engine` /
        :meth:`_build_engine_spec`；本方法只是「drain 到底」的薄包装，
        与公开入口 :meth:`run_stream` 同源（一次运行两种消费方式）。
        """
        async for _event in self._run_stream_skeleton(task, context, history):
            pass
        return self._skeleton_result()

    async def run_stream(
        self,
        task: str,
        *,
        context: str | None = None,
        history: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """**统一引擎的唯一流式入口**（U5.5）。

        逐事件产出：``text_delta`` / ``thinking_*`` / 生命周期事件 /
        ``pause`` / ``error`` / ``done``（恰一次）。

        **为什么不接受 ``engine`` 参数**（有意非对称）：legacy 引擎没有流式
        入口——它的事件走 ``_emit`` 回调副作用，不进生成器。暴露
        ``engine="legacy"`` 只会拿到 error/done 的残缺流，是 API 陷阱。
        要 legacy 行为请用 ``arun(engine="legacy")``（U6 删除前仍可达）。

        **一次运行只能消费一次**：`AgentLoop` 实例持有 ``_strategy`` /
        ``_skeleton_ctx`` 等可变状态，同一实例重复 run 未定义。
        """
        async for ev in self._run_stream_skeleton(task, context, history):
            yield ev

    async def _run_stream_skeleton(
        self,
        task: str,
        context: str | None,
        history: list[dict[str, Any]] | None,
    ) -> AsyncIterator[dict[str, Any]]:
        """装配引擎并驱动其 ``run_stream``（内部实现，公开入口的委托目标）。"""
        skeleton = self._build_engine()
        spec = self._build_engine_spec(task, context, history)
        async for ev in skeleton.run_stream(spec):
            yield ev

    def _skeleton_result(self) -> LoopResult:
        """取最近一次引擎运行的结果（``run_to_completion`` 等价物）。"""
        skeleton = getattr(self, "_skeleton", None)
        if skeleton is None:
            raise RuntimeError("no skeleton run yet")
        return skeleton._last_result

    def _build_engine(self) -> Any:
        """构造引擎及其全部 adapter（U5.5 从 ``_run_via_skeleton`` 提取）。

        handlers 绑定 self，复用全部既有能力——引擎本体不知道预算怎么算、
        工具怎么执行。
        """
        from .modes import deciders_for
        from .loop_engine import LoopEngine
        from .loop_engine_adapters import (
            LoopActor,
            LoopFinalize,
            LoopHooks,
            LoopIterationStart,
            LoopPrepare,
            LoopReasoner,
        )

        strategy = self._strategy
        max_iter = self._effective_max_iterations()
        # U4：halt_check 由装配层注入（R-001：kernel 不 import interfaces/cli）。
        # 默认 None = 无外部终止源（运行时零变化）。
        deciders = deciders_for(
            strategy, self, max_iterations=max_iter,
            halt_check=getattr(self, "_halt_check", None),
        )
        # 引擎需要 strategy 槽位被绑定（deciders_for 已做），再补
        # skeleton 自身的 hooks。
        skeleton = LoopEngine(
            reasoner=LoopReasoner(self),
            actor=LoopActor(self),
            deciders=deciders,
            hooks=LoopHooks(self),
            finalize=LoopFinalize(self),
            prepare=LoopPrepare(self),
            # 批次 13 S3-a：wrap_up_nudge 原内联在 legacy 循环体，
            # 翻默认后丢失——收进引擎的 on_iteration_start 钩子。
            on_iteration_start=LoopIterationStart(self),
            # 统一引擎 U3：双通道流式——loop 侧事件桥进 run_stream
            event_source=lambda: self,
        )
        self._skeleton = skeleton  # _skeleton_result() 用它取 result
        return skeleton

    def _build_engine_spec(
        self,
        task: str,
        context: str | None,
        history: list[dict[str, Any]] | None,
    ) -> Any:
        """构造一次运行的 spec（U5.5 从 ``_run_via_skeleton`` 提取）。"""
        from .loop_engine import LoopEngineSpec

        return LoopEngineSpec(
            task=task,
            context=context,
            history=history,
            max_iterations=self._effective_max_iterations(),
            session_id=self.session_id or "",
        )

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
        # **批次 V3-b**：外部注入源合流。``_inbox`` 是进程内 API 的
        # asyncio.Queue（``inject``/``ainject``）；GraphEngine 编排路径的
        # steer 走 mailbox 的 ``SteerInbox``——经构造方挂到本钩子，在同一
        # 安全点（compaction 后、LLM 前）进入会话。合批形状与原 react 节点
        # 逐字一致（单条 user 消息、``"\\n\\n".join``）；失败不崩轮。
        hook = getattr(self, "_inbox_drain_hook", None)
        if hook is not None:
            try:
                texts = hook() or []
            except Exception:  # noqa: BLE001 — 注入失败不崩轮（react 同款）
                texts = []
            if texts:
                messages.append(
                    {"role": "user", "content": "\n\n".join(str(t) for t in texts)}
                )
        while not self._inbox.empty():
            try:
                msg = self._inbox.get_nowait()
                messages.append(msg)
            except asyncio.QueueEmpty:
                break


    async def _get_response(
        self,
        messages: list[dict[str, Any]],
        iteration: int,
        hook_ctx: Any,
        result: LoopResult,
    ) -> "LLMResponse | None":
        """Chat/stream call with the non-streaming fallback（U6-2 后 async 唯一形态）.

        Returns the response, or ``None`` when the stream-fallback error
        path already fired ``on_error`` (caller breaks the loop).
        Raises ``LLMError`` when the error was NOT stream-fallback-eligible
        (caller handles + fires ``on_error``).
        """
        try:
            if self._stream_mode:
                # Trace LLM request envelope before streaming
                self._trace_llm_request(messages, iteration, tools=None)
                return await self._with_iteration_timeout(
                    self._astream_chat(messages, iteration), iteration, result,
                )
            tools = self._effective_tools(iteration)
            # Trace LLM request envelope before non-streaming call
            self._trace_llm_request(messages, iteration, tools=tools)
            return await self._with_iteration_timeout(
                self.client.achat(messages, tools=tools), iteration, result,
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
                return await self._with_iteration_timeout(
                    self.client.achat(messages, tools=tools), iteration, result,
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
        """Write a trace entry if trace writer is active.

        批次 13 S3-b1：实现搬到 ``agent_telemetry.trace``（全仓 32 处按
        方法名引用，故名字保留在此）。"""
        _tel.trace(self, entry)

    def _make_event_bridge(self) -> tuple[list[dict[str, Any]], Callable[[], None]]:
        """统一引擎 U3：事件桥——把 ``_emit`` 的事件同时压进一个列表。

        引擎 ``run_stream`` 经 ``event_source`` 拿到 ``(pending, detach)``：
        桥接期间 loop 侧所有 ``_emit``（text_delta / thinking_delta /
        llm_usage 等）**原回调照常执行**（chat SSE 不受影响），同时把
        ``(type, payload)`` 压进 pending；引擎在步骤间隙 drain 并 yield。
        detach 恢复原回调。零锁：单事件循环内生产（同步回调）与消费
        （引擎 await 点）交替。
        """
        pending: list[dict[str, Any]] = []
        original = self._on_event

        def _bridge(kind: str, payload: dict | None = None, *a: Any, **kw: Any) -> Any:
            pending.append({"type": kind, "payload": payload or {}})
            if original is not None:
                return original(kind, payload, *a, **kw)
            return None

        self._on_event = _bridge

        def _detach() -> None:
            self._on_event = original

        return pending, _detach

    def _trace_llm_request(
        self,
        messages: list[dict[str, Any]],
        iteration: int,
        tools: list[dict[str, Any]] | None = None,
    ) -> None:
        """Log full LLM request envelope (DSH request/header pattern).

        批次 13 S3-b1：实现（95 行）搬到 ``agent_telemetry.trace_llm_request``。
        两个 sink 的语义与阈值保持不变。
        """
        _tel.trace_llm_request(self, messages, iteration, tools)

    # ── P3-d: Goal + Hypothesis integration ────────

    def _maybe_auto_create_hypothesis(self, task: str) -> None:
        """Auto-create an exploring hypothesis per (strategy, market) on first run.

        Per the P3-b user decision, this fires only when:
          - enable_hypothesis_auto_create is True
          - session_id is set
          - strategy_name is set
          - registry has no matching (strategy, market) hypothesis yet

        Failures are swallowed (logged at most) to avoid breaking the loop.

        **批次 13 附录 §4**：``hypothesis.auto_creator`` 这个 capability
        目前**未注册**（随 hypothesis 子系统一起归档在
        ``testcase/golden/apps/hypothesis/``，见 ``apps/capabilities.py:64``），
        因此本函数每一次都会在 ``resolve()`` 处抛
        ``CapabilityNotFound`` 并被下面的 ``except`` 吞掉——功能实际不存在。
        默认值已改为 ``False`` 以免开关谎报；hypothesis 恢复注册后
        再把它翻回 ``True``。
        """
        if not self.enable_hypothesis_auto_create:
            return
        if not self.session_id or not self.strategy_name:
            return
        try:
            from agentnodes.kernel.agent.capability import resolve

            HypothesisAutoCreator = resolve("hypothesis.auto_creator")
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
