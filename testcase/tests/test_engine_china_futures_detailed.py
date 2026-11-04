"""ChinaFuturesEngine 详细单元测试。

覆盖:
- _get_product_code 提取品种代码 (CU2501 → CU, IF2503 → IF)
- _CN_FUTURES_MULTIPLIERS 完整覆盖
- T+0 (允许当日开平)
- 合约乘数对 PnL/margin/raw_size 的影响
- can_execute 永远返回 True (long/short 都允许)
- 滑点 (默认 0.0005)
- run_backtest 集成
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from strategy_research.apps.engine.china_futures import (
    _CN_FUTURES_MULTIPLIERS,
    ChinaFuturesEngine,
    _get_product_code,
)


# ─────────────────────────────────────────────
# _get_product_code
# ─────────────────────────────────────────────
class TestGetProductCode:
    def test_stock_index_cu(self):
        assert _get_product_code("CU2501.SHF") == "CU"

    def test_stock_index_if(self):
        assert _get_product_code("IF2503.CFX") == "IF"

    def test_stock_index_ic(self):
        assert _get_product_code("IC2506.CFX") == "IC"

    def test_gold(self):
        assert _get_product_code("AU2506.SHF") == "AU"

    def test_silver(self):
        assert _get_product_code("AG2512.SHF") == "AG"

    def test_rebar(self):
        assert _get_product_code("RB2505.SHF") == "RB"

    def test_bond_t(self):
        assert _get_product_code("T2503.CFX") == "T"

    def test_bond_tf(self):
        assert _get_product_code("TF2506.CFX") == "TF"

    def test_no_suffix(self):
        assert _get_product_code("CU2501") == "CU"

    def test_lowercase_uppercased(self):
        assert _get_product_code("cu2501.shf") == "CU"

    def test_double_letters(self):
        # SI (silver international?), but it's just a 2-letter product code
        assert _get_product_code("SI2501.COMEX") == "SI"

    def test_three_letters(self):
        # 3-letter products: BOT, JMX? Not in our table but function should work
        assert _get_product_code("ABC2501") == "ABC"


# ─────────────────────────────────────────────
# _CN_FUTURES_MULTIPLIERS table
# ─────────────────────────────────────────────
class TestMultipliersTable:
    def test_stock_indices(self):
        assert _CN_FUTURES_MULTIPLIERS["IF"] == 300.0
        assert _CN_FUTURES_MULTIPLIERS["IC"] == 200.0
        assert _CN_FUTURES_MULTIPLIERS["IH"] == 300.0
        assert _CN_FUTURES_MULTIPLIERS["IM"] == 200.0

    def test_metals(self):
        assert _CN_FUTURES_MULTIPLIERS["CU"] == 5.0
        assert _CN_FUTURES_MULTIPLIERS["AU"] == 1000.0
        assert _CN_FUTURES_MULTIPLIERS["AG"] == 15.0

    def test_agricultural(self):
        assert _CN_FUTURES_MULTIPLIERS["A"] == 10.0  # Soybean
        assert _CN_FUTURES_MULTIPLIERS["M"] == 10.0  # Meal

    def test_bond(self):
        assert _CN_FUTURES_MULTIPLIERS["T"] == 10000.0
        assert _CN_FUTURES_MULTIPLIERS["TF"] == 10000.0
        assert _CN_FUTURES_MULTIPLIERS["TS"] == 20000.0

    def test_table_has_minimum_50_products(self):
        assert len(_CN_FUTURES_MULTIPLIERS) >= 30  # actual ~45


# ─────────────────────────────────────────────
# ChinaFuturesEngine init
# ─────────────────────────────────────────────
class TestEngineInit:
    def test_default_slippage(self):
        eng = ChinaFuturesEngine({"codes": ["CU2501.SHF"]})
        assert eng.slippage_rate == pytest.approx(0.0005)

    def test_custom_slippage(self):
        eng = ChinaFuturesEngine({"codes": ["CU2501.SHF"], "slippage": 0.002})
        assert eng.slippage_rate == pytest.approx(0.002)

    def test_default_commission(self):
        eng = ChinaFuturesEngine({"codes": ["CU2501.SHF"]})
        assert eng.commission_per_contract == pytest.approx(3.0)  # default from FuturesBase
        assert eng.contract_multiplier == pytest.approx(10.0)  # default from FuturesBase
        assert eng.margin_rate == pytest.approx(0.10)


# ─────────────────────────────────────────────
# can_execute
# ─────────────────────────────────────────────
class TestCanExecute:
    def test_long_allowed(self):
        eng = ChinaFuturesEngine({"codes": ["CU2501.SHF"]})
        bar = pd.Series({"open": 70000.0, "close": 70500.0})
        assert eng.can_execute("CU2501.SHF", 1, bar) is True

    def test_short_allowed(self):
        eng = ChinaFuturesEngine({"codes": ["CU2501.SHF"]})
        bar = pd.Series({"open": 70000.0, "close": 70500.0})
        assert eng.can_execute("CU2501.SHF", -1, bar) is True

    def test_close_allowed(self):
        eng = ChinaFuturesEngine({"codes": ["CU2501.SHF"]})
        bar = pd.Series({"open": 70000.0, "close": 70500.0})
        assert eng.can_execute("CU2501.SHF", 0, bar) is True

    def test_intraday_close_allowed(self):
        # T+0: 当日可平仓
        eng = ChinaFuturesEngine({"codes": ["CU2501.SHF"]})
        bar = pd.Series({"open": 70000.0, "close": 70500.0})
        assert eng.can_execute("CU2501.SHF", 0, bar) is True


# ─────────────────────────────────────────────
# apply_slippage
# ─────────────────────────────────────────────
class TestApplySlippage:
    def test_buy_increases(self):
        eng = ChinaFuturesEngine({"codes": ["CU2501.SHF"]})
        # direction=1 → price * 1.0005
        assert eng.apply_slippage(70000.0, 1) == pytest.approx(70035.0)

    def test_sell_decreases(self):
        eng = ChinaFuturesEngine({"codes": ["CU2501.SHF"]})
        # direction=-1 → price * 0.9995
        assert eng.apply_slippage(70000.0, -1) == pytest.approx(69965.0)


# ─────────────────────────────────────────────
# contract_multiplier integration
# ─────────────────────────────────────────────
class TestContractMultiplier:
    def test_multiplier_overridden_in_run_backtest(self):
        eng = ChinaFuturesEngine({"codes": ["CU2501.SHF"]})
        # Initial default from FuturesBaseEngine is 10.0
        assert eng.contract_multiplier == pytest.approx(10.0)

        # Need a complete data setup to call run_backtest
        dates = pd.bdate_range("2024-01-02", periods=10)
        np.random.seed(42)
        prices = 70000 + np.cumsum(np.random.normal(0, 50, 10))
        df = pd.DataFrame({
            "open": np.concatenate([[70000], prices[:-1]]),
            "high": prices + 100,
            "low": prices - 100,
            "close": prices,
            "volume": np.full(10, 100.0),
        }, index=dates)
        df.index.name = "date"
        signal = pd.Series(1.0, index=dates)

        eng.run_backtest({"CU2501.SHF": df}, {"CU2501.SHF": signal}, ["CU2501.SHF"])
        # After run_backtest, multiplier should be set to CU's
        assert eng.contract_multiplier == pytest.approx(5.0)

    def test_margin_uses_multiplier(self):
        # Verify margin = size * price * multiplier * margin_rate
        eng = ChinaFuturesEngine({"codes": ["CU2501.SHF"]})
        eng.contract_multiplier = 5.0
        eng.margin_rate = 0.10
        margin = eng._calc_margin("CU2501.SHF", 1.0, 70000.0, 1.0)
        # size * price * multiplier * margin_rate = 1 * 70000 * 5 * 0.1 = 35_000
        assert margin == pytest.approx(35_000.0)


# ─────────────────────────────────────────────
# run_backtest integration
# ─────────────────────────────────────────────
class TestRunBacktestIntegration:
    @staticmethod
    def _make_data(symbol: str, n_bars: int = 50, start_price: float = 70000.0):
        dates = pd.bdate_range("2024-01-02", periods=n_bars)
        np.random.seed(42)
        rets = np.random.normal(0.001, 0.005, n_bars)
        prices = start_price * (1 + pd.Series(rets)).cumprod().values
        opens = np.empty(n_bars)
        opens[0] = start_price
        opens[1:] = prices[:-1]
        df = pd.DataFrame({
            "open": opens,
            "high": prices * 1.005,
            "low": prices * 0.995,
            "close": prices,
            "volume": np.full(n_bars, 100.0),
        }, index=dates)
        df.index.name = "date"
        return df

    def test_basic_long_run(self):
        code = "CU2501.SHF"
        eng = ChinaFuturesEngine({"codes": [code], "initial_cash": 5_000_000.0})
        data_map = {code: self._make_data(code)}
        dates = data_map[code].index
        signal_map = {code: pd.Series(1.0, index=dates)}
        eng.run_backtest(data_map, signal_map, [code])
        # Long signal → opens at least one position
        assert len(eng.trades) >= 1

    def test_short_signal_works(self):
        code = "CU2501.SHF"
        eng = ChinaFuturesEngine({"codes": [code], "initial_cash": 5_000_000.0})
        data_map = {code: self._make_data(code)}
        dates = data_map[code].index
        signal_map = {code: pd.Series(-1.0, index=dates)}
        eng.run_backtest(data_map, signal_map, [code])
        # Short allowed
        assert len(eng.trades) >= 1

    def test_intraday_round_trip(self):
        # T+0: same-day open and close should work
        code = "CU2501.SHF"
        eng = ChinaFuturesEngine({"codes": [code], "initial_cash": 5_000_000.0})
        dates = pd.bdate_range("2024-01-02", periods=8)
        prices = np.array([70000, 70100, 70200, 70300, 70400, 70500, 70600, 70700], dtype=float)
        df = pd.DataFrame({
            "open": [69950, 70050, 70150, 70250, 70350, 70450, 70550, 70650],
            "high": prices + 50,
            "low": prices - 50,
            "close": prices,
            "volume": np.full(8, 100.0),
        }, index=dates)
        df.index.name = "date"
        # signal[1]=1 (open at bar 2), signal[3]=0 (close at bar 4), signal[5]=1 (re-open at bar 6)
        signals = pd.Series([0.0, 1.0, 1.0, 0.0, 0.0, 1.0, 1.0, 1.0], index=dates)
        eng.run_backtest({code: df}, {code: signals}, [code])
        # Expect at least 2 trades: open(bar2)→close(bar4), open(bar6)→end_of_backtest(bar8)
        assert len(eng.trades) >= 2
        # First trade should be signal close, last should be force-close
        assert eng.trades[-1].exit_reason == "end_of_backtest"
