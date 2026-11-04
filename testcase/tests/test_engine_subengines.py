"""Tests for engine sub-engines (forex, crypto, global_futures, etc.)."""

from __future__ import annotations

import pandas as pd
import pytest

from strategy_research.apps.engine.crypto import CryptoEngine
from strategy_research.apps.engine.forex import ForexEngine
from strategy_research.apps.engine.global_equity import GlobalEquityEngine
from strategy_research.apps.engine.global_futures import (
    _GLOBAL_FUTURES_MULTIPLIERS,
    GlobalFuturesEngine,
    _get_global_product_code,
)
from strategy_research.apps.engine.india_equity import IndiaEquityEngine


class TestGlobalFuturesEngine:
    def test_get_product_code_simple(self):
        assert _get_global_product_code("ES") == "ES"
        assert _get_global_product_code("CL") == "CL"
        assert _get_global_product_code("GC") == "GC"

    def test_get_product_code_with_dot(self):
        assert _get_global_product_code("ES.HOT") == "ES"

    def test_get_product_code_with_digit_prefix(self):
        assert _get_global_product_code("6E.FUT") == "6E"

    def test_multipliers_dict_has_common_products(self):
        assert "ES" in _GLOBAL_FUTURES_MULTIPLIERS
        assert "CL" in _GLOBAL_FUTURES_MULTIPLIERS
        assert "GC" in _GLOBAL_FUTURES_MULTIPLIERS

    def test_engine_init(self):
        engine = GlobalFuturesEngine({"slippage": 0.001})
        assert engine.slippage_rate == 0.001

    def test_engine_default_slippage(self):
        engine = GlobalFuturesEngine({})
        assert engine.slippage_rate == 0.0005

    def test_can_execute_returns_true(self):
        engine = GlobalFuturesEngine({})
        bar = pd.Series({"close": 100})
        assert engine.can_execute("ES", 1, bar) is True

    def test_apply_slippage_long(self):
        engine = GlobalFuturesEngine({"slippage": 0.001})
        price = engine.apply_slippage(100.0, 1)
        assert price == pytest.approx(100.1)

    def test_apply_slippage_short(self):
        engine = GlobalFuturesEngine({"slippage": 0.001})
        price = engine.apply_slippage(100.0, -1)
        assert price == pytest.approx(99.9)


class TestCryptoEngine:
    def test_engine_init_defaults(self):
        engine = CryptoEngine({})
        assert engine.maker_rate == 0.0002
        assert engine.taker_rate == 0.0005
        assert engine.funding_rate == 0.0001

    def test_engine_init_custom(self):
        engine = CryptoEngine({
            "maker_rate": 0.0001,
            "taker_rate": 0.0003,
            "funding_rate": 0.0002,
        })
        assert engine.maker_rate == 0.0001
        assert engine.funding_rate == 0.0002

    def test_can_execute_24_7(self):
        engine = CryptoEngine({})
        bar = pd.Series({"close": 50000})
        assert engine.can_execute("BTCUSDT", 1, bar) is True
        assert engine.can_execute("ETHUSDT", -1, bar) is True

    def test_round_size(self):
        engine = CryptoEngine({})
        assert engine.round_size(1.234567890, 50000) == 1.234568
        assert engine.round_size(-0.5, 50000) == 0.0  # max(0, -0.5) = 0

    def test_calc_commission_open(self):
        engine = CryptoEngine({"taker_rate": 0.0005})
        cost = engine.calc_commission(1.0, 100.0, 1, is_open=True)
        assert cost == pytest.approx(100 * 0.0005)

    def test_calc_commission_close(self):
        engine = CryptoEngine({"maker_rate": 0.0002})
        cost = engine.calc_commission(1.0, 100.0, -1, is_open=False)
        assert cost == pytest.approx(100 * 0.0002)

    def test_apply_slippage(self):
        engine = CryptoEngine({"slippage": 0.001})
        assert engine.apply_slippage(100.0, 1) == pytest.approx(100.1)
        assert engine.apply_slippage(100.0, -1) == pytest.approx(99.9)


