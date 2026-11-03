"""Finance research goal subsystem (P3-a).

This package implements a research-only goal ledger for tracking finance
research objectives, claims, criteria, and evidence. It does NOT support
live trading or order execution (see policy.py).
"""

from __future__ import annotations

from .completion_strategy import (
    AutoCompleteStrategy,
    CompletionStrategy,
    CompletionStrategyFactory,
    LiteCompleteStrategy,
    ManualCompleteStrategy,
)
from .context import (
    CONTINUABLE_GOAL_STATUSES,
    OPEN_CRITERION_STATUSES,
    criterion_is_covered,
    default_goal_criteria,
    format_goal_context,
    format_goal_continuation_prompt,
    get_current_goal_context,
    goal_needs_continuation,
    goal_progress_tuple,
)
from .event_bus import (
    CollectingObserver,
    GoalPanelObserver,
    LoggerObserver,
    WorkflowEventBus,
    WorkflowEventObserver,
)
from .models import (
    AuditRow,
    EvidenceInput,
    EvidenceRecord,
    GoalClaim,
    GoalCriterion,
    GoalRecord,
    GoalStatus,
    JournalEntry,
    RiskTier,
    StaleGoalError,
)
from .policy import normalize_required_text, reject_live_execution_objective
from .store import GoalStore
from .validator_registry import (
    ValidatorRegistry,
    register_default_validators,
)
from .workflow import (
    CompletionConfig,
    GoalAgentConfig,
    GoalWorkflowConfig,
    GoalWorkflowGoalConfig,
    GoalWorkflowRunner,
    GoalWorkflowState,
)
from .workflow_config import list_goal_workflows, load_goal_workflow

__all__ = [
    "AuditRow",
    "AutoCompleteStrategy",
    "CollectingObserver",
    "CompletionConfig",
    "CompletionStrategy",
    "CompletionStrategyFactory",
    "CONTINUABLE_GOAL_STATUSES",
    "EvidenceInput",
    "EvidenceRecord",
    "GoalAgentConfig",
    "GoalClaim",
    "GoalCriterion",
    "GoalPanelObserver",
    "GoalRecord",
    "GoalStatus",
    "GoalStore",
    "GoalWorkflowConfig",
    "GoalWorkflowGoalConfig",
    "GoalWorkflowRunner",
    "GoalWorkflowState",
    "JournalEntry",
    "LiteCompleteStrategy",
    "LoggerObserver",
    "ManualCompleteStrategy",
    "OPEN_CRITERION_STATUSES",
    "RiskTier",
    "StaleGoalError",
    "ValidatorRegistry",
    "WorkflowEventBus",
    "WorkflowEventObserver",
    "criterion_is_covered",
    "default_goal_criteria",
    "format_goal_context",
    "format_goal_continuation_prompt",
    "get_current_goal_context",
    "goal_needs_continuation",
    "goal_progress_tuple",
    "load_goal_workflow",
    "list_goal_workflows",
    "normalize_required_text",
    "register_default_validators",
    "reject_live_execution_objective",
]
