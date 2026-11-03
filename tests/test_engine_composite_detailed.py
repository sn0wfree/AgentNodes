"""CompositeEngine 详细单元测试。

覆盖:
- _detect_market_simple 全分支
- 跨市场状态共享
- 子引擎委托
- 混合资产组合
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from strategy_research.apps.engine.composite import CompositeEngine, _detect_market_simple
from strategy_research.apps.engine.models import Position


def _bar(date, close, name=True):
    s = pd.Series({"open": close, "high": close * 1.001, "low": close * 0.999, "close": close, "volume": 1000.0})
    if name:
        s.name = pd.Timestamp(date)
    return s


@staticmethod
def _make_data(symbol, n_bars=20, start_price=10.0, freq="B"):
    dates = pd.date_range("2024-01-02", periods=n_bars, freq=freq)
    np.random.seed(hash(symbol) % 2**31)
    rets = np.random.normal(0.001, 0.005, n_bars)
    prices = start_price * (1 + pd.Series(rets)).cumprod().values
    opens = np.empty(n_bars)
    opens[0] = start_price
    opens[1:] = prices[:-1]
    df = pd.DataFrame({
        "open": opens, "high": prices * 1.005, "low": prices * 0.995,
        "close": prices, "volume": np.full(n_bars, 1000.0),
    }, index=dates)
    df.index.name = "date"
    return df


# ─────────────────────────────────────────────
# _detect_market_simple
# ─────────────────────────────────────────────
class TestDetectMarketSimple:
    def test_a_share_sz(self):
        assert _detect_market_simple("000001.SZ") == "a_share"

    def test_a_share_sh(self):
        assert _detect_market_simple("600000.SH") == "a_share"

    def test_a_share_bj(self):
        assert _detect_market_simple("830001.BJ") == "a_share"

    def test_hk_equity(self):
        assert _detect_market_simple("0700.HK") == "hk_equity"

    def test_india_ns(self):
        assert _detect_market_simple("RELIANCE.NS") == "india_equity"

    def test_india_bo(self):
        assert _detect_market_simple("TCS.BO") == "india_equity"

    def test_crypto_dash(self):
        assert _detect_market_simple("BTC-USDT") == "crypto"

    def test_crypto_slash(self):
        assert _detect_market_simple("ETH/USDT") == "crypto"

    def test_forex_6chars(self):
        assert _detect_market_simple("EURUSD") == "forex"
        assert _detect_market_simple("GBPJPY") == "forex"

    def test_us_equity(self):
        assert _detect_market_simple("AAPL") == "us_equity"
        assert _detect_market_simple("MSFT") == "us_equity"

    def test_7char_fallback(self):
        # 7 chars → a_share (not forex which requires exactly 6)
        assert _detect_market_simple("EURUSDX") == "a_share"

    def test_numeric_only(self):
        assert _detect_market_simple("600000") == "a_share"

    def test_long_alpha_4chars(self):
        assert _detect_market_simple("BABA") == "us_equity"


# ─────────────────────────────────────────────
# CompositeEngine init
# ─────────────────────────────────────────────
class TestCompositeInit:
    def test_symbol_market_built(self):
        codes = ["600000.SH", "0700.HK", "AAPL"]
        cfg = {"codes": codes, "initial_cash": 1_000_000.0}
        eng = CompositeEngine(cfg, codes)
        assert eng._symbol_market["600000.SH"] == "a_share"
        assert eng._symbol_market["0700.HK"] == "hk_equity"
        assert eng._symbol_market["AAPL"] == "us_equity"

    def test_rule_engines_count(self):
        eng = CompositeEngine({"codes": ["600000.SH"]}, ["600000.SH"])
        assert len(eng._rule_engines) == 5

    def test_initial_capital(self):
        eng = CompositeEngine({"codes": ["AAPL"], "initial_cash": 500_000.0}, ["AAPL"])
        assert eng.initial_capital == 500_000.0


# ─────────────────────────────────────────────
# can_execute: delegates per market
# ─────────────────────────────────────────────
class TestCanExecute:
    def test_a_share_long_allowed(self):
        eng = CompositeEngine({"codes": ["600000.SH"]}, ["600000.SH"])
        bar = _bar("2024-01-02", 10.0)
        assert eng.can_execute("600000.SH", 1, bar) is True

    def test_a_share_t1_close_same_day(self):
        eng = CompositeEngine({"codes": ["600000.SH"]}, ["600000.SH"])
        ts = pd.Timestamp("2024-01-02")
        eng.positions["600000.SH"] = Position(
            symbol="600000.SH", direction=1, entry_price=10.0,
            entry_time=ts, size=100, leverage=1.0,
        )
        bar = _bar("2024-01-02", 10.5)
        assert eng.can_execute("600000.SH", 0, bar) is False

    def test_us_equity_always_allowed(self):
        eng = CompositeEngine({"codes": ["AAPL"]}, ["AAPL"])
        bar = _bar("2024-01-02", 150.0)
        assert eng.can_execute("AAPL", 0, bar) is True
        assert eng.can_execute("AAPL", 1, bar) is True
        assert eng.can_execute("AAPL", -1, bar) is True

    def test_crypto_always_allowed(self):
        eng = CompositeEngine({"codes": ["BTC-USDT"]}, ["BTC-USDT"])
        bar = _bar("2024-01-02", 40000.0)
        assert eng.can_execute("BTC-USDT", -1, bar) is True

    def test_forex_always_allowed(self):
        eng = CompositeEngine({"codes": ["EURUSD"]}, ["EURUSD"])
        bar = _bar("2024-01-02", 1.1)
        assert eng.can_execute("EURUSD", -1, bar) is True

    def test_hk_equity_always_allowed(self):
        eng = CompositeEngine({"codes": ["0700.HK"]}, ["0700.HK"])
        bar = _bar("2024-01-02", 300.0)
        assert eng.can_execute("0700.HK", -1, bar) is True


# ─────────────────────────────────────────────
# round_size delegation
# ─────────────────────────────────────────────
class TestRoundSize:
    def test_a_share_100lot(self):
        eng = CompositeEngine({"codes": ["600000.SH"]}, ["600000.SH"])
        eng._active_symbol = "600000.SH"
        assert eng.round_size(150, 10.0) == 100

    def test_us_fractional(self):
        eng = CompositeEngine({"codes": ["AAPL"]}, ["AAPL"])
        eng._active_symbol = "AAPL"
        result = eng.round_size(123.456, 150.0)
        assert result == pytest.approx(123.46)

    def test_hk_100lot(self):
        eng = CompositeEngine({"codes": ["0700.HK"]}, ["0700.HK"])
        eng._active_symbol = "0700.HK"
        assert eng.round_size(550, 300.0) == 500

    def test_crypto_6decimal(self):
        eng = CompositeEngine({"codes": ["BTC-USDT"]}, ["BTC-USDT"])
        eng._active_symbol = "BTC-USDT"
        result = eng.round_size(1.23456789, 40000.0)
        assert result == pytest.approx(1.234568)

    def test_forex_2decimal(self):
        eng = CompositeEngine({"codes": ["EURUSD"]}, ["EURUSD"])
        eng._active_symbol = "EURUSD"
        result = eng.round_size(1234.567, 1.1)
        assert result == pytest.approx(1234.57)


# ─────────────────────────────────────────────
# calc_commission delegation
# ─────────────────────────────────────────────
class TestCalcCommission:
    def test_a_share_commission(self):
        eng = CompositeEngine({"codes": ["600000.SH"]}, ["600000.SH"])
        eng._active_symbol = "600000.SH"
        # A-share: comm = max(notional * 0.00025, 5.0) + notional * 0.00001
        comm = eng.calc_commission(1000, 10.0, 1, is_open=True)
        notional = 10000
        expected = max(notional * 0.00025, 5.0) + notional * 0.00001
        assert comm == pytest.approx(expected)

    def test_us_zero_commission(self):
        eng = CompositeEngine({"codes": ["AAPL"]}, ["AAPL"])
        eng._active_symbol = "AAPL"
        assert eng.calc_commission(100, 150.0, 1, is_open=True) == 0.0

    def test_hk_commission(self):
        eng = CompositeEngine({"codes": ["0700.HK"]}, ["0700.HK"])
        eng._active_symbol = "0700.HK"
        comm = eng.calc_commission(500, 300.0, 1, is_open=True)
        notional = 500 * 300
        expected = notional * 0.00015 + notional * 0.001 + notional * 0.0000565 + notional * 0.00002
        assert comm == pytest.approx(expected)


# ─────────────────────────────────────────────
# apply_slippage delegation
# ─────────────────────────────────────────────
class TestApplySlippage:
    def test_a_share_slippage(self):
        eng = CompositeEngine({"codes": ["600000.SH"]}, ["600000.SH"])
        eng._active_symbol = "600000.SH"
        assert eng.apply_slippage(100.0, 1) == pytest.approx(100.1)

    def test_us_slippage(self):
        eng = CompositeEngine({"codes": ["AAPL"]}, ["AAPL"])
        eng._active_symbol = "AAPL"
        assert eng.apply_slippage(150.0, 1) == pytest.approx(150.075)

    def test_crypto_slippage(self):
        eng = CompositeEngine({"codes": ["BTC-USDT"]}, ["BTC-USDT"])
        eng._active_symbol = "BTC-USDT"
        assert eng.apply_slippage(40000.0, 1) == pytest.approx(40020.0)


# ─────────────────────────────────────────────
# on_bar delegation
# ─────────────────────────────────────────────
class TestOnBar:
    def test_crypto_funding_applied(self):
        eng = CompositeEngine({"codes": ["BTC-USDT"]}, ["BTC-USDT"])
        ts = pd.Timestamp("2024-01-02T08:00:00")  # 08:00 UTC = funding hour
        eng.positions["BTC-USDT"] = Position(
            symbol="BTC-USDT", direction=1, entry_price=40000.0,
            entry_time=ts, size=0.1, leverage=10.0,
        )
        bar = _bar(ts, 40100.0)
        eng.on_bar("BTC-USDT", bar, ts)
        # Funding fee deducted
        assert eng.capital < 1_000_000.0

    def test_forex_swap_applied(self):
        eng = CompositeEngine({"codes": ["EURUSD"]}, ["EURUSD"])
        ts = pd.Timestamp("2024-01-02")  # Monday
        eng.positions["EURUSD"] = Position(
            symbol="EURUSD", direction=1, entry_price=1.1,
            entry_time=ts, size=10000, leverage=100.0,
        )
        bar = _bar(ts, 1.101)
        eng.on_bar("EURUSD", bar, ts)
        # Swap deducted for long (swap_long = -0.5 default)
        assert eng.capital < 1_000_000.0

    def test_a_share_no_on_bar(self):
        # A-share has no special on_bar logic (default no-op)
        eng = CompositeEngine({"codes": ["600000.SH"]}, ["600000.SH"])
        eng.capital = 100_000.0
        ts = pd.Timestamp("2024-01-02")
        eng.positions["600000.SH"] = Position(
            symbol="600000.SH", direction=1, entry_price=10.0,
            entry_time=ts, size=100, leverage=1.0,
        )
        bar = _bar(ts, 10.5)
        eng.on_bar("600000.SH", bar, ts)
        assert eng.capital == 100_000.0  # unchanged


# ─────────────────────────────────────────────
# state sharing
# ─────────────────────────────────────────────
class TestStateSharing:
    def test_shared_capital(self):
        eng = CompositeEngine({"codes": ["BTC-USDT"]}, ["BTC-USDT"])
        eng.capital = 100_000.0
        crypto_sub = eng._rule_engines["crypto"]
        eng._run_subengine_on_bar(crypto_sub, "BTC-USDT", _bar("2024-01-02", 40000.0), pd.Timestamp("2024-01-02"))
        assert eng.capital == crypto_sub.capital

    def test_shared_positions(self):
        eng = CompositeEngine({"codes": ["BTC-USDT"]}, ["BTC-USDT"])
        ts = pd.Timestamp("2024-01-02")
        eng.positions["BTC-USDT"] = Position(
            symbol="BTC-USDT", direction=1, entry_price=40000.0,
            entry_time=ts, size=0.1, leverage=10.0,
        )
        crypto_sub = eng._rule_engines["crypto"]
        eng._run_subengine_on_bar(crypto_sub, "BTC-USDT", _bar(ts, 40000.0), ts)
        assert eng.positions is crypto_sub.positions


# ─────────────────────────────────────────────
# cross-market isolation
# ─────────────────────────────────────────────
class TestCrossMarketIsolation:
    def test_a_share_does_not_affect_crypto(self):
        codes = ["600000.SH", "BTC-USDT"]
        eng = CompositeEngine({"codes": codes, "initial_cash": 1_000_000.0}, codes)
        bar = _bar("2024-01-02", 10.0)
        # A-share rules
        assert eng.can_execute("600000.SH", 1, bar) is True
        # Crypto rules are independent
        assert eng.can_execute("BTC-USDT", -1, bar) is True
        assert eng.can_execute("BTC-USDT", 0, bar) is True

    def test_different_slippage_rates(self):
        codes = ["600000.SH", "AAPL"]
        eng = CompositeEngine({"codes": codes}, codes)
        # A-share slippage = 0.001
        eng._active_symbol = "600000.SH"
        assert eng.apply_slippage(100.0, 1) == pytest.approx(100.1)
        # US slippage = 0.0005
        eng._active_symbol = "AAPL"
        assert eng.apply_slippage(100.0, 1) == pytest.approx(100.05)


# ─────────────────────────────────────────────
# run_backtest integration
# ─────────────────────────────────────────────
class TestRunBacktestIntegration:
    def test_multi_market_long(self):
        codes = ["600000.SH", "AAPL"]
        eng = CompositeEngine({"codes": codes, "initial_cash": 1_000_000.0}, codes)
        data_map = {c: _make_data(c) for c in codes}
        dates = data_map["600000.SH"].index
        signal_map = {c: pd.Series(0.5, index=dates) for c in codes}
        metrics = eng.run_backtest(data_map, signal_map, codes)
        assert "total_return" in metrics
        assert "sharpe" in metrics
        # Should have at least one trade (force-close)
        assert len(eng.trades) >= 1

    def test_single_asset_composite(self):
        codes = ["BTC-USDT"]
        eng = CompositeEngine({"codes": codes, "initial_cash": 500_000.0}, codes)
        data_map = {c: _make_data(c, start_price=40000.0) for c in codes}
        dates = data_map[codes[0]].index
        signal_map = {c: pd.Series(1.0, index=dates) for c in codes}
        metrics = eng.run_backtest(data_map, signal_map, codes)
        assert "total_return" in metrics
        assert len(eng.trades) >= 1

    def test_zero_signal_no_trades(self):
        codes = ["600000.SH"]
        eng = CompositeEngine({"codes": codes}, codes)
        data_map = {c: _make_data(c) for c in codes}
        dates = data_map[codes[0]].index
        signal_map = {c: pd.Series(0.0, index=dates) for c in codes}
        eng.run_backtest(data_map, signal_map, codes)
        # No trades (0 signal)
        assert len(eng.trades) == 0