class TestForexEngine:
    def test_engine_init_defaults(self):
        engine = ForexEngine({})
        assert engine.spread_pips == 1.5
        assert engine.pip_value == 0.0001
        assert engine.slippage_rate == 0.0001

    def test_engine_init_custom(self):
        engine = ForexEngine({
            "spread_pips": 2.0,
            "swap_long": -1.0,
        })
        assert engine.spread_pips == 2.0
        assert engine.swap_long == -1.0

    def test_can_execute(self):
        engine = ForexEngine({})
        bar = pd.Series({"close": 1.1000})
        assert engine.can_execute("EURUSD", 1, bar) is True

    def test_round_size(self):
        engine = ForexEngine({})
        assert engine.round_size(1.234, 1.1) == 1.23
        assert engine.round_size(-1.0, 1.1) == 0.0

    def test_calc_commission(self):
        engine = ForexEngine({"spread_pips": 2.0, "pip_value": 0.0001})
        cost = engine.calc_commission(10000, 1.1, 1, is_open=True)
        assert cost == pytest.approx(10000 * 2.0 * 0.0001 / 2)

    def test_apply_slippage(self):
        engine = ForexEngine({"slippage": 0.0001})
        assert engine.apply_slippage(1.1000, 1) == pytest.approx(1.10011)


class TestGlobalEquityEngine:
    def test_engine_init_defaults_us(self):
        engine = GlobalEquityEngine({})
        assert engine.market == "us"
        assert engine.slippage_us == 0.0005

    def test_engine_init_defaults_hk(self):
        engine = GlobalEquityEngine({}, market="hk")
        assert engine.market == "hk"
        assert engine.slippage_hk == 0.001

    def test_can_execute(self):
        engine = GlobalEquityEngine({})
        bar = pd.Series({"close": 150})
        assert engine.can_execute("AAPL", 1, bar) is True

    def test_round_size_us_fractional(self):
        engine = GlobalEquityEngine({})  # US by default
        assert engine.round_size(10.5, 150) == 10.5

    def test_round_size_hk_lot(self):
        engine = GlobalEquityEngine({}, market="hk")
        assert engine.round_size(250, 100) == 200  # Rounded down to nearest 100

    def test_calc_commission_us_zero(self):
        engine = GlobalEquityEngine({})  # US
        cost = engine.calc_commission(10.0, 150.0, 1, is_open=True)
        assert cost == 0.0

    def test_calc_commission_hk(self):
        engine = GlobalEquityEngine({}, market="hk")
        cost = engine.calc_commission(100, 100, 1, is_open=True)
        # notional = 10000
        # comm = 10000 * 0.00015 = 1.5
        # stamp = 10000 * 0.001 = 10
        # levy = 10000 * 0.0000565 = 0.565
        # settlement = 10000 * 0.00002 = 0.2
        # total = 12.265
        assert cost == pytest.approx(12.265)

    def test_apply_slippage_us(self):
        engine = GlobalEquityEngine({})
        assert engine.apply_slippage(100.0, 1) == pytest.approx(100.05)

    def test_apply_slippage_hk(self):
        engine = GlobalEquityEngine({}, market="hk")
        assert engine.apply_slippage(100.0, 1) == pytest.approx(100.1)


class TestIndiaEquityEngine:
    def test_engine_init_defaults(self):
        engine = IndiaEquityEngine({})
        assert engine.brokerage == pytest.approx(0.0003)
        assert engine.stt == pytest.approx(0.001)
        assert engine.gst == pytest.approx(0.18)

    def test_can_execute(self):
        engine = IndiaEquityEngine({})
        bar = pd.Series({"close": 2000})
        assert engine.can_execute("RELIANCE", 1, bar) is True

    def test_round_size(self):
        engine = IndiaEquityEngine({})
        assert engine.round_size(10.5, 2000) == 10
        assert engine.round_size(-1.0, 2000) == 0

    def test_calc_commission_default(self):
        engine = IndiaEquityEngine({})
        cost = engine.calc_commission(5.0, 2000.0, 1, is_open=True)
        assert cost > 0

    def test_calc_commission_close_has_stt(self):
        engine = IndiaEquityEngine({})
        cost_open = engine.calc_commission(5.0, 2000.0, -1, is_open=True)
        cost_close = engine.calc_commission(5.0, 2000.0, -1, is_open=False)
        # Close should have higher cost due to STT + stamp duty
        assert cost_close > cost_open
