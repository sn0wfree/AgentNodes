"""W2 — merged CLI groups: serve / backtest / check.

Maps user-facing task commands onto the existing handlers:

    serve [--api-only] [--webui-only]
    backtest run|rerun|replay   →  cmd_run / cmd_evaluate / cmd_reproduce
    check factor|run|accept     →  cmd_validate / validate-run / accept

Legacy top-level names (run/evaluate/reproduce/validate/api/webui)
remain registered for compatibility; new code and docs should use these.
"""
from __future__ import annotations

import argparse


def add_backtest_parser(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    p = subparsers.add_parser(
        "backtest",
        help="回测：run=新跑 / rerun=复跑 strategy.py / replay=复现 run",
    )
    sub = p.add_subparsers(dest="backtest_command", help="回测子命令")

    run_p = sub.add_parser("run", help="运行回测（原 run）")
    run_p.add_argument("path", nargs="?", default=".", help="工作区路径")
    run_p.add_argument("--strategy", "-s", help="策略名称")
    run_p.add_argument("--action", "-a", help="行动类型")
    run_p.add_argument("--description", "-d", help="描述")
    run_p.add_argument("--timeout", "-t", type=int, default=300, help="超时 (秒)")

    rerun_p = sub.add_parser("rerun", help="复跑当前 strategy.py（原 evaluate）")
    rerun_p.add_argument("path", nargs="?", default=".", help="工作区路径")
    rerun_p.add_argument("--strategy", "-s", help="策略名称")
    rerun_p.add_argument("--description", "-d", default="", help="描述")
    rerun_p.add_argument("--timeout", "-t", type=int, default=300, help="超时 (秒)")

    replay_p = sub.add_parser("replay", help="复现实验（原 reproduce）")
    replay_p.add_argument("path", nargs="?", default=".", help="工作区路径")
    replay_p.add_argument("run", nargs="?", help="Run 名称 (例如: run_0001)")
    replay_p.add_argument("--strategy", "-s", help="策略名称")
    return p


def add_check_parser(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    p = subparsers.add_parser(
        "check",
        help="校验：factor=因子 / run=回测产物 / accept=策略验收",
    )
    sub = p.add_subparsers(dest="check_command", help="校验子命令")

    fac = sub.add_parser("factor", help="验证因子（原 validate）")
    fac.add_argument("path", nargs="?", default=".", help="工作区路径")
    fac.add_argument("--strategy", "-s", help="策略名称")
    fac.add_argument("--factor", "-f", help="因子表达式")
    fac.add_argument("--source", help="因子来源")

    run = sub.add_parser("run", help="对回测 run_dir 跑验证（原 validate-run）")
    run.add_argument("run_dir", nargs="?", help="run 目录")
    # validate-run 挂在 validation.cli；此处透传额外参数
    run.add_argument("extra", nargs=argparse.REMAINDER, help="透传给 validate-run")

    acc = sub.add_parser("accept", help="策略验收决策（原 accept）")
    acc.add_argument("run_dir", nargs="?", help="run 目录")
    acc.add_argument("extra", nargs=argparse.REMAINDER, help="透传给 accept")
    return p


def dispatch_backtest(args: argparse.Namespace) -> int:
    """Route backtest subcommands to legacy handlers."""
    from .. import cmd_evaluate, cmd_reproduce, cmd_run

    # Promote merged flags into the legacy Namespace shape.
    kind = getattr(args, "backtest_command", None)
    if kind == "run":
        return cmd_run(args)
    if kind == "rerun":
        return cmd_evaluate(args)
    if kind == "replay":
        return cmd_reproduce(args)
    return 0


def dispatch_check(args: argparse.Namespace) -> int:
    """Route check subcommands to legacy handlers."""
    from .. import cmd_validate

    kind = getattr(args, "check_command", None)
    if kind == "factor":
        return cmd_validate(args)
    if kind == "run":
        return _run_legacy(["validate-run", *_extra(args)])
    if kind == "accept":
        return _run_legacy(["accept", *_extra(args)])
    return 0


def _extra(args: argparse.Namespace) -> list[str]:
    extra = list(getattr(args, "extra", []) or [])
    # argparse.REMAINDER keeps a leading '--' sometimes
    return [x for x in extra if x != "--"]


def _run_legacy(argv: list[str]) -> int:
    """Invoke the legacy argparse entry with a rewritten argv."""
    import sys

    from .. import main as cli_main

    old = sys.argv
    try:
        sys.argv = ["quantnodes-research", *argv]
        return int(cli_main() or 0)
    finally:
        sys.argv = old


def dispatch_serve(args: argparse.Namespace) -> int:
    """serve: webui (default) or api-only."""
    from .server import cmd_api_serve, cmd_webui_serve

    if getattr(args, "api_only", False):
        return cmd_api_serve(args)
    return cmd_webui_serve(args)
