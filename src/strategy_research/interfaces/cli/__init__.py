"""quantnodes-research CLI — 策略研究工作区管理。

Phase 2.2 refactor: the simple top-level commands (init / status / run /
evaluate / preflight / validate / list / import / autoresearch / reproduce)
have been migrated to ``cli.commands.core_commands`` and registered via
the ``@cli_command`` decorator. ``main()`` now builds the parser, wires
all registered commands, then dispatches via ``registry.dispatch``.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

# Core commands: imports this side-effect to register them in the dispatcher.
from .commands import core_commands  # noqa: F401  (registration side effect)
from .commands import llm as _llm_cmd  # noqa: F401  (registration side effect)
# autoresearch 命令已随 apps/autoresearch 移除（2026-11-05 批次 3）
from .commands.registry import ensure_builtin_commands as _ensure_builtin
from .commands.registry import wire_commands as _wire
from .commands.server import (
    cmd_api_serve,
    cmd_mcp_list_tools,
    cmd_mcp_serve,
    cmd_webui_serve,
)
from .commands.session import (
    cmd_session_delete,
    cmd_session_list,
    cmd_session_search,
    cmd_session_show,
)
from .commands.skills import cmd_skills_list, cmd_skills_search, cmd_skills_show
from .commands.swarm import (
    cmd_swarm_cancel,
    cmd_swarm_inspect,
    cmd_swarm_list,
    cmd_swarm_run,
)
from .llm_config import _cli_overrides_from_args as _cli_overrides_from_args
from .llm_config import _cmd_llm_list_profiles
from .llm_config import build_llm_config as build_llm_config

# ============================================================
# init 命令 — 5 步 TTY 向导，仿 vibe-trading cli/onboard.py:361
# ============================================================

def cmd_run_onboarding(args: argparse.Namespace) -> int:
    """`quantnodes-research init` — 5 步凭证 TTY 向导。

    写入：
    - LLM 结构化字段 → ``~/.quantnodes/llm.json``（保留其它顶层 key）
    - 凭证 token → ``~/.quantnodes/.env``（chmod 0600）
    """
    from dotenv import load_dotenv
    from rich.panel import Panel
    from rich.prompt import Confirm
    from rich.table import Table

    from strategy_research.interfaces.cli.onboard import (
        _QUANTNODES_DOTENV_PATH,
        _QUANTNODES_LLM_JSON_PATH,
        is_onboarded,
        run_onboarding,
    )
    from strategy_research.interfaces.cli.theme import get_console

    console = get_console()

    if is_onboarded() and not getattr(args, "force", False):
        console.print(
            f"[yellow]Config already exists:[/yellow] {_QUANTNODES_LLM_JSON_PATH}"
        )
        if not Confirm.ask("Overwrite?", default=False):
            console.print("[dim]Aborted.[/dim]")
            return 0
        _wipe_existing_env()

    try:
        written = run_onboarding()
    except RuntimeError as exc:
        # TTY branch raises if stdin/stdout aren't real terminals (e.g.
        # `init --force > log.txt`). Translate to a friendly message
        # rather than dumping a traceback.
        msg = str(exc)
        if "TTY" in msg or "non-TTY" in msg or "inputs" in msg:
            console.print(
                "[yellow]init wizard requires an interactive terminal; "
                "redirecting or piping prevents it from running.[/yellow]"
            )
            return 1
        raise
    if written is None:
        return 1

    # Load the dotenv sidecar so LLM_API_KEY (and TUSHARE_TOKEN) enter
    # os.environ before the bridge resolves "env:LLM_API_KEY" in llm.json.
    load_dotenv(_QUANTNODES_DOTENV_PATH, override=True)

    panel = Table.grid(expand=True)
    panel.add_column(width=10, style="dim")
    panel.add_column(ratio=1)
    panel.add_row("LLM config", f"[cyan]{written}[/cyan]")
    panel.add_row("Tokens",     f"[cyan]{_QUANTNODES_DOTENV_PATH}[/cyan]")
    panel.add_row("Run",        "[bold]quantnodes-research[/bold]")
    console.print(Panel(panel, title="Setup complete",
                        border_style="green", padding=(0, 1)))
    return 0


def _wipe_existing_env() -> None:
    """On overwrite, also nuke the now-orphaned
    ``~/.quantnodes/strategy_research/`` directory that the wizard no
    longer writes to. Idempotent.
    """
    from strategy_research.interfaces.cli.onboard import _QUANTNODES_LLM_JSON_PATH
    legacy_dir = _QUANTNODES_LLM_JSON_PATH.parent / "strategy_research"
    if not legacy_dir.exists():
        return
    import shutil
    try:
        shutil.rmtree(legacy_dir)
    except OSError:
        pass


def cmd_status(args: argparse.Namespace) -> int:
    """执行 status 命令。"""
    path = Path(args.path).resolve()

    # 检查工作区
    if not (path / "config.yaml").exists():
        print(f"❌ 不是有效的工作区: {path}")
        return 1

    # 读取 config.yaml
    config_path = path / "config.yaml"
    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    workspace_name = config.get("workspace", {}).get("name", "unknown")
    strategies = config.get("strategies", [])

    print(f"📁 工作区: {workspace_name}")
    print(f"   路径: {path}")
    print()

    # 列出策略
    print(f"📊 策略 ({len(strategies)}):")
    for s in strategies:
        sname = s.get("name", "unknown")
        stype = s.get("type", "unknown")
        goal = s.get("goal_metric", "unknown")
        print(f"  - {sname} (类型: {stype}, 目标: {goal})")

        # 统计 runs
        runs_dir = path / "strategies" / sname / "runs"
        if runs_dir.exists():
            runs = [d for d in runs_dir.iterdir() if d.is_dir() and d.name.startswith("run_")]
            if runs:
                # 读取 results.tsv
                results_file = runs_dir / "results.tsv"
                if results_file.exists():
                    with open(results_file, "r", encoding="utf-8") as f:
                        lines = f.readlines()
                    total = len(lines) - 1  # 减去 header
                    keeps = sum(1 for l in lines[1:] if "\tkeep\t" in l)
                    discards = sum(1 for l in lines[1:] if "\tdiscard\t" in l)
                    print(f"    实验: {total} 轮 (keep: {keeps}, discard: {discards})")

                    # 最后一轮
                    if total > 0:
                        last = lines[-1].strip().split("\t")
                        if len(last) >= 5:
                            print(f"    最新: {last[0]} | {last[2]} | calmar={last[3]} | {last[-1]}")
                else:
                    print(f"    实验: {len(runs)} 个目录")
            else:
                print("    实验: 无")
        else:
            print("    实验: 无 runs 目录")

    # DuckDB 状态
    db_path = path / "data.duckdb"
    if db_path.exists():
        print(f"\n🗄️  DuckDB: {db_path}")
        try:
            import duckdb
            conn = duckdb.connect(str(db_path), read_only=True)
            tables = conn.execute("SHOW TABLES").fetchall()
            for t in tables:
                table_name = t[0]
                # Use parameterized query with identifier quoting to prevent SQL injection
                row = conn.execute(
                    f"SELECT COUNT(*) FROM \"{table_name}\""
                ).fetchone()
                count = row[0] if row else 0
                print(f"  - {table_name}: {count} 行")
            conn.close()
        except Exception as e:
            print(f"  ⚠️  读取失败: {e}")
    else:
        print("\n🗄️  DuckDB: 不存在")

    return 0


def cmd_reproduce(args: argparse.Namespace) -> int:
    """执行 reproduce 命令。"""
    path = Path(args.path).resolve()

    # 检查工作区
    if not (path / "config.yaml").exists():
        print(f"❌ 不是有效的工作区: {path}")
        return 1

    # 读取 config.yaml
    config_path = path / "config.yaml"
    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    strategy_name = args.strategy
    if not strategy_name:
        strategy_name = config.get("workspace", {}).get("default_strategy")
        if not strategy_name:
            print("❌ 未指定策略名称，请使用 --strategy <name>")
            return 1

    run_name = args.run
    if not run_name:
        print("❌ 请指定 run 名称，例如: quantnodes-research reproduce <path> run_0001")
        return 1

    # 检查 run 目录
    run_dir = path / "strategies" / strategy_name / "runs" / run_name
    if not run_dir.exists():
        print(f"❌ Run 目录不存在: {run_dir}")
        return 1

    # 检查 strategy.py 快照
    snapshot = run_dir / "strategy.py"
    if not snapshot.exists():
        print(f"❌ 快照文件不存在: {snapshot}")
        return 1

    # 复制快照到策略目录
    strategy_dir = path / "strategies" / strategy_name
    target = strategy_dir / "strategy.py"

    # 备份当前 strategy.py
    import shutil
    backup = strategy_dir / "strategy.py.bak"
    if target.exists():
        shutil.copy2(target, backup)
        print(f"✓ 备份当前配置: {backup}")

    # 复制快照
    shutil.copy2(snapshot, target)
    print(f"✓ 恢复快照: {run_name}/strategy.py → strategy.py")

    # 显示 metrics.json (如果存在)
    metrics_file = run_dir / "metrics.json"
    if metrics_file.exists():
        import json
        with open(metrics_file, "r", encoding="utf-8") as f:
            metrics = json.load(f)
        print(f"\n📊 实验指标 ({run_name}):")
        for k, v in metrics.items():
            if k not in ("run", "commit", "timestamp"):
                print(f"  {k}: {v}")

    print(f"\n✅ 已恢复到 {run_name}")
    print(f"   运行策略: cd {strategy_dir} && python strategy.py")
    return 0


# ============================================================

def _build_parser() -> tuple[argparse.ArgumentParser, dict[str, argparse.ArgumentParser]]:
    """Build the top-level CLI argument parser."""
    parser = argparse.ArgumentParser(
        prog="quantnodes-research",
        description="通用策略自动研究框架",
    )

    # ── Top-level LLM flags (apply globally) ────────────
    parser.add_argument(
        "--llm-list-profiles",
        action="store_true",
        help="列出所有 LLM profile 后退出 (调试用)",
    )

    subparsers = parser.add_subparsers(dest="command", help="命令")

    # init/status/reproduce/run/evaluate/preflight/validate/list/import/
    # autoresearch/llm — registered once via @cli_command in
    # commands/core_commands.py + commands/llm.py; wired below by _wire().

    # session
    session_parser = subparsers.add_parser("session", help="会话管理")
    hangs_parser = subparsers.add_parser("hangs", help="卡死防护事件报告")
    hangs_parser.add_argument("--hours", type=float, default=24,
                              help="回看窗口（小时），默认 24")
    hangs_parser.add_argument("--limit", "-l", type=int, default=50,
                              help="最近事件条数上限，默认 50")
    session_subparsers = session_parser.add_subparsers(dest="session_command", help="会话命令")

    # session stats
    session_stats_parser = session_subparsers.add_parser("stats", help="查看写入统计")
    session_stats_parser.add_argument("--recent", "-r", type=int, default=10, help="显示最近 N 条记录")

    # session list
    session_list_parser = session_subparsers.add_parser("list", help="列出会话")
    session_list_parser.add_argument("--limit", "-l", type=int, default=20, help="显示数量")

    # session show
    session_show_parser = session_subparsers.add_parser("show", help="显示会话详情")
    session_show_parser.add_argument("session_id", help="会话 ID")

    # session search
    session_search_parser = session_subparsers.add_parser("search", help="搜索消息")
    session_search_parser.add_argument("query", help="搜索关键词")
    session_search_parser.add_argument("--limit", "-l", type=int, default=20, help="返回数量")

    # session delete
    session_delete_parser = session_subparsers.add_parser("delete", help="删除会话")
    session_delete_parser.add_argument("session_id", help="会话 ID")

    # skills
    skills_parser = subparsers.add_parser("skills", help="技能管理")
    skills_subparsers = skills_parser.add_subparsers(dest="skills_command", help="技能命令")

    skills_list_parser = skills_subparsers.add_parser("list", help="列出所有技能")
    skills_list_parser.add_argument("--category", "-c", help="按类别筛选")

    skills_show_parser = skills_subparsers.add_parser("show", help="显示技能内容")
    skills_show_parser.add_argument("name", help="技能名称")

    skills_search_parser = skills_subparsers.add_parser("search", help="搜索技能")
    skills_search_parser.add_argument("query", help="搜索关键词")

    # swarm
    swarm_parser = subparsers.add_parser("swarm", help="多智能体协同")
    swarm_subparsers = swarm_parser.add_subparsers(dest="swarm_command", help="swarm 命令")

    swarm_subparsers.add_parser("list", help="列出所有 preset")

    swarm_inspect_parser = swarm_subparsers.add_parser("inspect", help="显示 preset 结构")
    swarm_inspect_parser.add_argument("name", help="preset 名称")

    swarm_run_parser = swarm_subparsers.add_parser("run", help="执行 swarm preset")
    swarm_run_parser.add_argument("name", help="preset 名称")
    swarm_run_parser.add_argument("--workspace", "-w", required=True, help="工作区路径")
    swarm_run_parser.add_argument("--task", "-t", default="", help="任务描述")

    swarm_cancel_parser = swarm_subparsers.add_parser("cancel", help="取消运行中的 swarm")
    swarm_cancel_parser.add_argument("run_id", help="运行 ID")

    # mcp
    mcp_parser = subparsers.add_parser("mcp", help="MCP 服务器")
    mcp_subparsers = mcp_parser.add_subparsers(dest="mcp_command", help="MCP 命令")

    mcp_serve_parser = mcp_subparsers.add_parser("serve", help="启动 MCP 服务器")
    mcp_serve_parser.add_argument("--transport", choices=["stdio", "sse"], default="stdio", help="传输方式")
    mcp_serve_parser.add_argument("--port", type=int, default=8900, help="SSE 端口")

    mcp_subparsers.add_parser("list-tools", help="列出所有 MCP 工具")

    # export 子命令已随 apps/export 移除（2026-11-04 做减法批次 2）

    # schedule (P5)
    from strategy_research.kernel.scheduled_research.cli import add_schedule_subparsers
    add_schedule_subparsers(subparsers)

    # goal / hypothesis / validation / portfolio 四组子命令已随对应 apps 包
    # 移除（2026-11-04 做减法批次 2；归档见 testcase/golden/apps/）。
    # engine / accept 两组子命令已随 apps/engine、apps/strategy_acceptance
    # 移除（2026-11-05 做减法批次 4）。

    # api serve
    api_parser = subparsers.add_parser("api", help="HTTP API 服务器")
    api_subparsers = api_parser.add_subparsers(dest="api_command", help="API 命令")
    api_serve_parser = api_subparsers.add_parser("serve", help="启动 API 服务器")
    api_serve_parser.add_argument("--host", default="127.0.0.1", help="监听地址 (默认 127.0.0.1)")
    api_serve_parser.add_argument("--port", type=int, default=8765, help="监听端口 (默认 8765)")
    api_serve_parser.add_argument("--reload", action="store_true", help="热重载 (开发模式)")
    api_serve_parser.add_argument("--workspace", "-w", default=".", help="工作区路径")
    api_serve_parser.add_argument("--goal-db", help="Goal DB 路径 (可选)")
    api_serve_parser.add_argument("--hypotheses-path", help="Hypotheses JSON 路径 (可选)")

    # webui serve
    webui_parser = subparsers.add_parser("webui", help="Web UI 仪表盘")
    webui_subparsers = webui_parser.add_subparsers(dest="webui_command", help="Web UI 命令")
    webui_serve_parser = webui_subparsers.add_parser("serve", help="启动 Web UI 服务器")
    webui_serve_parser.add_argument("--host", default="127.0.0.1", help="监听地址 (默认 127.0.0.1)")
    webui_serve_parser.add_argument("--port", type=int, default=8766, help="监听端口 (默认 8766)")
    webui_serve_parser.add_argument("--reload", action="store_true", help="热重载 (开发模式)")
    webui_serve_parser.add_argument("--workspace", "-w", default=".", help="工作区路径")
    webui_serve_parser.add_argument("--goal-db", help="Goal DB 路径 (可选)")
    webui_serve_parser.add_argument("--hypotheses-path", help="Hypotheses JSON 路径 (可选)")

    # serve (merged W2) — Web UI + API; --api-only for API server only
    serve_parser = subparsers.add_parser(
        "serve",
        help="启动服务（默认 Web UI+API；--api-only 仅 API）",
    )
    serve_parser.add_argument("--host", default="127.0.0.1", help="监听地址 (默认 127.0.0.1)")
    serve_parser.add_argument("--port", type=int, default=8766, help="监听端口 (默认 8766)")
    serve_parser.add_argument("--reload", action="store_true", help="热重载 (开发模式)")
    serve_parser.add_argument("--workspace", "-w", default=".", help="工作区路径")
    serve_parser.add_argument("--goal-db", help="Goal DB 路径 (可选)")
    serve_parser.add_argument("--hypotheses-path", help="Hypotheses JSON 路径 (可选)")
    serve_parser.add_argument("--static-dir", help="前端静态文件目录 (默认 webui/static)")
    serve_parser.add_argument(
        "--api-only",
        action="store_true",
        help="只启动 HTTP API（不挂 SPA）",
    )

    # W2 merged task commands（backtest / check 两组已随批次 4 移除，
    # 2026-11-05；只剩 serve 的 dispatch，注册在下方 serve_parser）

    # ── compact show (opencode-aligned config inspector) ─────────
    compact_parser = subparsers.add_parser(
        "compact", help="compact 配置相关命令"
    )
    compact_subparsers = compact_parser.add_subparsers(dest="compact_command")
    compact_show_parser = compact_subparsers.add_parser(
        "show", help="显示当前生效的 compact 配置（含派生值）"
    )
    compact_show_parser.add_argument(
        "--llm-config", help="llm.json 路径 (默认 ~/.quantnodes/llm.json)"
    )

    # ── Wire registered commands (Phase 2.2) ──────────────
    # All @cli_command-decorated commands (init/status/reproduce/run/
    # evaluate/preflight/validate/list/import) are registered via
    # cli.commands.core_commands on import.
    #
    # ensure_builtin_commands() 兜底：注册依赖 import 副作用，而
    # reset_registry() 清空注册表后模块不会重新执行 —— 没有这一步，
    # 同进程内第二次 _build_parser() 会拿到空 registry。
    _ensure_builtin()
    _wire(subparsers)

    return parser, {
        "session": session_parser,
        "skills": skills_parser,
        "swarm": swarm_parser,
        "mcp": mcp_parser,
        "api": api_parser,
        "webui": webui_parser,
        "compact": compact_parser,
    }


def main() -> int:
    """CLI entry point."""
    parser, subparsers = _build_parser()

    # ── Parse + handle global flags ─────────────────
    args = parser.parse_args()

    # --llm-list-profiles: print and exit early
    if getattr(args, "llm_list_profiles", False):
        return _cmd_llm_list_profiles()

    # ── Dispatch to registered handler if present (Phase 2.2) ───
    # Migrated commands (init / status / reproduce / run / evaluate /
    # preflight / validate / list / import / autoresearch) have
    # args.handler set by argparse's set_defaults(). For everything
    # else we fall through to the legacy if/elif chain below.
    if getattr(args, "handler", None) is not None:
        return args.handler(args)

    dispatcher = {
        "session": _dispatch_session,
        "hangs": _dispatch_hangs,
        "skills": _dispatch_skills,
        "swarm": _dispatch_swarm,
        "mcp": _dispatch_mcp,
        "schedule": _dispatch_schedule,
        "api": _dispatch_api,
        "webui": _dispatch_webui,
        "compact": _dispatch_compact,
        "serve": _dispatch_serve,
        # engine / accept / backtest / check 已随批次 4 移除（2026-11-05）
    }
    handler_fn = dispatcher.get(args.command)
    if handler_fn is not None:
        return handler_fn(args, subparsers)

    parser.print_help()
    return 0


# ── Legacy subcommand dispatch helpers ─────────────────────

def _dispatch_session(args, parsers) -> int:
    """Dispatch the session subcommand."""
    if args.session_command == "stats":
        # B4 D3: retired with the legacy metrics logger.
        print("session stats 已退役（B4 D3）。写入健康请用 EventStore.health_report()。")
        return 0
    elif args.session_command == "list":
        return cmd_session_list(args)
    elif args.session_command == "show":
        return cmd_session_show(args)
    elif args.session_command == "search":
        return cmd_session_search(args)
    elif args.session_command == "delete":
        return cmd_session_delete(args)
    else:
        parsers["session"].print_help()
        return 0


def _dispatch_hangs(args, parsers) -> int:
    """Dispatch the hangs report subcommand."""
    from strategy_research.foundation.observability.hanging_events import HangingEventsStore

    with HangingEventsStore() as store:
        rep = store.report(hours=args.hours, limit=args.limit)

    print(f"=== 卡死防护事件报告 (最近 {args.hours:.0f}h, 共 {rep['total_events']} 条) ===")
    print("-- 按事件类型 --")
    for t, n in sorted(rep["by_type"].items(), key=lambda kv: -kv[1]):
        print(f"  {t:24s} {n}")
    print("-- 按 study --")
    if not rep["by_study"]:
        print("  (无)")
    for row in rep["by_study"]:
        print(f"  {row['study_id']:36s} {row['count']}")
    print("-- 最近事件 --")
    if not rep["recent"]:
        print("  (无)")
    for ev in rep["recent"]:
        print(f"  {ev['created_at_iso'][:19]}  {ev['event_type']:24s} "
              f"study={ev['study_id'] or '-':.12s}")
    return 0


def _dispatch_skills(args, parsers) -> int:
    """Dispatch the skills subcommand."""
    if args.skills_command == "list":
        return cmd_skills_list(args)
    elif args.skills_command == "show":
        return cmd_skills_show(args)
    elif args.skills_command == "search":
        return cmd_skills_search(args)
    else:
        parsers["skills"].print_help()
        return 0


def _dispatch_swarm(args, parsers) -> int:
    """Dispatch the swarm subcommand."""
    if args.swarm_command == "list":
        return cmd_swarm_list(args)
    elif args.swarm_command == "inspect":
        return cmd_swarm_inspect(args)
    elif args.swarm_command == "run":
        return cmd_swarm_run(args)
    elif args.swarm_command == "cancel":
        return cmd_swarm_cancel(args)
    else:
        parsers["swarm"].print_help()
        return 0


def _dispatch_mcp(args, parsers) -> int:
    """Dispatch the mcp subcommand."""
    if args.mcp_command == "serve":
        return cmd_mcp_serve(args)
    elif args.mcp_command == "list-tools":
        return cmd_mcp_list_tools(args)
    else:
        parsers["mcp"].print_help()
        return 0


def _dispatch_schedule(args, parsers) -> int:
    """Dispatch the schedule subcommand."""
    from strategy_research.kernel.scheduled_research.cli import (
        cmd_schedule_cancel,
        cmd_schedule_create,
        cmd_schedule_delete,
        cmd_schedule_list,
        cmd_schedule_run,
        cmd_schedule_show,
        cmd_schedule_start,
    )
    if args.schedule_command == "create":
        return cmd_schedule_create(args)
    elif args.schedule_command == "list":
        return cmd_schedule_list(args)
    elif args.schedule_command == "show":
        return cmd_schedule_show(args)
    elif args.schedule_command == "cancel":
        return cmd_schedule_cancel(args)
    elif args.schedule_command == "delete":
        return cmd_schedule_delete(args)
    elif args.schedule_command == "run":
        return cmd_schedule_run(args)
    elif args.schedule_command == "start":
        return cmd_schedule_start(args)
    else:
        return 0


# _dispatch_goal / _dispatch_hypothesis / _dispatch_validate_run 已随
# apps/goal、apps/hypothesis、apps/validation 移除（2026-11-04 做减法批次 2）。
# _dispatch_engine / _dispatch_accept 已随 apps/engine、
# apps/strategy_acceptance 移除（2026-11-05 做减法批次 4）。


def _dispatch_api(args, parsers) -> int:
    """Dispatch the api subcommand."""
    if args.api_command == "serve":
        return cmd_api_serve(args)
    else:
        parsers["api"].print_help()
        return 0


def _dispatch_webui(args, parsers) -> int:
    """Dispatch the webui subcommand."""
    if args.webui_command == "serve":
        return cmd_webui_serve(args)
    else:
        parsers["webui"].print_help()
        return 0


def _dispatch_compact(args, parsers) -> int:
    """Dispatch the compact subcommand."""
    if getattr(args, "compact_command", None) == "show":
        from .commands.compact_show import cmd_compact_show

        return cmd_compact_show(args)
    else:
        parsers["compact"].print_help()
        return 0


def _dispatch_serve(args, parsers) -> int:
    """Dispatch the serve subcommand (W2: --api-only supported)."""
    from .commands.merged import dispatch_serve

    return dispatch_serve(args)


# _dispatch_backtest / _dispatch_check 已随 apps/backtest、
# apps/factor_validate、apps/strategy_acceptance 移除（2026-11-05 批次 4）。


if __name__ == "__main__":
    sys.exit(main())
