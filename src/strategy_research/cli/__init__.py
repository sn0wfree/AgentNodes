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
from .commands.autoresearch import _spawn_agent as _spawn_agent
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

    from strategy_research.cli.onboard import (
        _QUANTNODES_DOTENV_PATH,
        _QUANTNODES_LLM_JSON_PATH,
        is_onboarded,
        run_onboarding,
    )
    from strategy_research.cli.theme import get_console

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
    from strategy_research.cli.onboard import _QUANTNODES_LLM_JSON_PATH
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


def cmd_run(args: argparse.Namespace) -> int:
    """执行 run 命令 - 运行回测。"""
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

    from strategy_research.apps.backtest import run_backtest_script as run_backtest
    result = run_backtest(
        workspace_path=path,
        strategy_name=strategy_name,
        action=args.action or "manual",
        description=args.description or "",
        timeout=args.timeout,
    )

    if result["success"]:
        print(f"✅ 回测完成: {result['run']}")
        metrics = result["metrics"]
        print(f"   calmar: {metrics.get('calmar', 'N/A')}")
        print(f"   sharpe: {metrics.get('sharpe', 'N/A')}")
        print(f"   max_dd: {metrics.get('max_dd', 'N/A')}")
        print(f"   ann_return: {metrics.get('ann_return', 'N/A')}")
    else:
        print(f"❌ 回测失败: {result['error'][:200]}")
        return 1

    return 0


def cmd_evaluate(args: argparse.Namespace) -> int:
    """执行 evaluate 命令 — 重跑当前 strategy.py 并写新 run_XXXX。

    与 run 的区别: evaluate 不需要 action / description,
    专为手动复跑 baseline / 验证修改后的 strategy.py 设计。
    """
    path = Path(args.path).resolve()

    if not (path / "config.yaml").exists():
        print(f"❌ 不是有效的工作区: {path}")
        return 1

    config_path = path / "config.yaml"
    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    strategy_name = args.strategy
    if not strategy_name:
        strategy_name = config.get("workspace", {}).get("default_strategy")
        if not strategy_name:
            print("❌ 未指定策略名称，请使用 --strategy <name>")
            return 1

    from strategy_research.apps.backtest import run_backtest_script

    print(f"🔄 复跑策略: {strategy_name}")
    result = run_backtest_script(
        workspace_path=path,
        strategy_name=strategy_name,
        action="evaluate",
        description=args.description or "Manual re-evaluation via `evaluate`",
        timeout=args.timeout,
    )

    if result["success"]:
        m = result["metrics"]
        print(f"\n✅ 复跑成功: {result['run']}")
        print(f"   Calmar   = {m.get('calmar', 0):.4f}")
        print(f"   Sharpe   = {m.get('sharpe', 0):.4f}")
        print(f"   MaxDD    = {m.get('max_dd', 0):.4f}")
        print(f"   AnnRet   = {m.get('ann_return', 0):.4f}")
        print(f"   AnnVol   = {m.get('ann_vol', 0):.4f}")
        print(f"   Sortino  = {m.get('sortino', 0):.4f}")
        print(f"   Turnover = {m.get('turnover', 0):.4f}")
        print(f"\n📁 详见: {path / 'strategies' / strategy_name / 'runs' / result['run']}")
        return 0
    else:
        print(f"\n❌ 复跑失败: {result.get('error', 'unknown')[:300]}")
        return 1


def cmd_preflight(args: argparse.Namespace) -> int:
    """执行 preflight 命令 — 启动前环境检查。

    检查项：
    - LLM Provider key (critical)
    - DuckDB 可写
    - 至少一个数据源可用
    - OHLCV 数据完整性

    Critical 失败时返回 1，否则 0。
    """
    from strategy_research.apps.preflight import run_preflight

    workspace = Path(args.path).resolve() if args.path else Path.cwd()
    results = run_preflight(workspace, verbose=True)

    critical_failed = [r for r in results if r.critical and r.status != "ready"]
    return 1 if critical_failed else 0


