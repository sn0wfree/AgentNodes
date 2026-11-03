"""Tests for market engines — ChinaA / GlobalEquity / Crypto / Forex / Futures / Composite"""

from __future__ import annotations

import pandas as pd

from strategy_research.apps.engine.china_a import ChinaAEngine
from strategy_research.apps.engine.china_futures import ChinaFuturesEngine
from strategy_research.apps.engine.composite import CompositeEngine
from strategy_research.apps.engine.crypto import CryptoEngine
from strategy_research.apps.engine.forex import ForexEngine
from strategy_research.apps.engine.global_equity import GlobalEquityEngine
from strategy_research.apps.engine.india_equity import IndiaEquityEngine
from strategy_research.apps.engine.signals import ConstantWeightEngine

# ============================================================
# helpers
# ============================================================


def _make_data(
    start: str = "2024-01-02",
    n_days: int = 20,
    codes: list[str] | None = None,
    trend: float = 0.001,
) -> dict[str, pd.DataFrame]:
    if codes is None:
        codes = ["A"]
    dates = pd.bdate_range(start, periods=n_days)
    result = {}
    for i, code in enumerate(codes):
        base = 100.0 + i * 10
        prices = [base * (1 + trend) ** j for j in range(n_days)]
        result[code] = pd.DataFrame(
            {
                "open": [p * 0.999 for p in prices],
                "high": [p * 1.005 for p in prices],
                "low": [p * 0.995 for p in prices],
                "close": prices,
                "volume": [1000.0 + j * 10 for j in range(n_days)],
            },
            index=dates,
        )
    return result


def _run_engine(engine, data_map, weights, codes=None):
    if codes is None:
        codes = list(data_map.keys())
    sig_eng = ConstantWeightEngine(weights)
    signal_map = sig_eng.generate(data_map)
    return engine.run_backtest(data_map, signal_map, codes)


# ============================================================
# ChinaAEngine
# ============================================================


class TestChinaAEngine:
    def test_no_short(self):
        engine = ChinaAEngine({})
        bar = pd.Series({"open": 100, "close": 100})
        assert engine.can_execute("000001.SZ", -1, bar) is False

    def test_t_plus_1(self):
        engine = ChinaAEngine({})
        ts = pd.Timestamp("2024-01-02")
        bar = pd.Series({"open": 100, "close": 100}, name=ts)
        from strategy_research.apps.engine.models import Position
        engine.positions["000001.SZ"] = Position(
            symbol="000001.SZ", direction=1, entry_price=100.0,
            entry_time=ts, size=100,
        )
        # Same day: can't sell
        assert engine.can_execute("000001.SZ", 0, bar) is False

    def test_100_share_lots(self):
        engine = ChinaAEngine({})
        assert engine.round_size(150, 100) == 100
        assert engine.round_size(99, 100) == 0
        assert engine.round_size(250, 100) == 200

    def test_commission_with_minimum(self):
        engine = ChinaAEngine({})
        # Small trade: commission_min applies
        comm = engine.calc_commission(10, 10.0, 1, is_open=True)
        assert comm >= 5.0  # ¥5 minimum

    def test_stamp_tax_sell_only(self):
        engine = ChinaAEngine({})
        buy_comm = engine.calc_commission(1000, 100.0, 1, is_open=True)
        sell_comm = engine.calc_commission(1000, 100.0, 1, is_open=False)
        assert sell_comm > buy_comm

    def test_run_backtest(self):
        engine = ChinaAEngine({})
        data = _make_data(n_days=20, codes=["000001.SZ"])
        m = _run_engine(engine, data, {"000001.SZ": 1.0})
        assert m["trade_count"] >= 1


# ============================================================
# GlobalEquityEngine
# ============================================================


class TestGlobalEquityEngine:
    def test_us_zero_commission(self):
        engine = GlobalEquityEngine({}, market="us")
        comm = engine.calc_commission(100, 150.0, 1, is_open=True)
        assert comm == 0.0

    def test_hk_commission(self):
        engine = GlobalEquityEngine({}, market="hk")
        comm = engine.calc_commission(100, 150.0, 1, is_open=True)
        assert comm > 0

    def test_us_fractional(self):
        engine = GlobalEquityEngine({}, market="us")
        assert engine.round_size(10.5, 100) == 10.5

    def test_hk_100_lots(self):
        engine = GlobalEquityEngine({}, market="hk")
        assert engine.round_size(150, 100) == 100

    def test_can_short(self):
        engine = GlobalEquityEngine({}, market="us")
        bar = pd.Series({"open": 100, "close": 100})
        assert engine.can_execute("AAPL", -1, bar) is True


