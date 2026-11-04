"""Unit tests for validation.walk_forward_analysis."""
from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from strategy_research.apps.validation.trade_input import TradeInput
from strategy_research.apps.validation.walk_forward import walk_forward_analysis


def _equity(n: int = 100, start: float = 100.0, drift: float = 0.001) -> pd.Series:
    idx = pd.date_range("2024-01-01", periods=n, freq="D")
    vals = start * (1 + drift) ** np.arange(n)
    return pd.Series(vals, index=idx)


def _trade(entry: datetime, pnl: float = 1.0) -> TradeInput:
    return TradeInput(
        symbol="S",
        direction=1,
        entry_price=10.0,
        exit_price=11.0,
        entry_time=entry,
        exit_time=entry + timedelta(hours=1),
        size=1.0,
        pnl=pnl,
        pnl_pct=pnl / 100.0,
    )


class TestTooShortSeries:
    def test_returns_error_dict(self):
        eq = _equity(n=4)
        out = walk_forward_analysis(eq, n_windows=5)
        assert "error" in out
        assert "need at least" in out["error"]
        assert out["n_bars"] == 4


class TestBasicShape:
    def test_has_expected_keys(self):
        out = walk_forward_analysis(_equity(50), n_windows=5)
        for key in (
            "n_windows", "windows", "profitable_windows",
            "consistency_rate", "return_mean", "return_std",
            "sharpe_mean", "sharpe_std", "bars_per_year",
        ):
            assert key in out

    def test_window_count(self):
        out = walk_forward_analysis(_equity(50), n_windows=5)
        assert out["n_windows"] == 5
        assert len(out["windows"]) == 5

    def test_window_fields(self):
        out = walk_forward_analysis(_equity(50), n_windows=3)
        w = out["windows"][0]
        for key in ("window", "start", "end", "return", "sharpe", "max_dd", "trades", "win_rate"):
            assert key in w

    def test_profitable_windows_on_uptrend(self):
        out = walk_forward_analysis(_equity(50, drift=0.01), n_windows=5)
        assert out["profitable_windows"] == 5
        assert out["consistency_rate"] == 1.0


class TestTradesAttribution:
    def test_trades_counted_per_window(self):
        eq = _equity(50)
        t0 = eq.index[5].to_pydatetime()
        trades = [_trade(t0, pnl=1.0), _trade(t0, pnl=-1.0)]
        out = walk_forward_analysis(eq, trades=trades, n_windows=5)
        total = sum(w["trades"] for w in out["windows"])
        assert total == 2

    def test_win_rate_all_win(self):
        eq = _equity(50)
        t0 = eq.index[5].to_pydatetime()
        trades = [_trade(t0, pnl=2.0), _trade(t0, pnl=3.0)]
        out = walk_forward_analysis(eq, trades=trades, n_windows=5)
        rates = [w["win_rate"] for w in out["windows"] if w["trades"] > 0]
        assert rates and all(r == 1.0 for r in rates)


class TestEdgeCases:
    def test_flat_equity_zero_return(self):
        idx = pd.date_range("2024-01-01", periods=20, freq="D")
        eq = pd.Series(100.0, index=idx)
        out = walk_forward_analysis(eq, n_windows=4)
        assert all(w["return"] == 0.0 for w in out["windows"])

    def test_custom_bars_per_year(self):
        out = walk_forward_analysis(_equity(50), n_windows=5, bars_per_year=12)
        assert out["bars_per_year"] == 12

    def test_consistency_rate_bounded(self):
        out = walk_forward_analysis(_equity(50, drift=-0.01), n_windows=5)
        assert 0.0 <= out["consistency_rate"] <= 1.0
