"""SwarmRuntime — DAG-based multi-agent orchestration with hook support (P3.1).

Breaking change in Phase 3:
  ``execute()`` now accepts an optional ``hooks`` parameter that enables
  external code to observe and control the execution lifecycle.

Hook callbacks:
  - ``on_layer_start(layer_idx, agents, context)``
  - ``on_agent_complete(agent_id, result, context)``
  - ``on_layer_complete(layer_idx, agents, results)``
  - ``should_stop() -> bool``
"""
from __future__ import annotations

import logging
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..workflow.dag import topological_layers
from .types import AgentCall, AgentStatus, SwarmHook

logger = logging.getLogger(__name__)


# ── Data classes ───────────────────────────────────────────────


@dataclass
class SwarmPreset:
    """A swarm preset loaded from YAML.

    Unified preset type (P3.3): covers both generic swarm presets
    and goal-specific workflow presets.  The goal/completion/branches
    fields are optional — generic swarm presets leave them as None.

    This replaces the separate ``GoalWorkflowConfig`` type for the
    execution layer.  The goal workflow loader still produces the
    richer ``GoalWorkflowConfig`` for YAML parsing, but converts
    it to ``SwarmPreset`` before passing to SwarmRuntime.
    """

    name: str
    description: str = ""
    agents: list[AgentCall] = field(default_factory=list)
    dag: dict[str, list[str]] = field(default_factory=dict)
    # Goal-specific fields (None for generic swarm presets)
    goal: dict[str, Any] | None = None
    completion: dict[str, Any] | None = None
    branches: list[dict[str, Any]] = field(default_factory=list)
    version: str = "1.0"
    # Budget fields (Phase 1.2: migrated from Study)
    # B3 (2026-10-28): budget_token is carried through from the study/goal
    # config but SwarmRuntime does not enforce it -- _budget_exceeded checks
    # turn/time only. Kept as a field because goal/workflow.py:136 forwards
    # it from a real study budget; deleting it would break that path.
    # preset_loader warns when a preset YAML sets it, so the gap is loud.
    budget_token: int | None = None
    budget_turn: int | None = None
    budget_time_seconds: float | None = None


@dataclass
class AgentResult:
    """Result of a single agent execution."""

    agent_id: str
    status: AgentStatus = AgentStatus.PENDING
    output: str = ""
    error: str | None = None
    elapsed_s: float = 0.0
    # Unified output envelope (workflow-module design, Commit 1):
    # all optional — legacy consumers reading only output/status are
    # unaffected.  Populated by workflow node dispatchers.
    summary: str = ""
    artifacts: dict[str, Any] = field(default_factory=dict)
    metrics: dict[str, Any] = field(default_factory=dict)


@dataclass
class SwarmResult:
    """Result of a swarm execution."""

    run_id: str = ""
    preset_name: str = ""
    agent_results: dict[str, AgentResult] = field(default_factory=dict)
    final_output: str = ""
    elapsed_s: float = 0.0
    success: bool = False


# ── SwarmRuntime ──────────────────────────────────────────────


