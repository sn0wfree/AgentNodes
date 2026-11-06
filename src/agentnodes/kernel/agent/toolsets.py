"""Named toolsets for Agent runs (A2).

Single source of truth for role / task → tool whitelist.

**只收通用能力**（2026-11-06 批次 4 · 三步走第一步）：本表是 kernel 内置的
"标准库"——类比 Python 的 ``builtins``，只放平台无关、任何 agent 都可能用到的
工具。业务域工具（量化/回测/因子）不在此表，它们由 L2 插件自带工具清单，
在装配期经 ``CapabilityRegistry`` 注入（见 ``apps/agent_plugins/``）。

历史上的 10 个业务角色（researcher / data_quality / factor_analyst /
strategist / portfolio_construction / risk_controller / attribution_analyst /
anti_overfit_analyst / backtest_diagnostics / evaluator）已迁到
``apps/agent_plugins/quant.py``，本表不再持有它们。

Fail-safe: unknown toolset name resolves to the minimal read-only set.
``None`` / ``\\"full\\"`` means "do not filter".
"""

from __future__ import annotations

from typing import Iterable

from .tools import ToolRegistry

# Name → tool names. Keep lists small: every entry costs prompt tokens.
#
# **只收通用工具**。此处出现的每个名字都必须能在 ``build_default_registry()``
# 里找到（`scripts/arch/check_tool_refs.py` 强制）。
TOOLSETS: dict[str, list[str]] = {
    # Absolute minimum: look around + ask for docs.
    "minimal": [
        "read",
        "list",
        "tool_help",
    ],
    # Generic chat assistant: read, write, browse, run commands.
    "chat": [
        "read",
        "list",
        "write",
        "websearch",
        "webfetch",
        "shell",
        "run_bg_command",
        "tool_help",
    ],
    # Read-only exploration (code/data archaeology).
    "explorer": [
        "read",
        "list",
        "list_history",
        "git_diff",
        "websearch",
        "webfetch",
        "tool_help",
    ],
    # Code editing / implementation work.
    "coder": [
        "read",
        "list",
        "write",
        "git_diff",
        "list_history",
        "shell",
        "run_bg_command",
        "todowrite",
        "tool_help",
    ],
    # Planning: think about what to do next, no side effects.
    "planner": [
        "read",
        "list",
        "list_history",
        "todowrite",
        "tool_help",
    ],
    # Reviewing someone else's work.
    "critic": [
        "read",
        "list",
        "list_history",
        "git_diff",
        "tool_help",
    ],
    # Delegating to sub-agents.
    "orchestrator": [
        "read",
        "list",
        "task",
        "todowrite",
        "skill",
        "list_skills",
        "tool_help",
    ],
    # Default full-capability agent (everything the registry offers).
    "general": [
        "read",
        "list",
        "write",
        "git_diff",
        "list_history",
        "websearch",
        "webfetch",
        "shell",
        "run_bg_command",
        "task",
        "skill",
        "list_skills",
        "todowrite",
        "show_chart",
        "show_report",
        "read_document",
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
