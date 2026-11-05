"""Walk-Forward analysis (P3-c).

Splits the equity curve into N sequential windows and checks consistency
of returns / Sharpe / max-drawdown across them. Useful for detecting
regime-specific performance (a strategy may be great in one window but
terrible in another).

Adapted from vibe-trading-ai 0.1.11 (MIT License, HKUDS).
"""

from __future__ import annotations

from typing import Any


def _as_dt(value: Any) -> Any:
    """Format an index label as date-like string when possible."""
    return value.date() if hasattr(value, "date") else value

import numpy as np
import pandas as pd

from .trade_input import TradeInput
from .utils import _sharpe


def walk_forward_analysis(
    equity_curve: pd.Series,
    trades: list[TradeInput] | None = None,
    n_windows: int = 5,
    bars_per_year: int = 252,
) -> dict[str, Any]:
    """Split backtest into sequential windows, check consistency.

    Each window is evaluated independently (returns normalised to window
    start).

    Args:
        equity_curve: Equity time series.
        trades: Optional list of trades (for per-window win-rate).
        n_windows: Number of non-overlapping windows.
        bars_per_year: Annualisation factor.

    Returns:
        Dict with per_window stats and consistency metrics.
    """
    if len(equity_curve) < n_windows * 2:
        return {
            "error": f"need at least {n_windows * 2} bars for {n_windows} windows",
            "n_bars": len(equity_curve),
        }

    indices = equity_curve.index
    window_size = len(indices) // n_windows
    windows: list[dict[str, Any]] = []

    for i in range(n_windows):
        start_idx = i * window_size
        end_idx = (i + 1) * window_size if i < n_windows - 1 else len(indices)
        win_eq = equity_curve.iloc[start_idx:end_idx]
        win_start = indices[start_idx]
        win_end = indices[end_idx - 1]

        win_trades = [
            t for t in (trades or [])
            if pd.Timestamp(win_start) <= t.entry_time <= pd.Timestamp(win_end)
        ]

        ret = float(win_eq.iloc[-1] / win_eq.iloc[0] - 1) if win_eq.iloc[0] > 0 else 0.0
        win_returns = win_eq.pct_change().dropna().values
        sharpe = _sharpe(win_returns, bars_per_year) if len(win_returns) > 1 else 0.0

        peak = win_eq.cummax()
        dd = (win_eq - peak) / peak.replace(0, 1)
        max_dd = float(dd.min())

        win_pnls = [t.pnl for t in win_trades]
        win_rate = len([p for p in win_pnls if p > 0]) / len(win_pnls) if win_pnls else 0.0

        windows.append(
            {
                "window": i + 1,
                "start": str(_as_dt(win_start)),
                "end": str(_as_dt(win_end)),
                "return": round(ret, 6),
                "sharpe": round(sharpe, 4),
                "max_dd": round(max_dd, 6),
                "trades": len(win_trades),
                "win_rate": round(win_rate, 4),
            }
        )

    returns_list = [w["return"] for w in windows]
    sharpes_list = [w["sharpe"] for w in windows]
    profitable_windows = sum(1 for r in returns_list if r > 0)

    return {
        "n_windows": n_windows,
        "windows": windows,
        "profitable_windows": profitable_windows,
        "consistency_rate": round(profitable_windows / n_windows, 4),
        "return_mean": round(float(np.mean(returns_list)), 6),
        "return_std": round(float(np.std(returns_list)), 6),
        "sharpe_mean": round(float(np.mean(sharpes_list)), 4),
        "sharpe_std": round(float(np.std(sharpes_list)), 4),
        "bars_per_year": bars_per_year,
    }


__all__ = ["walk_forward_analysis"]
