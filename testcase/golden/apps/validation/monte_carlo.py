"""Monte Carlo permutation test for backtest significance (P3-c).

Shuffles trade PnL order to test whether the observed Sharpe / max
drawdown is significantly better than a random ordering of the same
trades. This is the cheapest significance test for a backtest with a
small number of trades.

Adapted from vibe-trading-ai 0.1.11 (MIT License, HKUDS).
"""

from __future__ import annotations

from typing import Any

import numpy as np

from .trade_input import TradeInput
from .utils import _sharpe


def monte_carlo_test(
    trades: list[TradeInput],
    initial_capital: float,
    n_simulations: int = 1000,
    seed: int = 42,
    bars_per_year: int = 252,
) -> dict[str, Any]:
    """Shuffle trade PnL order to test path significance.

    Null hypothesis: the observed Sharpe / max-drawdown is no better than
    a random ordering of the same trades.

    Args:
        trades: Completed round-trip trades from a backtest.
        initial_capital: Starting capital.
        n_simulations: Number of random permutations.
        seed: Random seed for reproducibility.
        bars_per_year: Annualisation factor.

    Returns:
        Dict with actual_sharpe, p_value_sharpe, actual_max_dd,
        p_value_max_dd, simulated_sharpes percentiles.
    """
    if len(trades) < 3:
        return {"error": "need at least 3 trades", "p_value_sharpe": 1.0, "n_trades": len(trades)}

    pnls = np.array([t.pnl for t in trades], dtype=float)
    actual = _path_metrics(pnls, initial_capital, bars_per_year)

    rng = np.random.default_rng(seed)
    sharpe_count = 0
    dd_count = 0
    sim_sharpes = []

    for _ in range(n_simulations):
        shuffled = rng.permutation(pnls)
        sim = _path_metrics(shuffled, initial_capital, bars_per_year)
        sim_sharpes.append(sim["sharpe"])
        if sim["sharpe"] >= actual["sharpe"]:
            sharpe_count += 1
        if sim["max_dd"] >= actual["max_dd"]:  # less negative = "better"
            dd_count += 1

    sim_arr = np.array(sim_sharpes)
    return {
        "actual_sharpe": round(actual["sharpe"], 4),
        "actual_max_dd": round(actual["max_dd"], 4),
        "p_value_sharpe": round(sharpe_count / n_simulations, 4),
        "p_value_max_dd": round(dd_count / n_simulations, 4),
        "simulated_sharpe_mean": round(float(sim_arr.mean()), 4),
        "simulated_sharpe_std": round(float(sim_arr.std()), 4),
        "simulated_sharpe_p5": round(float(np.percentile(sim_arr, 5)), 4),
        "simulated_sharpe_p95": round(float(np.percentile(sim_arr, 95)), 4),
        "n_simulations": n_simulations,
        "n_trades": len(trades),
        "bars_per_year": bars_per_year,
    }


def _path_metrics(pnls: np.ndarray, initial_capital: float, bars_per_year: int) -> dict[str, float]:
    """Compute Sharpe and max drawdown from a PnL sequence."""
    equity = initial_capital + np.cumsum(pnls)
    returns = np.diff(equity) / equity[:-1] if len(equity) > 1 else np.array([0.0])
    sharpe = _sharpe(returns, bars_per_year) if len(returns) > 1 else 0.0
    peak = np.maximum.accumulate(equity)
    dd = (equity - peak) / np.where(peak > 0, peak, 1.0)
    max_dd = float(dd.min())
    return {"sharpe": sharpe, "max_dd": max_dd}


__all__ = ["monte_carlo_test"]
