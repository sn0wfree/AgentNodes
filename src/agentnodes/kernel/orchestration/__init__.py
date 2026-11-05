"""orchestration —— E1 统一入口：契约 + mailbox。

公开面刻意最小：WorkRequest/Receipt 是唯一对外契约；
Mailbox 是排队域；EntryPoint 是 per-session 分桶门面（快同步）。

消费者接线（后续刀号）：A2 SessionController 消费 poll_due；
A4 启用 steer 真注入；E5 启用持久化幂等。E1 阶段由测试驱动。
"""
from __future__ import annotations

from .contracts import (
    DEFAULT_QUEUE_MODE,
    BudgetSpec,
    ExecutionSpec,
    QueueMode,
    Receipt,
    RejectReason,
    SourceKind,
    WorkKind,
    WorkRequest,
    validate_work_request,
)
from .mailbox import DEFAULT_CAP, Mailbox, QueueItem
from .entry_point import EntryPoint
from .config_hub import ConfigError, GatewayConfig, MailboxConfig, PlatformConfig, config_fingerprint, load_platform_config
from .budget_gate import BudgetDecision, BudgetGate, BudgetUsage, DEFAULT_SESSION_BUDGET, effective_budget
from .branch_contracts import (
    BranchContextLike,
    RoundOutcomeLike,
    assert_branch_context_shape,
    assert_round_outcome_shape,
)

__all__ = [
    "DEFAULT_QUEUE_MODE",
    "BudgetSpec",
    "ExecutionSpec",
    "QueueMode",
    "Receipt",
    "RejectReason",
    "SourceKind",
    "WorkKind",
    "WorkRequest",
    "validate_work_request",
    "DEFAULT_CAP",
    "Mailbox",
    "QueueItem",
    "EntryPoint",
    "ConfigError",
    "GatewayConfig",
    "MailboxConfig",
    "PlatformConfig",
    "config_fingerprint",
    "load_platform_config",
    "BudgetDecision",
    "BudgetGate",
    "BudgetUsage",
    "DEFAULT_SESSION_BUDGET",
    "effective_budget",
    "BranchContextLike",
    "RoundOutcomeLike",
    "assert_branch_context_shape",
    "assert_round_outcome_shape",
]
