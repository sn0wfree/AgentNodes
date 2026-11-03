"""Director —— 停因 → 下一步建议（R6，确定性版）。

架构位置：`docs/architecture-diagram.html` ② Gateway 的 Director 格。
图上语义「停因 → 动作 · propose_next · 产出 = BranchContext、以 followup
提交 mailbox；红线：不读预算、不得自行开 run」。

**本实现刻意是确定性的**（规则表，不含 LLM）：项目真实需要的只是
「study 停止后给出可执行的下一步」，而不是自由发挥的导演。LLM 自由
导演属新功能，不在重构范围内（见评估文档 §1.3）。

红线执行方式：
- **不读预算**：本模块不 import/不查询任何 budget 对象；停因是字符串。
- **不自行开 run**：`propose_next` 是纯函数，只返回建议；调用方
  （scheduler）只发事件，是否执行由上层决定。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

__all__ = ["DirectiveProposal", "propose_next", "ACTION_CONTINUE", "ACTION_RESCOPE",
           "ACTION_ESCALATE", "ACTION_STOP"]

ACTION_CONTINUE = "continue"
ACTION_RESCOPE = "rescope"
ACTION_ESCALATE = "escalate"
ACTION_STOP = "stop"


@dataclass(frozen=True)
class DirectiveProposal:
    """一次「下一步」建议。纯数据，不含预算、不含执行副作用。"""

    action: str            # continue | rescope | escalate | stop
    reason: str            # 触发它的停因（原样回填，便于对账）
    study_id: str
    detail: str = ""       # 人读的一句话
    payload: dict = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "reason": self.reason,
            "study_id": self.study_id,
            "detail": self.detail,
            "payload": dict(self.payload),
        }


# 停因 → (action, detail)。键是 ShutdownReason 的字符串值
# （core/study/runner.py:76-91），新增停因时在此补一行即可。
_MAP: dict[str, tuple[str, str]] = {
    # 正常收工
    "targets_met": (ACTION_STOP, "指标达标，正常收工"),
    # 预算：不在这里判断还有多少——只建议「交给有权限的人决定」
    "budget_exceeded": (ACTION_ESCALATE, "预算耗尽：是否加预算由人决定"),
    # 方向走不通
    "stagnation": (ACTION_RESCOPE, "连续无改进：建议换场景/换目标"),
    "stagnation_discard_streak": (ACTION_RESCOPE, "连续被丢弃：建议换方向"),
    "novelty_rejected": (ACTION_RESCOPE, "新颖性门连拒：当前方向已饱和"),
    "repeated_deviation": (ACTION_RESCOPE, "反复偏离目标：建议重构目标"),
    "review_failed": (ACTION_RESCOPE, "评审未通过：建议改方法"),
    # 人为或环境
    "cancelled": (ACTION_STOP, "已被取消，不再推进"),
    "paused": (ACTION_STOP, "已暂停，等待人工恢复"),
    # 结构上限（达到轮数上限本身不是失败）
    "max_rounds": (ACTION_ESCALATE, "达到轮数上限：是否加轮由人决定"),
    "early_stopped": (ACTION_RESCOPE, "提前停止：建议评估是否需要调整"),
    "needs_refresh": (ACTION_ESCALATE, "数据需刷新：需要人工确认数据源"),
    # 错误一律交给人
    "error": (ACTION_ESCALATE, "执行出错：需要人工排查"),
}


def propose_next(
    study: Any,
    reason: str,
    *,
    metrics: dict | None = None,
) -> DirectiveProposal:
    """根据停因给出下一步建议（确定性，无 LLM）。

    未知停因 → escalate（不猜）。这是刻意的：猜错方向的代价高于让人看一眼。
    """
    study_id = str(getattr(study, "study_id", "") or "")
    action, detail = _MAP.get(reason, (ACTION_ESCALATE, f"未知停因 {reason!r}：交人判断"))
    return DirectiveProposal(
        action=action,
        reason=reason,
        study_id=study_id,
        detail=detail,
        payload={"metrics_present": metrics is not None},
    )
