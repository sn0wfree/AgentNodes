"""Evolution — RSI Tier 1 闭环（施工方案 §4）。

模块职责：

    holdout.py             只读的验证轨迹集（H5）+ 确定性抽样
    genome_proposals.py    GenomeProposal / Tier 划分 / 状态机
    validation_gate.py     验证、±20% 阈值、连续 3 次失败回滚
    skill_memory.py        装配期解析生效的 Tier 1 配置
    evolution_agent.py     只读证据 → 单个提案

**H3（Tier 0 冻结）在两处结构性地保证**：
1. ``GenomeProposal.new()`` 构造时拒绝 Tier 0
2. ``validation_gate`` 对野生对象（如从 DB 反序列化）再拒一次
3. ``SkillMemoryResolver`` 从 tier1_active 里丢弃 Tier 0 键

三层而不是一层，是因为"不可改"这种约束一旦只靠一处检查，绕过路径
总是会以意想不到的方式出现。
"""

from __future__ import annotations

from .evolution_agent import EVOLUTION_TOOLS, EvolutionAgent, EvolutionContext
from .genome_proposals import (
    TIER0_COMPONENTS,
    TIER1_COMPONENTS,
    GenomeProposal,
    ProposalStatus,
    TierViolation,
)
from .holdout import (
    HOLDOUT_STATUSES,
    HoldoutSet,
    Trajectory,
    compute_task_signature,
    sample,
)
from .isolated_replay import IsolatedSlotReplay, SlotUnavailable
from .skill_memory import EffectiveHarnessConfig, resolve as resolve_harness
from .validation_gate import (
    DEGRADATION_THRESHOLD,
    ROLLBACK_AFTER_FAILURES,
    ValidationGate,
    ValidationResult,
)

__all__ = [
    "DEGRADATION_THRESHOLD",
    "EVOLUTION_TOOLS",
    "EffectiveHarnessConfig",
    "EvolutionAgent",
    "EvolutionContext",
    "GenomeProposal",
    "HOLDOUT_STATUSES",
    "HoldoutSet",
    "IsolatedSlotReplay",
    "ProposalStatus",
    "ROLLBACK_AFTER_FAILURES",
    "SlotUnavailable",
    "TIER0_COMPONENTS",
    "TIER1_COMPONENTS",
    "TierViolation",
    "Trajectory",
    "ValidationGate",
    "ValidationResult",
    "compute_task_signature",
    "resolve_harness",
    "sample",
]
