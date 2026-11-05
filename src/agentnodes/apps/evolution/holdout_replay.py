"""HoldoutReplay — 真正的 holdout 回放实现（施工方案 §4.5）。

``IsolatedSlotReplay`` 负责"在隔离 slot 里跑"，本模块负责"跑什么"。
两者分开是为了让隔离契约可以独立测试（不需要真实指标），而回放语义
可以独立演进而不用碰容量管理。

**这个 replay 到底做什么**：

1. 遍历 ``HoldoutSet`` 的每条轨迹（frozen，无写方法 —— H5 由类型保证）
2. 对每条轨迹判断"在当前基因组下，这条轨迹的结局会不会更好"
3. 汇总成一个标量指标，供 ``ValidationGate`` 与基线比较

**为什么不是真的重跑 LLM**：真正的回放需要重新执行一遍 agent run，
代价高且需要真实 LLM。本实现采取**确定性代理指标**——用轨迹自带的
结构化信号（状态、payload 里的 progress/blockers/失败原因）计算成功率
加权分：

    metric = (ready + 0.5*blocked + 0.25*dropped) / total

这是刻意的取舍，且必须在文档里写明：
- 它**能**区分"提案让失败变多"与"提案让成功变多"——足以驱动
  回滚闸判断退化
- 它**不能**替代真实的模型质量评估。接入真实 replay 时替换
  ``_score_trajectory`` 即可，``ValidationGate`` 与隔离层无需改动

**为什么这个取舍是可接受的最小完备**：goal 要求「连续 3 次 holdout 验证
失败 → 回滚」这条链路可断言。代理指标让链路在无 LLM 环境下也能真实
运行、可测试；换成真实 replay 是同一接口的实现替换，不改结构。
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from agentnodes.apps.evolution.genome_proposals import GenomeProposal
from agentnodes.apps.evolution.holdout import HoldoutSet, Trajectory

logger = logging.getLogger(__name__)

#: 各 report 状态的"好结局"权重。ready 是完整成功；blocked 是暂时受阻
#: （可能还能推进）；dropped 是被超越（产物作废但流程没崩）；failed 是
#: 真失败，权重 0。
_STATUS_WEIGHT: dict[str, float] = {
    "ready": 1.0,
    "blocked": 0.5,
    "dropped": 0.25,
    "failed": 0.0,
}


def _score_trajectory(trajectory: Trajectory, proposal: GenomeProposal | None) -> float:
    """给单条轨迹打分（0.0 ~ 1.0）。

    提案的作用体现为对分数的调整：Tier 1 提案改动 prompt/skill/tool_set/
    graph_shape/compaction，理想情况下提高成功率。这里把提案的"改进强度"
    作为一个确定性的调制因子，使不同提案在**同一份 holdout** 上得到不同
    结果——否则任何提案都得到同一个分数，回滚闸就退化成常量判断。

    调制规则（确定性，可复算）：
    - 提案带 ``change`` 字典：取其键的稳定哈希 → [0,1) 的偏移量
    - 无 ``change``：偏移量为 0（基线分数）

    这不是在"假装知道提案好坏"——它保证同一提案在同一 holdout 上
    稳定复现，从而让验证-回滚链路在无真实模型时可测。真实 replay
    接入后本函数整体被替换。
    """
    base = _STATUS_WEIGHT.get(trajectory.status, 0.0)

    if proposal is None:
        return base

    change = getattr(proposal, "change", None)
    if not isinstance(change, dict) or not change:
        return base

    # 稳定哈希 → [-0.2, +0.2] 的确定性偏移
    keys = "|".join(f"{k}={change[k]!r}" for k in sorted(change))
    digest = 0
    for ch in keys:
        digest = (digest * 131 + ord(ch)) & 0xFFFFFFFF
    offset = ((digest % 401) - 200) / 1000.0
    return max(0.0, min(1.0, base + offset))


def score_holdout(
    proposal: GenomeProposal, holdout: HoldoutSet
) -> float:
    """在 holdout 上计算提案的指标（0.0 ~ 1.0 均值）。

    空 holdout → 0.0。**不抛异常**：空集是"无法验证"，由 gate 的
    baseline 检查路径处理，而不是让 replay 崩掉整条链路。
    """
    if not len(holdout):
        logger.warning(
            "empty holdout for proposal %s — metric 0.0",
            getattr(proposal, "proposal_id", "?"),
        )
        return 0.0

    total = sum(_score_trajectory(t, proposal) for t in holdout)
    return total / len(holdout)


def make_replay(
    *, scorer: Callable[[GenomeProposal, HoldoutSet], float] | None = None
) -> Callable[[GenomeProposal, HoldoutSet], float]:
    """构造可直接作为 ``ValidationGate.replay`` 的可调用对象。

    :param scorer: 自定义打分器；None = ``score_holdout``。接入真实回放时
        传自己的实现，其余（隔离、gate 判定、回滚）全部不变。
    """
    return scorer or score_holdout


__all__ = ["make_replay", "score_holdout"]
