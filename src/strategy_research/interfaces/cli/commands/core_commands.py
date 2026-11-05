"""Core CLI commands migrated to the registry pattern (Phase 2.2).

This module registers the simple, top-level CLI commands (init / status /
reproduce / run / evaluate / preflight / validate / list / import /
autoresearch) using the new ``@cli_command`` decorator.

The dispatch happens automatically via ``cli.commands.registry.dispatch``;
the legacy ``cli.main()``'s long ``elif args.command == "..."`` chain is
replaced by a single ``dispatch(args, parser=parser)`` call.

The cmd_* functions below were extracted from ``cli/__init__.py`` with no
behaviour changes. They are referenced from there via
``from strategy_research.interfaces.cli.commands.core_commands import ...`` (the
imports at the top of cli/__init__.py).
"""

from __future__ import annotations

import argparse
import logging

from .registry import cli_command

logger = logging.getLogger(__name__)

# Shared parent for LLM-flagged commands (run / evaluate / autoresearch).
from strategy_research.interfaces.cli.llm_config import _LLM_PARENT  # noqa: E402

# ── init ────────────────────────────────────────────────────────────


@cli_command(
    "init",
    help="5-step TTY wizard: provider → model → API key → timeout → (Tushare)",
    description=(
        "Run the credentials wizard that writes "
        "~/.quantnodes/strategy_research/.env. Mirrors "
        "vibe-trading's init UX (single-responsibility: no workspace "
        "scaffolding, no auto backtest, no auto git init). Use `--force` "
        "to overwrite an existing .env."
    ),
    add=lambda p: p.add_argument(
        "--force", action="store_true",
        help="Overwrite an existing ~/.quantnodes/strategy_research/.env",
    ),
)
def cmd_init(args: argparse.Namespace) -> int:
    """5-step credentials wizard."""
    from strategy_research.interfaces.cli import cmd_run_onboarding
    return cmd_run_onboarding(args)


# ── status ──────────────────────────────────────────────────────────


@cli_command(
    "status",
    help="查看工作区状态",
    add=lambda p: p.add_argument("path", nargs="?", default=".", help="工作区路径"),
)
def cmd_status(args: argparse.Namespace) -> int:
    """Show workspace/research status."""
    from strategy_research.interfaces.cli import cmd_status as _impl
    return _impl(args)


# ── reproduce ───────────────────────────────────────────────────────


@cli_command(
    "reproduce",
    help="复现实验",
    add=lambda p: (
        p.add_argument("path", nargs="?", default=".", help="工作区路径"),
        p.add_argument("run", nargs="?", help="Run 名称 (例如: run_0001)"),
        p.add_argument("--strategy", "-s", help="策略名称"),
    ),
)
def cmd_reproduce(args: argparse.Namespace) -> int:
    """Reproduce a previous experiment run."""
    from strategy_research.interfaces.cli import cmd_reproduce as _impl
    return _impl(args)


# ── run ─────────────────────────────────────────────────────────────


# ── evaluate ────────────────────────────────────────────────────────


# ── preflight 已随 apps/preflight 移除（2026-11-05 批次 5）────────────


# ── validate ────────────────────────────────────────────────────────


# ── list ────────────────────────────────────────────────────────────


# ── import 已随 apps/data_import 移除（2026-11-05 批次 5）─────────────


# ── autoresearch 命令已随 apps/autoresearch 移除（2026-11-05 批次 3）──

