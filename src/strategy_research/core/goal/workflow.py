"""Goal Workflow Engine — DAG-based research workflow for goals.

P3.9 refactor: GoalWorkflowRunner now fully delegates to SwarmRuntime
+ GoalWorkflowHook.  The runner becomes a thin orchestrator that:
  1. Creates a goal in GoalStore
  2. Converts GoalWorkflowConfig → SwarmPreset
  3. Creates GoalWorkflowHook for evidence collection + auto-complete
  4. Delegates DAG execution to SwarmRuntime.execute()
  5. Syncs state back from the hook after execution
"""
from __future__ import annotations

import asyncio
import logging
import time
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..swarm.runtime import AgentStatus  # P1.8: for resume_and_continue

logger = logging.getLogger(__name__)


# ── Configuration Models ─────────────────────────────────────


@dataclass
class GoalWorkflowGoalConfig:
    """Goal-specific configuration for the workflow."""

    default_criteria: list[str] = field(default_factory=list)
    risk_tier: str = "research_general"


@dataclass
class GoalAgentConfig:
    """Configuration for a single agent node in the workflow."""

    id: str
    prompt_file: str
    tools: list[str] = field(default_factory=list)
    input_from: list[str] = field(default_factory=list)
    evidence_criterion: int = 0
    timeout: int = 120
    max_retries: int = 3
    condition: str | None = None
    # Phase: support python_executor and evaluator types
    executor_type: str = "llm"  # llm | python_executor | evaluator
    python_function: str | None = None


@dataclass
class CompletionConfig:
    """How the workflow completes the goal."""

    mode: str = "auto"  # auto | manual | lite
    auto_audit: bool = True
    require_all_evidence: bool = True
    # Metric targets (Phase 1.3: migrated from Study)
    metric_targets: list[dict] | None = None  # [{"name": "calmar", "op": ">=", "value": 0.5}]
    # Monitor config (Phase 1.5: migrated from Study)
    monitor_interval_seconds: int | None = None


@dataclass
class BranchConfig:
    """Conditional branch configuration."""

    condition: str
    action: str  # skip | retry | redirect
    target: str
    reason: str = ""


@dataclass
class GoalWorkflowConfig:
    """Complete workflow configuration loaded from YAML."""

    name: str
    description: str
    version: str = "1.0"
    goal: GoalWorkflowGoalConfig = field(default_factory=GoalWorkflowGoalConfig)
    agents: list[GoalAgentConfig] = field(default_factory=list)
    dag: dict[str, list[str]] = field(default_factory=dict)
    completion: CompletionConfig = field(default_factory=CompletionConfig)
    branches: list[BranchConfig] = field(default_factory=list)
    # Budget fields (Phase 1.2: migrated from Study)
    budget_token: int | None = None
    budget_turn: int | None = None
    budget_time_seconds: float | None = None

    def to_swarm_preset(self) -> Any:
        """Convert to a SwarmPreset for use with SwarmRuntime (P3.3)."""
        from ..swarm.runtime import SwarmPreset
        from ..workflow.types import AgentCall

        agent_calls = []
        for agent in self.agents:
            agent_calls.append(AgentCall(
                agent_name=agent.id,
                prompt=agent.prompt_file,
                context={
                    "tools": agent.tools,
                    "input_from": agent.input_from,
                    "evidence_criterion": agent.evidence_criterion,
                    "timeout": agent.timeout,
                    "max_retries": agent.max_retries,
                    "executor_type": agent.executor_type,
                    "python_function": agent.python_function,
                },
            ))

        return SwarmPreset(
            name=self.name,
            description=self.description,
            agents=agent_calls,
            dag=self.dag,
            goal={
                "default_criteria": self.goal.default_criteria,
                "risk_tier": self.goal.risk_tier,
            },
            completion={
                "mode": self.completion.mode,
                "auto_audit": self.completion.auto_audit,
                "require_all_evidence": self.completion.require_all_evidence,
                "metric_targets": self.completion.metric_targets,
                "monitor_interval_seconds": self.completion.monitor_interval_seconds,
            },
            branches=[
                {"condition": b.condition, "action": b.action,
                 "target": b.target, "reason": b.reason}
                for b in self.branches
            ],
            budget_token=self.budget_token,
            budget_turn=self.budget_turn,
            budget_time_seconds=self.budget_time_seconds,
            version=self.version,
        )