def cmd_validate(args: argparse.Namespace) -> int:
    """执行 validate 命令 - 验证因子。"""
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

    factor_code = args.factor
    if not factor_code:
        print("❌ 请指定因子表达式，例如: --factor 'ts_return(close, 20)'")
        return 1

    import pandas as pd

    from strategy_research.apps.factor_validate import validate_factor

    # 加载价格数据 (简单示例: 从 CSV 加载)
    prices_file = path / "data" / "prices.csv"
    if not prices_file.exists():
        print(f"❌ 价格数据不存在: {prices_file}")
        print("   请将价格数据保存到 data/prices.csv (index=date, columns=assets)")
        return 1

    prices = pd.read_csv(prices_file, index_col=0, parse_dates=True)

    # 验证因子
    result = validate_factor(
        factor_code=factor_code,
        prices=prices,
        strategy_name=strategy_name,
        source=args.source or "cli",
    )

    print(f"\n📊 因子验证结果: {factor_code}")
    print(f"   通过: {'✓' if result['passed'] else '❌'}")
    print(f"   IC mean: {result['ic_mean']:.4f}")
    print(f"   IR: {result['ir']:.4f}")
    print(f"   Rank IC: {result['rank_ic_mean']:.4f}")
    print(f"   综合评分: {result['overall_score']:.4f}")

    if result["fail_reasons"]:
        print("\n   失败原因:")
        for reason in result["fail_reasons"]:
            print(f"     - {reason}")

    if result["scores"]:
        print("\n   6 维评分:")
        for k, v in result["scores"].items():
            print(f"     {k}: {v:.4f}")

    return 0


def cmd_list(args: argparse.Namespace) -> int:
    """执行 list 命令 - 列出实验。"""
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

    from strategy_research.apps.backtest import get_best_experiment, get_experiment_history

    experiments = get_experiment_history(path, strategy_name, limit=args.limit)

    if not experiments:
        print("📭 没有实验记录")
        return 0

    print(f"\n📋 实验记录 ({strategy_name}):")
    print(f"{'run':<12} {'action':<20} {'calmar':<10} {'sharpe':<10} {'status':<10} {'description'}")
    print("-" * 80)

    for exp in experiments:
        run = exp.get("run", "")
        action = exp.get("action", "")
        calmar = exp.get("calmar", "N/A")
        sharpe = exp.get("sharpe", "N/A")
        status = exp.get("status", "")
        desc = exp.get("description", "")

        # 状态标记
        status_mark = "✓" if status == "keep" else "✗" if status == "discard" else "○"

        print(f"{run:<12} {action:<20} {calmar:<10} {sharpe:<10} {status_mark} {desc}")

    # 显示最佳实验
    best = get_best_experiment(path, strategy_name)
    if best:
        print(f"\n🏆 最佳实验: {best.get('run')} (calmar={best.get('calmar')})")

    return 0


def cmd_import(args: argparse.Namespace) -> int:
    """执行 import 命令 - 导入价格数据。"""
    path = Path(args.path).resolve()

    # 检查工作区
    if not (path / "config.yaml").exists():
        print(f"❌ 不是有效的工作区: {path}")
        return 1

    from strategy_research.core.db import init_db

    # 确保 DuckDB 初始化
    init_db(path)

    strategy_name = args.strategy
    source = args.source

    # 本地文件源
    if source == "sample":
        print(
            "❌ 'sample' source temporarily unavailable: generate_sample_data "
            "returns close-only data. Use tushare/akshare source or run "
            "run_backtest (auto fetches OHLCV)."
        )
        return 1

    if source in ("csv", "parquet"):
        success = _import_local_file(source, args, path, strategy_name)
    elif source in ("tushare", "ifind", "akshare", "auto"):
        success = _import_api_source(source, args, path, strategy_name)
    elif source == "fred":
        success = _import_fred(args, path, strategy_name)
    else:
        print(f"❌ 未知数据源: {source}")
        print("   支持: csv, parquet, sample, tushare, ifind, fred, akshare, auto")
        return 1

    if success:
        print("\n✅ 数据导入完成")
        from strategy_research.core.db import get_price_data_info
        info = get_price_data_info(path, strategy_name)
        if info:
            print(f"   资产数: {info.get('n_assets', 0)}")
            print(f"   日期数: {info.get('n_dates', 0)}")
            print(f"   时间范围: {info.get('start_date')} ~ {info.get('end_date')}")
        return 0
    else:
        print("\n❌ 数据导入失败")
        return 1


