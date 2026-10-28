"""AgentExecutor — the single agent dispatch layer (unified engine).

One entrypoint executes any :class:`AgentPlugin`:

- ``executor_type="llm"`` → unified prompt path (role prompt + common
  principles + upstream outputs + context) → ``AgentLoop``
- ``executor_type="python"`` → :mod:`exec_registry` python function
- ``executor_type="evaluator"`` → :mod:`exec_registry` evaluator

This merges the two historical paths (``role_factory.run_agent_via_llm``
on the study side, ``SwarmRuntime._execute_agent`` → SwarmWorker on the
orchestration side) into one. ``role_factory`` keeps its public API and
delegates here.
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .dag_config import AgentNodeConfig
from .plugin import AgentPlugin

logger = logging.getLogger(__name__)

_AGENT_LOOP_KWARGS = (
    "strategy_name", "strategy_dir", "runs_dir", "results_tsv",
    "write_roots", "read_roots",
    "session_manager", "session_id", "iteration_timeout_s",
    "wrap_up_nudge", "force_final_text", "no_progress_window",
    "max_iterations",
)
# Context keys never rendered into the prompt's Current Context section.
_NON_PROMPT_KEYS = frozenset(_AGENT_LOOP_KWARGS) | {
    "tools", "input_from", "executor_type", "python_function",
    "behavior", "loop_strategy", "timeout", "evidence_criterion",
    # B3: was leaking into the prompt as "max_retries: 2" in the Current
    # Context block — a per-node execution knob, not task context.
    "max_retries",
}


@dataclass
class AgentExecutionResult:
    """Unified result for one agent execution."""

    agent_id: str
    status: str = "pending"        # success | error | skipped | pending
    output: str = ""
    error: str | None = None
    elapsed_s: float = 0.0
    summary: str = ""
    metrics: dict[str, Any] = field(default_factory=dict)
    artifacts: dict[str, Any] = field(default_factory=dict)

    @property
    def success(self) -> bool:
        return self.status == "success"

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "status": self.status,
            "output": self.output,
            "error": self.error,
            "elapsed_s": self.elapsed_s,
            "summary": self.summary,
            "metrics": self.metrics,
        }


def first_two_sentences(text: str) -> str:
    """Summary extraction (SwarmWorker parity)."""
    text = (text or "").strip()
    if not text:
        return ""
    parts = re.split(r"(?<=[.!?。！？])\s+", text, maxsplit=2)
    if len(parts) >= 2:
        return " ".join(parts[:2]).strip()[:400]
    cjk = re.split(r"(?<=[.!?。！？])", text, maxsplit=2)
    if len(cjk) >= 2:
        return "".join(cjk[:2]).strip()[:400]
    return text[:400]


# Errors that a retry cannot fix. Everything else (timeout / 5xx / malformed
# response) gets another attempt. Mirrors the classification the AgentLoop
# already uses for its stream→chat fallback (loop_progress.py:51-64), so the
# two layers agree on what "permanent" means.
_PERMANENT_ERROR_MARKERS = (
    "LLMAuthError",
    "LLMRateLimitError",
    "LLMConfigError",
    "LLMQuotaError",
    "no prompt_file",
    "empty system prompt",
)


def _is_retryable_execution_error(error: str | None) -> bool:
    """False for failures that repetition cannot fix."""
    if not error:
        return False
    return not any(marker in error for marker in _PERMANENT_ERROR_MARKERS)


class AgentExecutor:
    """Dispatch an AgentPlugin to the right execution backend."""

    def __init__(
        self,
        registry: Any | None = None,
        llm_config: Any | None = None,
    ):
        from .registry import get_default_registry
        self._registry = registry or get_default_registry()
        self._llm_config = llm_config

    # ── Public API ──────────────────────────────────────────────

    def execute(
        self,
        plugin: AgentPlugin,
        task: str,
        workspace: Path,
        *,
        context: dict[str, Any] | None = None,
        upstream_outputs: dict[str, str] | None = None,
        previous_outputs: list[Any] | None = None,
        history: list[dict[str, Any]] | None = None,
        node: AgentNodeConfig | None = None,
        on_event: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> AgentExecutionResult:
        """Execute one agent.

        Args:
            plugin: the agent definition.
            task: the current task text (becomes the user message core).
            workspace: filesystem root for tools / prompts.
            context: merged into the prompt ``Current Context`` section;
                known keys (strategy_dir, runs_dir, results_tsv,
                write_roots, read_roots, session_manager, loop_strategy,
                max_iterations, iteration_timeout_s, ...) are also
                forwarded to the AgentLoop constructor.
            upstream_outputs: ``{upstream_agent_id: output}`` rendered
                into the prompt (DAG edge payloads).
            previous_outputs: legacy list form (kept for role_factory
                compatibility); rendered after upstream_outputs.
            history: optional chat history injected into AgentLoop.
            node: per-node overrides (timeout / max_iterations /
                tools_override).
        """
        t0 = time.perf_counter()
        try:
            if plugin.executor_type == "python":
                return self._exec_python(
                    plugin, workspace, upstream_outputs, context, t0,
                )
            if plugin.executor_type == "evaluator":
                return self._exec_evaluator(
                    plugin, workspace, upstream_outputs, context, t0,
                )
            return self._exec_llm_with_retry(
                plugin, task, workspace,
                context=context,
                upstream_outputs=upstream_outputs,
                previous_outputs=previous_outputs,
                history=history,
                node=node,
                on_event=on_event,
                t0=t0,
            )
        except Exception as exc:  # noqa: BLE001
            return AgentExecutionResult(
                agent_id=plugin.id,
                status="error",
                error=str(exc),
                elapsed_s=round(time.perf_counter() - t0, 2),
            )

    def _exec_llm_with_retry(
        self,
        plugin: AgentPlugin,
        task: str,
        workspace: Path,
        *,
        retries_attempted: int = 0,
        **kwargs: Any,
    ) -> AgentExecutionResult:
        """``_exec_llm`` plus the ``max_retries`` contract (B3).

        ``AgentNodeConfig.max_retries`` has been declared since the DAG
        config landed but was never read anywhere -- a promise the code did
        not keep. It is honoured here, at the node boundary, so a transient
        provider failure does not lose the whole node.

        Retries are **opt-in**: ``max_retries`` defaults to ``None``, and
        swarm builds its node configs without it (runtime.py:369-374), so
        existing behaviour is unchanged. Permanent failures (missing or
        empty prompt) are not retried -- repeating them only burns time.
        """
        node = kwargs.get("node")
        budget = getattr(node, "max_retries", None)
        result = self._exec_llm(plugin, task, workspace, **kwargs)

        if result.status != "error" or budget is None:
            return result
        if retries_attempted >= int(budget):
            return result
        if not _is_retryable_execution_error(result.error):
            return result

        logger.warning(
            "agent %s failed (attempt %d/%d), retrying: %s",
            plugin.id, retries_attempted + 1, int(budget), result.error,
        )
        return self._exec_llm_with_retry(
            plugin, task, workspace,
            retries_attempted=retries_attempted + 1,
            **kwargs,
        )

    # ── python executor ─────────────────────────────────────────

    def _exec_python(
        self,
        plugin: AgentPlugin,
        workspace: Path,
        upstream: dict[str, str] | None,
        context: dict[str, Any] | None,
        t0: float,
    ) -> AgentExecutionResult:
        from . import exec_registry

        fn_name = plugin.python_function or plugin.id
        fn = exec_registry.get_python_executor(fn_name)
        if fn is None:
            return AgentExecutionResult(
                agent_id=plugin.id, status="error",
                error=f"No python executor registered for {fn_name!r}",
                elapsed_s=round(time.perf_counter() - t0, 2),
            )
        kwargs: dict[str, Any] = {
            "workspace_path": workspace,
            "upstream": upstream or {},
        }
        for key in ("strategy_name", "action", "description", "run_dir",
                    "timeout"):
            if context and key in context:
                kwargs[key] = context[key]
        result = fn(**kwargs)
        output = (
            json.dumps(result, ensure_ascii=False, default=str)
            if isinstance(result, dict) else str(result)
        )
        return AgentExecutionResult(
            agent_id=plugin.id, status="success", output=output,
            elapsed_s=round(time.perf_counter() - t0, 2),
        )

    # ── evaluator ───────────────────────────────────────────────

    def _exec_evaluator(
        self,
        plugin: AgentPlugin,
        workspace: Path,
        upstream: dict[str, str] | None,
        context: dict[str, Any] | None,
        t0: float,
    ) -> AgentExecutionResult:
        from . import exec_registry

        fn_name = plugin.python_function or plugin.id
        fn = exec_registry.get_evaluator(fn_name)
        if fn is None:
            fn = exec_registry.get_python_executor(fn_name)
        if fn is None:
            return AgentExecutionResult(
                agent_id=plugin.id, status="error",
                error=f"No evaluator registered for {fn_name!r}",
                elapsed_s=round(time.perf_counter() - t0, 2),
            )
        metrics = exec_registry.extract_metrics_from_upstream(upstream)
        extra = {
            k: v for k, v in (context or {}).items()
            if k not in ("executor_type", "python_function", "tools",
                         "input_from", "timeout")
        }
        result = fn(metrics=metrics, **extra)
        output = (
            json.dumps(result, ensure_ascii=False, default=str)
            if isinstance(result, dict) else str(result)
        )
        return AgentExecutionResult(
            agent_id=plugin.id, status="success", output=output,
            elapsed_s=round(time.perf_counter() - t0, 2),
        )

    # ── LLM (AgentLoop) ─────────────────────────────────────────

    def build_task_text(
        self,
        plugin: AgentPlugin,
        task: str,
        context: dict[str, Any] | None,
        upstream_outputs: dict[str, str] | None,
        previous_outputs: list[Any] | None,
    ) -> str:
        """Unified user-message composition (both systems' semantics)."""
        parts: list[str] = []
        if context:
            state_keys = {
                k: v for k, v in context.items()
                if k not in _NON_PROMPT_KEYS
            }
            if state_keys:
                parts.append("## Current Context\n" + json.dumps(
                    state_keys, ensure_ascii=False, default=str,
                ))
        if upstream_outputs:
            parts.append("## Upstream Agent Outputs")
            for agent_id, out in upstream_outputs.items():
                parts.append(f"### {agent_id}\n```\n{out}\n```")
        if previous_outputs:
            parts.append("## 之前 Agent 输出 (来自上一阶段)")
            for i, prev in enumerate(previous_outputs, 1):
                if isinstance(prev, dict):
                    parts.append(
                        f"### 第 {i} 阶段输出:\n```json\n"
                        f"{json.dumps(prev, ensure_ascii=False)}\n```"
                    )
                else:
                    parts.append(f"### 第 {i} 阶段输出:\n```\n{prev}\n```")
        parts.append("## 当前任务\n" + task)
        return "\n\n".join(parts)

    def _exec_llm(
        self,
        plugin: AgentPlugin,
        task: str,
        workspace: Path,
        *,
        context: dict[str, Any] | None,
        upstream_outputs: dict[str, str] | None,
        previous_outputs: list[Any] | None,
        history: list[dict[str, Any]] | None,
        node: AgentNodeConfig | None,
        on_event: Callable[[str, dict[str, Any]], None] | None,
        t0: float,
    ) -> AgentExecutionResult:
        from ..llm import LLMConfig
        from .builtin_tools import build_default_registry
        from .loop import AgentLoop
        from .prompt_builder import PromptBuilderFactory

        if not plugin.prompt_file:
            return AgentExecutionResult(
                agent_id=plugin.id, status="error",
                error=f"llm plugin {plugin.id!r} has no prompt_file",
                elapsed_s=round(time.perf_counter() - t0, 2),
            )

        system_prompt = PromptBuilderFactory.get(plugin.id).build_system_prompt(
            plugin.id, {},
        )
        if not system_prompt:
            return AgentExecutionResult(
                agent_id=plugin.id, status="error",
                error=f"empty system prompt for {plugin.id!r}",
                elapsed_s=round(time.perf_counter() - t0, 2),
            )

        ctx = context or {}
        cfg = self._llm_config or LLMConfig.load()

        loop_kwargs: dict[str, Any] = {
            k: ctx[k] for k in _AGENT_LOOP_KWARGS if k in ctx
        }
        loop_spec = ctx.get("loop_strategy")
        if loop_spec is not None:
            from .strategy.profile_resolver import resolve_loop_strategy
            loop_kwargs["strategy"] = resolve_loop_strategy(loop_spec)
        max_iterations = (
            (getattr(node, 'max_iterations', None) if node else None)
            or loop_kwargs.pop("max_iterations", None)
            or plugin.default_max_iterations
        )

        tools = (
            getattr(node, 'tools_override', None) if node and getattr(node, 'tools_override', None) is not None
            else list(plugin.tools)
        )

        # Enforce a per-iteration timeout derived from the plugin/node
        # timeout budget. Without this, a single hung LLM stream (with
        # wallclock_timeout_s defaulting to 1800s) can stall an agent
        # for 30 minutes per iteration. The context key (explicit
        # caller override) always wins.
        if "iteration_timeout_s" not in loop_kwargs:
            total_budget = (
                getattr(node, 'timeout', None) if node else None
            ) or plugin.default_timeout or 180
            # Split the budget across max iterations, floor 120s,
            # ceiling 600s — long enough for thinking models + tool
            # calls, short enough to bound a stuck iteration.
            per_iter = max(120.0, min(600.0, float(total_budget) / max(1, max_iterations)))
            loop_kwargs["iteration_timeout_s"] = per_iter

        loop = AgentLoop(
            config=cfg,
            registry=build_default_registry(),
            workspace=workspace,
            system_prompt=system_prompt,
            allowed_tools=tools,
            max_iterations=max_iterations,
            role=plugin.id,
            on_event=on_event,
            **loop_kwargs,
        )

        full_task = self.build_task_text(
            plugin, task, ctx, upstream_outputs, previous_outputs,
        )
        result = loop.run(full_task, history=history)

        ok = result.success
        return AgentExecutionResult(
            agent_id=plugin.id,
            status="success" if ok else "error",
            output=result.answer,
            error=result.error,
            elapsed_s=round(time.perf_counter() - t0, 2),
            summary=first_two_sentences(result.answer or ""),
            metrics={
                "iterations": result.iterations,
                "tool_calls_made": result.tool_calls_made,
                "finished_reason": result.finished_reason,
            },
        )


__all__ = ["AgentExecutor", "AgentExecutionResult", "first_two_sentences"]
