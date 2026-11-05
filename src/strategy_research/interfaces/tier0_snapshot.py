"""Tier 0 冻结值采集（组装根，L1）。

**为什么需要这个模块**：``GenomeVersion.tier0_frozen`` 此前**永远为空**
——所有统计都显示它只被读取和继承，从没有任何代码往里写。于是"Tier 0
冻结"只有两道防线（构造时拒绝、验证时拒绝），缺了第三道"值本身被快照
下来"，也就无法回答一个基本问题：**某个版本生效时，预算是多少？**

**为什么在这里而不在 kernel**：Tier 0 的四类值分散在三个层 + 一份资源：

| 值 | 位置 | 层 |
|---|---|---|
| 预算上限 | ``kernel.agent.strategy.budget.BudgetLimits`` | L3 |
| 停止策略 | ``kernel.agent.strategy.loop_strategy.LoopConfig`` | L3 |
| 状态机 | ``kernel.orchestration.ticket._ASSIGNMENT_TRANSITIONS`` | L3 |
| 方法论 | ``templates/.prompts/_common/principles.md`` | 资源 |

> 2026-11-05 批次 3：状态机随 ``apps/study`` 移除，从 L2 迁到 L3
> （Ticket/Assignment 是通用任务身份，见 ``kernel/orchestration/ticket.py``）。
> 采集点仍在 L1——它是组装根，且将来若有值回落到 L2，这里不用再改。

kernel 不能 import apps（H1）。L1 组装根是唯一同时看得到四者的位置，
且依赖方向天然向下（L1 → L2/L3）。

**只读**：采集只做读取与快照，不改任何运行时行为。即使采集失败，基因组
也照常工作——``tier0_frozen`` 是审计资产，不是执行依赖。
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def collect_tier0() -> dict[str, Any]:
    """采集当前进程的 Tier 0 冻结值快照。

    返回的 dict 会被写进 ``GenomeVersion.tier0_frozen``，并在每次
    ``activate()`` 时原样继承——这样任意历史版本都能回答"那一刻的
    Tier 0 是什么"。

    **永不抛出**：任何一项采集失败就跳过该项，其余照常。基因组是执行
    依赖，不能因为审计资产采不到就起不来。
    """
    out: dict[str, Any] = {}

    out["budget"] = _collect_budget()
    out["stop_policy"] = _collect_stop_policy()
    out["state_machine"] = _collect_state_machine()
    out["methodology"] = _collect_methodology()

    return {k: v for k, v in out.items() if v is not None}


def _collect_budget() -> dict[str, Any] | None:
    """预算上限的类默认值与字段（``BudgetLimits``）。"""
    try:
        import dataclasses

        from strategy_research.kernel.agent.strategy.budget import BudgetLimits

        # 记录"字段 + 类默认值"而不是某个运行实例的值——实例值随 run 变化，
        # 快照要的是"这一类预算的契约形状"。
        return {
            "fields": sorted(f.name for f in dataclasses.fields(BudgetLimits)),
            "defaults": {
                f.name: f.default
                for f in dataclasses.fields(BudgetLimits)
                if f.default is not dataclasses.MISSING
            },
            "source": "kernel.agent.strategy.budget.BudgetLimits",
        }
    except Exception as exc:  # noqa: BLE001
        logger.debug("tier0 budget collection failed: %s", exc)
        return None


def _collect_stop_policy() -> dict[str, Any] | None:
    """停止策略相关字段（``LoopConfig`` 里的上限与阈值）。"""
    try:
        import dataclasses

        from strategy_research.kernel.agent.strategy.loop_strategy import LoopConfig

        stop_fields = (
            "max_iterations", "no_progress_window", "threshold_tokens",
            "wrap_up_ratio", "budget_max_tokens", "budget_max_cost_usd",
            "budget_max_seconds",
        )
        defaults = {}
        field_map = {f.name: f for f in dataclasses.fields(LoopConfig)}
        for name in stop_fields:
            f = field_map.get(name)
            if f is not None and f.default is not dataclasses.MISSING:
                defaults[name] = f.default
        return {
            "fields": sorted(defaults),
            "defaults": defaults,
            "source": "kernel.agent.strategy.loop_strategy.LoopConfig",
        }
    except Exception as exc:  # noqa: BLE001
        logger.debug("tier0 stop_policy collection failed: %s", exc)
        return None


def _collect_state_machine() -> dict[str, Any] | None:
    """Ticket / Assignment 的合法转移表（``kernel.orchestration.ticket``）。

    2026-11-05 批次 3：随 ``apps/study`` 移除，从 L2 迁到 L3。
    Ticket/Assignment 是通用任务身份，不是研究业务（逐字段论证见
    ``kernel/orchestration/ticket.py`` 的模块 docstring）。
    """
    try:
        from strategy_research.kernel.orchestration.ticket import (
            _ASSIGNMENT_TRANSITIONS,
            _TICKET_TRANSITIONS,
        )

        def _dump(table: dict[Any, frozenset]) -> dict[str, list[str]]:
            return {
                getattr(k, "value", str(k)): sorted(
                    getattr(v, "value", str(v)) for v in vs
                )
                for k, vs in table.items()
            }

        return {
            "ticket": _dump(_TICKET_TRANSITIONS),
            "assignment": _dump(_ASSIGNMENT_TRANSITIONS),
            "source": "kernel.orchestration.ticket",
        }
    except Exception as exc:  # noqa: BLE001
        logger.debug("tier0 state_machine collection failed: %s", exc)
        return None


def _collect_methodology() -> dict[str, Any] | None:
    """方法论文档的**指纹**（内容不进快照，只存摘要）。"""
    try:
        import hashlib
        from pathlib import Path

        import strategy_research as _sr

        common = Path(_sr.__file__).parent / "templates" / ".prompts" / "_common"
        if not common.is_dir():
            return None

        entries: dict[str, str] = {}
        for md in sorted(common.rglob("*.md")):
            digest = hashlib.sha256(md.read_bytes()).hexdigest()[:16]
            entries[str(md.relative_to(common)).replace("\\", "/")] = digest
        return {
            "files": entries,
            "source": "templates/.prompts/_common",
            "note": "只存指纹；正文是 Tier 0 内容，不复制进基因组",
        }
    except Exception as exc:  # noqa: BLE001
        logger.debug("tier0 methodology collection failed: %s", exc)
        return None


__all__ = ["collect_tier0"]
