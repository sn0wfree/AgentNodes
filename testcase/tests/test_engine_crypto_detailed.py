"""CryptoEngine 详细单元测试。"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from strategy_research.apps.engine.crypto import CryptoEngine
from strategy_research.apps.engine.models import Position


def _make_bar(close_price, name=True, dt=None):
    s = pd.Series(
        {
            "open": close_price,
            "high": close_price * 1.001,
            "low": close_price * 0.999,
            "close": close_price,
            "volume": 100.0,
        }
    )
    if name and dt is not None:
        s.name = dt
    return s


def _make_data(symbol, n_bars=50, start_price=100.0, freq="1h"):
    dates = pd.date_range("2024-01-02", periods=n_bars, freq=freq)
    np.random.seed(42)
    rets = np.random.normal(0.001, 0.005, n_bars)
    prices = start_price * (1 + pd.Series(rets)).cumprod().values
    opens = np.empty(n_bars)
    opens[0] = start_price
    opens[1:] = prices[:-1]
    df = pd.DataFrame(
        {
            "open": opens,
            "high": prices * 1.005,
            "low": prices * 0.995,
            "close": prices,
            "volume": np.full(n_bars, 1000.0),
        },
        index=dates,
    )
    df.index.name = "date"
    return df


# ─────────────────────────────────────────────
# Init
# ─────────────────────────────────────────────


class TestCryptoEngineInit:
    def test_default_maker_rate(self):
        eng = CryptoEngine({})
        assert eng.maker_rate == pytest.approx(0.0002)

    def test_default_taker_rate(self):
        eng = CryptoEngine({})
        assert eng.taker_rate == pytest.approx(0.0005)

    def test_default_slippage(self):
        eng = CryptoEngine({})
        assert eng.slippage_rate == pytest.approx(0.0005)

    def test_default_funding_rate(self):
        eng = CryptoEngine({})
        assert eng.funding_rate == pytest.approx(0.0001)

    def test_custom_maker_rate_from_config(self):
        eng = CryptoEngine({"maker_rate": 0.0001})
        assert eng.maker_rate == pytest.approx(0.0001)

    def test_custom_taker_rate_from_config(self):
        eng = CryptoEngine({"taker_rate": 0.0008})
        assert eng.taker_rate == pytest.approx(0.0008)

    def test_custom_slippage_from_config(self):
        eng = CryptoEngine({"slippage": 0.002})
        assert eng.slippage_rate == pytest.approx(0.002)

    def test_custom_funding_rate_from_config(self):
        eng = CryptoEngine({"funding_rate": 0.0003})
        assert eng.funding_rate == pytest.approx(0.0003)

    def test_funding_applied_is_set(self):
        eng = CryptoEngine({})
        assert isinstance(eng._funding_applied, set)

    def test_funding_daily_done_is_set(self):
        eng = CryptoEngine({})
        assert isinstance(eng._funding_daily_done, set)

    def test_funding_sets_start_empty(self):
        eng = CryptoEngine({})
        assert len(eng._funding_applied) == 0
        assert len(eng._funding_daily_done) == 0

    def test_inherits_base_state(self):
        eng = CryptoEngine({"initial_cash": 500_000.0})
        assert eng.capital == pytest.approx(500_000.0)
        assert eng.initial_capital == pytest.approx(500_000.0)
        assert eng.positions == {}
        assert eng.trades == []


# ─────────────────────────────────────────────
# can_execute (24/7, no T+1)
# ─────────────────────────────────────────────


class TestCanExecute:
    def test_long_allowed(self):
        eng = CryptoEngine({})
        bar = _make_bar(100.0)
        assert eng.can_execute("BTC/USDT", 1, bar) is True

    def test_short_allowed(self):
        eng = CryptoEngine({})
        bar = _make_bar(100.0)
        assert eng.can_execute("BTC/USDT", -1, bar) is True

    def test_close_allowed(self):
        eng = CryptoEngine({})
        bar = _make_bar(100.0)
        assert eng.can_execute("BTC/USDT", 0, bar) is True

    def test_intraday_close_allowed(self):
        eng = CryptoEngine({})
        bar = _make_bar(100.0)
        assert eng.can_execute("BTC/USDT", 0, bar) is True

    def test_always_true_regardless_of_bar(self):
        eng = CryptoEngine({})
        bar = _make_bar(99_999.99)
        assert eng.can_execute("BTC/USDT", 1, bar) is True
        assert eng.can_execute("ETH/USDT", -1, bar) is True
        assert eng.can_execute("SOL/USDT", 0, bar) is True


# ─────────────────────────────────────────────
# round_size
# ─────────────────────────────────────────────


class TestRoundSize:
    def test_rounds_to_6_decimals(self):
        eng = CryptoEngine({})
        result = eng.round_size(1.123456789, 100.0)
        assert result == pytest.approx(1.123457)

    def test_negative_size_becomes_zero(self):
        eng = CryptoEngine({})
        assert eng.round_size(-0.5, 100.0) == 0.0

    def test_large_negative_becomes_zero(self):
        eng = CryptoEngine({})
        assert eng.round_size(-100.0, 100.0) == 0.0

    def test_zero_size_remains_zero(self):
        eng = CryptoEngine({})
        assert eng.round_size(0.0, 100.0) == 0.0

    def test_positive_size_unchanged_at_6_decimals(self):
        eng = CryptoEngine({})
        assert eng.round_size(1.5, 100.0) == pytest.approx(1.5)

    def test_max_with_zero_used(self):
        eng = CryptoEngine({})
        assert eng.round_size(0.000001, 100.0) == pytest.approx(0.000001)
        assert eng.round_size(-0.5, 100.0) == 0.0

    def test_price_argument_ignored(self):
        eng = CryptoEngine({})
        a = eng.round_size(1.234567, 100.0)
        b = eng.round_size(1.234567, 50_000.0)
        assert a == pytest.approx(b)


# ─────────────────────────────────────────────
# calc_commission (open=taker, close=maker)
# ─────────────────────────────────────────────


class TestCalcCommission:
    def test_open_uses_taker_rate(self):
        eng = CryptoEngine({})
        comm = eng.calc_commission(1.0, 100.0, 1, is_open=True)
        assert comm == pytest.approx(1.0 * 100.0 * 0.0005)

    def test_close_uses_maker_rate(self):
        eng = CryptoEngine({})
        comm = eng.calc_commission(1.0, 100.0, 1, is_open=False)
        assert comm == pytest.approx(1.0 * 100.0 * 0.0002)

    def test_open_taker_higher_than_close_maker(self):
        eng = CryptoEngine({})
        open_comm = eng.calc_commission(1.0, 100.0, 1, is_open=True)
        close_comm = eng.calc_commission(1.0, 100.0, 1, is_open=False)
        assert open_comm > close_comm

    def test_long_open_commission(self):
        eng = CryptoEngine({})
        assert eng.calc_commission(10.0, 50.0, 1, True) == pytest.approx(10.0 * 50.0 * 0.0005)

    def test_short_open_commission(self):
        eng = CryptoEngine({})
        assert eng.calc_commission(10.0, 50.0, -1, True) == pytest.approx(10.0 * 50.0 * 0.0005)

    def test_custom_rates_open(self):
        eng = CryptoEngine({"taker_rate": 0.001, "maker_rate": 0.0005})
        assert eng.calc_commission(1.0, 1000.0, 1, True) == pytest.approx(1.0)
        assert eng.calc_commission(1.0, 1000.0, 1, False) == pytest.approx(0.5)

    def test_direction_does_not_change_commission_amount(self):
        eng = CryptoEngine({})
        long_open = eng.calc_commission(2.0, 500.0, 1, True)
        short_open = eng.calc_commission(2.0, 500.0, -1, True)
        assert long_open == pytest.approx(short_open)


# ─────────────────────────────────────────────
# apply_slippage
# ─────────────────────────────────────────────


class TestApplySlippage:
    def test_buy_increases_price(self):
        eng = CryptoEngine({})
        assert eng.apply_slippage(100.0, 1) == pytest.approx(100.0 * 1.0005)

    def test_sell_decreases_price(self):
        eng = CryptoEngine({})
        assert eng.apply_slippage(100.0, -1) == pytest.approx(100.0 * 0.9995)

    def test_default_rate_symmetric(self):
        eng = CryptoEngine({})
        buy = eng.apply_slippage(100.0, 1)
        sell = eng.apply_slippage(100.0, -1)
        assert (100.0 - sell) == pytest.approx(buy - 100.0)

    def test_custom_slippage(self):
        eng = CryptoEngine({"slippage": 0.001})
        assert eng.apply_slippage(100.0, 1) == pytest.approx(100.1)
        assert eng.apply_slippage(100.0, -1) == pytest.approx(99.9)

    def test_close_direction_zero_slippage(self):
        eng = CryptoEngine({})
        assert eng.apply_slippage(100.0, 0) == pytest.approx(100.0)


# ─────────────────────────────────────────────
# on_bar — funding fee + liquidation hook
# ─────────────────────────────────────────────


class TestOnBar:
    def test_no_position_no_capital_change(self):
        eng = CryptoEngine({})
        ts = pd.Timestamp("2024-01-02 08:00:00")
        bar = _make_bar(100.0, dt=ts)
        before = eng.capital
        eng.on_bar("BTC/USDT", bar, ts)
        assert eng.capital == pytest.approx(before)

    def test_funding_fee_deducted_long(self):
        eng = CryptoEngine({"initial_cash": 100_000.0})
        pos = Position(
            symbol="BTC/USDT",
            direction=1,
            entry_price=100.0,
            entry_time=pd.Timestamp("2024-01-02 07:00:00"),
            size=1.0,
            leverage=1.0,
        )
        eng.positions["BTC/USDT"] = pos
        eng._bar_idx = 5
        ts = pd.Timestamp("2024-01-02 08:00:00")
        bar = _make_bar(100.0, dt=ts)
        before = eng.capital
        eng.on_bar("BTC/USDT", bar, ts)
        expected_fee = 1.0 * 100.0 * 0.0001 * 1
        assert eng.capital == pytest.approx(before - expected_fee)

    def test_funding_fee_credited_short(self):
        eng = CryptoEngine({"initial_cash": 100_000.0})
        pos = Position(
            symbol="BTC/USDT",
            direction=-1,
            entry_price=100.0,
            entry_time=pd.Timestamp("2024-01-02 07:00:00"),
            size=1.0,
            leverage=1.0,
        )
        eng.positions["BTC/USDT"] = pos
        eng._bar_idx = 5
        ts = pd.Timestamp("2024-01-02 08:00:00")
        bar = _make_bar(100.0, dt=ts)
        before = eng.capital
        eng.on_bar("BTC/USDT", bar, ts)
        expected_fee = 1.0 * 100.0 * 0.0001 * -1
        assert eng.capital == pytest.approx(before - expected_fee)
        assert eng.capital > before

    def test_liquidation_closes_position_with_correct_reason(self):
        eng = CryptoEngine({"initial_cash": 100_000.0})
        pos = Position(
            symbol="BTC/USDT",
            direction=1,
            entry_price=100.0,
            entry_time=pd.Timestamp("2024-01-02 00:00:00"),
            size=1.0,
            leverage=10.0,
        )
        eng.positions["BTC/USDT"] = pos
        eng._bar_idx = 5
        ts = pd.Timestamp("2024-01-02 10:00:00")
        bar = _make_bar(85.0, dt=ts)
        eng.on_bar("BTC/USDT", bar, ts)
        assert "BTC/USDT" not in eng.positions
        assert len(eng.trades) == 1
        assert eng.trades[0].exit_reason == "liquidation"

    def test_no_liquidation_no_trade_recorded(self):
        eng = CryptoEngine({"initial_cash": 100_000.0})
        pos = Position(
            symbol="BTC/USDT",
            direction=1,
            entry_price=100.0,
            entry_time=pd.Timestamp("2024-01-02 00:00:00"),
            size=1.0,
            leverage=10.0,
        )
        eng.positions["BTC/USDT"] = pos
        eng._bar_idx = 5
        ts = pd.Timestamp("2024-01-02 10:00:00")
        bar = _make_bar(105.0, dt=ts)
        eng.on_bar("BTC/USDT", bar, ts)
        assert "BTC/USDT" in eng.positions
        assert len(eng.trades) == 0

    def test_non_funding_hour_deducts_once(self):
        eng = CryptoEngine({"initial_cash": 100_000.0})
        pos = Position(
            symbol="BTC/USDT",
            direction=1,
            entry_price=100.0,
            entry_time=pd.Timestamp("2024-01-02 00:00:00"),
            size=1.0,
            leverage=1.0,
        )
        eng.positions["BTC/USDT"] = pos
        eng._bar_idx = 5
        ts = pd.Timestamp("2024-01-02 10:00:00")
        bar = _make_bar(100.0, dt=ts)
        before = eng.capital
        eng.on_bar("BTC/USDT", bar, ts)
        eng.on_bar("BTC/USDT", bar, ts)
        expected = 100.0 * 0.0001 * 1.0
        assert eng.capital == pytest.approx(before - expected)


# ─────────────────────────────────────────────
# on_bar — long vs short funding sign
# ─────────────────────────────────────────────


class TestOnBarLongFunding:
    def test_long_funding_reduces_capital(self):
        eng = CryptoEngine({"initial_cash": 100_000.0})
        eng.positions["BTC/USDT"] = Position(
            symbol="BTC/USDT",
            direction=1,
            entry_price=100.0,
            entry_time=pd.Timestamp("2024-01-02 00:00:00"),
            size=1.0,
            leverage=1.0,
        )
        eng._bar_idx = 1
        ts = pd.Timestamp("2024-01-02 08:00:00")
        bar = _make_bar(100.0, dt=ts)
        before = eng.capital
        eng.on_bar("BTC/USDT", bar, ts)
        assert eng.capital < before

    def test_short_funding_increases_capital(self):
        eng = CryptoEngine({"initial_cash": 100_000.0})
        eng.positions["BTC/USDT"] = Position(
            symbol="BTC/USDT",
            direction=-1,
            entry_price=100.0,
            entry_time=pd.Timestamp("2024-01-02 00:00:00"),
            size=1.0,
            leverage=1.0,
        )
        eng._bar_idx = 1
        ts = pd.Timestamp("2024-01-02 08:00:00")
        bar = _make_bar(100.0, dt=ts)
        before = eng.capital
        eng.on_bar("BTC/USDT", bar, ts)
        assert eng.capital > before

    def test_long_short_capital_delta_mirror(self):
        long_eng = CryptoEngine({"initial_cash": 100_000.0})
        short_eng = CryptoEngine({"initial_cash": 100_000.0})
        ts = pd.Timestamp("2024-01-02 08:00:00")
        bar = _make_bar(100.0, dt=ts)
        for e, d in ((long_eng, 1), (short_eng, -1)):
            e.positions["BTC/USDT"] = Position(
                symbol="BTC/USDT",
                direction=d,
                entry_price=100.0,
                entry_time=pd.Timestamp("2024-01-02 00:00:00"),
                size=1.0,
                leverage=1.0,
            )
            e._bar_idx = 1
        long_eng.on_bar("BTC/USDT", bar, ts)
        short_eng.on_bar("BTC/USDT", bar, ts)
        assert (long_eng.capital - 100_000.0) == pytest.approx(-(short_eng.capital - 100_000.0))

    def test_funding_magnitude_matches_formula(self):
        eng = CryptoEngine({"initial_cash": 100_000.0})
        eng.positions["BTC/USDT"] = Position(
            symbol="BTC/USDT",
            direction=1,
            entry_price=100.0,
            entry_time=pd.Timestamp("2024-01-02 00:00:00"),
            size=2.0,
            leverage=1.0,
        )
        eng._bar_idx = 1
        ts = pd.Timestamp("2024-01-02 08:00:00")
        bar = _make_bar(150.0, dt=ts)
        before = eng.capital
        eng.on_bar("BTC/USDT", bar, ts)
        expected = 2.0 * 150.0 * 0.0001 * 1
        assert eng.capital == pytest.approx(before - expected)


# ─────────────────────────────────────────────
# run_backtest integration
# ─────────────────────────────────────────────


class TestRunBacktestIntegration:
    def test_basic_long_run(self):
        code = "BTC/USDT"
        eng = CryptoEngine({})
        df = _make_data(code, n_bars=50, start_price=100.0)
        signal_map = {code: pd.Series(1.0, index=df.index)}
        eng.run_backtest({code: df}, signal_map, [code])
        assert len(eng.trades) >= 1

    def test_short_run(self):
        code = "BTC/USDT"
        eng = CryptoEngine({})
        df = _make_data(code, n_bars=50, start_price=100.0)
        signal_map = {code: pd.Series(-1.0, index=df.index)}
        eng.run_backtest({code: df}, signal_map, [code])
        assert len(eng.trades) >= 1

    def test_zero_signal_no_trades(self):
        code = "BTC/USDT"
        eng = CryptoEngine({})
        df = _make_data(code, n_bars=20, start_price=100.0)
        signal_map = {code: pd.Series(0.0, index=df.index)}
        eng.run_backtest({code: df}, signal_map, [code])
        assert len(eng.trades) == 0
        assert eng.positions == {}

    def test_metrics_keys_present(self):
        code = "BTC/USDT"
        eng = CryptoEngine({})
        df = _make_data(code, n_bars=30, start_price=100.0)
        signal_map = {code: pd.Series(1.0, index=df.index)}
        metrics = eng.run_backtest({code: df}, signal_map, [code])
        for key in (
            "total_return",
            "annual_return",
            "sharpe",
            "max_drawdown",
            "calmar",
            "sortino",
            "win_rate",
            "trade_count",
            "final_value",
        ):
            assert key in metrics, f"missing key: {key}"

    def test_metrics_values_finite(self):
        code = "BTC/USDT"
        eng = CryptoEngine({})
        df = _make_data(code, n_bars=30, start_price=100.0)
        signal_map = {code: pd.Series(1.0, index=df.index)}
        metrics = eng.run_backtest({code: df}, signal_map, [code])
        assert np.isfinite(metrics["total_return"])
        assert np.isfinite(metrics["sharpe"])
        assert np.isfinite(metrics["max_drawdown"])

    def test_initial_capital_preserved(self):
        code = "BTC/USDT"
        eng = CryptoEngine({"initial_cash": 200_000.0})
        df = _make_data(code, n_bars=20, start_price=100.0)
        signal_map = {code: pd.Series(0.0, index=df.index)}
        eng.run_backtest({code: df}, signal_map, [code])
        assert eng.capital == pytest.approx(200_000.0)

    def test_equity_snapshots_generated(self):
        code = "BTC/USDT"
        eng = CryptoEngine({})
        df = _make_data(code, n_bars=20, start_price=100.0)
        signal_map = {code: pd.Series(1.0, index=df.index)}
        eng.run_backtest({code: df}, signal_map, [code])
        assert len(eng.equity_snapshots) == len(df)
