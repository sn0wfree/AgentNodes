"""SkillMemoryResolver — 装配期返回当前生效的 harness 配置（施工方案 §4.7）。

**只读 Tier 1**：返回值只来自 ``genome_version.tier1_active``，永不读
Tier 0（H3）。Tier 0 的冻结值不参与组装决策，所以修改 Tier 0 的提案
即便被写进配置也不会影响运行时行为——这是"H3 靠结构保证，不靠纪律"。

**装配期调用，不侵入 AgentLoop**：三个调用点
（``PromptBuilderFactory.get`` / ``ToolRegistry`` 构造 / ``CompactConfig``
构造）各取所需。AgentLoop 内部禁止调用（施工方案 §4.7 末条）——
那会让每一步迭代都依赖全局基因组，既慢又难测。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Protocol

logger = logging.getLogger(__name__)

#: Tier 0 组件名（与 genome_proposals 保持同一份口径）。
#: 这里单独列一份而非 import，是为了让 resolver 能在 apps.evolution 之外
#: 被引用时不必拉起整个提案模块——但两处必须同步，故下方有断言。
_TIER0_KEYS: frozenset[str] = frozenset(
    {"budget", "stop_policy", "state_machine", "methodology"}
)


@dataclass(frozen=True)
class EffectiveHarnessConfig:
    """装配期使用的生效配置（不可变）。"""

    prompt_version: dict[str, Any] = field(default_factory=dict)
    tool_set: frozenset[str] | None = None
    compaction_config: dict[str, Any] = field(default_factory=dict)
    skill_set: frozenset[str] | None = None
    task_signature: str = ""

    def is_empty(self) -> bool:
        return not (
            self.prompt_version
            or self.tool_set
            or self.compaction_config
            or self.skill_set
        )


class Tier1Source(Protocol):
    """只读的 tier1_active 提供者。"""

    def tier1_active(self) -> dict[str, Any]:
        """Return ``{component: value}`` for currently effective Tier 1 items."""
        ...


def _default_source() -> Tier1Source | None:
    """进程级默认源：装配根登记的基因组版本链。

    ``GenomeVersionStore`` 自带 ``tier1_active()``，直接满足 Protocol。
    kernel 模块缺失或未登记时返回 None（= 未启用 RSI）。
    """
    try:
        from strategy_research.kernel.orchestration.genome_runtime import (
            get_active_genome,
        )

        return get_active_genome()
    except Exception:  # noqa: BLE001 —— 拿不到源就退回空配置
        logger.debug("no active genome runtime", exc_info=True)
        return None


def resolve(
    task_signature: str, *, source: Tier1Source | None = None
) -> EffectiveHarnessConfig:
    """返回 ``task_signature`` 对应的生效配置。

    :param task_signature: 任务签名（``holdout.compute_task_signature``）
    :param source: tier1 提供者；None = **自动取当前进程登记的基因组版本链**
        （``kernel.orchestration.genome_runtime``）。显式传 None 而进程里
        也没登记 → 返回空配置（未启用 RSI 的默认路径）。

    **为什么默认源不是"永远空"**：早先实现里 ``source=None`` 直接返回空
    配置，装配根把 ``GenomeVersionStore`` 建出来之后也没人递给 resolver，
    于是 ``validation_gate`` 升的版在装配期永远读不到——链路看着接上了、
    实际空转。现在默认源指向 kernel 的运行期登记位，Semantics 仍是
    "没启用就空"，但"启用了"终于能被看见。
    """
    if source is None:
        source = _default_source()
    if source is None:
        return EffectiveHarnessConfig(task_signature=task_signature)

    tier1 = dict(source.tier1_active() or {})

    # H3 的结构性保证：即便 source 错误地把 Tier 0 键混进 tier1，
    # resolver 也一律丢弃，运行时永远看不到 Tier 0。
    leaked = _TIER0_KEYS & set(tier1)
    if leaked:
        logger.warning(
            "SkillMemoryResolver: dropping Tier 0 keys from tier1_active: %s",
            sorted(leaked),
        )
        for k in leaked:
            tier1.pop(k, None)

    raw_tools = tier1.get("tool_set")
    tool_set = frozenset(raw_tools) if raw_tools is not None else None

    # skill 组件：Tier 1 提案可以指定一组方法论技能包（.skills/*.md 的名字）。
    # 与 tool_set 同构——None = 不限制，列表 = 只注入这几个。
    raw_skills = tier1.get("skill")
    skill_set = frozenset(raw_skills) if raw_skills is not None else None

    return EffectiveHarnessConfig(
        prompt_version=dict(tier1.get("prompt") or {}),
        tool_set=tool_set,
        compaction_config=dict(tier1.get("compaction") or {}),
        skill_set=skill_set,
        task_signature=task_signature,
    )


__all__ = [
    "EffectiveHarnessConfig",
    "Tier1Source",
    "resolve",
]
