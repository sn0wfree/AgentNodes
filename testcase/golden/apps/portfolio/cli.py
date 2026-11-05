"""Portfolio CLI — portfolio run / list / show / correlate。"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import List, Optional, cast

import pandas as pd

from .combiner import combine_equity_curves, equal_weight, risk_parity, sharpe_weight
from .correlation import correlation_matrix, correlation_pairs
from .metrics import portfolio_metrics
from .models import PortfolioConfig


def _load_equity_curve(run_dir: Path) -> Optional[pd.Series]:
    """从 run_dir 加载 equity_curve.csv 或 metrics.json。"""
    equity_path = run_dir / "equity_curve.csv"
    if equity_path.exists():
        df = pd.read_csv(equity_path, index_col=0, parse_dates=True)
        if "equity" in df.columns:
            return cast(pd.Series, df["equity"])
        return cast(pd.Series, df.iloc[:, 0])
    return None


def _find_runs(strategy_dir: Path) -> List[Path]:
    """找到策略目录下所有 run 目录。"""
    runs_dir = strategy_dir / "runs"
    if not runs_dir.exists():
        return []
    return sorted(
        [d for d in runs_dir.iterdir() if d.is_dir() and d.name.startswith("run_")],
        key=lambda p: p.name,
    )


def cmd_portfolio_run(args) -> None:
    """执行组合回测。"""
    config_path = Path(args.config)
    if not config_path.exists():
        print(f"✗ 配置文件不存在: {config_path}", file=sys.stderr)
        sys.exit(1)

    import yaml
    with open(config_path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    portfolio_cfg = PortfolioConfig(
        name=cfg.get("name", "portfolio"),
        strategies=cfg.get("strategies", []),
        combine=cfg.get("combine", "equal_weight"),
        initial_cash=cfg.get("initial_cash", 1_000_000),
    )

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # 加载策略权益曲线
    curves = {}
    for strat_name in portfolio_cfg.strategies:
        strat_dir = config_path.parent / "strategies" / strat_name
        if not strat_dir.exists():
            print(f"⚠ 策略目录不存在: {strat_dir}", file=sys.stderr)
            continue
        runs = _find_runs(strat_dir)
        if runs:
            latest = runs[-1]
            eq = _load_equity_curve(latest)
            if eq is not None:
                curves[strat_name] = eq

    if not curves:
        print("✗ 无可用的策略权益曲线", file=sys.stderr)
        sys.exit(1)

    # 计算权重
    if portfolio_cfg.combine == "risk_parity":
        weights = risk_parity(curves)
    elif portfolio_cfg.combine == "sharpe_weight":
        weights = sharpe_weight(curves)
    else:
        weights = equal_weight(portfolio_cfg.strategies)

    # 组合
    portfolio_curve = combine_equity_curves(curves, weights)

    if portfolio_curve.empty:
        print("✗ 组合权益曲线为空", file=sys.stderr)
        sys.exit(1)

    # 计算指标
    pm = portfolio_metrics(portfolio_curve, curves, weights)

    # 保存结果
    portfolio_curve.to_csv(output_dir / "portfolio_equity.csv", header=True)

    result = {
        "name": portfolio_cfg.name,
        "combine": portfolio_cfg.combine,
        "weights": weights,
        "metrics": pm.to_dict(),
        "strategies": portfolio_cfg.strategies,
    }
    with open(output_dir / "portfolio_metrics.json", "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    # 输出摘要
    print(f"✓ 组合 '{portfolio_cfg.name}' 完成")
    print(f"  策略数: {pm.n_strategies}")
    print(f"  权重: {weights}")
    print(f"  Sharpe: {pm.sharpe:.4f}")
    print(f"  年化收益: {pm.annual_return:.4f}")
    print(f"  最大回撤: {pm.max_drawdown:.4f}")
    print(f"  VaR(95%): {pm.var_95:.4f}")
    print(f"  分散化比率: {pm.diversification_ratio:.4f}")
    print(f"  结果目录: {output_dir}")


def cmd_portfolio_list(args) -> None:
    """列出所有策略。"""
    strategy_dir = Path(args.strategy_dir)
    if not strategy_dir.exists():
        print(f"✗ 目录不存在: {strategy_dir}", file=sys.stderr)
        sys.exit(1)

    strategies = []
    for d in sorted(strategy_dir.iterdir()):
        if d.is_dir() and (d / "strategy.py").exists():
            runs = _find_runs(d)
            strategies.append({
                "name": d.name,
                "runs": len(runs),
                "has_equity": any((r / "equity_curve.csv").exists() for r in runs),
            })

    if not strategies:
        print("(无策略)")
        return

    print(f"=== {len(strategies)} 个策略 ===")
    for s in strategies:
        eq_mark = "✓" if s["has_equity"] else " "
        print(f"  {s['name']} (runs={s['runs']}, equity={eq_mark})")


def cmd_portfolio_show(args) -> None:
    """显示组合结果。"""
    result_dir = Path(args.result_dir)
    metrics_path = result_dir / "portfolio_metrics.json"
    if not metrics_path.exists():
        print(f"✗ 未找到: {metrics_path}", file=sys.stderr)
        sys.exit(1)

    with open(metrics_path, "r", encoding="utf-8") as f:
        result = json.load(f)

    print(f"=== 组合: {result['name']} ===")
    print(f"组合方式: {result['combine']}")
    print(f"策略: {result['strategies']}")
    print(f"权重: {result['weights']}")
    print()
    print("=== 指标 ===")
    for k, v in result["metrics"].items():
        print(f"  {k}: {v}")


def cmd_portfolio_correlate(args) -> None:
    """输出策略间相关性矩阵。"""
    strategy_dir = Path(args.strategy_dir)
    if not strategy_dir.exists():
        print(f"✗ 目录不存在: {strategy_dir}", file=sys.stderr)
        sys.exit(1)

    curves = {}
    for d in sorted(strategy_dir.iterdir()):
        if d.is_dir() and (d / "strategy.py").exists():
            runs = _find_runs(d)
            if runs:
                eq = _load_equity_curve(runs[-1])
                if eq is not None:
                    curves[d.name] = eq

    if len(curves) < 2:
        print("⚠ 需要至少2个策略有权益曲线")
        return

    corr = correlation_matrix(curves)
    pairs = correlation_pairs(curves)

    output = {
        "matrix": corr.to_dict() if not corr.empty else {},
        "pairs": [
            {"a": p.strategy_a, "b": p.strategy_b, "correlation": p.correlation}
            for p in pairs
        ],
    }

    output_path = Path(args.output) if hasattr(args, "output") and args.output else None
    if output_path:
        output_path.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"✓ 相关性矩阵已保存: {output_path}")
    else:
        print(json.dumps(output, indent=2, ensure_ascii=False))


def add_portfolio_subparsers(subparsers) -> None:
    """添加 portfolio 子命令。"""
    portfolio_cmd = subparsers.add_parser("portfolio", help="组合回测")
    portfolio_sub = portfolio_cmd.add_subparsers(dest="portfolio_action")

    # portfolio run
    run_p = portfolio_sub.add_parser("run", help="执行组合回测")
    run_p.add_argument("--config", required=True, help="组合配置 YAML")
    run_p.add_argument("--output-dir", required=True, help="输出目录")
    run_p.set_defaults(func=cmd_portfolio_run)

    # portfolio list
    list_p = portfolio_sub.add_parser("list", help="列出所有策略")
    list_p.add_argument("--strategy-dir", required=True, help="策略根目录")
    list_p.set_defaults(func=cmd_portfolio_list)

    # portfolio show
    show_p = portfolio_sub.add_parser("show", help="显示组合结果")
    show_p.add_argument("result_dir", help="组合结果目录")
    show_p.set_defaults(func=cmd_portfolio_show)

    # portfolio correlate
    corr_p = portfolio_sub.add_parser("correlate", help="策略相关性矩阵")
    corr_p.add_argument("--strategy-dir", required=True, help="策略根目录")
    corr_p.add_argument("--output", help="输出 JSON 路径")
    corr_p.set_defaults(func=cmd_portfolio_correlate)


__all__ = [
    "add_portfolio_subparsers",
    "cmd_portfolio_run",
    "cmd_portfolio_list",
    "cmd_portfolio_show",
    "cmd_portfolio_correlate",
]
