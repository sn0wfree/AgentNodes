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

        **证据经解析出的工具获取**：当 ``resolved_tools`` 非空时，走
        ``read_journal`` / ``read_scoreboard`` / ``read_validation`` /
        ``read_holdout`` 四个经 ``CapabilityRegistry`` 解析的工具；否则回退
        到直接调用数据源（保留无 registry 的简单路径）。两条路径产出同一
        ``EvolutionContext``，因此 policy 无需感知差异。
        """
        signature = compute_task_signature(objective, role, tool_set)
        ctx = EvolutionContext(
            task_signature=signature,
            journal=self._journal(),
            scoreboard=self._scoreboard(),
            validation=self._validation(limit),
            holdout=self._holdout(signature, seed),
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

    # ── 证据获取（经解析出的工具，或直接走数据源） ─────────────────

    def _journal(self) -> list[dict[str, Any]]:
        fn = self.resolved_tools.get("read_journal")
        if fn is not None:
            return list(fn(self.journal, limit=50))
        return list(self.journal.read_journal(limit=50))

    def _scoreboard(self) -> dict[str, Any]:
        fn = self.resolved_tools.get("read_scoreboard")
        if fn is not None:
            return dict(fn(self.scoreboard))
        return dict(self.scoreboard.read_scoreboard())

    def _validation(self, limit: int) -> list[dict[str, Any]]:
        fn = self.resolved_tools.get("read_validation")
        if fn is not None:
            return list(fn(self.validation, limit=limit))
        return list(self.validation.read_validation(limit=limit))

    def _holdout(self, signature: str, seed: int) -> HoldoutSet:
        fn = self.resolved_tools.get("read_holdout")
        if fn is not None:
            return fn(self.holdout, signature, seed)
        return self.holdout.read_holdout(signature, seed)

    # ── 工具解析（阶段二的 CapabilityRegistry） ─────────────────────

    @classmethod
    def with_capabilities(cls, **sources: Any) -> "EvolutionAgent":
        """Build an agent whose tools resolve via CapabilityRegistry.

        工具的解析在**装配期**发生（一次），不在每次 propose 时——
        与阶段二"register 一次、resolve 一次"的口径一致。

        **2026-11-05 批次 8**：``evolution`` 已从 apps/（L2）搬到
        kernel/evolution/（L3），它是内核的自我进化机制，不再是业务侧
        提供的策略。因此这里**不再 import agentnodes.apps**（那会是
        L3→L2 上行违规），改为直接读本包的 ``tools`` 模块构造注册项。
        """
        from agentnodes.kernel.agent.capability import (
            CapabilityNotFound,
            get_default_registry,
        )

        registry = get_default_registry()
        missing = [
            k for k in (f"evolution.{n}" for n in sorted(EVOLUTION_TOOLS))
            if not registry.has(k)
        ]

        def _factory_for(tool_name: str):
            """从本包的 tools 模块取实现（L3 内部依赖，同层合法）。"""
            from agentnodes.kernel.evolution import tools as evo_tools

            def factory(_registry):
                return getattr(evo_tools, tool_name)

            return factory

        for tool_name in sorted(EVOLUTION_TOOLS):
            kind = f"evolution.{tool_name}"
            if kind not in registry:
                registry.register(kind, _factory_for(tool_name))

        tools = {}
        for name in sorted(EVOLUTION_TOOLS):
            try:
                tools[name] = registry.resolve(f"evolution.{name}")
            except CapabilityNotFound:
                # 注册失败（实现缺失）时跳过——不阻断构造
                continue
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
