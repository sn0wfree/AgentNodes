import numpy as np
import pandas as pd
import pytest

from strategy_research.apps.engine.global_equity import GlobalEquityEngine


def _bar(date, close, name=True):
    s = pd.Series({"open": close, "high": close*1.001, "low": close*0.999, "close": close, "volume": 1000.0})
    if name:
        s.name = pd.Timestamp(date)
    return s


class TestInit:
    def test_us_defaults(self):
        engine = GlobalEquityEngine({}, market="us")
        assert engine.market == "us"
        assert engine.slippage_us == pytest.approx(0.0005)

    def test_hk_defaults(self):
        engine = GlobalEquityEngine({}, market="hk")
        assert engine.slippage_hk == pytest.approx(0.001)
        assert engine.hk_commission == pytest.approx(0.00015)
        assert engine.hk_stamp_tax == pytest.approx(0.001)
        assert engine.hk_levy == pytest.approx(0.0000565)
        assert engine.hk_settlement == pytest.approx(0.00002)

    def test_default_market(self):
        assert GlobalEquityEngine({}).market == "us"

    def test_custom_rates(self):
        engine = GlobalEquityEngine({"slippage_us": 0.002, "hk_commission": 0.003}, market="hk")
        assert engine.slippage_us == pytest.approx(0.002)
        assert engine.hk_commission == pytest.approx(0.003)


class TestCanExecute:
    @pytest.mark.parametrize("market,direction", [("us", 1), ("us", -1), ("us", 0), ("hk", 1), ("hk", -1), ("hk", 0)])
    def test_all_directions_allowed(self, market, direction):
        assert GlobalEquityEngine({}, market=market).can_execute("TEST", direction, _bar("2024-01-02", 10)) is True


class TestRoundSize:
    @pytest.mark.parametrize("raw,expected", [(1.234, 1.23), (1.235, 1.24), (0.009, 0.01), (-2.0, 0.0)])
    def test_us_fractional(self, raw, expected):
        assert GlobalEquityEngine({}, market="us").round_size(raw, 10) == expected

    @pytest.mark.parametrize("raw,expected", [(99, 0), (100, 100), (199.9, 100), (250, 200), (-100, 0)])
    def test_hk_lots(self, raw, expected):
        assert GlobalEquityEngine({}, market="hk").round_size(raw, 10) == expected


class TestCalcCommission:
    @pytest.mark.parametrize("is_open,direction", [(True, 1), (False, 1), (True, -1), (False, -1)])
    def test_us_zero(self, is_open, direction):
        assert GlobalEquityEngine({}, market="us").calc_commission(100, 50, direction, is_open) == 0.0

    def test_hk_all_four_fees(self):
        engine = GlobalEquityEngine({}, market="hk")
        expected = 1000 * 20 * (0.00015 + 0.001 + 0.0000565 + 0.00002)
        assert engine.calc_commission(1000, 20, 1, True) == pytest.approx(expected)

    def test_hk_open_and_close_equal(self):
        engine = GlobalEquityEngine({}, market="hk")
        assert engine.calc_commission(500, 40, 1, True) == pytest.approx(engine.calc_commission(500, 40, 1, False))

    def test_hk_custom_fees(self):
        engine = GlobalEquityEngine({"hk_commission": 0.001, "hk_stamp_tax": 0.002, "hk_levy": 0.003, "hk_settlement": 0.004}, market="hk")
        assert engine.calc_commission(100, 10, 1, True) == pytest.approx(10.0)


class TestApplySlippage:
    @pytest.mark.parametrize("market,direction,expected", [("us", 1, 100.05), ("us", -1, 99.95), ("hk", 1, 100.1), ("hk", -1, 99.9)])
    def test_market_rate(self, market, direction, expected):
        assert GlobalEquityEngine({}, market=market).apply_slippage(100, direction) == pytest.approx(expected)


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

    @pytest.mark.parametrize("market,symbol,cash,start_price", [("us", "AAPL", 100000.0, 100.0), ("hk", "0700.HK", 1000000.0, 300.0)])
    def test_basic_long(self, market, symbol, cash, start_price):
        engine = GlobalEquityEngine({"initial_cash": cash}, market=market)
        data = self._make_data(symbol, start_price=start_price)
        metrics = engine.run_backtest({symbol: data}, {symbol: pd.Series(1.0, index=data.index)}, [symbol])
        assert len(engine.trades) >= 1
        assert metrics["trade_count"] >= 1
        assert "total_return" in metrics
        assert "sharpe" in metrics
        assert engine.trades[-1].exit_reason == "end_of_backtest"

    def test_zero_signal_metrics(self):
        symbol = "AAPL"
        engine = GlobalEquityEngine({}, market="us")
        data = self._make_data(symbol)
        metrics = engine.run_backtest({symbol: data}, {symbol: pd.Series(0.0, index=data.index)}, [symbol])
        assert metrics["trade_count"] == 0
        assert len(engine.trades) == 0