def _import_local_file(source: str, args, path: Path, strategy_name: str) -> bool:
    """Import from a local csv/parquet file."""
    if not args.file:
        print("❌ 请指定 --file 参数")
        return False
    try:
        if source == "csv":
            from strategy_research.apps.data_import import import_csv_ohlcv
            return import_csv_ohlcv(
                path, strategy_name, args.file,
                date_column=args.date_column,
                asset_column=args.asset_column,
            )
        from strategy_research.apps.data_import import import_parquet_ohlcv
        return import_parquet_ohlcv(
            path, strategy_name, args.file,
            date_column=args.date_column,
            asset_column=args.asset_column,
        )
    except (FileNotFoundError, ValueError) as e:
        print(f"❌ 数据导入失败: {e}")
        return False


def _import_api_source(source: str, args, path: Path, strategy_name: str) -> bool:
    """Import from a codes-based API source (tushare/ifind/akshare/auto)."""
    if not args.codes:
        print("❌ 请指定 --codes 参数 (如: 000001.SZ,600519.SH)")
        return False
    codes = [c.strip() for c in args.codes.split(",")]
    from strategy_research.apps.data_import import (
        import_akshare,
        import_from_source,
        import_ifind,
        import_tushare,
    )
    importer = {
        "tushare": import_tushare,
        "ifind": import_ifind,
        "akshare": import_akshare,
        "auto": import_from_source,
    }[source]
    if source == "auto":
        return importer(path, strategy_name, "auto", codes,
                        args.start_date, args.end_date, incremental=args.incremental)
    return importer(path, strategy_name, codes,
                    args.start_date, args.end_date, incremental=args.incremental)


def _import_fred(args, path: Path, strategy_name: str) -> bool:
    """Import FRED series (core series when --codes is omitted)."""
    if not args.codes:
        from strategy_research.apps.data_source.fred_loader import CORE_SERIES
        codes = CORE_SERIES
        print(f"📡 导入 FRED 核心系列 ({len(codes)} 个)...")
    else:
        codes = [c.strip() for c in args.codes.split(",")]
    from strategy_research.apps.data_import import import_fred
    return import_fred(
        path, strategy_name, codes,
        args.start_date, args.end_date,
        incremental=args.incremental,
    )



