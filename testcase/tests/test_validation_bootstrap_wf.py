"""Tests for core.validation.bootstrap + walk_forward (P3-c)."""

from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from strategy_research.apps.validation.bootstrap import bootstrap_sharpe_ci
from strategy_research.apps.validation.trade_input import TradeInput
from strategy_research.apps.validation.walk_forward import walk_forward_analysis


def _make_equity_curve(n: int = 100, start: float = 100_000.0, drift: float = 0.001, vol: float = 0.01, seed: int = 42) -> pd.Series:
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2024-01-01", periods=n, freq="D")
    returns = rng.normal(drift, vol, n)
    equity = start * (1 + pd.Series(returns, index=dates)).cumprod()
    return equity


def _make_trade(pnl: float, day: int = 0) -> TradeInput:
    base = datetime(2024, 1, 1)
    return TradeInput(
        symbol="T", direction=1,
        entry_price=100.0, exit_price=100.0 + pnl,
        entry_time=base + timedelta(days=day),
        exit_time=base + timedelta(days=day + 1),
        size=1.0, pnl=pnl, pnl_pct=pnl / 100.0,
        holding_bars=1, exit_reason="signal",
    )


# ─── Bootstrap ─────────────────────────────────────────────────────────


class TestBootstrap:
    def test_returns_required_keys(self):
        eq = _make_equity_curve(n=100)
        result = bootstrap_sharpe_ci(eq, n_bootstrap=50, seed=42)
        for key in [
            "observed_sharpe", "ci_lower", "ci_upper",
            "median_sharpe", "prob_positive",
            "confidence", "n_bootstrap", "n_returns",
        ]:
            assert key in result

    def test_reproducible(self):
        eq = _make_equity_curve(n=100)
        r1 = bootstrap_sharpe_ci(eq, n_bootstrap=50, seed=42)
        r2 = bootstrap_sharpe_ci(eq, n_bootstrap=50, seed=42)
        assert r1["ci_lower"] == r2["ci_lower"]
        assert r1["ci_upper"] == r2["ci_upper"]

    def test_too_few_returns(self):
        eq = _make_equity_curve(n=3)
        result = bootstrap_sharpe_ci(eq)
        assert "error" in result

    def test_positive_strategy_high_prob(self):
        """Strong positive drift → prob_positive should be high."""
        eq = _make_equity_curve(n=252, drift=0.002, vol=0.005, seed=42)
        result = bootstrap_sharpe_ci(eq, n_bootstrap=100, seed=42)
        assert result["prob_positive"] > 0.7

    def test_confidence_level_changes_width(self):
        eq = _make_equity_curve(n=100)
        r90 = bootstrap_sharpe_ci(eq, n_bootstrap=100, confidence=0.90, seed=42)
        r99 = bootstrap_sharpe_ci(eq, n_bootstrap=100, confidence=0.99, seed=42)
        # 99% CI should be wider than 90% CI
        assert (r99["ci_upper"] - r99["ci_lower"]) >= (r90["ci_upper"] - r90["ci_lower"])


# ─── Walk-Forward ──────────────────────────────────────────────────────


class TestWalkForward:
    def test_returns_required_keys(self):
        eq = _make_equity_curve(n=100)
        result = walk_forward_analysis(eq, n_windows=5)
        for key in [
            "n_windows", "windows", "profitable_windows",
            "consistency_rate", "return_mean", "return_std",
            "sharpe_mean", "sharpe_std",
        ]:
            assert key in result

    def test_window_count_matches_request(self):
        eq = _make_equity_curve(n=100)
        result = walk_forward_analysis(eq, n_windows=4)
        assert result["n_windows"] == 4
        assert len(result["windows"]) == 4

    def test_too_few_bars(self):
        eq = _make_equity_curve(n=5)
        result = walk_forward_analysis(eq, n_windows=5)
        assert "error" in result

    def test_positive_drift_all_windows_profitable(self):
        """Strong positive drift → most windows should be profitable."""
        eq = _make_equity_curve(n=252, drift=0.002, vol=0.005, seed=42)
        result = walk_forward_analysis(eq, n_windows=5)
        assert result["profitable_windows"] >= 3

    def test_trades_optional(self):
        """walk_forward_analysis should work without trades."""
        eq = _make_equity_curve(n=100)
        result = walk_forward_analysis(eq, n_windows=5)
        assert result["n_windows"] == 5
        for w in result["windows"]:
            assert w["trades"] == 0
            assert w["win_rate"] == 0.0

    def test_per_window_trade_count(self):
        eq = _make_equity_curve(n=100)
        trades = [_make_trade(10.0, day=i) for i in range(0, 100, 5)]
        result = walk_forward_analysis(eq, trades, n_windows=5)
        # Total trades across all windows ≤ total trades
        total_window_trades = sum(w["trades"] for w in result["windows"])
        assert total_window_trades == len(trades)
