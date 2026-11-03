import numpy as np
import pandas as pd
import pytest

from strategy_research.apps.engine.india_equity import IndiaEquityEngine


def _bar(date, close, name=True):
    s = pd.Series({"open": close, "high": close*1.001, "low": close*0.999, "close": close, "volume": 1000.0})
    if name:
        s.name = pd.Timestamp(date)
    return s


class TestInit:
    def test_default_rates(self):
        engine = IndiaEquityEngine({})
        assert engine.brokerage == pytest.approx(0.0003)
        assert engine.stt == pytest.approx(0.001)
        assert engine.exchange_txn == pytest.approx(0.0000345)
        assert engine.sebi_charge == pytest.approx(0.000001)
        assert engine.stamp_duty == pytest.approx(0.00015)
        assert engine.gst == pytest.approx(0.18)

    def test_default_slippage(self):
        assert IndiaEquityEngine({}).slippage_rate == pytest.approx(0.001)

    def test_custom_rates(self):
        engine = IndiaEquityEngine({"brokerage": 0.001, "stt": 0.002, "gst": 0.2, "slippage": 0.003})
        assert engine.brokerage == pytest.approx(0.001)
        assert engine.stt == pytest.approx(0.002)
        assert engine.gst == pytest.approx(0.2)
        assert engine.slippage_rate == pytest.approx(0.003)


class TestCanExecute:
    @pytest.mark.parametrize("direction", [1, -1, 0])
    def test_all_directions_allowed(self, direction):
        assert IndiaEquityEngine({}).can_execute("RELIANCE.NS", direction, _bar("2024-01-02", 2500)) is True


class TestRoundSize:
    @pytest.mark.parametrize("raw,expected", [(1.9, 1), (1.0, 1), (0.99, 0), (100.999, 100), (0, 0), (-0.5, 0), (-10, 0)])
    def test_integer_truncation(self, raw, expected):
        assert IndiaEquityEngine({}).round_size(raw, 100) == expected


class TestCalcCommission:
    def test_open_components(self):
        engine = IndiaEquityEngine({})
        notional = 100000
        expected = notional * (0.0003 + 0.0000345 + 0.000001) * 1.18
        assert engine.calc_commission(1000, 100, 1, True) == pytest.approx(expected)

    def test_close_components(self):
        engine = IndiaEquityEngine({})
        notional = 100000
        expected = notional * (0.0003 + 0.0000345 + 0.000001 + 0.001 + 0.00015) * 1.18
        assert engine.calc_commission(1000, 100, 1, False) == pytest.approx(expected)

    def test_close_costs_more_than_open(self):
        engine = IndiaEquityEngine({})
        assert engine.calc_commission(100, 500, 1, False) > engine.calc_commission(100, 500, 1, True)

    @pytest.mark.parametrize("direction", [1, -1, 0])
    def test_direction_does_not_change_fee(self, direction):
        engine = IndiaEquityEngine({})
        expected = engine.calc_commission(10, 100, 1, True)
        assert engine.calc_commission(10, 100, direction, True) == pytest.approx(expected)

    def test_zero_notional(self):
        assert IndiaEquityEngine({}).calc_commission(0, 100, 1, True) == 0.0


class TestCalcCommissionGST:
    @pytest.mark.parametrize("is_open", [True, False])
    def test_default_multiplier(self, is_open):
        engine = IndiaEquityEngine({})
        base_rate = 0.0003 + 0.0000345 + 0.000001
        if not is_open:
            base_rate += 0.001 + 0.00015
        assert engine.calc_commission(100, 100, 1, is_open) == pytest.approx(10000 * base_rate * 1.18)

    def test_zero_gst(self):
        engine = IndiaEquityEngine({"gst": 0.0})
        assert engine.calc_commission(100, 100, 1, True) == pytest.approx(10000 * (0.0003 + 0.0000345 + 0.000001))

    def test_custom_gst_multiplier(self):
        engine = IndiaEquityEngine({"gst": 0.25})
        base = 10000 * (0.0003 + 0.0000345 + 0.000001)
        assert engine.calc_commission(100, 100, 1, True) == pytest.approx(base * 1.25)


class TestApplySlippage:
    @pytest.mark.parametrize("direction,expected", [(1, 100.1), (-1, 99.9), (0, 100.0)])
    def test_direction(self, direction, expected):
        assert IndiaEquityEngine({}).apply_slippage(100, direction) == pytest.approx(expected)

    def test_custom_rate(self):
        assert IndiaEquityEngine({"slippage": 0.01}).apply_slippage(100, 1) == pytest.approx(101)


class TestRunBacktestIntegration:
    @staticmethod
    def _make_data(symbol, n_bars=50, start_price=10.0):
        dates = pd.bdate_range("2024-01-02", periods=n_bars)
        np.random.seed(42)
        rets = np.random.normal(0.001, 0.005, n_bars)
        prices = start_price * (1 + pd.Series(rets)).cumprod().values
        opens = np.empty(n_bars)
        opens[0] = start_price
        opens[1:] = prices[:-1]
        df = pd.DataFrame({"open": opens, "high": prices*1.005, "low": prices*0.995, "close": prices, "volume": np.full(n_bars, 1000.0)}, index=dates)
        df.index.name = "date"
        return df

    def test_basic_long(self):
        symbol = "RELIANCE.NS"
        engine = IndiaEquityEngine({"initial_cash": 1000000})
        data = self._make_data(symbol, start_price=2500)
        metrics = engine.run_backtest({symbol: data}, {symbol: pd.Series(1.0, index=data.index)}, [symbol])
        assert len(engine.trades) >= 1
        assert metrics["trade_count"] >= 1
        assert engine.trades[-1].exit_reason == "end_of_backtest"

    def test_metrics_keys(self):
        symbol = "RELIANCE.NS"
        engine = IndiaEquityEngine({"initial_cash": 1000000})
        data = self._make_data(symbol, start_price=2500)
        metrics = engine.run_backtest({symbol: data}, {symbol: pd.Series(1.0, index=data.index)}, [symbol])
        for key in ["total_return", "annual_return", "sharpe", "max_drawdown", "win_rate", "trade_count"]:
            assert key in metrics

    def test_short_creates_trade(self):
        symbol = "RELIANCE.NS"
        engine = IndiaEquityEngine({"initial_cash": 1000000})
        data = self._make_data(symbol, start_price=2500)
        engine.run_backtest({symbol: data}, {symbol: pd.Series(-1.0, index=data.index)}, [symbol])
        assert len(engine.trades) >= 1
        assert engine.trades[0].direction == -1
