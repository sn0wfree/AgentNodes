"""``compact show`` — print effective compaction config.

Shows every field of ``CompactConfig`` so the user can see exactly
what's in effect. Highlights opencode-aligned defaults and the
derived threshold (from model context) when ``threshold_tokens`` is None.

Usage:
    agentnodes compact show
"""
from __future__ import annotations

from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from agentnodes.interfaces.cli.theme import get_console
from agentnodes.kernel.agent.compact import (
    CompactConfig,
    _resolve_threshold_tokens,
)


def cmd_compact_show(args: Any = None) -> int:
    """Print effective compaction configuration.

    Reads LLMConfig to get model_context_tokens + model_max_output_tokens,
    then resolves the trigger threshold (opencode formula) and prints
    every field of CompactConfig in a Rich table.
    """
    console: Console = get_console()
    cfg = _build_effective_config()
    return _print_config(console, cfg)


def _build_effective_config() -> CompactConfig:
    """Load the user's CompactConfig from llm.json (or defaults)."""
    try:
        from agentnodes.foundation.llm.config import find_llm_config_path, load_config

        config_path = find_llm_config_path()
        config = load_config(config_path=config_path)
        return config.compact_config or CompactConfig()
    except Exception:
        # If llm.json is missing or invalid, fall back to defaults
        return CompactConfig()


def _get_model_context() -> tuple[int | None, int | None]:
    """Return (model_context_tokens, model_max_output_tokens) from LLMConfig."""
    try:
        from agentnodes.foundation.llm.config import find_llm_config_path, load_config

        config_path = find_llm_config_path()
        config = load_config(config_path=config_path)
        return config.model_context_tokens, config.model_max_output_tokens
    except Exception:
        return None, None


def _print_config(console: Console, cfg: CompactConfig) -> int:
    """Render the CompactConfig in a Rich table."""
    model_context, model_max_output = _get_model_context()
    threshold = _resolve_threshold_tokens(cfg, model_context, model_max_output)

    table = Table(
        title="Compact Configuration (effective values)",
        show_header=True,
        header_style="bold cyan",
    )
    table.add_column("Parameter", style="cyan", no_wrap=True)
    table.add_column("Value", style="green")
    table.add_column("Note", style="dim")

    # Master switch
    table.add_row(
        "enabled",
        str(cfg.enabled),
        "master switch",
    )

    # Trigger
    table.add_row(
        "threshold_tokens",
        str(cfg.threshold_tokens) + (" (None = derived)" if cfg.threshold_tokens is None else ""),
        "absolute trigger; None = auto-derive",
    )
    if cfg.threshold_tokens is None:
        # Show the derived value
        table.add_row(
            "  → derived_threshold",
            f"{threshold:,}",
            "opencode: context - max(output, buffer)",
        )
    table.add_row(
        "compaction_buffer_tokens",
        f"{cfg.compaction_buffer_tokens:,}",
        "opencode DEFAULT_BUFFER",
    )

    # Layer ratios
    table.add_row("", "", "")  # separator
    table.add_row(
        "microcompact_ratio",
        f"{cfg.microcompact_ratio}",
        "L1: truncate tool outputs",
    )
    table.add_row(
        "llm_summarize_ratio",
        f"{cfg.llm_summarize_ratio}",
        "L4: LLM-driven summary",
    )
    table.add_row(
        "hard_truncate_ratio",
        f"{cfg.hard_truncate_ratio}",
        "L3: drop oldest messages",
    )
    table.add_row(
        "overflow_ratio",
        f"{cfg.overflow_ratio}",
        "overflow detection",
    )

    # L1 settings
    table.add_row("", "", "")
    table.add_row(
        "microcompact_tool_result_chars",
        f"{cfg.microcompact_tool_result_chars:,}",
        "opencode TOOL_OUTPUT_MAX_CHARS (chars, not tokens)",
    )
    table.add_row(
        "tool_truncate_chars",
        f"{len(cfg.tool_truncate_chars)} tools",
        "per-tool char limits",
    )
    table.add_row(
        "collapse_keep_recent",
        str(cfg.collapse_keep_recent),
        "protect last N tool outputs",
    )

    # L4 settings
    table.add_row("", "", "")
    if cfg.preserve_recent_tokens is None:
        preserve_str = "dynamic (25% of context)"
    else:
        preserve_str = f"{cfg.preserve_recent_tokens:,}"
    table.add_row(
        "preserve_recent_tokens",
        preserve_str,
        "recent budget (opencode DEFAULT_KEEP_TOKENS-like)",
    )
    table.add_row(
        "tail_turns",
        str(cfg.tail_turns),
        "keep last N turns verbatim",
    )
    table.add_row(
        "summary_output_tokens",
        f"{cfg.summary_output_tokens:,}",
        "cap; actual = min(model_output, this)",
    )
    table.add_row(
        "enable_incremental_summary",
        str(cfg.enable_incremental_summary),
        "use previous summary as base",
    )

    console.print(table)

    # Context section
    if model_context or model_max_output:
        console.print()
        console.print(
            Panel(
                f"context_tokens: {model_context or '(not set)'}\n"
                f"max_output_tokens: {model_max_output or '(not set)'}",
                title="Model context",
                border_style="dim",
            )
        )

    return 0


__all__ = ["cmd_compact_show"]
