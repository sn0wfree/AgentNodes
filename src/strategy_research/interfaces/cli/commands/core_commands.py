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


@cli_command(
    "run",
    help="运行回测",
    parents=[_LLM_PARENT],
    add=lambda p: (
        p.add_argument("path", nargs="?", default=".", help="工作区路径"),
        p.add_argument("--strategy", "-s", help="策略名称"),
        p.add_argument("--action", "-a", help="行动类型"),
        p.add_argument("--description", "-d", help="描述"),
        p.add_argument("--timeout", "-t", type=int, default=300, help="超时时间 (秒)"),
    ),
)
def cmd_run(args: argparse.Namespace) -> int:
    """Run the research/backtest pipeline."""
    from strategy_research.interfaces.cli import cmd_run as _impl
    return _impl(args)


# ── evaluate ────────────────────────────────────────────────────────


@cli_command(
    "evaluate",
    help="复跑当前 strategy.py",
    parents=[_LLM_PARENT],
    add=lambda p: (
        p.add_argument("path", nargs="?", default=".", help="工作区路径"),
        p.add_argument("--strategy", "-s", help="策略名称"),
        p.add_argument("--description", "-d", default="", help="描述"),
        p.add_argument("--timeout", "-t", type=int, default=300, help="超时时间 (秒)"),
    ),
)
def cmd_evaluate(args: argparse.Namespace) -> int:
    """Evaluate a strategy's results."""
    from strategy_research.interfaces.cli import cmd_evaluate as _impl
    return _impl(args)


# ── preflight ───────────────────────────────────────────────────────


@cli_command(
    "preflight",
    help="启动前环境检查",
    add=lambda p: p.add_argument("path", nargs="?", default=".", help="工作区路径"),
)
def cmd_preflight(args: argparse.Namespace) -> int:
    """Pre-flight checks before running."""
    from strategy_research.interfaces.cli import cmd_preflight as _impl
    return _impl(args)


# ── validate ────────────────────────────────────────────────────────


@cli_command(
    "validate",
    help="验证因子",
    add=lambda p: (
        p.add_argument("path", nargs="?", default=".", help="工作区路径"),
        p.add_argument("--strategy", "-s", help="策略名称"),
        p.add_argument("--factor", "-f", help="因子表达式"),
        p.add_argument("--source", help="因子来源"),
    ),
)
def cmd_validate(args: argparse.Namespace) -> int:
    """Validate workspace or run outputs."""
    from strategy_research.interfaces.cli import cmd_validate as _impl
    return _impl(args)


# ── list ────────────────────────────────────────────────────────────


@cli_command(
    "list",
    help="列出实验",
    add=lambda p: (
        p.add_argument("path", nargs="?", default=".", help="工作区路径"),
        p.add_argument("--strategy", "-s", help="策略名称"),
        p.add_argument("--limit", "-l", type=int, default=20, help="显示数量"),
    ),
)
def cmd_list(args: argparse.Namespace) -> int:
    """List strategies or runs in the workspace."""
    from strategy_research.interfaces.cli import cmd_list as _impl
    return _impl(args)


# ── import ──────────────────────────────────────────────────────────


@cli_command(
    "import",
    help="导入价格数据",
    add=lambda p: (
        p.add_argument("path", nargs="?", default=".", help="工作区路径"),
        p.add_argument("--strategy", "-s", required=True, help="策略名称"),
        p.add_argument(
            "--source", required=True,
            choices=["csv", "parquet", "sample", "tushare", "ifind", "fred", "akshare", "auto"],
            help="数据源",
        ),
        p.add_argument("--file", "-f", help="数据文件路径 (csv/parquet)"),
        p.add_argument("--codes", "-c", help="资产代码列表，逗号分隔 (API 数据源)"),
        p.add_argument("--start-date", default="2020-01-01", help="开始日期 (API 数据源)"),
        p.add_argument("--end-date", default="2025-12-31", help="结束日期 (API 数据源)"),
        p.add_argument("--incremental", action="store_true", default=True, help="增量更新 (默认开启)"),
        p.add_argument("--no-incremental", dest="incremental", action="store_false", help="全量替换"),
        p.add_argument("--date-column", default="date", help="日期列名 (csv)"),
        p.add_argument("--price-column", default="close", help="价格列名 (csv)"),
        p.add_argument("--asset-column", help="资产代码列名 (csv, 宽格式不需要)"),
        p.add_argument("--n-assets", type=int, default=10, help="示例资产数量 (sample)"),
        p.add_argument("--n-days", type=int, default=504, help="示例天数 (sample)"),
    ),
)
def cmd_import(args: argparse.Namespace) -> int:
    """Import data (OHLCV, factors, ...) into the workspace."""
    from strategy_research.interfaces.cli import cmd_import as _impl
    return _impl(args)


# ── autoresearch 命令已随 apps/autoresearch 移除（2026-11-05 批次 3）──

