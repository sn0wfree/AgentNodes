"""量化域的指标显示名注册（L2 业务内容）。

**批次 4d 迁出**：``_METRIC_LABELS``（夏普比率 / 卡玛比率 / 最大回撤 ...）
原住在 ``kernel/agent/builtin_tools/display_tools.py``。通用平台不该内置
任何一个业务域的指标名称——法律报告讲"胜诉率"，医学报告讲"灵敏度"。

装配根（L1）在需要量化展示时调用 ``register_all()``。
"""
from __future__ import annotations

#: 量化指标 key → 中文显示名
QUANT_METRIC_LABELS: dict[str, str] = {
    "ann_return": "年化收益",
    "sharpe": "夏普比率",
    "max_dd": "最大回撤",
    "calmar": "卡玛比率",
    "sortino": "索提诺",
    "ann_vol": "年化波动",
    "turnover": "年换手",
    "win_rate": "胜率",
}

#: 量化域的输出字段展示偏好（核心字段 = 默认展开）
QUANT_CORE_FIELDS: set[str] = {
    "risk_passed", "risk_rating", "overfit_passed", "weighted_score", "method",
}

#: 量化域的输出字段展示偏好（非核心 = 可折叠）
QUANT_NON_CORE_FIELDS: set[str] = {
    "weights", "risk_contributions", "var_95", "cvar_95", "max_drawdown",
    "stress_results", "tail_risk", "evidence_contribution",
    "thresholds_breached", "open_required_items", "methods_passed",
}

#: 量化域的「指标关键词」——用于断言识别（claim_validator）
QUANT_CLAIM_KEYWORDS: list[str] = [
    # English
    "sharpe", "sortino", "calmar", "ic", "return", "pnl", "nav",
    "alpha", "beta", "drawdown", "mdd", "maxdd", "volatility", "vol",
    "turnover", "winrate", "win rate", "profit", "yield",
    # Chinese
    "夏普", "年化", "收益", "回撤", "胜率", "波动", "换手", "净值", "盈利",
]


def register_all() -> dict[str, int]:
    """把量化域的展示偏好与断言词表注册进 kernel。幂等。"""
    from agentnodes.kernel.agent.builtin_tools.display_tools import (
        register_metric_labels,
    )
    from agentnodes.kernel.agent.prompt_schema_extractor import (
        register_field_hints,
    )
    from agentnodes.kernel.agent.validators.claim_validator import (
        register_metric_keywords,
    )

    register_metric_labels(QUANT_METRIC_LABELS)
    register_field_hints(
        core=QUANT_CORE_FIELDS, non_core=QUANT_NON_CORE_FIELDS
    )
    register_metric_keywords(QUANT_CLAIM_KEYWORDS)
    return {
        "metric_labels": len(QUANT_METRIC_LABELS),
        "field_hints": len(QUANT_CORE_FIELDS) + len(QUANT_NON_CORE_FIELDS),
        "claim_keywords": len(QUANT_CLAIM_KEYWORDS),
    }


__all__ = [
    "QUANT_CLAIM_KEYWORDS",
    "QUANT_CORE_FIELDS",
    "QUANT_METRIC_LABELS",
    "QUANT_NON_CORE_FIELDS",
    "register_all",
]
