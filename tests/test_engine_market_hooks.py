"""Tests for engine market hooks."""

from __future__ import annotations

import pandas as pd
import pytest

from strategy_research.core.engine.market_hooks import (
    _TIER_TABLE,
    FUNDING_HOURS,
    _maintenance_rate,
    calc_crypto_funding_fee,
    check_crypto_liquidation,
)
from strategy_research.foundation.utils.backtest_models import Position


class TestFundingHours:
    def test_funding_hours_set(self):
        assert FUNDING_HOURS == {0, 8, 16}


class TestCalcCryptoFundingFee:
    def test_no_position(self):
        bar = pd.Series({"close": 100})
        fee = calc_crypto_funding_fee(
            "BTCUSDT", bar, pd.Timestamp("2023-01-01 08:00"),
            positions={}, funding_rate=0.0001, applied_set=set(), daily_done_set=set(),
        )
        assert fee == 0.0

    def test_long_position_funding(self):
        pos = Position(
            symbol="BTCUSDT",
            direction=1,
            entry_price=100,
            entry_time=pd.Timestamp("2023-01-01"),
            size=10,
            leverage=1.0,
        )
        bar = pd.Series({"close": 105})
        fee = calc_crypto_funding_fee(
            "BTCUSDT", bar, pd.Timestamp("2023-01-01 08:00"),
            positions={"BTCUSDT": pos}, funding_rate=0.0001, applied_set=set(), daily_done_set=set(),
        )
        # 10 * 105 * 0.0001 * 1 = 0.105
        assert fee == pytest.approx(0.105)

    def test_short_position_funding(self):
        pos = Position(
            symbol="BTCUSDT",
            direction=-1,
            entry_price=100,
            entry_time=pd.Timestamp("2023-01-01"),
            size=10,
            leverage=1.0,
        )
        bar = pd.Series({"close": 105})
        fee = calc_crypto_funding_fee(
            "BTCUSDT", bar, pd.Timestamp("2023-01-01 08:00"),
            positions={"BTCUSDT": pos}, funding_rate=0.0001, applied_set=set(), daily_done_set=set(),
        )
        # 10 * 105 * 0.0001 * -1 = -0.105
        assert fee == pytest.approx(-0.105)

    def test_dedup_at_funding_hour(self):
        pos = Position(
            symbol="BTCUSDT",
            direction=1,
            entry_price=100,
            entry_time=pd.Timestamp("2023-01-01"),
            size=10,
        )
        bar = pd.Series({"close": 100})
        applied = set()
        daily = set()

        # First call at funding hour should apply
        fee1 = calc_crypto_funding_fee(
            "BTCUSDT", bar, pd.Timestamp("2023-01-01 08:00"),
            positions={"BTCUSDT": pos}, funding_rate=0.0001,
            applied_set=applied, daily_done_set=daily,
        )
        assert fee1 > 0

        # Second call same hour should be 0
        fee2 = calc_crypto_funding_fee(
            "BTCUSDT", bar, pd.Timestamp("2023-01-01 08:00"),
            positions={"BTCUSDT": pos}, funding_rate=0.0001,
            applied_set=applied, daily_done_set=daily,
        )
        assert fee2 == 0.0


class TestCheckCryptoLiquidation:
    def test_no_position(self):
        bar = pd.Series({"close": 100})
        assert check_crypto_liquidation("BTCUSDT", bar, {}) is False

    def test_no_leverage(self):
        pos = Position(
            symbol="BTCUSDT",
            direction=1,
            entry_price=100,
            entry_time=pd.Timestamp("2023-01-01"),
            size=10,
            leverage=1.0,
        )
        bar = pd.Series({"close": 50})  # Big drop
        # Leverage <= 1.0 should never liquidate
        assert check_crypto_liquidation("BTCUSDT", bar, {"BTCUSDT": pos}) is False

    def test_no_liquidation_safe(self):
        pos = Position(
            symbol="BTCUSDT",
            direction=1,
            entry_price=100,
            entry_time=pd.Timestamp("2023-01-01"),
            size=1,
            leverage=10,
        )
        bar = pd.Series({"close": 95})  # Small drop, safe
        assert check_crypto_liquidation("BTCUSDT", bar, {"BTCUSDT": pos}) is False

    def test_liquidation_triggered(self):
        pos = Position(
            symbol="BTCUSDT",
            direction=1,
            entry_price=100,
            entry_time=pd.Timestamp("2023-01-01"),
            size=1,
            leverage=20,
        )
        bar = pd.Series({"close": 50})  # Huge drop with high leverage
        # Should trigger liquidation
        assert check_crypto_liquidation("BTCUSDT", bar, {"BTCUSDT": pos}) is True


class TestMaintenanceRate:
    def test_low_notional(self):
        rate = _maintenance_rate(50_000)
        assert rate == 0.004  # First tier

    def test_mid_notional(self):
        rate = _maintenance_rate(200_000)
        assert rate == 0.006

    def test_high_notional(self):
        rate = _maintenance_rate(800_000)
        assert rate == 0.01  # 3rd tier

    def test_very_high_notional(self):
        rate = _maintenance_rate(8_000_000)
        assert rate == 0.05  # 5th tier

    def test_extreme_notional(self):
        rate = _maintenance_rate(200_000_000)
        assert rate == 0.10  # Max tier

    def test_tier_table_order(self):
        for threshold, rate in _TIER_TABLE:
            assert isinstance(threshold, (int, float))
            assert isinstance(rate, float)