class SwarmRuntime:
    """DAG-based multi-agent swarm runtime with hook support.

    Usage:
        runtime = SwarmRuntime()
        preset = load_preset(preset_path)
        result = runtime.execute(preset, workspace, task, hooks=[my_hook])
    """

    def __init__(
        self,
        max_workers: int = 4,
    ) -> None:
        self._max_workers = max_workers
        self._active_runs: dict[str, bool] = {}

    def execute(
        self,
        preset: SwarmPreset,
        workspace: Path,
        task: str,
        hooks: list[SwarmHook] | None = None,
        pre_completed: dict[str, AgentResult] | None = None,
        start_layer: int = 0,
    ) -> SwarmResult:
        """Execute a swarm preset with optional lifecycle hooks.

        Args:
            preset: Swarm preset defining agents + DAG.
            workspace: Filesystem root for prompt files.
            task: The task prompt string.
            hooks: Optional list of SwarmHook instances to call
                   at each lifecycle point.
            pre_completed: Optional dict of agent_id → AgentResult for
                agents that already executed in a prior run. Their
                outputs are loaded into ``result.agent_results`` before
                the loop starts, so downstream layers see them via
                ``_gather_upstream`` and skip re-execution.
            start_layer: Index of the first layer to actually execute
                (0-based). Earlier layers' agents should appear in
                ``pre_completed``. Default 0 (start from first layer).

        Returns:
            SwarmResult with per-agent outputs and success flag.
        """
        run_id = f"swarm_{uuid.uuid4().hex[:8]}"
        self._active_runs[run_id] = True
        hooks = hooks or []

        result = SwarmResult(run_id=run_id, preset_name=preset.name)
        # Seed pre-completed agent outputs so downstream layers see them.
        if pre_completed:
            for aid, ar in pre_completed.items():
                result.agent_results[aid] = ar
        t0 = time.perf_counter()

        # Phase 1.2: Budget tracking (migrated from Study)
        budget_turns = 0
        budget_time = 0.0

        try:
            layers = topological_layers(preset.dag)
            branches = preset.branches or []

            for layer_idx, layer in enumerate(layers):
                if layer_idx < start_layer:
                    # Pre-completed layer — skip execution but still
                    # notify the hook so UI state stays consistent.
                    self._emit(hooks, "on_layer_start", layer_idx, layer, {})
                    self._emit(
                        hooks, "on_layer_complete",
                        layer_idx, layer,
                        {aid: r for aid, r in result.agent_results.items()},
                    )
                    continue

                if run_id not in self._active_runs:
                    break  # cancelled via cancel(run_id)

                # Phase 1.2: Check budget before layer
                if self._budget_exceeded(preset, budget_turns, budget_time):
                    logger.info("SwarmRuntime budget exceeded at layer %d", layer_idx)
                    break

                # ── Hook: on_layer_start
                self._emit(hooks, "on_layer_start", layer_idx, layer, {})

                self._execute_layer(
                    layer, preset, workspace, task, result, hooks,
                )

                # Phase 1.2: Accumulate budget from executed agents
                for agent_id in layer:
                    ar = result.agent_results.get(agent_id)
                    if ar and ar.status == AgentStatus.SUCCESS:
                        budget_turns += 1
                budget_time = time.perf_counter() - t0

                # ── Hook: on_layer_complete
                self._emit(
                    hooks, "on_layer_complete",
                    layer_idx, layer,
                    {aid: r for aid, r in result.agent_results.items()},
                )

                # ── Branch evaluation (P3.7): skip / retry on remaining layers
                if branches:
                    self._evaluate_branches_after_layer(
                        branches, result.agent_results,
                        layers, layer_idx,
                    )

                # ── Hook: should_stop
                if self._any_hook_should_stop(hooks):
                    logger.info("SwarmRuntime stopped by hook at layer %d", layer_idx)
                    break

            result.success = all(
                r.status == AgentStatus.SUCCESS
                for r in result.agent_results.values()
            )
            completed = [
                r for r in result.agent_results.values()
                if r.status == AgentStatus.SUCCESS
            ]
            if completed:
                result.final_output = completed[-1].output

        finally:
            self._active_runs.pop(run_id, None)
            result.elapsed_s = round(time.perf_counter() - t0, 2)

        return result

    def cancel(self, run_id: str) -> bool:
        """Cancel a running swarm."""
        if run_id in self._active_runs:
            del self._active_runs[run_id]
            return True
        return False

    # ── Budget helpers (Phase 1.2) ────────────────────────────

    @staticmethod
    def _budget_exceeded(
        preset: SwarmPreset, turns: int, elapsed_s: float,
    ) -> bool:
        """Check if any budget limit has been exceeded."""
        if preset.budget_turn is not None and turns >= preset.budget_turn:
            return True
        if preset.budget_time_seconds is not None and elapsed_s >= preset.budget_time_seconds:
            return True
        return False

    # ── Hook helpers ───────────────────────────────────────────

    def _emit(
        self,
        hooks: list[SwarmHook],
        method_name: str,
        *args: Any,
    ) -> None:
        """Call a hook method on all hooks, swallowing errors."""
        for hook in hooks:
            try:
                method = getattr(hook, method_name, None)
                if method is not None:
                    method(*args)
            except Exception as exc:                    # noqa: BLE001
                logger.warning(
                    "Hook %s.%s failed: %s",
                    getattr(hook, "name", type(hook).__name__),
                    method_name, exc,
                )

    def _any_hook_should_stop(self, hooks: list[SwarmHook]) -> bool:
        """Return True if any hook's should_stop() returns True."""
        for hook in hooks:
            try:
                if hook.should_stop():
                    return True
            except Exception as exc:                    # noqa: BLE001
                logger.warning("Hook %s.should_stop failed: %s", hook.name, exc)
        return False

    # ── Agent execution ────────────────────────────────────────

    # Registry for python_executor functions: agent_name → callable
    _python_executors: dict[str, callable] = {}

    @classmethod
    def register_python_executor(cls, name: str, fn: callable) -> None:
        """Register a Python function to be called for python_executor agents."""
        cls._python_executors[name] = fn

    def _execute_agent(
        self,
        agent_call: AgentCall,
        workspace: Path,
        task: str,
        upstream: dict[str, str],
    ) -> AgentResult:
        """Execute a single agent.

        Supports three executor types via the unified AgentExecutor:
        - "llm" (default): AgentLoop with unified prompt path
        - "python_executor": registered Python function
        - "evaluator": decide() function for keep/discard
        """
        from ..agent.dag_config import AgentNodeConfig
        from ..agent.executor import AgentExecutor
        from ..agent.plugin import AgentPlugin
        from ..agent.registry import get_default_registry

        t0 = time.perf_counter()
        ctx = agent_call.context if hasattr(agent_call, "context") else {}
        executor_type = ctx.get("executor_type", "llm")
        plugin_id = ctx.get("agent_name", agent_call.agent_name)
        prompt_file = (getattr(agent_call, "prompt", "") or "").strip()

        # Build plugin from the global registry. Preset agents commonly
        # carry a custom id with a role prompt_file (e.g. id="allocator",
        # prompt=".prompts/portfolio_construction.md"), so fall back to
        # resolving by the prompt-file stem before giving up.
        reg = get_default_registry()
        plugin = reg.get(str(plugin_id))
        if plugin is None and prompt_file:
            stem = Path(prompt_file).stem
            plugin = reg.get(stem)
            if plugin is not None:
                plugin_id = stem

        if plugin is None:
            if ctx.get("allow_stub"):
                # Explicit opt-in for structural tests: a successful
                # no-op with no LLM call. Production presets never set
                # this — an unresolvable agent must fail the run instead
                # of faking SUCCESS (the old unconditional stub let a
                # typo'd agent id "complete" silently, and goal hooks
                # collected "[stub] ..." text as real evidence).
                return AgentResult(
                    agent_id=agent_call.agent_name,
                    status=AgentStatus.SUCCESS,
                    output=f"[stub] {agent_call.agent_name}: completed",
                    elapsed_s=round(time.perf_counter() - t0, 2),
                )
            if not prompt_file:
                return AgentResult(
                    agent_id=agent_call.agent_name,
                    status=AgentStatus.ERROR,
                    error=(
                        f"agent {agent_call.agent_name!r} is not registered "
                        f"and has no prompt_file; register it in the agent "
                        f"registry or set prompt_file in the preset"
                    ),
                    elapsed_s=round(time.perf_counter() - t0, 2),
                )
            # Unregistered but carries a prompt file: execute via an
            # ad-hoc plugin keyed by the prompt stem — the executor's
            # PromptBuilderFactory auto-discovers templates/.prompts/
            # <stem>.md, so custom roles run real agents. A missing
            # prompt file surfaces as an honest executor error.
            stem = Path(prompt_file).stem or str(agent_call.agent_name)
            plugin_id = stem
            timeout = ctx.get("timeout", 180)
            py_fn = ctx.get("python_function")
            plugin = AgentPlugin(
                id=stem, name=stem, category="execution",
                description=stem,
                prompt_file=prompt_file,
                tools=tuple(str(t) for t in (ctx.get("tools") or [])),
                executor_type=str(executor_type),
                python_function=str(py_fn) if py_fn is not None else None,
                default_timeout=int(timeout) if timeout is not None else 180,
            )

        node = AgentNodeConfig(
            id=str(plugin_id),
            timeout=ctx.get("timeout"),
            max_iterations=ctx.get("max_iterations"),
            tools_override=list(ctx["tools"]) if "tools" in ctx else None,
        )

        executor = AgentExecutor(reg)
        result = executor.execute(
            plugin, task, workspace,
            context=ctx,
            upstream_outputs=upstream if upstream else None,
            node=node,
        )

        # Map unified status to SwarmRuntime's AgentStatus enum.
        status_map = {
            "success": AgentStatus.SUCCESS,
            "error": AgentStatus.ERROR,
            "skipped": AgentStatus.SKIPPED,
        }
        return AgentResult(
            agent_id=agent_call.agent_name,
            status=status_map.get(result.status, AgentStatus.ERROR),
            output=result.output,
            error=result.error,
            elapsed_s=result.elapsed_s,
            summary=result.summary,
            metrics=result.metrics,
            artifacts=result.artifacts,
        )

    def _execute_python_executor(
        self,
        agent_call: AgentCall,
        workspace: Path,
        upstream: dict[str, str],
        ctx: dict,
        t0: float,
    ) -> AgentResult:
        """Execute a python_executor agent by calling a registered function."""
        import json

        fn_name = ctx.get("python_function", agent_call.agent_name)
        fn = self._python_executors.get(fn_name)
        if fn is None:
            return AgentResult(
                agent_id=agent_call.agent_name,
                status=AgentStatus.ERROR,
                error=f"No python_executor registered for '{fn_name}'",
                elapsed_s=round(time.perf_counter() - t0, 2),
            )

        # Build kwargs from context and upstream
        kwargs = {
            "workspace_path": workspace,
            "upstream": upstream,
        }
        # Pass extra kwargs from context
        for key in ("strategy_name", "action", "description", "run_dir", "timeout"):
            if key in ctx:
                kwargs[key] = ctx[key]

        try:
            result = fn(**kwargs)
            output = json.dumps(result, ensure_ascii=False, default=str) if isinstance(result, dict) else str(result)
            return AgentResult(
                agent_id=agent_call.agent_name,
                status=AgentStatus.SUCCESS,
                output=output,
                elapsed_s=round(time.perf_counter() - t0, 2),
            )
        except Exception as exc:
            return AgentResult(
                agent_id=agent_call.agent_name,
                status=AgentStatus.ERROR,
                error=str(exc),
                elapsed_s=round(time.perf_counter() - t0, 2),
            )

    def _execute_evaluator(
        self,
        agent_call: AgentCall,
        workspace: Path,
        upstream: dict[str, str],
        ctx: dict,
        t0: float,
    ) -> AgentResult:
        """Execute an evaluator agent (e.g. decide() for keep/discard)."""
        import json

        fn_name = ctx.get("python_function", "decide")
        fn = self._python_executors.get(fn_name)
        if fn is None:
            return AgentResult(
                agent_id=agent_call.agent_name,
                status=AgentStatus.ERROR,
                error=f"No evaluator registered for '{fn_name}'",
                elapsed_s=round(time.perf_counter() - t0, 2),
            )

        # Extract metrics from upstream results
        metrics = {}
        for aid, result_str in upstream.items():
            if isinstance(result_str, str):
                try:
                    parsed = json.loads(result_str)
                    if isinstance(parsed, dict) and "metrics" in parsed:
                        metrics = parsed["metrics"]
                except (json.JSONDecodeError, TypeError):
                    pass

        try:
            result = fn(metrics=metrics, **{k: v for k, v in ctx.items() if k not in ("executor_type", "python_function")})
            output = json.dumps(result, ensure_ascii=False, default=str) if isinstance(result, dict) else str(result)
            return AgentResult(
                agent_id=agent_call.agent_name,
                status=AgentStatus.SUCCESS,
                output=output,
                elapsed_s=round(time.perf_counter() - t0, 2),
            )
        except Exception as exc:
            return AgentResult(
                agent_id=agent_call.agent_name,
                status=AgentStatus.ERROR,
                error=str(exc),
                elapsed_s=round(time.perf_counter() - t0, 2),
            )

    # ── DAG helpers ────────────────────────────────────────────

    def _find_agent(self, agents: list[AgentCall], agent_id: str) -> AgentCall | None:
        for a in agents:
            if a.agent_name == agent_id:
                return a
        return None

    def _execute_layer(
        self,
        layer: list[str],
        preset: SwarmPreset,
        workspace: Path,
        task: str,
        result: SwarmResult,
        hooks: list[SwarmHook] | None,
    ) -> None:
        """Run one layer's agents in parallel, collecting outputs."""
        with ThreadPoolExecutor(max_workers=self._max_workers) as executor:
            layer_futures_local: dict[Any, str] = {}
            for agent_id in layer:
                # Skip agents already completed in a prior run.
                if agent_id in result.agent_results:
                    continue
                agent_call = self._find_agent(preset.agents, agent_id)
                if agent_call is None:
                    continue

                upstream = self._gather_upstream(
                    agent_id, preset.dag, result.agent_results,
                )
                future = executor.submit(
                    self._execute_agent,
                    agent_call, workspace, task, upstream,
                )
                layer_futures_local[future] = agent_id

            for future in as_completed(layer_futures_local):
                agent_id = layer_futures_local[future]
                try:
                    agent_result = future.result()
                    result.agent_results[agent_id] = agent_result

                    # ── Hook: on_agent_complete
                    self._emit(
                        hooks, "on_agent_complete",
                        agent_id, agent_result, {},
                    )

                except Exception as exc:  # noqa: BLE001
                    # AgentStatus has no FAILED member — an exception in
                    # the handler used to crash with AttributeError
                    # instead of recording the agent failure.
                    result.agent_results[agent_id] = AgentResult(
                        agent_id=agent_id,
                        status=AgentStatus.ERROR,
                        error=str(exc),
                    )
                    self._emit(
                        hooks, "on_agent_complete",
                        agent_id,
                        result.agent_results[agent_id],
                        {},
                    )

    def _gather_upstream(
        self,
        agent_id: str,
        dag: dict[str, list[str]],
        results: dict[str, AgentResult],
    ) -> dict[str, str]:
        upstream_ids = dag.get(agent_id, [])
        upstream = {}
        for uid in upstream_ids:
            r = results.get(uid)
            if r and r.status == AgentStatus.SUCCESS:
                upstream[uid] = r.output
        return upstream

    # ── Branch support (P3.7) ───────────────────────────────────

    def _evaluate_branches_after_layer(
        self,
        branches: list[dict[str, Any]],
        agent_results: dict[str, AgentResult],
        layers: list[list[str]],
        layer_idx: int,
    ) -> None:
        """Evaluate branches after a layer and log applied skip/retry."""
        layer_results = self._build_layer_results(agent_results)
        skip, retry = self._apply_branches(
            branches, layer_results, layers, layer_idx,
        )
        if skip or retry:
            logger.info(
                "Branches after layer %d: skip=%s retry=%s",
                layer_idx, skip, retry,
            )

    def _build_layer_results(
        self,
        agent_results: dict[str, AgentResult],
    ) -> dict[str, Any]:
        """Convert AgentResult dict → layer_results for expression evaluator.

        Shape: ``{agent_id: {"output": {field: val}}}`` so conditions like
        ``risk_controller.output.max_drawdown < -0.2`` resolve. Each agent's
        ``output`` is the JSON-parsed worker payload; if its ``answer`` is
        itself JSON, its keys are merged up so ``output.max_drawdown`` works
        for both the wrapper payload and the inner answer dict.
        """
        import json

        layer_results: dict[str, Any] = {}
        for aid, ar in agent_results.items():
            if ar.status != AgentStatus.SUCCESS or not ar.output:
                continue
            try:
                parsed = json.loads(ar.output)
            except (json.JSONDecodeError, TypeError):
                parsed = {"output": ar.output}
            if isinstance(parsed, dict):
                out = dict(parsed)
                answer = parsed.get("answer")
                if isinstance(answer, str):
                    try:
                        answer_obj = json.loads(answer)
                    except (json.JSONDecodeError, TypeError):
                        answer_obj = None
                    if isinstance(answer_obj, dict):
                        # Merge inner answer keys up so output.<field> resolves
                        out.update(answer_obj)
                layer_results[aid] = {"output": out}
            else:
                layer_results[aid] = {"output": {"answer": ar.output}}
        return layer_results

    def _apply_branches(
        self,
        branches: list[dict[str, Any]],
        layer_results: dict[str, Any],
        layers: list[list[str]],
        current_layer_idx: int,
    ) -> tuple[list[str], list[str]]:
        """Evaluate branch conditions and apply skip/retry to remaining layers.

        Returns ``(skipped, retried)`` agent lists for logging.

        Actions:
          - ``skip``: remove target from all remaining layers.
          - ``retry``: ensure target runs again — add it to the next
            remaining layer if it is not already scheduled there.
          - ``redirect``: NOT implemented (documented as future work).

        Branch config shape: ``{condition, action, target, reason}``.
        """
        from ..goal.expression_evaluator import evaluate_condition

        skipped: list[str] = []
        retried: list[str] = []

        remaining = layers[current_layer_idx + 1:]
        if not branches or not remaining:
            return skipped, retried

        for branch in branches:
            condition = branch.get("condition", "")
            action = branch.get("action", "")
            target = branch.get("target", "")
            if not condition or not target:
                continue
            try:
                hit = evaluate_condition(condition, layer_results)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Branch condition %r failed: %s", condition, exc)
                continue
            if not hit:
                continue

            self._apply_branch_action(
                action, target, remaining,
                layer_results, skipped, retried,
            )
        return skipped, retried

    @staticmethod
    def _apply_branch_action(
        action: str,
        target: str,
        remaining: list[list[str]],
        layer_results: dict[str, Any],
        skipped: list[str],
        retried: list[str],
    ) -> None:
        """Apply one branch action to the remaining layers."""
        if action == "skip":
            for layer in remaining:
                if target in layer:
                    layer.remove(target)
                    skipped.append(target)
        elif action == "retry":
            if target in layer_results and target not in remaining[0]:
                remaining[0].append(target)
                retried.append(target)
        else:
            logger.info(
                "Branch action %r for %s not implemented (redirect future work)",
                action, target,
            )


