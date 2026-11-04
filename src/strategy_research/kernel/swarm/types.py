"""Core data types for the SwarmRuntime DAG execution API.

Lives next to SwarmRuntime (not in workflow/) because these types
are the native interface of SwarmRuntime.execute(): AgentCall for
describing a node, AgentStatus for its outcome, SwarmHook for
lifecycle observation.

W10-2 (2026-11-04): ``SwarmPreset`` / ``AgentResult`` / ``SwarmResult``
live here too, not in ``runtime.py``. They used to sit in the 945-line
engine implementation even though the engine is the *minority* consumer:
``AgentResult`` was imported by 5 modules outside ``swarm/`` (3 of them
``kernel/workflow/*`` at module top level) versus 1 definition site.
Contract types belong in the contract module, so the dependency edge
says "workflow depends on the swarm contract", not "workflow depends on
the swarm engine". ``runtime.py`` re-exports all three, so every existing
``from ...swarm.runtime import AgentResult`` keeps working.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol


class AgentStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    ERROR = "error"
    SKIPPED = "skipped"
    AWAITING = "awaiting"  # approval gate: execution paused, waiting for user


@dataclass(frozen=True)
class AgentCall:
    """Description of one agent node in a SwarmPreset.

    Consumed by SwarmRuntime.execute() → AgentExecutor.
    """

    agent_name: str
    prompt: str
    context: dict[str, object] = field(default_factory=dict)
    metadata: dict[str, object] = field(default_factory=dict)


class SwarmHook(Protocol):
    """Hook point during SwarmRuntime.execute().

    Implement any subset of callbacks.  SwarmRuntime calls them at
    the corresponding execution points.  Callbacks must not raise;
    exceptions are logged and swallowed.
    """

    @property
    def name(self) -> str:
        """Human-readable hook name for logging."""
        ...

    def on_layer_start(
        self, layer_idx: int, agents: list[str], context: dict[str, Any],
    ) -> None:
        """Called before a new DAG layer begins execution."""
        ...

    def on_layer_complete(
        self,
        layer_idx: int,
        agents: list[str],
        results: dict[str, Any],
    ) -> None:
        """Called after all agents in a layer have finished."""
        ...

    def on_agent_complete(
        self,
        agent_id: str,
        result: Any,
        context: dict[str, Any],
    ) -> None:
        """Called after a single agent finishes (success or error)."""
        ...

    def should_stop(self) -> bool:
        """Return True to terminate the DAG early (e.g. goal complete)."""
        return False


# ── Execution payload contracts ──────────────────────────────────────


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
    # R-A: 同一 agent 因 branches[].action=retry 被重跑的次数上限。
    # 1 = 每个 agent 最多重跑一次，防「条件恒真 → 无限重跑」。
    branch_max_retries: int = 1


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


__all__ = [
    "AgentCall",
    "AgentResult",
    "AgentStatus",
    "SwarmHook",
    "SwarmPreset",
    "SwarmResult",
]
