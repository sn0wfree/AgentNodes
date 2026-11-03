"""Goal templates for common finance research patterns.

Each template defines a name, description, default criteria list, and
risk tier.  Users can select a template when starting a goal:

    /goal start "研究动量因子" --template factor_research

The template's criteria replace the default 3-criterion list.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class GoalTemplate:
    """A reusable research goal template."""

    name: str
    description: str
    criteria: list[str]
    risk_tier: str = "research_general"


# ── Predefined templates ──────────────────────────────────────

FACTOR_RESEARCH = GoalTemplate(
    name="因子研究",
    description="研究量化因子的有效性和稳健性",
    criteria=[
        "定义因子逻辑和标的池",
        "收集历史数据并回测因子表现",
        "分析因子衰减和稳健性",
        "记录风险提示和非建议边界",
    ],
    risk_tier="research_general",
)

MARKET_ANALYSIS = GoalTemplate(
    name="市场分析",
    description="分析市场趋势和板块轮动",
    criteria=[
        "定义分析维度和时间框架",
        "收集市场数据和指标",
        "形成分析结论和观点",
        "记录数据来源和局限性",
    ],
    risk_tier="market_specific_short_term",
)

RISK_ASSESSMENT = GoalTemplate(
    name="风险评估",
    description="评估投资组合或策略的风险",
    criteria=[
        "定义风险评估范围和指标",
        "收集风险因子数据",
        "计算风险指标和压力测试",
        "记录风险提示和边界条件",
    ],
    risk_tier="research_general",
)

STRATEGY_REVIEW = GoalTemplate(
    name="策略评审",
    description="评审交易策略的逻辑和表现",
    criteria=[
        "定义策略逻辑和假设",
        "收集策略历史表现数据",
        "分析策略的适用条件和局限",
        "记录风险提示和非建议边界",
    ],
    risk_tier="research_general",
)

PORTFOLIO_REVIEW = GoalTemplate(
    name="组合评审",
    description="评审投资组合的配置和表现",
    criteria=[
        "定义组合范围和基准",
        "收集持仓和收益数据",
        "分析配置合理性和风险敞口",
        "记录再平衡建议和风险提示",
    ],
    risk_tier="research_general",
)


# ── Template registry ──────────────────────────────────────────

TEMPLATES: dict[str, GoalTemplate] = {
    "factor_research": FACTOR_RESEARCH,
    "market_analysis": MARKET_ANALYSIS,
    "risk_assessment": RISK_ASSESSMENT,
    "strategy_review": STRATEGY_REVIEW,
    "portfolio_review": PORTFOLIO_REVIEW,
}


def get_template(key: str) -> GoalTemplate | None:
    """Look up a template by key. Returns None if not found."""
    return TEMPLATES.get(key)


def list_templates() -> dict[str, GoalTemplate]:
    """Return all registered templates."""
    return dict(TEMPLATES)


def template_names() -> list[str]:
    """Return sorted list of template keys."""
    return sorted(TEMPLATES.keys())


__all__ = [
    "GoalTemplate",
    "get_template",
    "list_templates",
    "template_names",
    "TEMPLATES",
    "FACTOR_RESEARCH",
    "MARKET_ANALYSIS",
    "RISK_ASSESSMENT",
    "STRATEGY_REVIEW",
    "PORTFOLIO_REVIEW",
]
