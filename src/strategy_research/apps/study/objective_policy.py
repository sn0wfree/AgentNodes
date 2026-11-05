"""研究目标的实盘执行拒绝策略（原 apps/goal/policy.py 的最小保留）。

**来历**：``apps/goal`` 已于 2026-11-04 做减法批次 2 移除，但
``study.scheduler.replace_objective`` 仍需要这条防线——研究目标的
objective 不允许包含实盘下单/执行指令（研究系统不下单）。
只保留 ``reject_live_execution_objective`` 一个函数；goal 管理相关
的其余 helper（normalize_required_text 等）随 goal 归档
（testcase/golden/apps/goal/policy.py）。
"""

from __future__ import annotations

import re

_EXECUTION_PATTERNS = (
    re.compile(r"\b(place|submit|execute|send)\b.{0,40}\b(order|trade)\b", re.I),
    re.compile(
        r"\b(buy|sell|short|long)\b.{0,40}\b(now|immediately|market order|limit order|shares?|contracts?|btc|eth|usdt)\b",
        re.I,
    ),
    re.compile(r"(下单|市价单|限价单|马上买|立即买|现在买|马上卖|立即卖|现在卖)"),
)


def reject_live_execution_objective(objective: str) -> None:
    """Reject direct live-trading or order-execution goal text.

    Raises:
        ValueError: If the objective looks like an execution request.
    """
    text = objective.strip()
    for pattern in _EXECUTION_PATTERNS:
        if pattern.search(text):
            raise ValueError("live trading or execution goals are not supported")


__all__ = ["reject_live_execution_objective"]
