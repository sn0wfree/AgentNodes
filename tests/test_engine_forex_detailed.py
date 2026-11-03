"""ForexEngine 详细单元测试。"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from strategy_research.apps.engine.forex import ForexEngine
from strategy_research.apps.engine.models import Position


def _make_bar(ts, close=1.10):
    s = pd.Series({
        "open": close,
        "high": close * 1.001,
        "low": close * 0.999,
        "close": close,
        "volume": 1000.0,
    })
    s.name = pd.Timestamp(ts)
    return s


def _make_data(symbol, n_bars=50, start_price=1.10, seed=42):
    dates = pd.bdate_range("2024-01-02", periods=n_bars)
    np.random.seed(seed)
    rets = np.random.normal(0.0001, 0.001, n_bars)
    prices = start_price * (1 + pd.Series(rets)).cumprod().values
    opens = np.empty(n_bars)
    opens[0] = start_price
    opens[1:] = prices[:-1]
    df = pd.DataFrame({
        "open": opens,
        "high": prices * 1.001,
        "low": prices * 0.999,
        "close": prices,
        "volume": np.full(n_bars, 1000.0),
    }, index=dates)
    df.index.name = "date"
    return df


# ─────────────────────────────────────────────
# Init defaults
# ─────────────────────────────────────────────
class TestForexEngineInit:
    def test_default_spread_pips(self):
        eng = ForexEngine({})
        assert eng.spread_pips == pytest.approx(1.5)

    def test_default_pip_value(self):
        eng = ForexEngine({})
        assert eng.pip_value == pytest.approx(0.0001)

    def test_default_slippage_rate(self):
        eng = ForexEngine({})
        assert eng.slippage_rate == pytest.approx(0.0001)

    def test_default_swap_long(self):
        eng = ForexEngine({})
        assert eng.swap_long == pytest.approx(-0.5)

    def test_default_swap_short(self):
        eng = ForexEngine({})
        assert eng.swap_short == pytest.approx(0.3)

    def test_default_swap_enabled(self):
        eng = ForexEngine({})
        assert eng.swap_enabled is True

    def test_custom_spread_pips(self):
        eng = ForexEngine({"spread_pips": 2.5})
        assert eng.spread_pips == pytest.approx(2.5)

    def test_custom_slippage(self):
        eng = ForexEngine({"slippage": 0.002})
        assert eng.slippage_rate == pytest.approx(0.002)

    def test_custom_swap_long(self):
        eng = ForexEngine({"swap_long": -1.0})
        assert eng.swap_long == pytest.approx(-1.0)

    def test_custom_swap_short(self):
        eng = ForexEngine({"swap_short": 0.5})
        assert eng.swap_short == pytest.approx(0.5)

    def test_swap_enabled_false(self):
        eng = ForexEngine({"swap_enabled": False})
        assert eng.swap_enabled is False

    def test_last_swap_dates_empty_dict(self):
        eng = ForexEngine({})
        assert eng._last_swap_dates == {}
        assert isinstance(eng._last_swap_dates, dict)

    def test_inherits_initial_capital(self):
        eng = ForexEngine({"initial_cash": 500_000.0})
        assert eng.initial_capital == pytest.approx(500_000.0)

    def test_inherits_default_leverage(self):
        eng = ForexEngine({"leverage": 50.0})
        assert eng.default_leverage == pytest.approx(50.0)


# ─────────────────────────────────────────────
# can_execute — 24x5, always True
# ─────────────────────────────────────────────
class TestCanExecute:
    def test_long_allowed(self):
        eng = ForexEngine({})
        bar = pd.Series({"close": 1.10})
        assert eng.can_execute("EURUSD", 1, bar) is True

    def test_short_allowed(self):
        eng = ForexEngine({})
        bar = pd.Series({"close": 1.10})
        assert eng.can_execute("EURUSD", -1, bar) is True

    def test_close_allowed(self):
        eng = ForexEngine({})
        bar = pd.Series({"close": 1.10})
        assert eng.can_execute("EURUSD", 0, bar) is True

    def test_independent_of_bar_value(self):
        eng = ForexEngine({})
        bar = pd.Series({"close": float("nan")})
        assert eng.can_execute("EURUSD", 1, bar) is True


# ─────────────────────────────────────────────
# round_size — 2 decimal rounding + max(0, x)
# ─────────────────────────────────────────────
class TestRoundSize:
    def test_normal_rounding_2_decimals(self):
        eng = ForexEngine({})
        assert eng.round_size(12345.6789, 1.10) == pytest.approx(12345.68)

    def test_already_rounded_passthrough(self):
        eng = ForexEngine({})
        assert eng.round_size(10000.0, 1.10) == pytest.approx(10000.0)

    def test_negative_becomes_zero(self):
        eng = ForexEngine({})
        assert eng.round_size(-100.0, 1.10) == pytest.approx(0.0)

    def test_zero_input(self):
        eng = ForexEngine({})
        assert eng.round_size(0.0, 1.10) == pytest.approx(0.0)

    def test_small_positive_rounds_up(self):
        eng = ForexEngine({})
        assert eng.round_size(0.005, 1.10) == pytest.approx(0.01)

    def test_rounding_down(self):
        eng = ForexEngine({})
        assert eng.round_size(12345.674, 1.10) == pytest.approx(12345.67)

    def test_price_ignored(self):
        eng = ForexEngine({})
        assert eng.round_size(1234.56, 1.0) == pytest.approx(1234.56)
        assert eng.round_size(1234.56, 999.0) == pytest.approx(1234.56)


# ─────────────────────────────────────────────
# calc_commission — half-spread per side
# ─────────────────────────────────────────────
class TestCalcCommission:
    def test_default_spread_formula(self):
        eng = ForexEngine({})
        assert eng.calc_commission(10000.0, 1.10, 1, True) == pytest.approx(0.75)

    def test_custom_spread(self):
        eng = ForexEngine({"spread_pips": 2.0, "pip_value": 0.0001})
        assert eng.calc_commission(10000.0, 1.10, 1, True) == pytest.approx(1.0)

    def test_larger_size_larger_commission(self):
        eng = ForexEngine({})
        c1 = eng.calc_commission(10000.0, 1.10, 1, True)
        c2 = eng.calc_commission(20000.0, 1.10, 1, True)
        assert c2 == pytest.approx(2 * c1)
        assert c2 > c1

    def test_open_and_close_same(self):
        eng = ForexEngine({})
        open_c = eng.calc_commission(10000.0, 1.10, 1, True)
        close_c = eng.calc_commission(10000.0, 1.10, 1, False)
        assert open_c == pytest.approx(close_c)

    def test_long_and_short_same(self):
        eng = ForexEngine({})
        long_c = eng.calc_commission(10000.0, 1.10, 1, True)
        short_c = eng.calc_commission(10000.0, 1.10, -1, True)
        assert long_c == pytest.approx(short_c)

    def test_zero_size_zero_commission(self):
        eng = ForexEngine({})
        assert eng.calc_commission(0.0, 1.10, 1, True) == pytest.approx(0.0)

    def test_price_does_not_affect_commission(self):
        eng = ForexEngine({})
        c1 = eng.calc_commission(10000.0, 1.10, 1, True)
        c2 = eng.calc_commission(10000.0, 1.50, 1, True)
        assert c1 == pytest.approx(c2)


# ─────────────────────────────────────────────
# apply_slippage
# ─────────────────────────────────────────────
class TestApplySlippage:
    def test_buy_increases_price(self):
        eng = ForexEngine({"slippage": 0.0001})
        assert eng.apply_slippage(1.10, 1) == pytest.approx(1.10011)

    def test_sell_decreases_price(self):
        eng = ForexEngine({"slippage": 0.0001})
        assert eng.apply_slippage(1.10, -1) == pytest.approx(1.09989)

    def test_zero_direction_no_slippage(self):
        eng = ForexEngine({})
        assert eng.apply_slippage(1.10, 0) == pytest.approx(1.10)

    def test_slippage_symmetric(self):
        eng = ForexEngine({"slippage": 0.001})
        buy = eng.apply_slippage(1.10, 1)
        sell = eng.apply_slippage(1.10, -1)
        assert (buy - 1.10) == pytest.approx(1.10 - sell)


# ─────────────────────────────────────────────
# on_bar — swap (long pays, short receives)
# ─────────────────────────────────────────────
class TestOnBarSwap:
    def test_long_pays_swap(self):
        eng = ForexEngine({})
        eng.capital = 100_000.0
        eng.positions["EURUSD"] = Position(
            symbol="EURUSD",
            direction=1,
            entry_price=1.10,
            entry_time=pd.Timestamp("2024-01-02"),
            size=10000,
            leverage=100.0,
        )
        ts = pd.Timestamp("2024-01-02")
        eng.on_bar("EURUSD", _make_bar(ts), ts)
        assert eng.capital == pytest.approx(100_000.0 - 0.55)

    def test_short_receives_swap(self):
        eng = ForexEngine({})
        eng.capital = 100_000.0
        eng.positions["EURUSD"] = Position(
            symbol="EURUSD",
            direction=-1,
            entry_price=1.10,
            entry_time=pd.Timestamp("2024-01-02"),
            size=10000,
            leverage=100.0,
        )
        ts = pd.Timestamp("2024-01-02")
        eng.on_bar("EURUSD", _make_bar(ts), ts)
        assert eng.capital == pytest.approx(100_000.0 + 0.33)

    def test_last_swap_date_recorded(self):
        eng = ForexEngine({})
        eng.positions["EURUSD"] = Position(
            symbol="EURUSD",
            direction=1,
            entry_price=1.10,
            entry_time=pd.Timestamp("2024-01-02"),
            size=10000,
            leverage=100.0,
        )
        ts = pd.Timestamp("2024-01-02")
        eng.on_bar("EURUSD", _make_bar(ts), ts)
        assert eng._last_swap_dates["EURUSD"] == ts.date()

    def test_multi_symbol_independent_swap(self):
        eng = ForexEngine({})
        eng.capital = 100_000.0
        eng.positions["EURUSD"] = Position(
            symbol="EURUSD",
            direction=1,
            entry_price=1.10,
            entry_time=pd.Timestamp("2024-01-02"),
            size=10000,
            leverage=100.0,
        )
        ts = pd.Timestamp("2024-01-02")
        eng.on_bar("EURUSD", _make_bar(ts), ts)
        eng.on_bar("GBPUSD", _make_bar(ts), ts)
        assert eng.capital == pytest.approx(100_000.0 - 0.55)
        assert "GBPUSD" not in eng._last_swap_dates


# ─────────────────────────────────────────────
# on_bar — Wednesday 3x swap
# ─────────────────────────────────────────────
class TestOnBarWednesday:
    def test_wednesday_triple_swap_long(self):
        eng = ForexEngine({})
        eng.capital = 100_000.0
        eng.positions["EURUSD"] = Position(
            symbol="EURUSD",
            direction=1,
            entry_price=1.10,
            entry_time=pd.Timestamp("2024-01-02"),
            size=10000,
            leverage=100.0,
        )
        ts = pd.Timestamp("2024-01-03")
        eng.on_bar("EURUSD", _make_bar(ts), ts)
        assert eng.capital == pytest.approx(100_000.0 - 1.65)

    def test_wednesday_triple_swap_short(self):
        eng = ForexEngine({})
        eng.capital = 100_000.0
        eng.positions["EURUSD"] = Position(
            symbol="EURUSD",
            direction=-1,
            entry_price=1.10,
            entry_time=pd.Timestamp("2024-01-02"),
            size=10000,
            leverage=100.0,
        )
        ts = pd.Timestamp("2024-01-03")
        eng.on_bar("EURUSD", _make_bar(ts), ts)
        assert eng.capital == pytest.approx(100_000.0 + 0.99)

    def test_thursday_normal_swap(self):
        eng = ForexEngine({})
        eng.capital = 100_000.0
        eng.positions["EURUSD"] = Position(
            symbol="EURUSD",
            direction=1,
            entry_price=1.10,
            entry_time=pd.Timestamp("2024-01-02"),
            size=10000,
            leverage=100.0,
        )
        ts = pd.Timestamp("2024-01-04")
        eng.on_bar("EURUSD", _make_bar(ts), ts)
        assert eng.capital == pytest.approx(100_000.0 - 0.55)


# ─────────────────────────────────────────────
# on_bar — same-day dedup
# ─────────────────────────────────────────────
class TestOnBarSameDayDedup:
    def test_same_date_twice_no_double_swap(self):
        eng = ForexEngine({})
        eng.capital = 100_000.0
        eng.positions["EURUSD"] = Position(
            symbol="EURUSD",
            direction=1,
            entry_price=1.10,
            entry_time=pd.Timestamp("2024-01-02"),
            size=10000,
            leverage=100.0,
        )
        ts = pd.Timestamp("2024-01-02")
        eng.on_bar("EURUSD", _make_bar(ts), ts)
        cap_after_first = eng.capital
        eng.on_bar("EURUSD", _make_bar(ts), ts)
        assert eng.capital == pytest.approx(cap_after_first)

    def test_different_dates_both_swap(self):
        eng = ForexEngine({})
        eng.capital = 100_000.0
        eng.positions["EURUSD"] = Position(
            symbol="EURUSD",
            direction=1,
            entry_price=1.10,
            entry_time=pd.Timestamp("2024-01-02"),
            size=10000,
            leverage=100.0,
        )
        ts1 = pd.Timestamp("2024-01-08")
        ts2 = pd.Timestamp("2024-01-09")
        eng.on_bar("EURUSD", _make_bar(ts1), ts1)
        eng.on_bar("EURUSD", _make_bar(ts2), ts2)
        assert eng.capital == pytest.approx(100_000.0 - 1.10)

    def test_friday_to_monday_no_dedup(self):
        eng = ForexEngine({})
        eng.capital = 100_000.0
        eng.positions["EURUSD"] = Position(
            symbol="EURUSD",
            direction=1,
            entry_price=1.10,
            entry_time=pd.Timestamp("2024-01-05"),
            size=10000,
            leverage=100.0,
        )
        fri = pd.Timestamp("2024-01-05")
        mon = pd.Timestamp("2024-01-08")
        eng.on_bar("EURUSD", _make_bar(fri), fri)
        cap_fri = eng.capital
        eng.on_bar("EURUSD", _make_bar(mon, close=1.11), mon)
        expected_swap_mon = 10000 * 1.11 * (-0.5) * 0.0001
        assert eng.capital == pytest.approx(cap_fri + expected_swap_mon)
        assert eng._last_swap_dates["EURUSD"] == mon.date()


# ─────────────────────────────────────────────
# on_bar — no position is a no-op
# ─────────────────────────────────────────────
class TestOnBarNoPosition:
    def test_no_position_no_swap(self):
        eng = ForexEngine({})
        eng.capital = 100_000.0
        ts = pd.Timestamp("2024-01-02")
        eng.on_bar("EURUSD", _make_bar(ts), ts)
        assert eng.capital == pytest.approx(100_000.0)
        assert eng._last_swap_dates == {}


# ─────────────────────────────────────────────
# on_bar — swap disabled
# ─────────────────────────────────────────────
class TestOnBarSwapDisabled:
    def test_swap_disabled_no_swap(self):
        eng = ForexEngine({"swap_enabled": False})
        eng.capital = 100_000.0
        eng.positions["EURUSD"] = Position(
            symbol="EURUSD",
            direction=1,
            entry_price=1.10,
            entry_time=pd.Timestamp("2024-01-02"),
            size=10000,
            leverage=100.0,
        )
        ts = pd.Timestamp("2024-01-02")
        eng.on_bar("EURUSD", _make_bar(ts), ts)
        assert eng.capital == pytest.approx(100_000.0)
        assert eng._last_swap_dates == {}

    def test_swap_disabled_wednesday_no_3x(self):
        eng = ForexEngine({"swap_enabled": False})
        eng.capital = 100_000.0
        eng.positions["EURUSD"] = Position(
            symbol="EURUSD",
            direction=1,
            entry_price=1.10,
            entry_time=pd.Timestamp("2024-01-02"),
            size=10000,
            leverage=100.0,
        )
        ts = pd.Timestamp("2024-01-03")
        eng.on_bar("EURUSD", _make_bar(ts), ts)
        assert eng.capital == pytest.approx(100_000.0)


# ─────────────────────────────────────────────
# run_backtest integration
# ─────────────────────────────────────────────
class TestRunBacktestIntegration:
    def test_basic_long_run(self):
        code = "EURUSD"
        eng = ForexEngine({"initial_cash": 1_000_000.0, "leverage": 100.0})
        df = _make_data(code, n_bars=30)
        signal = pd.Series(1.0, index=df.index)
        eng.run_backtest({code: df}, {code: signal}, [code])
        assert len(eng.trades) >= 1

    def test_basic_short_run(self):
        code = "EURUSD"
        eng = ForexEngine({"initial_cash": 1_000_000.0, "leverage": 100.0})
        df = _make_data(code, n_bars=30)
        signal = pd.Series(-1.0, index=df.index)
        eng.run_backtest({code: df}, {code: signal}, [code])
        assert len(eng.trades) >= 1

    def test_weekend_dates_handled(self):
        code = "EURUSD"
        eng = ForexEngine({"initial_cash": 1_000_000.0})
        weekend_dates = pd.DatetimeIndex([
            "2024-01-05",
            "2024-01-06",
            "2024-01-07",
            "2024-01-08",
        ])
        prices = np.array([1.10, 1.10, 1.10, 1.11])
        df = pd.DataFrame({
            "open": prices,
            "high": prices * 1.001,
            "low": prices * 0.999,
            "close": prices,
            "volume": np.full(4, 1000.0),
        }, index=weekend_dates)
        df.index.name = "date"
        signal = pd.Series(1.0, index=df.index)
        m = eng.run_backtest({code: df}, {code: signal}, [code])
        assert len(eng.equity_snapshots) == 4
        assert "sharpe" in m

    def test_signal_change_open_close_reopen(self):
        code = "EURUSD"
        eng = ForexEngine({"initial_cash": 1_000_000.0, "leverage": 100.0})
        dates = pd.bdate_range("2024-01-02", periods=8)
        prices = np.array([1.10, 1.11, 1.12, 1.13, 1.14, 1.15, 1.16, 1.17])
        df = pd.DataFrame({
            "open": prices,
            "high": prices + 0.001,
            "low": prices - 0.001,
            "close": prices,
            "volume": np.full(8, 1000.0),
        }, index=dates)
        df.index.name = "date"
        signals = pd.Series(
            [0.0, 1.0, 1.0, 0.0, 0.0, -1.0, -1.0, -1.0], index=dates
        )
        eng.run_backtest({code: df}, {code: signals}, [code])
        assert len(eng.trades) >= 2
        assert eng.trades[-1].exit_reason == "end_of_backtest"

    def test_metrics_keys_present(self):
        code = "EURUSD"
        eng = ForexEngine({"initial_cash": 1_000_000.0})
        df = _make_data(code, n_bars=20)
        signal = pd.Series(0.5, index=df.index)
        m = eng.run_backtest({code: df}, {code: signal}, [code])
        expected = {
            "final_value", "total_return", "annual_return", "max_drawdown",
            "sharpe", "calmar", "sortino", "win_rate", "profit_loss_ratio",
            "profit_factor", "max_consecutive_loss", "avg_holding_days",
            "trade_count", "benchmark_return", "excess_return",
            "information_ratio", "turnover",
        }
        assert expected.issubset(m.keys())
