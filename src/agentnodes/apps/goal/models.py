"""Data models for finance research goals."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class GoalStatus(str, Enum):
    """Lifecycle states for a finance research goal."""

    ACTIVE = "active"
    PAUSED = "paused"
    WAITING_USER = "waiting_user"
    NEEDS_REFRESH = "needs_refresh"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    COMPLIANCE_BLOCKED = "compliance_blocked"
    BLOCKED = "blocked"
    BUDGET_LIMITED = "budget_limited"
    USAGE_LIMITED = "usage_limited"
    COMPLETE = "complete"
    CANCELLED = "cancelled"
    SUPERSEDED = "superseded"


class RiskTier(str, Enum):
    """Risk classification for goal objectives."""

    RESEARCH_GENERAL = "research_general"
    MARKET_SPECIFIC_SHORT_TERM = "market_specific_short_term"
    PERSONALIZED_ADVICE_OR_POSITION_SIZING = "personalized_advice_or_position_sizing"
    LIVE_TRADING_OR_EXECUTION = "live_trading_or_execution"


class StaleGoalError(ValueError):
    """Raised when a model turn tries to mutate a stale or replaced goal."""


@dataclass(frozen=True)
class GoalRecord:
    """Persisted finance research goal record."""

    goal_id: str
    session_id: str
    status: GoalStatus
    objective: str
    ui_summary: str
    source: str
    protocol: str
    risk_tier: RiskTier
    token_budget: int | None = None
    tokens_used: int = 0
    turn_budget: int | None = None
    turns_used: int = 0
    time_budget_seconds: int | None = None
    time_used_seconds: int = 0
    budget_wrapup_sent: bool = False
    created_at: str = ""
    updated_at: str = ""
    completed_at: str | None = None
    recap: str | None = None
    progress_percent: float = 0.0
    parent_goal_id: str | None = None
    workflow_id: str | None = None
    # Isolation D1: owning user (backfilled from session on migration).
    user_id: str | None = None


@dataclass(frozen=True)
class GoalClaim:
    """A research claim tracked by the goal ledger."""

    claim_id: str
    goal_id: str
    session_id: str
    claim_type: str
    text: str
    status: str
    created_at: str = ""
    updated_at: str = ""


@dataclass(frozen=True)
class GoalCriterion:
    """A protocol criterion that must be covered before completion."""

    criterion_id: str
    goal_id: str
    session_id: str
    text: str
    required: bool = True
    status: str = "pending"
    freshness_requirement: str | None = None
    protocol_step: str | None = None
    created_at: str = ""
    updated_at: str = ""


@dataclass(frozen=True)
class EvidenceInput:
    """Input for appending traceable evidence to a goal."""

    text: str
    criterion_id: str | None = None
    claim_id: str | None = None
    evidence_type: str = "evidence"
    tool_call_id: str | None = None
    run_id: str | None = None
    source_provider: str | None = None
    source_type: str | None = None
    source_uri: str | None = None
    symbol_universe: list[str] = field(default_factory=list)
    benchmark: list[str] = field(default_factory=list)
    timeframe: str | None = None
    method: str | None = None
    assumptions: dict[str, Any] = field(default_factory=dict)
    artifact_path: str | None = None
    artifact_hash: str | None = None
    data_as_of: str | None = None
    confidence: str | None = None
    caveat: str | None = None
    contradicts_claim_ids: list[str] = field(default_factory=list)
    hypothesis_id: str | None = None


@dataclass(frozen=True)
class EvidenceRecord:
    """Persisted evidence row linked to a goal criterion or claim."""

    evidence_id: str
    goal_id: str
    session_id: str
    text: str
    criterion_id: str | None = None
    claim_id: str | None = None
    evidence_type: str = "evidence"
    tool_call_id: str | None = None
    run_id: str | None = None
    source_provider: str | None = None
    source_type: str | None = None
    source_uri: str | None = None
    symbol_universe: list[str] = field(default_factory=list)
    benchmark: list[str] = field(default_factory=list)
    timeframe: str | None = None
    method: str | None = None
    assumptions: dict[str, Any] = field(default_factory=dict)
    artifact_path: str | None = None
    artifact_hash: str | None = None
    retrieved_at: str = ""
    data_as_of: str | None = None
    freshness_status: str = "unknown"
    verification_status: str = "unverified"
    confidence: str | None = None
    caveat: str | None = None
    contradicts_claim_ids: list[str] = field(default_factory=list)
    hypothesis_id: str | None = None
    created_at: str = ""


@dataclass(frozen=True)
class AuditRow:
    """Completion audit row for one criterion."""

    criterion_id: str
    result: str
    evidence_ids: list[str]
    notes: str = ""


@dataclass(frozen=True)
class JournalEntry:
    """AEGIS Journal entry — tracks hypothesis, levers, and outcome per round.

    Persisted in ``goal_journal`` table. Each round's evolution attempt
    is recorded here for cross-round memory, attribution, and novelty checking.
    """

    entry_id: str
    goal_id: str
    session_id: str
    round_num: int
    hypothesis_id: str
    label: str
    levers: list[str] = field(default_factory=list)
    predicted_affected: list[str] = field(default_factory=list)
    # Falsifiable claims (captured pre-backtest at the novelty gate):
    # {"sharpe": {"direction": "up", "expected": 0.8, "tolerance": 0.3}}
    predictions: dict = field(default_factory=dict)
    # Backfilled post-backtest by fill_journal_prediction_outcome:
    # {"sharpe": {"actual": 0.55, "abs_error": 0.25,
    #             "direction_correct": True, "within_tolerance": True}}
    prediction_outcome: dict = field(default_factory=dict)
    gating_outcome: str = "pending"  # accepted | reverted | noop | pending
    gating_attribution: dict = field(default_factory=dict)
    changeset: dict | None = None
    retry_rationale: str | None = None
    archived_reason: str | None = None  # novelty | regression | lever-fatigue
    created_at: str = ""
    updated_at: str = ""
