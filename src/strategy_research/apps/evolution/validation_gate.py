"""ValidationGate — 提案的验证与回滚闸（施工方案 §4.5 / §4.8）。

**职责**：在隔离 slot 中用**只读** holdout 回放提案，比较指标，决定生效
或拒绝。它是 Tier 1 变更的唯一入口——没有任何路径能绕过它直接改配置。

**三条硬规则**：
1. **Tier 0 拒绝**（H3）：``target_component ∈ TIER0_COMPONENTS`` 直接拒。
   提案对象在构造时已拒一次；这里再拒一次，防止绕过 ``GenomeProposal.new``
   构造的野生对象（例如从 DB 反序列化的旧数据）。
2. **±20% 阈值**：holdout 指标劣化超过 20% 即失败；指标**方向相反且无法
   解释**时暂停通道并 escalate（goal §6 第 3 条）。
3. **连续 3 次失败 → 回滚**：``status=rolled_back`` + emit
   ``genome.rollback``，genome_version 回到上一版本。

**只读**：回放不写业务表，只写 ``validation_runs`` 审计表。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from .genome_proposals import (
    TIER0_COMPONENTS,
    GenomeProposal,
    ProposalStatus,
    TierViolation,
)
from .holdout import HoldoutSet

logger = logging.getLogger(__name__)

#: 指标劣化容忍阈值（施工方案 §4.5 第 3 步）。
DEGRADATION_THRESHOLD = 0.20

#: 连续失败多少次触发回滚（施工方案 §4.8）。
ROLLBACK_AFTER_FAILURES = 3


# ── 结果类型 ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    reason: str
    baseline_metric: float | None = None
    candidate_metric: float | None = None
    degradation: float | None = None
    escalated: bool = False


class GenomeVersionStore(Protocol):
    """genome_version 的读写接口（由 ConfigHub 实现或测试替身）。"""

    def current_version_id(self) -> str: ...

    def activate(self, proposal_id: str, tier1_active: dict[str, Any]) -> str:
        """激活提案，返回新 version_id。"""
        ...

    def rollback(self) -> str:
        """回退到上一版本，返回回退后的 version_id。"""
        ...


class EventSink(Protocol):
    def __call__(self, event_type: str, data: dict[str, Any]) -> None: ...


# ── Gate ─────────────────────────────────────────────────────────────


@dataclass
class ValidationGate:
    """提案验证闸。

    :param genome: 基因组版本存储
    :param emit: 事件发射器（``genome.rollback`` 等）
    :param replay: 回放函数 ``(proposal, holdout) -> metric``。
        **只读**契约由调用方保证：它只能跑 holdout、不能写业务表。
    :param baseline_metric: 当前基线指标提供者
    """

    genome: GenomeVersionStore
    emit: EventSink
    replay: Callable[[GenomeProposal, HoldoutSet], float]
    baseline_metric: Callable[[], float | None]

    _consecutive_failures: int = field(default=0, init=False)
    _history: list[ValidationResult] = field(default_factory=list, init=False)

    # ── 主入口 ──────────────────────────────────────────────────────

    def validate(
        self, proposal: GenomeProposal, holdout: HoldoutSet
    ) -> GenomeProposal:
        """验证提案并推进其状态。

        返回推进后的提案（approved / rejected / rolled_back）。
        """
        # 规则 1：Tier 0 一律拒绝（H3）。即便提案对象绕过了 new()，
        # 这里也必须拦住。
        if proposal.target_component in TIER0_COMPONENTS or proposal.is_tier0:
            return self._reject(
                proposal,
                f"Tier 0 component is frozen: {proposal.target_component!r}",
                escalated=False,
            )

        result = self._evaluate(proposal, holdout)
        self._history.append(result)

        # draft → validating 是进入评估的标记（施工方案 §4.5 流程第 1-2 步）
        working = (
            proposal
            if proposal.status is ProposalStatus.VALIDATING
            else proposal.to_validating()
        )

        if result.ok:
            self._consecutive_failures = 0
            approved = working.to_approved()
            self.genome.activate(
                approved.proposal_id, {approved.target_component: approved.change}
            )
            return approved

        # 规则 3：连续失败累计
        self._consecutive_failures += 1
        rejected = self._reject(working, result.reason, escalated=result.escalated)

        if self._consecutive_failures >= ROLLBACK_AFTER_FAILURES:
            return self._rollback(rejected, result.reason)
        return rejected

    # ── 评估 ────────────────────────────────────────────────────────

    def _evaluate(
        self, proposal: GenomeProposal, holdout: HoldoutSet
    ) -> ValidationResult:
        baseline = self.baseline_metric()
        try:
            candidate = float(self.replay(proposal, holdout))
        except Exception as exc:  # noqa: BLE001 —— 回放崩溃算验证失败
            logger.warning("replay failed for %s: %s", proposal.proposal_id, exc)
            return ValidationResult(ok=False, reason=f"replay failed: {exc}")

        if baseline is None or baseline == 0:
            # 无基线：不能判断优劣，按"需人工确认"处理（不静默放行）
            return ValidationResult(
                ok=False,
                reason="no baseline metric; cannot judge — escalated",
                candidate_metric=candidate,
                escalated=True,
            )

        degradation = (baseline - candidate) / abs(baseline)
        if degradation > DEGRADATION_THRESHOLD:
            return ValidationResult(
                ok=False,
                reason=(
                    f"metric degraded {degradation:.1%} "
                    f"(threshold {DEGRADATION_THRESHOLD:.0%})"
                ),
                baseline_metric=baseline,
                candidate_metric=candidate,
                degradation=degradation,
            )

        # 方向相反（候选远好于基线）也需警惕：可能是奖励黑客信号。
        # 施工方案 §4.5 只给了"不劣于基线"的下界；这里对"暴涨"要求
        # 同样走 escalation，而不是直接通过。
        if degradation < -DEGRADATION_THRESHOLD:
            return ValidationResult(
                ok=False,
                reason=(
                    f"metric improved {(-degradation):.1%} — implausible jump, "
                    "possible reward hacking; escalated"
                ),
                baseline_metric=baseline,
                candidate_metric=candidate,
                degradation=degradation,
                escalated=True,
            )

        return ValidationResult(
            ok=True,
            reason="within tolerance",
            baseline_metric=baseline,
            candidate_metric=candidate,
            degradation=degradation,
        )

    # ── 状态推进 ────────────────────────────────────────────────────

    @staticmethod
    def _reject(
        proposal: GenomeProposal, reason: str, *, escalated: bool
    ) -> GenomeProposal:
        status = ProposalStatus.VALIDATING
        working = (
            proposal
            if proposal.status is status
            else proposal.to_validating()
        )
        return working.to_rejected(reason)

    def _rollback(
        self, rejected: GenomeProposal, reason: str
    ) -> GenomeProposal:
        rolled = rejected.to_rolled_back(
            f"rolled back after {ROLLBACK_AFTER_FAILURES} consecutive failures: {reason}"
        )
        new_version = self.genome.rollback()
        self.emit("genome.rollback", {
            "proposal_id": rolled.proposal_id,
            "target_component": rolled.target_component,
            "reason": rolled.rejection_reason,
            "new_version_id": new_version,
            "consecutive_failures": self._consecutive_failures,
        })
        self._consecutive_failures = 0
        return rolled

    # ── 观测 ────────────────────────────────────────────────────────

    @property
    def consecutive_failures(self) -> int:
        return self._consecutive_failures

    @property
    def history(self) -> list[ValidationResult]:
        return list(self._history)


__all__ = [
    "DEGRADATION_THRESHOLD",
    "GenomeVersionStore",
    "ROLLBACK_AFTER_FAILURES",
    "ValidationGate",
    "ValidationResult",
    "TierViolation",
]
