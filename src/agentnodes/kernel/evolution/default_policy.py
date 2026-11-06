"""默认进化策略 —— 从证据推导一份 Tier 1 提案（施工方案 §4.3 的 policy）。

**为什么需要一个默认 policy**：``EvolutionAgent.policy=None`` 时永不产提案。
那是刻意保守的默认（生产环境不希望 agent 自己乱改），但"自我进化"若
没有 policy，整条闭环永远是 ``no_proposal``——零件全在，引擎不转。

**这个 policy 的决策依据全部来自只读证据**：

1. 失败率（``holdout.failure_rate``）高 → 收窄 tool_set（减少可犯错面）
2. 成功率已经很高 → 放宽 tool_set（探索更大动作空间）
3. 回滚历史多 → 缩小 compaction 窗口（减少上下文干扰）
4. 证据不足（样本 < 阈值）→ 不提案（避免小样本过拟合）

**为什么这些改动是安全的**：全部落在 Tier 1 的 ``tool_set`` / ``prompt`` /
``compaction`` 三类，都不碰预算、停止策略、状态机、方法论（Tier 0）。
且提案还要过 ``ValidationGate`` 的真实评测——policy 只是提出假设，
不是直接生效。

**为什么不做更聪明的策略**：LLM 驱动的 policy 需要 LLM 调用与提示工程，
属于后续工作。当前 policy 是确定性的、可复现的，足以驱动整条链路；
接入 LLM policy 时替换 ``default_policy`` 即可，无需改 Agent 或 Gate。
"""

from __future__ import annotations

import logging
from typing import Any

from .genome_proposals import GenomeProposal

logger = logging.getLogger(__name__)

#: 样本少于这个数就不提案——小样本上的"改进"多半是噪声。
MIN_HOLDOUT_SAMPLES = 5

#: 失败率高于此值 → 收窄工具集。
HIGH_FAILURE_RATE = 0.5

#: 失败率低于此值 → 可放宽工具集。
LOW_FAILURE_RATE = 0.2

#: 默认收窄/放宽的目标工具集（保守：只列只读工具）。
NARROW_TOOL_SET = ["read", "list", "list_history", "tool_help"]


def _tool_set_change(tools: list[str] | None) -> dict[str, Any]:
    """构造 ``tool_set`` 组件的 change（F1 形状契约的**产出侧**）。

    ``validation_gate.activate()`` 把 change **原样**写进
    ``tier1_active["tool_set"]``，所以产出形状必须与
    ``skill_memory._normalise_tool_set`` 的读取形状对齐：

    - ``{"tools": [...]}`` → 收窄到这几个
    - ``{"tools": None}``  → 不限制

    **为什么保留 ``{"tools": ...}`` 这层包装而不是直接给裸列表**：
    ``tool_set`` 组件以后可能还有别的键（如 ``exclude`` / ``version``），
    用 dict 承载才留得住扩展位。裸列表一旦写进磁盘，schema 就没法演进了。

    :param tools: None = 不限制；list = 白名单
    """
    return {"tools": None if tools is None else list(tools)}


def default_policy(ctx: Any) -> GenomeProposal | None:
    """从 ``EvolutionContext`` 推导提案。无足够证据时返回 None。

    决策顺序（先命中先返回）：证据不足 → 高失败率 → 低失败率 → 有回滚历史
    → 都不命中则不提。
    """
    holdout = ctx.holdout
    size = len(holdout)
    if size < MIN_HOLDOUT_SAMPLES:
        logger.debug(
            "policy: holdout too small (%d < %d), no proposal",
            size, MIN_HOLDOUT_SAMPLES,
        )
        return None

    failure_rate = ctx.failure_rate
    evidence = [f"holdout:{size} samples", f"failure_rate:{failure_rate:.2f}"]

    # ① 高失败率 → 收窄工具集（减少可犯错的动作面）
    if failure_rate > HIGH_FAILURE_RATE:
        return GenomeProposal.new(
            "tool_set",
            _tool_set_change(NARROW_TOOL_SET),
            evidence_refs=evidence + ["policy:high_failure_rate"],
        )

    # ② 低失败率 → 放宽（去掉收窄限制，回到全量）
    if failure_rate < LOW_FAILURE_RATE:
        return GenomeProposal.new(
            "tool_set",
            _tool_set_change(None),  # None = 不限制（与 harness_resolver 口径一致）
            evidence_refs=evidence + ["policy:low_failure_rate"],
        )

    # ③ 有回滚历史 → 缩小压缩窗口（减少上下文干扰）
    #
    # 字段名必须是 CompactConfig 的真实字段（``collapse_keep_recent``）——
    # ``apply_compaction_overrides`` 只应用 ``__dataclass_fields__`` 里存在的
    # 键，写错的键会被静默丢弃，提案看似通过、实际零效果。
    rollbacks = sum(
        1 for v in ctx.validation if str(v.get("reason") or "").startswith("genome.rollback")
    )
    if rollbacks:
        return GenomeProposal.new(
            "compaction",
            {"collapse_keep_recent": max(4, 20 - 4 * rollbacks)},
            evidence_refs=evidence + [f"rollbacks:{rollbacks}"],
        )

    logger.debug("policy: no actionable evidence (failure_rate=%.2f)", failure_rate)
    return None


__all__ = [
    "HIGH_FAILURE_RATE",
    "LOW_FAILURE_RATE",
    "MIN_HOLDOUT_SAMPLES",
    "NARROW_TOOL_SET",
    "default_policy",
]
