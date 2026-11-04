import numpy as np
import pandas as pd
import pytest

from strategy_research.apps.engine.global_futures import (
    _GLOBAL_FUTURES_MULTIPLIERS,
    GlobalFuturesEngine,
    _get_global_product_code,
)


def _bar(date, close, name=True):
    s = pd.Series({"open": close, "high": close*1.001, "low": close*0.999, "close": close, "volume": 1000.0})
    if name:
        s.name = pd.Timestamp(date)
    return s


class TestGetGlobalProductCode:
    @pytest.mark.parametrize("symbol,expected", [("ES2503", "ES"), ("CL2506", "CL"), ("6E2503", "6E"), ("FDAX2506", "FDAX"), ("GC2506", "GC"), ("NQ2503.CME", "NQ"), ("es2503.cme", "ES")])
    def test_extract(self, symbol, expected):
        assert _get_global_product_code(symbol) == expected


class TestMultipliersTable:
    @pytest.mark.parametrize("product,expected", [("ES", 50), ("NQ", 20), ("YM", 5), ("CL", 1000), ("GC", 100), ("SI", 5000), ("6E", 125000)])
    def test_specific_entry(self, product, expected):
        assert _GLOBAL_FUTURES_MULTIPLIERS[product] == expected

    def test_additional_entries(self):
        assert _GLOBAL_FUTURES_MULTIPLIERS["HG"] == 25000
        assert _GLOBAL_FUTURES_MULTIPLIERS["ZB"] == 1000
        assert _GLOBAL_FUTURES_MULTIPLIERS["ZN"] == 1000


class TestCanExecute:
    @pytest.mark.parametrize("direction", [1, -1, 0])
    def test_all_directions_allowed(self, direction):
        assert GlobalFuturesEngine({}).can_execute("ES2503", direction, _bar("2024-01-02", 4800)) is True


class TestApplySlippage:
    @pytest.mark.parametrize("direction,expected", [(1, 100.05), (-1, 99.95), (0, 100)])
    def test_default_rate(self, direction, expected):
        assert GlobalFuturesEngine({}).apply_slippage(100, direction) == pytest.approx(expected)

    def test_custom_rate(self):
        assert GlobalFuturesEngine({"slippage": 0.002}).apply_slippage(100, 1) == pytest.approx(100.2)


class TestContractMultiplier:
    @staticmethod
    def _inputs(symbol, price):
        dates = pd.bdate_range("2024-01-02", periods=5)
        data = pd.DataFrame({"open": price, "high": price*1.001, "low": price*0.999, "close": price, "volume": 1000.0}, index=dates)
        return data, pd.Series(0.0, index=dates)

    @pytest.mark.parametrize("symbol,price,expected", [("ES2503", 5000, 50), ("CL2506", 80, 1000), ("GC2506", 2000, 100)])
    def test_override_in_run_backtest(self, symbol, price, expected):
        engine = GlobalFuturesEngine({})
        data, signal = self._inputs(symbol, price)
        engine.run_backtest({symbol: data}, {symbol: signal}, [symbol])
        assert engine.contract_multiplier == expected

    def test_first_matching_code_wins(self):
        engine = GlobalFuturesEngine({})
        es_data, es_signal = self._inputs("ES2503", 5000)
        cl_data, cl_signal = self._inputs("CL2506", 80)
        engine.run_backtest({"ES2503": es_data, "CL2506": cl_data}, {"ES2503": es_signal, "CL2506": cl_signal}, ["ES2503", "CL2506"])
        assert engine.contract_multiplier == 50


class TestMarginWithMultiplier:
    @pytest.mark.parametrize("size,price,multiplier,rate", [(2, 5000, 50, 0.1), (3, 80, 1000, 0.12), (1, 2000, 100, 0.05)])
    def test_formula(self, size, price, multiplier, rate):
        engine = GlobalFuturesEngine({"contract_multiplier": multiplier, "margin_rate": rate})
        assert engine._calc_margin("TEST", size, price, 1) == pytest.approx(size * price * multiplier * rate)

    def test_leverage_does_not_change_margin(self):
        engine = GlobalFuturesEngine({"contract_multiplier": 50, "margin_rate": 0.1})
        assert engine._calc_margin("ES2503", 2, 5000, 10) == pytest.approx(50000)


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

    def test_basic_long_es(self):
        symbol = "ES2503"
        engine = GlobalFuturesEngine({"initial_cash": 5000000})
        data = self._make_data(symbol, start_price=5000)
        metrics = engine.run_backtest({symbol: data}, {symbol: pd.Series(1.0, index=data.index)}, [symbol])
        assert len(engine.trades) >= 1
        assert engine.trades[0].direction == 1
        assert metrics["trade_count"] >= 1
        assert engine.contract_multiplier == 50

    def test_basic_short_cl(self):
        symbol = "CL2506"
        engine = GlobalFuturesEngine({"initial_cash": 5000000})
        data = self._make_data(symbol, start_price=80)
        metrics = engine.run_backtest({symbol: data}, {symbol: pd.Series(-1.0, index=data.index)}, [symbol])
        assert len(engine.trades) >= 1
        assert engine.trades[0].direction == -1
        assert metrics["trade_count"] >= 1
        assert engine.contract_multiplier == 1000

    def test_force_close_and_metrics(self):
        symbol = "NQ2503"
        engine = GlobalFuturesEngine({"initial_cash": 5000000})
        data = self._make_data(symbol, start_price=18000)
        metrics = engine.run_backtest({symbol: data}, {symbol: pd.Series(1.0, index=data.index)}, [symbol])
        assert engine.trades[-1].exit_reason == "end_of_backtest"
        for key in ["total_return", "sharpe", "max_drawdown", "trade_count"]:
            assert key in metrics