# ── Workflow State ───────────────────────────────────────────


@dataclass
class GoalWorkflowState:
    """Tracks the execution state of a running workflow."""

    status: str = "idle"  # idle | running | paused | completed | error | cancelled
    current_layer: int = 0
    paused: bool = False
    cancelled: bool = False  # P3.4: immediate cancel flag
    pause_layer: int = -1
    agent_statuses: dict[str, str] = field(default_factory=dict)
    agent_errors: dict[str, str] = field(default_factory=dict)
    evidence_count: int = 0
    start_time: float = 0.0
    error_message: str = ""

    def set_agent_status(self, agent_id: str, status: str, error: str = "") -> None:
        """Update an agent's execution status."""
        self.agent_statuses[agent_id] = status
        if error:
            self.agent_errors[agent_id] = error

    def get_summary(self) -> dict[str, Any]:
        """Return a JSON-serializable summary of the state."""
        return {
            "status": self.status,
            "current_layer": self.current_layer,
            "paused": self.paused,
            "agent_statuses": dict(self.agent_statuses),
            "evidence_count": self.evidence_count,
            "error_message": self.error_message,
        }


# ── Evidence Collector (legacy, used by decorator chain) ─────


class GoalEvidenceCollector:
    """Collects agent outputs and appends them as goal evidence."""

    def __init__(self, store: Any, session_id: str, goal_id: str) -> None:
        self._store = store
        self._session_id = session_id
        self._goal_id = goal_id

    def collect(self, agent_id: str, result: dict[str, Any], criterion_idx: int) -> int:
        """Append agent output as evidence. Returns number added (0/1)."""
        from .models import EvidenceInput

        answer = result.get("answer", "")
        if not answer or len(answer.strip()) < 10:
            return 0

        snapshot = self._store.get_current_snapshot(self._session_id)
        if snapshot is None:
            return 0

        criteria = snapshot.get("criteria", [])
        if criterion_idx < 0 or criterion_idx >= len(criteria):
            return 0

        criterion_id = criteria[criterion_idx].get("criterion_id")
        if not criterion_id:
            return 0

        try:
            self._store.append_evidence(
                session_id=self._session_id,
                goal_id=self._goal_id,
                expected_goal_id=self._goal_id,
                evidence=EvidenceInput(
                    criterion_id=criterion_id,
                    text=answer[:2000],
                    source_provider="workflow",
                    source_type=agent_id,
                ),
            )
            return 1
        except Exception as exc:
            logger.warning("Failed to collect evidence from %s: %s", agent_id, exc)
            return 0


# ── Workflow Runner (P3.9: delegates to SwarmRuntime) ────────