# Main CLI
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

    # export
    export_parser = subparsers.add_parser("export", help="导出策略")
    export_parser.add_argument("path", nargs="?", default=".", help="工作区路径")
    export_parser.add_argument("--strategy", "-s", required=True, help="策略名称")
    export_parser.add_argument("--format", "-f", nargs="+", default=["pine", "tdx", "vnpy"],
                               choices=["pine", "tdx", "vnpy"], help="导出格式")
    export_parser.add_argument("--output", "-o", help="输出目录 (默认: workspace/exports/)")

    # schedule (P5)
    from strategy_research.core.scheduled_research.cli import add_schedule_subparsers
    add_schedule_subparsers(subparsers)

    # goal (P3-a)
    from strategy_research.core.goal.cli import add_goal_subparsers
    add_goal_subparsers(subparsers)

    # hypothesis (P3-b)
    from strategy_research.apps.hypothesis.cli import add_hypothesis_subparsers
    add_hypothesis_subparsers(subparsers)

    # validation (P3-c)
    from strategy_research.core.validation.cli import add_validate_subparsers
    add_validate_subparsers(subparsers)

    from strategy_research.core.engine.cli import add_engine_subparsers
    add_engine_subparsers(subparsers)

    # strategy acceptance (P6 Step 0) — 离线验收调试工具
    from strategy_research.apps.strategy_acceptance.cli import add_accept_subparsers
    add_accept_subparsers(subparsers)

    # portfolio (P3-d) — 组合回测 / 策略相关性
    from strategy_research.apps.portfolio.cli import add_portfolio_subparsers
    add_portfolio_subparsers(subparsers)

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

    # W2 merged task commands
    from .commands.merged import add_backtest_parser, add_check_parser

    add_backtest_parser(subparsers)
    add_check_parser(subparsers)

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
    # evaluate/preflight/validate/list/import/autoresearch) are
    # registered via cli.commands.core_commands on import.
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
        "export": _dispatch_export,
        "schedule": _dispatch_schedule,
        "goal": _dispatch_goal,
        "hypothesis": _dispatch_hypothesis,
        "validate-run": _dispatch_validate_run,
        "engine": _dispatch_engine,
        "accept": _dispatch_accept,
        "api": _dispatch_api,
        "webui": _dispatch_webui,
        "compact": _dispatch_compact,
        "serve": _dispatch_serve,
        "backtest": _dispatch_backtest,
        "check": _dispatch_check,
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


def _dispatch_export(args, parsers) -> int:
    """Dispatch the export subcommand."""
    from .commands.export import cmd_export

    return cmd_export(args)


def _dispatch_schedule(args, parsers) -> int:
    """Dispatch the schedule subcommand."""
    from strategy_research.core.scheduled_research.cli import (
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


def _dispatch_goal(args, parsers) -> int:
    """Dispatch the goal subcommand."""
    from strategy_research.core.goal.cli import (
        cmd_goal_audit,
        cmd_goal_cancel,
        cmd_goal_complete,
        cmd_goal_evidence,
        cmd_goal_list,
        cmd_goal_start,
        cmd_goal_status,
    )
    return {
        "start": cmd_goal_start,
        "status": cmd_goal_status,
        "evidence": cmd_goal_evidence,
        "audit": cmd_goal_audit,
        "complete": cmd_goal_complete,
        "list": cmd_goal_list,
        "cancel": cmd_goal_cancel,
    }.get(args.goal_command, lambda a: 1)(args)


def _dispatch_hypothesis(args, parsers) -> int:
    """Dispatch the hypothesis subcommand."""
    from strategy_research.apps.hypothesis.cli import (
        cmd_hypothesis_create,
        cmd_hypothesis_link,
        cmd_hypothesis_list,
        cmd_hypothesis_search,
        cmd_hypothesis_show,
        cmd_hypothesis_update,
    )
    return {
        "create": cmd_hypothesis_create,
        "list": cmd_hypothesis_list,
        "show": cmd_hypothesis_show,
        "update": cmd_hypothesis_update,
        "search": cmd_hypothesis_search,
        "link": cmd_hypothesis_link,
    }.get(args.hypothesis_command, lambda a: 1)(args)


def _dispatch_validate_run(args, parsers) -> int:
    """Dispatch the validate-run subcommand."""
    from strategy_research.core.validation.cli import cmd_validate_run
    return cmd_validate_run(args)


def _dispatch_engine(args, parsers) -> int:
    """Dispatch the engine subcommand."""
    from strategy_research.core.engine.cli import dispatch_engine
    return dispatch_engine(args)


def _dispatch_accept(args, parsers) -> int:
    """Dispatch the accept subcommand."""
    from strategy_research.apps.strategy_acceptance.cli import cmd_accept
    return cmd_accept(args)


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


def _dispatch_backtest(args, parsers) -> int:
    from .commands.merged import dispatch_backtest

    return dispatch_backtest(args)


def _dispatch_check(args, parsers) -> int:
    from .commands.merged import dispatch_check

    return dispatch_check(args)


if __name__ == "__main__":
    sys.exit(main())
