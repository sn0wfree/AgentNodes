"""GenomeProposal — Tier 1 基因组的变更提案（施工方案 §4.4）。

**为什么提案与实践分离**：EvolutionAgent 只能"提"，不能"改"。
提案先落库（status=draft），再由 validation_gate 在隔离 slot 里用
只读 holdout 验证，通过才生效。这条链路是 RSI 防奖励黑客的结构性保证——
如果 Agent 能直接改配置，它总能把指标调好看而学不到东西。

**Tier 划分（H3）**：
- Tier 0（冻结，不可提）：预算 / 停止 / 状态机 / 方法论
- Tier 1（可提，需验证）：prompt / skill / 图形状
- Tier 2（研究产物）：strategy.py 等，由研究域自改，不经 validation_gate
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any


class ProposalStatus(str, Enum):
    DRAFT = "draft"
    VALIDATING = "validating"
    APPROVED = "approved"
    REJECTED = "rejected"
    ROLLED_BACK = "rolled_back"


#: Tier 0 受保护组件（H3）。提案落在这些上必须被 validation_gate 拒绝。
TIER0_COMPONENTS: frozenset[str] = frozenset(
    {
        "budget",           # 预算（token/turn/time 上限）
        "stop_policy",      # 停止策略
        "state_machine",    # 状态机（Ticket/Assignment 转移规则）
        "methodology",      # 方法论（研究铁律）
    }
)

#: Tier 1 可提案组件（需 holdout 验证）。
TIER1_COMPONENTS: frozenset[str] = frozenset(
    {
        "prompt",       # role prompt 内容
        "skill",        # skill 技能包
        "graph_shape",  # 工作流图形状
        "tool_set",     # 工具白名单
        "compaction",   # 压缩配置
    }
)


class TierViolation(ValueError):
    """Raised when a proposal targets a Tier 0 component."""


@dataclass(frozen=True)
class GenomeProposal:
    proposal_id: str
    target_component: str
    change: dict[str, Any]
    evidence_refs: list[str] = field(default_factory=list)
    status: ProposalStatus = ProposalStatus.DRAFT
    created_at: str = ""
    validated_at: str | None = None
    rejection_reason: str | None = None

    @classmethod
    def new(
        cls,
        target_component: str,
        change: dict[str, Any],
        *,
        evidence_refs: list[str] | None = None,
        now: str = "",
        proposal_id: str | None = None,
    ) -> "GenomeProposal":
        """Create a draft proposal. Tier 0 targets raise immediately.

        在**构造时**就拒绝 Tier 0，而不是等 validation_gate——让违规
        在最靠近源头的地方暴露，而不是跨进程/跨阶段后才被发现。
        """
        if target_component in TIER0_COMPONENTS:
            raise TierViolation(
                f"Tier 0 component is frozen and cannot be proposed: "
                f"{target_component!r} (allowed: {sorted(TIER1_COMPONENTS)})"
            )
        if target_component not in TIER1_COMPONENTS:
            raise TierViolation(
                f"unknown target_component {target_component!r} "
                f"(allowed Tier 1: {sorted(TIER1_COMPONENTS)})"
            )
        return cls(
            proposal_id=proposal_id or f"gpr-{uuid.uuid4()}",
            target_component=target_component,
            change=dict(change),
            evidence_refs=list(evidence_refs or []),
            status=ProposalStatus.DRAFT,
            created_at=now or _now_iso(),
            validated_at=None,
            rejection_reason=None,
        )

    # ── 状态转移 ────────────────────────────────────────────────────

    def to_validating(self, *, now: str = "") -> "GenomeProposal":
        if self.status is not ProposalStatus.DRAFT:
            raise ValueError(f"cannot validate from {self.status.value}")
        return replace(self, status=ProposalStatus.VALIDATING)

    def to_approved(self, *, now: str = "") -> "GenomeProposal":
        if self.status is not ProposalStatus.VALIDATING:
            raise ValueError(f"cannot approve from {self.status.value}")
        return replace(
            self, status=ProposalStatus.APPROVED, validated_at=now or _now_iso()
        )

    def to_rejected(self, reason: str, *, now: str = "") -> "GenomeProposal":
        return replace(
            self,
            status=ProposalStatus.REJECTED,
            validated_at=now or _now_iso(),
            rejection_reason=reason,
        )

    def to_rolled_back(self, reason: str, *, now: str = "") -> "GenomeProposal":
        return replace(
            self,
            status=ProposalStatus.ROLLED_BACK,
            validated_at=now or _now_iso(),
            rejection_reason=reason,
        )

    @property
    def is_tier0(self) -> bool:
        return self.target_component in TIER0_COMPONENTS


def _now_iso() -> str:
    import datetime as _dt

    return (
        _dt.datetime.now(_dt.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


__all__ = [
    "GenomeProposal",
    "ProposalStatus",
    "TIER0_COMPONENTS",
    "TIER1_COMPONENTS",
    "TierViolation",
]
