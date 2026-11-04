"""EvolutionAgent — Tier 1 提案者（施工方案 §4.3）。

**定位**：不在常规 Worker 路径上跑。它在 Assignment 结束后被触发，
读一轮历史，产出一份 ``GenomeProposal``。仅此而已——
它**不能**改配置、不能写业务表、不能碰 Tier 0。

**工具集**（全部只读 + 一个提案口）：
    read_journal / read_scoreboard / read_validation / read_holdout
    propose_genome_update

**为什么不继承 AgentLoop**：AgentLoop 是"执行任务"的机器，带工具循环、
压缩、预算控制。EvolutionAgent 只需读 + 产出结构化提案。硬套 AgentLoop
会把 Tier 0 的预算/停止逻辑带进来，反而增加 H3 的破口。

**工具解析**：全部经 ``CapabilityRegistry.resolve``（阶段二成果），
这样本模块不必反向依赖 kernel 的注册表实现，且与其它工具一致地可替换。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from .genome_proposals import GenomeProposal, TierViolation
from .holdout import HoldoutSet, Trajectory, compute_task_signature, sample

logger = logging.getLogger(__name__)

#: EvolutionAgent 允许的工具白名单（施工方案 §4.3）。
EVOLUTION_TOOLS: frozenset[str] = frozenset(
    {
        "read_journal",
        "read_scoreboard",
        "read_validation",
        "read_holdout",
        "propose_genome_update",
    }
)


class JournalSource(Protocol):
    def read_journal(self, *, limit: int = 50) -> list[dict[str, Any]]: ...


class ScoreboardSource(Protocol):
    def read_scoreboard(self) -> dict[str, Any]: ...


class ValidationHistorySource(Protocol):
    def read_validation(self, *, limit: int = 50) -> list[dict[str, Any]]: ...


class HoldoutSource(Protocol):
    def read_holdout(self, task_signature: str, seed: int) -> HoldoutSet: ...


@dataclass
class EvolutionAgent:
    """只读证据 → 单个提案。

    :param journal / scoreboard / validation / holdout: 四个只读数据源
    :param policy: 把证据映射为提案的决策函数。默认策略保守（不提案）。
    :param tools: 已注册的工具解析器，默认用 capability registry
    """

    journal: JournalSource
    scoreboard: ScoreboardSource
    validation: ValidationHistorySource
    holdout: HoldoutSource
    policy: Callable[["EvolutionContext"], GenomeProposal | None] | None = None
    resolved_tools: dict[str, Callable[..., Any]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # 白名单自检：工具集必须恰好是 §4.3 那五个，多一个都算越权。
        missing = EVOLUTION_TOOLS - set(self.resolved_tools) if self.resolved_tools else set()
        if self.resolved_tools and missing:
            raise ValueError(
                f"EvolutionAgent missing required tools: {sorted(missing)}"
            )
        extra = set(self.resolved_tools) - EVOLUTION_TOOLS if self.resolved_tools else set()
        if extra:
            raise ValueError(
                f"EvolutionAgent has tools outside the whitelist: {sorted(extra)}"
            )

    # ── 主入口 ──────────────────────────────────────────────────────

    def propose(
        self,
        *,
        objective: str,
        role: str,
        tool_set: frozenset[str],
        seed: int,
        limit: int = 50,
    ) -> GenomeProposal | None:
        """读一轮证据，产出一份提案（或 None = 无有价值提案）。

        ``seed`` 显式传入（附录 C：不允许隐式不确定性）。
        """
        signature = compute_task_signature(objective, role, tool_set)
        ctx = EvolutionContext(
            task_signature=signature,
            journal=list(self.journal.read_journal(limit=limit)),
            scoreboard=dict(self.scoreboard.read_scoreboard()),
            validation=list(self.validation.read_validation(limit=limit)),
            holdout=self.holdout.read_holdout(signature, seed),
            seed=seed,
        )

        if self.policy is None:
            logger.debug("EvolutionAgent: no policy configured, no proposal")
            return None

        proposal = self.policy(ctx)
        if proposal is None:
            return None

        # 最后一道本地校验：policy 若产出 Tier 0 提案，这里就拦住，
        # 不让它进入 validation_gate（那里也会拒，但越早越好）。
        if proposal.target_component not in {
            "prompt", "skill", "graph_shape", "tool_set", "compaction",
        }:
            raise TierViolation(
                f"policy produced out-of-whitelist proposal: "
                f"{proposal.target_component!r}"
            )
        return proposal

    # ── 工具解析（阶段二的 CapabilityRegistry） ─────────────────────

    @classmethod
    def with_capabilities(cls, **sources: Any) -> "EvolutionAgent":
        """Build an agent whose tools resolve via CapabilityRegistry.

        工具的解析在**装配期**发生（一次），不在每次 propose 时——
        与阶段二"register 一次、resolve 一次"的口径一致。
        """
        from strategy_research.kernel.agent.capability import resolve

        tools = {name: resolve(f"evolution.{name}") for name in sorted(EVOLUTION_TOOLS)}
        return cls(resolved_tools=tools, **sources)


@dataclass(frozen=True)
class EvolutionContext:
    """一轮只读证据的集合（policy 的输入）。"""

    task_signature: str
    journal: list[dict[str, Any]]
    scoreboard: dict[str, Any]
    validation: list[dict[str, Any]]
    holdout: HoldoutSet
    seed: int

    @property
    def failure_rate(self) -> float:
        """holdout 里 failed 轨迹的占比（policy 常用的启发量）。"""
        if not len(self.holdout):
            return 0.0
        failed = sum(1 for t in self.holdout if t.status == "failed")
        return failed / len(self.holdout)


__all__ = [
    "EVOLUTION_TOOLS",
    "EvolutionAgent",
    "EvolutionContext",
]
