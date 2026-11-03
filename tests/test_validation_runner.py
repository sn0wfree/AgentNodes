"""Tests for core.validation.runner (P3-c) — orchestration."""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from strategy_research.apps.validation import MarketType
from strategy_research.apps.validation.runner import run_validation
from strategy_research.apps.validation.trade_input import TradeInput


def _make_equity_curve(n: int = 100, seed: int = 42) -> pd.Series:
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2024-01-01", periods=n, freq="D")
    returns = rng.normal(0.001, 0.01, n)
    return 100_000.0 * (1 + pd.Series(returns, index=dates)).cumprod()


def _make_trade(pnl: float, day: int = 0) -> TradeInput:
    base = datetime(2024, 1, 1)
    return TradeInput(
        symbol="T", direction=1,
        entry_price=100.0, exit_price=100.0 + pnl,
        entry_time=base + timedelta(days=day),
        exit_time=base + timedelta(days=day + 1),
        size=1.0, pnl=pnl, pnl_pct=pnl / 100.0,
    )


class TestRunValidation:
    def test_empty_config_returns_only_meta(self):
        eq = _make_equity_curve()
        result = run_validation({}, eq, trades=[], market=MarketType.A_SHARE)
        assert result["market"] == "a_share"
        assert result["bars_per_year"] == 252
        assert "monte_carlo" not in result
        assert "bootstrap" not in result
        assert "walk_forward" not in result

    def test_all_three_enabled(self):
        eq = _make_equity_curve(n=252)
        trades = [_make_trade(50.0) for _ in range(20)]
        config = {
            "validation": {
                "monte_carlo": True,
                "bootstrap": True,
                "walk_forward": True,
            }
        }
        result = run_validation(
            config, eq, trades=trades,
            initial_capital=100_000.0, market=MarketType.A_SHARE,
        )
        assert "monte_carlo" in result
        assert "bootstrap" in result
        assert "walk_forward" in result

    def test_dict_overrides(self):
        eq = _make_equity_curve(n=252)
        config = {
            "validation": {
                "monte_carlo": {"n_simulations": 50, "seed": 7},
                "bootstrap": {"n_bootstrap": 50, "confidence": 0.9, "seed": 7},
                "walk_forward": {"n_windows": 3},
            }
        }
        result = run_validation(config, eq, trades=[_make_trade(10.0) for _ in range(5)])
        assert result["monte_carlo"]["n_simulations"] == 50
        assert result["bootstrap"]["n_bootstrap"] == 50
        assert result["bootstrap"]["confidence"] == 0.9
        assert result["walk_forward"]["n_windows"] == 3

    def test_market_specific_bars_per_year(self):
        """Each market type uses its own bars_per_year.

        All 7 market types are now supported with correct bars_per_year values.
        """
        eq = _make_equity_curve(n=100)
        config = {"validation": {"monte_carlo": True}}
        result = run_validation(
            config, eq, trades=[_make_trade(10.0) for _ in range(5)],
            market=MarketType.CRYPTO,
        )
        # CRYPTO has its own bars_per_year (365)
        assert result["bars_per_year"] == 365
        assert result["market"] == "crypto"  # tag preserved
        assert result["monte_carlo"]["bars_per_year"] == 365

    def test_json_serializable(self):
        """Results must be JSON-serializable (allow_nan=False safe)."""
        eq = _make_equity_curve(n=100)
        config = {"validation": {"monte_carlo": True, "bootstrap": True, "walk_forward": True}}
        result = run_validation(config, eq, trades=[_make_trade(10.0) for _ in range(5)])
        # No NaN/inf in output
        encoded = json.dumps(result, allow_nan=False)
        decoded = json.loads(encoded)
        assert decoded["market"] == "a_share"

    def test_supported_markets_no_warning(self, recwarn):
        eq = _make_equity_curve(n=100)
        config = {"validation": {"monte_carlo": True}}
        run_validation(
            config, eq, trades=[_make_trade(10.0) for _ in range(5)],
            market=MarketType.HK_EQUITY,
        )
        # HK_EQUITY is supported; no warning
        assert not any("not yet implemented" in str(w.message) for w in recwarn)

    def test_default_market_is_a_share(self):
        eq = _make_equity_curve(n=100)
        result = run_validation({}, eq)
        assert result["market"] == "a_share"
        assert result["bars_per_year"] == 252
