"""Study task system — autoresearch 模式的服务化核心任务.

A study pairs a goal ledger (objective / criteria / evidence, owned by
``GoalStore``) with the execution state for an autoresearch loop driving
a ``workspace_path`` + ``strategy_name``. See ``docs/study-longhorizon-plan.md``.

Public API:
    StudyStore        — persistence (``studies`` table, shares goals.db)
    StudyRecord       — frozen dataclass for a study execution row
    StudyStatus       — lifecycle enum (queued / running / paused / ...)
    MetricTarget      — quantitative acceptance target (name/op/value)
    default_metric_targets — mirror ``AcceptanceConfig`` defaults
    ACTIVE_EXECUTION_STATUSES — statuses that count as "in flight"
"""

from __future__ import annotations

from .models import (
    ACTIVE_EXECUTION_STATUSES,
    MetricTarget,
    StudyDirective,
    StudyRecord,
    StudyRoundRecord,
    StudyStatus,
    default_metric_targets,
)
from .runner import (
    AutoresearchRunner,
    ControlToken,
    EventEmitter,
    NullEmitter,
    ShutdownReason,
    acceptance_config_from_targets,
    meets_metric_targets,
)
from .scheduler import StudyScheduler, make_event_bus_emitter
from .store import StudyStore
from .ticket import (
    REPORT_STATUSES,
    Assignment,
    AssignmentStatus,
    StaleTicketError,
    Ticket,
    TicketStatus,
)

# Backward-compat: AutoresearchExecutor points to the runner
AutoresearchExecutor = AutoresearchRunner

__all__ = [
    "ACTIVE_EXECUTION_STATUSES",
    "Assignment",
    "AssignmentStatus",
    "AutoresearchExecutor",
    "AutoresearchRunner",
    "ControlToken",
    "EventEmitter",
    "MetricTarget",
    "NullEmitter",
    "REPORT_STATUSES",
    "ShutdownReason",
    "StaleTicketError",
    "StudyDirective",
    "StudyRecord",
    "StudyRoundRecord",
    "StudyScheduler",
    "StudyStatus",
    "StudyStore",
    "Ticket",
    "TicketStatus",
    "acceptance_config_from_targets",
    "default_metric_targets",
    "make_event_bus_emitter",
    "meets_metric_targets",
]
