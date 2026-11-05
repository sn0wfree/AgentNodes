"""Named toolsets for Agent runs (A2).

Single source of truth for role / task → tool whitelist. Replaces the
scattered ``_ROLE_TOOL_WHITELIST`` in role_factory and ad-hoc
``allowed_tools`` lists.

Fail-safe: unknown toolset name resolves to the minimal read-only set.
``None`` / ``\"full\"`` means "do not filter".
"""

from __future__ import annotations

from typing import Iterable

from .tools import ToolRegistry

# Name → tool names. Keep lists small: every entry costs prompt tokens.
TOOLSETS: dict[str, list[str]] = {
    # Absolute minimum: look around + ask for docs.
    "minimal": [
        "read",
        "list",
        "tool_help",
    ],
    # Generic chat assistant.
    "chat": [
        "read",
        "list",
        "write",
        "compute_factor",
        "factor_analysis",
        "get_market_data",
        "search_symbol",
        "tool_help",
    ],
    # Study-round researcher (read-heavy).
    "researcher": [
        "read",
        "list_history",
        "factor_analysis",
        "websearch",
        "webfetch",
        "get_market_data",
        "search_symbol",
        "show_chart",
        "tool_help",
    ],
    "data_quality": [
        "read",
        "websearch",
        "webfetch",
        "get_market_data",
        "list_data_sources",
        "check_data",
        "clean_data",
        "run_bg_command",
        "tool_help",
    ],
    "factor_analyst": [
        "read",
        "compute_factor",
        "factor_analysis",
        "factor_cross_sectional_analysis",
        "factor_quintile_returns",
        "factor_ic_decay",
        "factor_turnover",
        "get_market_data",
        "tool_help",
    ],
    "strategist": [
        "read",
        "write",
        "run_backtest",
        "git_diff",
        "websearch",
        "webfetch",
        "get_market_data",
        "show_chart",
        "show_report",
        "tool_help",
    ],
    "portfolio_construction": [
        "read",
        "get_market_data",
        "tool_help",
    ],
    "risk_controller": [
        "read",
        "factor_analysis",
        "get_market_data",
        "tool_help",
    ],
    "attribution_analyst": [
        "read",
        "factor_analysis",
        "tool_help",
    ],
    "anti_overfit_analyst": [
        "read",
        "list_history",
        "factor_analysis",
        "tool_help",
    ],
    "backtest_diagnostics": [
        "read",
        "run_backtest",
        "git_diff",
        "strategy_compare",
        "drawdown_analysis",
        "benchmark_comparison",
        "show_chart",
        "show_report",
        "tool_help",
    ],
    "critic": [
        "read",
        "list_history",
        "tool_help",
    ],
    "planner": [
        "read",
        "websearch",
        "webfetch",
        "list_goals",
        "get_market_data",
        "tool_help",
    ],
    "evaluator": [
        "read",
        "list_history",
        "factor_analysis",
        "tool_help",
    ],
    "study_reviewer": [
        "read",
        "tool_help",
    ],
    "study_collector": [
        "read",
        "websearch",
        "webfetch",
        "list_history",
        "tool_help",
    ],
}

# Unfiltered pass-through.
FULL = "full"
MINIMAL = "minimal"


def resolve_toolset(
    name_or_tools: str | Iterable[str] | None,
) -> list[str] | None:
    """Resolve a toolset spec to a concrete allow-list.

    - ``None`` / ``\"full\"`` → None (no filtering)
    - ``\"<known-name>\"``   → that list
    - unknown name          → minimal (fail-safe)
    - iterable of names     → that list (explicit override)
    """
    if name_or_tools is None:
        return None
    if isinstance(name_or_tools, str):
        key = name_or_tools.strip().lower()
        if key in ("", FULL, "all", "none"):
            return None
        if key in TOOLSETS:
            return list(TOOLSETS[key])
        return list(TOOLSETS[MINIMAL])
    return [str(x) for x in name_or_tools]


def apply_toolset(
    registry: ToolRegistry,
    toolset: str | Iterable[str] | None,
    *,
    deny: Iterable[str] | None = None,
) -> ToolRegistry:
    """Return a registry filtered to the toolset (then minus ``deny``)."""
    allow = resolve_toolset(toolset)
    deny_list = [str(x) for x in (deny or [])]
    if allow is None and not deny_list:
        return registry
    return registry.restricted(allow=allow, deny=deny_list or None)


def toolset_names() -> list[str]:
    """All defined toolset names (sorted)."""
    return sorted(TOOLSETS)


__all__ = [
    "FULL",
    "MINIMAL",
    "TOOLSETS",
    "apply_toolset",
    "resolve_toolset",
    "toolset_names",
]
