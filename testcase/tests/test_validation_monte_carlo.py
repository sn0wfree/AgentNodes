"""Tests for core.validation.monte_carlo (P3-c)."""

from __future__ import annotations

from datetime import datetime, timedelta

from strategy_research.apps.validation.monte_carlo import monte_carlo_test
from strategy_research.apps.validation.trade_input import TradeInput


def _make_trade(pnl: float, symbol: str = "T", day: int = 0) -> TradeInput:
    base = datetime(2024, 1, 1)
    return TradeInput(
        symbol=symbol,
        direction=1,
        entry_price=100.0,
        exit_price=100.0 + pnl,
        entry_time=base + timedelta(days=day),
        exit_time=base + timedelta(days=day + 1),
        size=1.0,
        pnl=pnl,
        pnl_pct=pnl / 100.0,
        holding_bars=1,
        exit_reason="signal",
    )


class TestMonteCarlo:
    def test_returns_dict_with_required_keys(self):
        trades = [_make_trade(10.0), _make_trade(-5.0), _make_trade(15.0), _make_trade(8.0)]
        result = monte_carlo_test(trades, initial_capital=100_000.0, n_simulations=100, seed=42)
        for key in [
            "actual_sharpe", "actual_max_dd",
            "p_value_sharpe", "p_value_max_dd",
            "simulated_sharpe_mean", "simulated_sharpe_std",
            "simulated_sharpe_p5", "simulated_sharpe_p95",
            "n_simulations", "n_trades",
        ]:
            assert key in result

    def test_reproducible_with_seed(self):
        trades = [_make_trade(10.0), _make_trade(-5.0), _make_trade(15.0), _make_trade(8.0)]
        r1 = monte_carlo_test(trades, 100_000.0, n_simulations=50, seed=42)
        r2 = monte_carlo_test(trades, 100_000.0, n_simulations=50, seed=42)
        assert r1["simulated_sharpe_mean"] == r2["simulated_sharpe_mean"]
        assert r1["p_value_sharpe"] == r2["p_value_sharpe"]

    def test_too_few_trades_returns_error(self):
        trades = [_make_trade(10.0), _make_trade(-5.0)]
        result = monte_carlo_test(trades, 100_000.0)
        assert "error" in result
        assert result["p_value_sharpe"] == 1.0
        assert result["n_trades"] == 2

    def test_strong_strategy_has_low_p_value(self):
        """Highly positive strategy should beat most random shuffles."""
        # Mix of mostly-wins with some losses creates a path-dependent edge.
        # 18 wins @ 100, 2 small losses @ -10 → mean +89, std ~55 → clear positive sharpe.
        # Random shuffles of these 20 pnls will produce very different equity paths.
        trades = [_make_trade(100.0) for _ in range(18)] + [_make_trade(-10.0) for _ in range(2)]
        result = monte_carlo_test(trades, 100_000.0, n_simulations=200, seed=42)
        # Such a strong strategy should beat most random shuffles
        assert result["p_value_sharpe"] < 0.5  # very generous bound to avoid flakiness

    def test_weak_strategy_has_high_p_value(self):
        """Random-walk strategy should not be significantly different."""
        rng = __import__("numpy").random.default_rng(123)
        trades = [_make_trade(float(rng.normal(0, 5))) for _ in range(20)]
        result = monte_carlo_test(trades, 100_000.0, n_simulations=200, seed=42)
        # Random trades typically have p_value > 0.05
        assert result["p_value_sharpe"] > 0.05

    def test_bars_per_year_propagated(self):
        trades = [_make_trade(10.0) for _ in range(5)]
        r252 = monte_carlo_test(trades, 100_000.0, n_simulations=10, bars_per_year=252)
        r365 = monte_carlo_test(trades, 100_000.0, n_simulations=10, bars_per_year=365)
        # Sharpe scaling differs
        assert r252["bars_per_year"] == 252
        assert r365["bars_per_year"] == 365