# ============================================================
# CryptoEngine
# ============================================================


class TestCryptoEngine:
    def test_24_7_execution(self):
        engine = CryptoEngine({})
        bar = pd.Series({"open": 50000, "close": 50000})
        assert engine.can_execute("BTC-USDT", 1, bar) is True
        assert engine.can_execute("BTC-USDT", -1, bar) is True

    def test_commission_taker_maker(self):
        engine = CryptoEngine({})
        open_comm = engine.calc_commission(1.0, 50000, 1, is_open=True)
        close_comm = engine.calc_commission(1.0, 50000, 1, is_open=False)
        assert open_comm > close_comm  # taker > maker

    def test_round_size_6_decimals(self):
        engine = CryptoEngine({})
        assert engine.round_size(1.123456789, 50000) == 1.123457

    def test_run_backtest(self):
        engine = CryptoEngine({})
        data = _make_data(n_days=20, codes=["BTC-USDT"])
        m = _run_engine(engine, data, {"BTC-USDT": 1.0})
        assert m["trade_count"] >= 1


# ============================================================
# ForexEngine
# ============================================================


class TestForexEngine:
    def test_spread_cost(self):
        engine = ForexEngine({})
        comm = engine.calc_commission(100000, 1.1, 1, is_open=True)
        assert comm > 0

    def test_fractional_size(self):
        engine = ForexEngine({})
        assert engine.round_size(10.5, 1.1) == 10.5


# ============================================================
# IndiaEquityEngine
# ============================================================


class TestIndiaEquityEngine:
    def test_commission_with_gst(self):
        engine = IndiaEquityEngine({})
        comm = engine.calc_commission(100, 1000.0, 1, is_open=True)
        assert comm > 0

    def test_integer_lots(self):
        engine = IndiaEquityEngine({})
        assert engine.round_size(10.5, 100) == 10


# ============================================================
# FuturesBaseEngine
# ============================================================


class TestFuturesBaseEngine:
    def test_pnl_with_multiplier(self):
        engine = ChinaFuturesEngine({"contract_multiplier": 10.0})
        pnl = engine._calc_pnl("CU2501.SHFE", 1, 1, 100.0, 101.0)
        assert pnl == 10.0  # 1 * 1 * 1 * 10

    def test_margin_with_multiplier(self):
        engine = ChinaFuturesEngine({"contract_multiplier": 10.0, "margin_rate": 0.1})
        margin = engine._calc_margin("CU2501.SHFE", 1, 100.0, 1.0)
        assert margin == 100.0  # 1 * 100 * 10 * 0.1

    def test_commission_per_contract(self):
        engine = ChinaFuturesEngine({"commission_per_contract": 3.0})
        comm = engine.calc_commission(5, 100.0, 1, is_open=True)
        assert comm == 15.0


# ============================================================
# CompositeEngine
# ============================================================


class TestCompositeEngine:
    def test_multi_market(self):
        codes = ["000001.SZ", "AAPL", "BTC-USDT"]
        engine = CompositeEngine({}, codes)
        data = {}
        dates = pd.bdate_range("2024-01-02", periods=10)
        for code in codes:
            base = 100.0 if "SZ" in code else (150.0 if code == "AAPL" else 50000.0)
            prices = [base * 1.001 ** j for j in range(10)]
            data[code] = pd.DataFrame({
                "open": [p * 0.999 for p in prices],
                "high": [p * 1.005 for p in prices],
                "low": [p * 0.995 for p in prices],
                "close": prices,
                "volume": [1000.0] * 10,
            }, index=dates)

        weights = {c: 1.0 / len(codes) for c in codes}
        m = _run_engine(engine, data, weights, codes)
        assert m["trade_count"] >= 0

    def test_market_detection(self):
        engine = CompositeEngine({}, ["000001.SZ", "AAPL", "BTC-USDT"])
        assert engine._symbol_market["000001.SZ"] == "a_share"
        assert engine._symbol_market["AAPL"] == "us_equity"
        assert engine._symbol_market["BTC-USDT"] == "crypto"