class GoalWorkflowRunner:
    """DAG-based workflow executor for research goals.

    P3.9: Fully delegates to SwarmRuntime + GoalWorkflowHook.
    The runner is a thin orchestrator that:
      1. Creates a goal in GoalStore
      2. Converts GoalWorkflowConfig → SwarmPreset
      3. Creates GoalWorkflowHook for evidence collection
      4. Delegates DAG execution to SwarmRuntime.execute()
      5. Syncs state back after execution
    """

    def __init__(
        self,
        config: GoalWorkflowConfig,
        session_id: str,
        *,
        agent_runner: Any = None,
        agent_runner_type: str = "stub",
        store: Any = None,
        runner_kwargs: dict[str, Any] | None = None,
        use_validators: bool = True,
        workspace: Path | None = None,
        session_service: Any = None,
    ) -> None:
        """Initialize the runner.

        Args:
            config: Workflow configuration loaded from YAML.
            session_id: Current session id.
            agent_runner: [deprecated] Pre-built AgentRunner. Use AgentRunnerRegistry instead.
            agent_runner_type: [deprecated] Type for SwarmRuntime controller selection.
            store: GoalStore instance.
            runner_kwargs: [deprecated] Pass-through kwargs for controller.
            use_validators: Whether to register validators.
            workspace: Workspace path for prompt file resolution.
            session_service: SessionService for cooperative mutex with chat.
        """
        # P1.6: Deprecation warnings for dead parameters (v0.5.3)
        if agent_runner is not None:
            warnings.warn(
                "agent_runner is deprecated in v0.5.3 and will be removed in v0.6.0. "
                "Use AgentRunnerRegistry.register() instead.",
                DeprecationWarning, stacklevel=2,
            )
        if agent_runner_type != "stub":
            warnings.warn(
                "agent_runner_type is deprecated in v0.5.3 and will be removed in v0.6.0. "
                "The runner now delegates to SwarmRuntime + GoalWorkflowHook.",
                DeprecationWarning, stacklevel=2,
            )
        if runner_kwargs:
            warnings.warn(
                "runner_kwargs is deprecated in v0.5.3 and will be removed in v0.6.0. "
                "Configure agents via YAML or AgentRunnerRegistry.",
                DeprecationWarning, stacklevel=2,
            )

        self._config = config
        self._session_id = session_id
        self._store = store
        self._workspace = workspace or Path.cwd()
        self._event_bus = WorkflowEventBus()
        self._agent_runner_type = agent_runner_type
        self._runner_kwargs = runner_kwargs or {}
        self._session_service = session_service
        # Phase 1.4: directive store for mid-execution user commands
        self._directives: list[dict] = []
        # Phase 1.5: background monitor task
        self._monitor_task: asyncio.Task | None = None

        self._state = GoalWorkflowState()
        self._goal_id: str = ""
        self._hook: Any = None  # GoalWorkflowHook after start()

    # ── Public API ────────────────────────────────────────────

    @property
    def state(self) -> GoalWorkflowState:
        return self._state

    @property
    def goal_id(self) -> str:
        return self._goal_id

    def set_goal_id(self, goal_id: str) -> None:
        """Set the goal_id to reuse an existing goal (skip goal creation in start)."""
        self._goal_id = goal_id

    @property
    def event_bus(self) -> "WorkflowEventBus":
        return self._event_bus

    def _agent_event_bridge(self):
        """agent 级事件 → WorkflowEventBus 的适配回调。

        SwarmRuntime 的 agent 事件（llm_request / iter_start / tool_call /
        …，已经 runtime 侧按 SUMMARY_EVENT_TYPES 白名单过滤并注入 agent
        字段）转发到既有观察者总线，与 agent_start / agent_complete 这些
        层级事件并存——观察者按类型自取。异常隔离照搬 bus.emit 的约定：
        一个坏观察者不能拖垮工作流（bus 内部已兜，这里不再包）。

        此前 goal/swarm 的 agent 执行轨迹完全不可见：executor 本就支持
        on_event（executor.py 已传给 AgentLoop），但 runtime 调用时没传
        （2026-11-02 接线，docs/study-goal-agent-events-design-20261102.md）。
        """
        from ..observability import strip_llm_request_fields

        def on_event(event_type: str, data: dict) -> None:
            self._event_bus.emit(
                event_type, **strip_llm_request_fields(event_type, dict(data)),
            )

        return on_event


    def subscribe(self, observer: Any) -> None:
        self._event_bus.subscribe(observer)

    def unsubscribe(self, observer: Any) -> None:
        self._event_bus.unsubscribe(observer)

    # ── Directive API (Phase 1.4) ──────────────────────────────

    def add_directive(self, content: str) -> None:
        """Add a user directive to be injected into the next layer's agents."""
        from datetime import datetime, timezone
        self._directives.append({
            "content": content,
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
        logger.info("Directive added to workflow %s: %s", self._goal_id, content[:50])

    def consume_directives(self) -> str | None:
        """Consume all pending directives and return them as a single string.

        Returns None if no directives are pending.
        """
        if not self._directives:
            return None
        text = "\n".join(d["content"] for d in self._directives)
        self._directives.clear()
        logger.info("Directives consumed for workflow %s", self._goal_id)
        return text

    # ── Monitor mode (Phase 1.5) ──────────────────────────────

    def launch_monitor(self) -> None:
        """Launch background monitor task if monitor_interval is configured."""
        interval = self._config.completion.monitor_interval_seconds
        if interval and interval > 0:
            self._monitor_task = asyncio.create_task(self._monitor_background())
            logger.info("Monitor launched for workflow %s (interval=%ds)",
                        self._goal_id, interval)

    async def _monitor_background(self) -> None:
        """Periodic re-backtest after goal completion to detect drift."""
        interval = self._config.completion.monitor_interval_seconds or 60
        metric_targets = self._config.completion.metric_targets
        if not metric_targets:
            return

        strategy_name = None
        for agent in self._config.agents:
            if "backtest" in agent.id.lower():
                strategy_name = agent.id
                break

        while True:
            await asyncio.sleep(interval)
            try:
                from ...core.study.runner import meets_metric_targets
                metrics = await asyncio.to_thread(
                    self._run_monitor_check, strategy_name,
                )
                if metrics and not meets_metric_targets(metrics, metric_targets):
                    logger.info("Monitor drift detected for workflow %s", self._goal_id)
                    self._state.status = "needs_refresh"
                    self._event_bus.emit(
                        "workflow_drift_detected",
                        goal_id=self._goal_id,
                        metrics=metrics,
                    )
                    return
            except asyncio.CancelledError:
                return
            except Exception as exc:
                logger.warning("Monitor check failed for %s: %s", self._goal_id, exc)

    def _run_monitor_check(self, strategy_name: str | None = None) -> dict[str, float] | None:
        """Run a single monitor check: re-backtest and return metrics."""
        # Simplified: try to import and run backtest if available
        try:

            from strategy_research.apps.backtest import run_backtest_script
            result = run_backtest_script(
                workspace_path=self._workspace,
                strategy_name=strategy_name or "default",
                action="monitor",
                description="workflow monitor check",
            )
            if result.get("success"):
                return result.get("metrics", {})
        except Exception as exc:
            logger.debug("Monitor backtest failed: %s", exc)
        return None

    def get_progress(self) -> dict[str, Any]:
        """Return current workflow progress for UI display."""
        total_agents = len(self._config.agents)
        hook = self._hook
        evidence_count = hook.evidence_count if hook else 0
        completed = hook.completed if hook else False

        agent_statuses = dict(self._state.agent_statuses)
        completed_count = sum(
            1 for s in agent_statuses.values() if s in ("success", "skipped")
        )

        total_layers = 0
        try:
            from ..workflow.dag import topological_layers
            dag = {a.id: [d for d in self._config.dag.get(a.id, [])]
                   for a in self._config.agents}
            total_layers = len(topological_layers(dag))
        except Exception:  # noqa: BLE001
            total_layers = 0

        return {
            "goal_id": self._goal_id,
            "status": self._state.status,
            "current_layer": self._state.current_layer,
            "total_layers": total_layers,
            "agents_completed": completed_count,
            "agents_total": total_agents,
            "evidence_count": evidence_count,
            "paused": self._state.paused,
            "agent_statuses": agent_statuses,
            "hook_completed": completed,
        }

    # ── Lifecycle (P3.9: delegates to SwarmRuntime) ───────────

    async def start(self, objective: str) -> str:
        """Start the workflow: create goal → delegate to SwarmRuntime → return goal_id."""
        if self._store is None:
            from .store import GoalStore
            self._store = GoalStore()


        self._state.status = "running"
        self._state.start_time = time.time()
        self._event_bus.emit("workflow_start", workflow=self._config.name)

        # 0. Session mutex: wait for chat to be idle, then claim slot
        if self._session_service is not None:
            while self._session_service.is_session_processing(self._session_id):
                await asyncio.sleep(0.25)
            self._session_service.mark_session_processing(self._session_id, True)

        try:
            return await self._start_inner(objective)
        except Exception as exc:
            self._state.status = "error"
            self._state.error_message = str(exc)
            self._event_bus.emit("workflow_failed", error=str(exc))
            logger.error("Goal workflow failed: %s", exc)
            return self._goal_id
        finally:
            # 0b. Release session slot
            if self._session_service is not None:
                self._session_service.mark_session_processing(self._session_id, False)

    async def _start_inner(self, objective: str) -> str:
        """Inner start logic (session slot already claimed)."""
        from .context import default_goal_criteria
        from .models import RiskTier

        # 1. Create the goal (or reuse existing if goal_id already set)
        if self._goal_id:
            # Reuse existing goal — skip replace_goal
            goal = self._store.get_current_goal(self._session_id)
        else:
            criteria = self._config.goal.default_criteria or default_goal_criteria()
            risk_tier = RiskTier(self._config.goal.risk_tier)
            goal = self._store.replace_goal(
                session_id=self._session_id,
                objective=objective,
                criteria=criteria,
                source="workflow",
                protocol=self._config.name,
                risk_tier=risk_tier,
                workflow_id=self._config.name,
            )
            self._goal_id = goal.goal_id

        # 2. Build evidence_map from agent configs
        evidence_map: dict[str, int] = {
            agent.id: agent.evidence_criterion
            for agent in self._config.agents
        }

        # 3. Create GoalWorkflowHook (P1.2: pass runner for cancelled check)
        from .completion_strategy import CompletionStrategyFactory
        from .workflow_hook import GoalWorkflowHook

        self._hook = GoalWorkflowHook(
            session_id=self._session_id,
            goal_id=self._goal_id,
            evidence_map=evidence_map,
            store=self._store,
            runner=self,  # P1.2: hook checks runner._state.cancelled
            completion_strategy=CompletionStrategyFactory.get(
                self._config.completion.mode,
            ),
            completion_mode=self._config.completion.mode,
            workflow_name=self._config.name,
            event_bus=self._event_bus,
            metric_targets=self._config.completion.metric_targets,
        )

        # 4. Convert config → SwarmPreset
        preset = self._config.to_swarm_preset()

        # 5. Build SwarmRuntime
        from ..swarm.runtime import SwarmRuntime
        runtime = SwarmRuntime(on_event=self._agent_event_bridge())

        # 6. Execute via SwarmRuntime (in thread since it's sync)
        try:
            result = await asyncio.to_thread(
                runtime.execute,
                preset,
                self._workspace,
                objective,
                [self._hook],
            )

            # 7. Sync state back from hook
            self._state.evidence_count = self._hook.evidence_count
            if self._hook.completed:
                self._state.status = "completed"
            elif result.success:
                self._state.status = "completed"
            else:
                self._state.status = "error"
                self._state.error_message = "One or more agents failed"

            # Phase 1.5: Launch monitor if configured
            if self._state.status == "completed":
                self.launch_monitor()

            logger.info(
                "Goal workflow finished: goal_id=%s status=%s evidence=%d",
                self._goal_id, self._state.status, self._hook.evidence_count,
            )

        except Exception as exc:
            self._state.status = "error"
            self._state.error_message = str(exc)
            self._event_bus.emit("workflow_failed", error=str(exc))
            logger.error("Goal workflow failed: %s", exc)

        return self._goal_id

    async def resume_and_continue(self) -> str:
        """Resume a previously checkpointed workflow from where it stopped.

        Loads the latest checkpoint for ``self._goal_id`` (or the
        ``goal_id`` passed via ``start(goal_id=...)`` — not currently
        supported), seeds ``SwarmRuntime`` with the saved
        ``layer_results`` as ``pre_completed``, and continues execution
        from the next layer. The existing goal row is reused (no new
        ``replace_goal`` call), so existing evidence and progress are
        preserved.

        Returns:
            The goal_id on which the workflow is continuing.

        Raises:
            FileNotFoundError: No checkpoint exists for this goal.
        """
        from ..swarm.runtime import AgentResult, SwarmRuntime
        from .checkpoint_store import CheckpointStore

        cps = CheckpointStore()
        cp_data = cps.load(self._session_id, self._goal_id)
        if cp_data is None:
            raise FileNotFoundError(
                f"No checkpoint for session={self._session_id} goal={self._goal_id}"
            )

        layer_results_raw = cp_data.get("layer_results", {})
        # Reconstruct AgentResult instances so the runtime + branches
        # can reuse the captured outputs verbatim.
        pre_completed: dict[str, AgentResult] = {}
        for aid, payload in layer_results_raw.items():
            if not isinstance(payload, dict):
                continue
            out = payload.get("output", payload)
            # Accept either {"output": "..."} (already extracted by the
            # hook) or a raw worker JSON payload — normalize to a string.
            if isinstance(out, dict):
                import json as _json
                out_str = _json.dumps(out, ensure_ascii=False, default=str)
            else:
                out_str = str(out)
            pre_completed[aid] = AgentResult(
                agent_id=aid,
                status=AgentStatus.SUCCESS,
                output=out_str,
            )

        # Determine start_layer from saved state
        state_data = cp_data.get("state", {})
        # current_layer in state is 1-based (hook sets it to layer_idx+1).
        # Use it directly to start from the next layer index.
        prev_layer_1based = int(state_data.get("current_layer", 0) or 0)
        start_layer = prev_layer_1based  # next layer to execute

        # Rebuild the evidence-map and hook (same as start, but no
        # replace_goal). The hook's _layer_results are pre-seeded so
        # subsequent evidence_collection doesn't double-count.
        from .completion_strategy import CompletionStrategyFactory
        from .workflow_hook import GoalWorkflowHook

        evidence_map: dict[str, int] = {
            agent.id: agent.evidence_criterion
            for agent in self._config.agents
        }

        self._hook = GoalWorkflowHook(
            session_id=self._session_id,
            goal_id=self._goal_id,
            evidence_map=evidence_map,
            store=self._store,
            runner=self,
            completion_strategy=CompletionStrategyFactory.get(
                self._config.completion.mode,
            ),
            completion_mode=self._config.completion.mode,
            workflow_name=self._config.name,
            event_bus=self._event_bus,
            metric_targets=self._config.completion.metric_targets,
        )
        # Seed the hook's _layer_results so on_layer_complete doesn't
        # re-parse already-saved outputs.
        self._hook._layer_results = layer_results_raw

        # Restore snapshot state on the runner
        self._state.status = "running"
        self._state.current_layer = prev_layer_1based
        self._state.evidence_count = int(state_data.get("evidence_count", 0) or 0)
        self._state.agent_statuses = dict(state_data.get("agent_statuses", {}))

        self._event_bus.emit("workflow_resumed", goal_id=self._goal_id)

        # Convert config → SwarmPreset (same as start)
        preset = self._config.to_swarm_preset()
        runtime = SwarmRuntime(on_event=self._agent_event_bridge())

        try:
            # Need the original objective for prompts; reload from goal
            if self._store is None:
                from .store import GoalStore
                self._store = GoalStore()
            goal = self._store.get_goal(self._goal_id)
            objective = goal.objective if goal else ""

            result = await asyncio.to_thread(
                runtime.execute,
                preset,
                self._workspace,
                objective,
                [self._hook],
                pre_completed,
                start_layer,
            )

            self._state.evidence_count = self._hook.evidence_count
            if self._hook.completed:
                self._state.status = "completed"
            elif result.success:
                self._state.status = "completed"
            else:
                self._state.status = "error"
                self._state.error_message = "One or more agents failed after resume"

            logger.info(
                "Goal workflow resumed+finished: goal_id=%s status=%s evidence=%d",
                self._goal_id, self._state.status, self._hook.evidence_count,
            )
        except Exception as exc:
            self._state.status = "error"
            self._state.error_message = str(exc)
            self._event_bus.emit("workflow_failed", error=str(exc))
            logger.error("Goal workflow resume failed: %s", exc)

        return self._goal_id

    async def start_sub_workflow(
        self,
        objective: str,
        parent_goal_id: str,
    ) -> str:
        """Start a child workflow linked to a parent goal (P3.5).

        Creates a new goal with ``parent_goal_id`` set, then executes
        the DAG.  The child goal is independent — it has its own
        criteria, evidence, and completion lifecycle.

        Args:
            objective: Research objective for the child goal.
            parent_goal_id: The parent goal to link to.

        Returns:
            The child goal id.
        """
        if self._store is None:
            from .store import GoalStore
            self._store = GoalStore()

        from .context import default_goal_criteria
        from .models import RiskTier

        self._state.status = "running"
        self._state.start_time = time.time()
        self._event_bus.emit(
            "sub_workflow_start",
            parent_goal_id=parent_goal_id,
            workflow=self._config.name,
        )

        criteria = self._config.goal.default_criteria or default_goal_criteria()
        risk_tier = RiskTier(self._config.goal.risk_tier)

        goal = self._store.replace_goal(
            session_id=self._session_id,
            objective=objective,
            criteria=criteria,
            source="workflow",
            protocol=self._config.name,
            risk_tier=risk_tier,
            parent_goal_id=parent_goal_id,
        )
        self._goal_id = goal.goal_id

        evidence_map = {a.id: a.evidence_criterion for a in self._config.agents}

        from .completion_strategy import CompletionStrategyFactory
        from .workflow_hook import GoalWorkflowHook

        self._hook = GoalWorkflowHook(
            session_id=self._session_id,
            goal_id=self._goal_id,
            evidence_map=evidence_map,
            store=self._store,
            runner=self,
            completion_strategy=CompletionStrategyFactory.get(
                self._config.completion.mode,
            ),
            completion_mode=self._config.completion.mode,
            workflow_name=self._config.name,
            event_bus=self._event_bus,
            metric_targets=self._config.completion.metric_targets,
        )

        preset = self._config.to_swarm_preset()
        from ..swarm.runtime import SwarmRuntime
        runtime = SwarmRuntime(on_event=self._agent_event_bridge())

        try:
            result = await asyncio.to_thread(
                runtime.execute,
                preset,
                self._workspace,
                objective,
                [self._hook],
            )
            self._state.evidence_count = self._hook.evidence_count
            self._state.status = "completed" if (
                self._hook.completed or result.success
            ) else "error"

            self._event_bus.emit(
                "sub_workflow_complete",
                parent_goal_id=parent_goal_id,
                child_goal_id=self._goal_id,
            )
            logger.info(
                "Sub-workflow finished: parent=%s child=%s status=%s",
                parent_goal_id, self._goal_id, self._state.status,
            )
        except Exception as exc:
            self._state.status = "error"
            self._state.error_message = str(exc)
            self._event_bus.emit("workflow_failed", error=str(exc))
            logger.error("Sub-workflow failed: %s", exc)

        return self._goal_id

    def pause(self, *, immediate: bool = False) -> None:
        """Pause workflow execution.

        Args:
            immediate: If True, set cancelled flag (SwarmRuntime will
                       stop after current layer via should_stop).
        """
        self._state.paused = True
        if immediate:
            self._state.cancelled = True
            self._event_bus.emit(
                "workflow_cancelled", layer=self._state.current_layer,
            )
            logger.info("Goal workflow cancelled immediately")
        else:
            self._event_bus.emit(
                "workflow_paused", layer=self._state.current_layer,
            )
            logger.info("Goal workflow paused gracefully")

    def resume(self) -> None:
        """Resume workflow execution after pause."""
        self._state.paused = False
        self._state.cancelled = False
        self._event_bus.emit("workflow_resumed")
        logger.info("Goal workflow resumed")

    # ── Checkpoint (P3.6) ─────────────────────────────────────

    def checkpoint(self) -> Path | None:
        """Save current workflow state to disk for crash recovery.

        Returns the checkpoint directory path, or None if no store.

        Phase 4 P1.3: Now saves real layer_results from the hook,
        not an empty dict. This enables ``resume_from_checkpoint``
        to restore agent outputs for skip logic.
        """
        from .checkpoint_store import CheckpointStore as _CPS

        hook = self._hook
        layer_results: dict[str, Any] = {}
        if hook is not None:
            raw = getattr(hook, "_layer_results", None)
            if isinstance(raw, dict):
                layer_results = raw
            elif raw is not None:
                # Fallback: try evidence_count + completed info
                layer_results = {
                    "_summary": {
                        "evidence_count": getattr(hook, "evidence_count", 0),
                        "completed": getattr(hook, "completed", False),
                    }
                }

        cp = _CPS()
        return cp.save(
            session_id=self._session_id,
            goal_id=self._goal_id,
            state=self._state.get_summary(),
            layer_results=layer_results,
            workflow_name=self._config.name,
        )

    @classmethod
    def resume_from_checkpoint(
        cls,
        session_id: str,
        goal_id: str,
        config: GoalWorkflowConfig,
        store: Any = None,
        workspace: Path | None = None,
    ) -> "GoalWorkflowRunner | None":
        """Restore a runner from a saved checkpoint (P3.6).

        Args:
            session_id: Session id from the checkpoint.
            goal_id: Goal id from the checkpoint.
            config: Workflow config (must match the original).
            store: GoalStore instance.
            workspace: Workspace path.

        Returns:
            A GoalWorkflowRunner with restored state, or None if
            checkpoint not found.
        """
        from .checkpoint_store import CheckpointStore as _CPS
        cp = _CPS()
        data = cp.load(session_id, goal_id)
        if data is None:
            return None

        runner = cls(
            config=config,
            session_id=session_id,
            store=store,
            workspace=workspace,
        )
        # Restore state
        state_data = data["state"]
        runner._state.status = state_data.get("status", "idle")
        runner._state.current_layer = state_data.get("current_layer", 0)
        runner._state.evidence_count = state_data.get("evidence_count", 0)
        runner._state.agent_statuses = state_data.get("agent_statuses", {})
        runner._goal_id = goal_id

        # P1.3: Restore layer_results into hook (create hook if needed)
        layer_results = data.get("layer_results", {})
        if layer_results:
            if runner._hook is None:
                from .workflow_hook import GoalWorkflowHook
                runner._hook = GoalWorkflowHook.__new__(GoalWorkflowHook)
                runner._hook._layer_results = {}
                runner._hook._completed = False
                runner._hook._evidence_count = 0
                runner._hook._session_id = session_id
                runner._hook._goal_id = goal_id
                runner._hook._evidence_map = {}
                runner._hook._store = None
                runner._hook._runner = runner
                runner._hook._run_store = None
                runner._hook._run_id = ""
                runner._hook._completion_strategy = None
                runner._hook._completion_mode = ""
                runner._hook._workflow_name = config.name
                runner._hook._event_bus = runner._event_bus
            runner._hook._layer_results = layer_results

        logger.info(
            "Resumed workflow from checkpoint: goal_id=%s layer=%d evidence=%d",
            goal_id, runner._state.current_layer, runner._state.evidence_count,
        )
        return runner


# Re-exports for convenience
from .event_bus import WorkflowEventBus  # noqa: E402

__all__ = [
    "GoalWorkflowConfig",
    "GoalWorkflowGoalConfig",
    "GoalAgentConfig",
    "CompletionConfig",
    "BranchConfig",
    "GoalWorkflowState",
    "GoalEvidenceCollector",
    "GoalWorkflowRunner",
    "WorkflowEventBus",
]
