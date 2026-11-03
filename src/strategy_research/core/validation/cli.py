"""CLI handler for the validation subsystem (P3-d).

Subcommand:
  validate <run_dir> — run Monte Carlo / Bootstrap / Walk-Forward on a
                        strategy run directory.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from .market import MarketType
from .runner import run_validation
from .trade_input import TradeInput

if TYPE_CHECKING:
    import pandas as pd


def _load_nav_from_duckdb(run_dir: Path) -> "pd.Series | None":
    """Attempt to load the NAV history from DuckDB for this run.

    Returns a pandas Series (indexed by date) or None if unavailable.
    Strategy-research stores NAV in DuckDB via ``save_nav_history``.
    """
    try:
        from strategy_research.apps.db import load_nav_history
        # Best effort: extract workspace path and strategy name from run_dir
        # run_dir: <workspace>/strategies/<strategy>/runs/<run>
        parts = run_dir.parts
        if "runs" not in parts:
            return None
        runs_idx = parts.index("runs")
        if runs_idx < 2:
            return None
        strategy_name = parts[runs_idx - 1]
        workspace_path = Path(*parts[: runs_idx - 2]) if runs_idx >= 3 else Path(parts[0])
        run_name = parts[runs_idx + 1]
        return load_nav_history(workspace_path, strategy_name, run_name)
    except Exception as e:
        import logging
        logging.getLogger(__name__).debug("Failed to load NAV from DuckDB: %s", e)
        return None


def _load_trades_from_artifacts(run_dir: Path) -> list[TradeInput]:
    """Attempt to load real trades from trades.csv artifact.

    Returns a list of TradeInput objects, or empty list if unavailable.
    """
    import csv
    trades_path = run_dir / "trades.csv"
    if not trades_path.exists():
        return []
    try:
        trades = []
        with open(trades_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    entry_time = datetime.fromisoformat(row["entry_time"])
                    exit_time = datetime.fromisoformat(row["exit_time"])
                except (ValueError, KeyError):
                    continue
                trades.append(TradeInput(
                    symbol=row.get("symbol", "UNKNOWN"),
                    direction=int(row.get("direction", 1)),
                    entry_price=float(row.get("entry_price", 0.0)),
                    exit_price=float(row.get("exit_price", 0.0)),
                    entry_time=entry_time,
                    exit_time=exit_time,
                    size=float(row.get("size", 1.0)),
                    pnl=float(row.get("pnl", 0.0)),
                    pnl_pct=float(row.get("pnl_pct", 0.0)),
                    holding_bars=int(row.get("holding_bars", 0)),
                    exit_reason=row.get("exit_reason", "signal"),
                ))
        return trades
    except Exception:
        return []


def _load_nav_synthetic(run_dir: Path) -> "pd.Series | None":
    """Build a deterministic synthetic NAV from metrics.json (last-resort fallback)."""
    import pandas as pd
    metrics_path = run_dir / "metrics.json"
    if not metrics_path.exists():
        return None
    try:
        metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    try:
        ann_return = float(metrics.get("ann_return", 0.0))
    except (TypeError, ValueError):
        return None
    daily_ret = ann_return / 252
    idx = pd.date_range("2024-01-01", periods=60, freq="D")
    return pd.Series(
        [100_000.0 * (1 + daily_ret) ** (i + 1) for i in range(60)],
        index=idx,
    )


def _build_synthetic_trades(run_dir: Path) -> list[TradeInput]:
    """Build a minimal set of synthetic trades from metrics.json.

    For v0.3.0 we only need Monte Carlo path significance, which works on
    any pnl sequence. A real implementation would derive trades from
    positions/trades artifacts.
    """
    metrics_path = run_dir / "metrics.json"
    if not metrics_path.exists():
        return []
    try:
        metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    except Exception as e:
        import logging
        logging.getLogger(__name__).debug("Failed to read metrics.json: %s", e)
        return []
    ann_return = float(metrics.get("ann_return", 0.0))
    float(metrics.get("sharpe", 0.0))
    n_synthetic = 20
    base = ann_return / max(n_synthetic, 1)
    trades = []
    for i in range(n_synthetic):
        # Alternate positive / slightly-negative to give MC something to permute
        pnl = base * 1000 if i % 2 == 0 else -base * 200
        trades.append(TradeInput(
            symbol="SYN", direction=1,
            entry_price=100.0, exit_price=100.0 + pnl,
            entry_time=__import__("datetime").datetime(2024, 1, 1),
            exit_time=__import__("datetime").datetime(2024, 1, 2),
            size=1.0, pnl=pnl, pnl_pct=pnl / 100.0,
            exit_reason="synthetic",
        ))
    return trades


def cmd_validate_run(args: argparse.Namespace) -> int:
    """Run validation tools on a backtest run directory."""
    run_dir = Path(args.run_dir).expanduser().resolve()
    if not run_dir.is_dir():
        print(f"✗ run_dir not found: {run_dir}", file=sys.stderr)
        return 1

    # 1. Load equity curve (NAV) from DuckDB or fallback to synthetic
    equity_curve = _load_nav_from_duckdb(run_dir)
    if equity_curve is None or len(equity_curve) < 5:
        equity_curve = _load_nav_synthetic(run_dir)
        if equity_curve is not None:
            print("⚠ using synthetic NAV (DuckDB NAV history unavailable)", file=sys.stderr)
    if equity_curve is None:
        print("✗ could not load equity curve (no DuckDB NAV + no metrics.json)",
              file=sys.stderr)
        return 1

    # 2. Build trade list (real from trades.csv, fallback to synthetic)
    trades = _load_trades_from_artifacts(run_dir)
    if trades:
        print(f"✓ loaded {len(trades)} trades from trades.csv", file=sys.stderr)
    else:
        trades = _build_synthetic_trades(run_dir)
        if trades:
            print("⚠ no real trades found; using synthetic trades from metrics.json", file=sys.stderr)
        else:
            print("⚠ no trades available; skipping Monte Carlo + Walk-Forward per-trade checks",
                  file=sys.stderr)

    # 3. Build config from CLI flags
    config: dict = {}
    val_cfg: dict = {}
    if args.monte_carlo:
        val_cfg["monte_carlo"] = {"n_simulations": args.n_simulations, "seed": args.seed}
    if args.bootstrap:
        val_cfg["bootstrap"] = {"n_bootstrap": args.n_bootstrap, "seed": args.seed}
    if args.walk_forward:
        val_cfg["walk_forward"] = {"n_windows": args.n_windows}
    if val_cfg:
        config["validation"] = val_cfg

    # 4. Run
    market = MarketType(args.market)
    try:
        results = run_validation(
            config=config,
            equity_curve=equity_curve,
            trades=trades,
            initial_capital=100_000.0,
            market=market,
        )
    except Exception as e:
        print(f"✗ validation failed: {e}", file=sys.stderr)
        return 1

    # 5. Output
    out_path = run_dir / "validation.json"
    out_path.write_text(
        json.dumps(results, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    print(f"✓ Validation results written: {out_path}")
    print(json.dumps(results, indent=2, ensure_ascii=False, allow_nan=False))
    return 0


def add_validate_subparsers(subparsers) -> None:
    parser = subparsers.add_parser("validate-run", help="对回测 run_dir 跑验证")
    parser.add_argument("run_dir", help="回测 run 目录路径")
    parser.add_argument(
        "--market", default="a_share",
        choices=[m.value for m in MarketType],
        help="目标市场 (默认 a_share)",
    )
    parser.add_argument("--monte-carlo", action="store_true", help="跑 Monte Carlo permutation")
    parser.add_argument("--n-simulations", type=int, default=1000, help="MC 模拟次数")
    parser.add_argument("--bootstrap", action="store_true", help="跑 Bootstrap Sharpe CI")
    parser.add_argument("--n-bootstrap", type=int, default=1000, help="Bootstrap 重采样次数")
    parser.add_argument("--walk-forward", action="store_true", help="跑 Walk-Forward 分析")
    parser.add_argument("--n-windows", type=int, default=5, help="WF 窗口数")
    parser.add_argument("--seed", type=int, default=42, help="随机种子")