# ── Register built-in python executors ────────────────────────────


def _register_builtin_executors() -> None:
    """Register built-in python_executor functions for backtest and decide."""
    try:
        from ..backtest import run_backtest_script

        def _backtest_executor(workspace_path, upstream=None, **kwargs):
            """Run backtest script and return metrics."""
            strategy_name = kwargs.get("strategy_name", "default")
            action = kwargs.get("action", "unknown")
            description = kwargs.get("description", "")
            result = run_backtest_script(
                workspace_path=workspace_path,
                strategy_name=strategy_name,
                action=action,
                description=description,
            )
            return result

        SwarmRuntime.register_python_executor("run_backtest_script", _backtest_executor)
    except ImportError:
        logger.debug("backtest module not available, python_executor not registered")

    try:
        from ..strategy_acceptance import decide as _decide_fn

        def _decide_executor(metrics=None, **kwargs):
            """Run decide() for keep/discard decision."""
            llm_verdict = kwargs.get("llm_verdict")
            cfg = kwargs.get("cfg")
            stagnation_count = kwargs.get("stagnation_count", 0)
            return _decide_fn(
                metrics=metrics or {},
                llm_verdict=llm_verdict,
                cfg=cfg,
                stagnation_count=stagnation_count,
            )

        SwarmRuntime.register_python_executor("decide", _decide_executor)
    except ImportError:
        logger.debug("strategy_acceptance module not available, evaluator not registered")


# Auto-register on module import
_register_builtin_executors()

