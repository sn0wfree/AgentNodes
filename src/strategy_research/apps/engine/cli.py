"""Engine CLI — `strategy-research engine` 命令组。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from strategy_research.apps.engine.config import BacktestConfigSchema


def add_engine_subparsers(subparsers: Any) -> None:
    """注册 engine 子命令。"""
    engine_parser = subparsers.add_parser("engine", help="Bar-by-bar 回测引擎")
    engine_subparsers = engine_parser.add_subparsers(dest="engine_command", help="引擎命令")

    # engine run-backtest
    bt_parser = engine_subparsers.add_parser("run-backtest", help="运行引擎回测")
    bt_parser.add_argument("--workspace", required=True, help="工作区路径")
    bt_parser.add_argument("--strategy", required=True, help="策略名称")
    bt_parser.add_argument("--signal-engine", required=True, help="signal_engine.py 路径")
    bt_parser.add_argument("--market", default="auto", help="市场类型 (auto/china_a/global_equity/crypto/forex/...)")
    bt_parser.add_argument("--codes", nargs="+", help="标的代码列表")
    bt_parser.add_argument("--start-date", help="起始日期 YYYY-MM-DD")
    bt_parser.add_argument("--end-date", help="结束日期 YYYY-MM-DD")
    bt_parser.add_argument("--initial-capital", type=float, default=1_000_000.0, help="初始资金")
    bt_parser.add_argument("--bars-per-year", type=int, default=252, help="年化 bar 数")
    bt_parser.add_argument("--commission-rate", type=float, default=0.001, help="手续费率")
    bt_parser.add_argument("--slippage-bps", type=float, default=0.0, help="滑点 (bps)")
    bt_parser.add_argument("--output-dir", help="artifacts 输出目录")
    bt_parser.add_argument("--optimizer", default="none",
                           choices=["none", "equal_volatility", "risk_parity", "mean_variance",
                                    "max_diversification", "turnover_aware"],
                           help="权重优化器")
    bt_parser.add_argument("--json", action="store_true", dest="json_output", help="JSON 输出")

    # engine list-engines
    engine_subparsers.add_parser("list-engines", help="列出可用市场引擎")

    # engine validate-signal
    vs_parser = engine_subparsers.add_parser("validate-signal", help="校验 signal_engine.py 安全性")
    vs_parser.add_argument("file", help="signal_engine.py 路径")


# ============================================================
# 命令实现
# ============================================================


def cmd_engine_run_backtest(args: argparse.Namespace) -> int:
    """执行 engine run-backtest 命令。"""
    workspace_path = Path(args.workspace).resolve()

    if not (workspace_path / "config.yaml").exists():
        print(f"❌ 不是有效的工作区: {workspace_path}")
        return 1

    # 构建 config
    config = {
        "market_type": args.market,
        "codes": args.codes or [],
        "start_date": args.start_date,
        "end_date": args.end_date,
        "initial_capital": args.initial_capital,
        "commission_rate": args.commission_rate,
        "slippage_bps": args.slippage_bps,
    }

    # 校验
    errors = BacktestConfigSchema.validate_config(config)
    if errors:
        print("❌ 配置校验失败:")
        for e in errors:
            print(f"  - {e}")
        return 1

    # 运行
    try:
        from strategy_research.apps.engine.runner import run_engine_backtest

        result = run_engine_backtest(
            workspace_path=workspace_path,
            strategy_name=args.strategy,
            config=config,
            signal_engine_path=Path(args.signal_engine).resolve(),
            bars_per_year=args.bars_per_year,
            optimizer=args.optimizer if args.optimizer != "none" else None,
            # progress lines on stdout → captured into run.log in the
            # backgrounded mode (log-progress liveness for long backtests)
            progress_every=0,
        )
    except Exception as e:
        print(f"❌ 回测失败: {e}")
        return 1

    # 输出
    if args.json_output:
        print(json.dumps(result, indent=2, default=str))
    else:
        print("✅ 回测完成")
        print(f"   sharpe:   {result.get('sharpe', 'N/A')}")
        print(f"   max_dd:   {result.get('max_drawdown', 'N/A')}")
        print(f"   calmar:   {result.get('calmar', 'N/A')}")
        print(f"   total_return: {result.get('total_return', 'N/A')}")
        print(f"   trades:   {result.get('trade_count', 'N/A')}")

    return 0


def cmd_engine_list_engines(args: argparse.Namespace) -> int:
    """执行 engine list-engines 命令。"""
    from strategy_research.apps.engine import (
        ChinaAEngine,
        ChinaFuturesEngine,
        CompositeEngine,
        CryptoEngine,
        ForexEngine,
        FuturesBaseEngine,
        GlobalEquityEngine,
        GlobalFuturesEngine,
        IndiaEquityEngine,
    )

    engines = {
        "china_a": ChinaAEngine,
        "global_equity": GlobalEquityEngine,
        "crypto": CryptoEngine,
        "forex": ForexEngine,
        "india_equity": IndiaEquityEngine,
        "futures_base": FuturesBaseEngine,
        "china_futures": ChinaFuturesEngine,
        "global_futures": GlobalFuturesEngine,
        "composite": CompositeEngine,
    }

    print("可用市场引擎:")
    print("-" * 40)
    for name, cls in engines.items():
        doc = (cls.__doc__ or "").strip().split("\n")[0]
        print(f"  {name:20s}  {doc}")
    print(f"\n共 {len(engines)} 个引擎")
    return 0


def cmd_engine_validate_signal(args: argparse.Namespace) -> int:
    """执行 engine validate-signal 命令。"""
    from strategy_research.apps.engine.runner import _validate_signal_engine_source

    file_path = Path(args.file).resolve()
    if not file_path.exists():
        print(f"❌ 文件不存在: {file_path}")
        return 1

    try:
        _validate_signal_engine_source(file_path)
        print(f"✅ 安全校验通过: {file_path}")
        return 0
    except ValueError as e:
        print(f"❌ 安全校验失败: {e}")
        return 1


def dispatch_engine(args: argparse.Namespace) -> int:
    """分发 engine 子命令。"""
    commands = {
        "run-backtest": cmd_engine_run_backtest,
        "list-engines": cmd_engine_list_engines,
        "validate-signal": cmd_engine_validate_signal,
    }
    handler = commands.get(args.engine_command)
    if handler is None:
        print("用法: strategy-research engine <command>")
        print("  run-backtest      运行引擎回测")
        print("  list-engines      列出可用市场引擎")
        print("  validate-signal   校验 signal_engine.py 安全性")
        # 未知子命令是用法错误：返回 0 会让 shell 脚本与后台任务把
        # 「什么都没跑」当成成功（2026-11-02 补测发现）。
        return 2
    return handler(args)
