"""ChinaAEngine 详细单元测试。

覆盖:
- 涨跌停 (10% 主板 / 20% 创业板科创板 / 30% 北交所)
- T+1 当日买入不可卖
- 100 股整手 round_size
- 佣金: 万2.5 + 万0.1 过户费, 最低 ¥5
- 印花税: 仅卖出收取
- 滑点: rate × direction
- 禁止做空
- _price_limit 辅助函数
- _calc_pct_change 辅助函数
- 集成 run_backtest: 全流程验证
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from strategy_research.apps.engine.china_a import (
    ChinaAEngine,
    _calc_pct_change,
    _price_limit,
)
from strategy_research.apps.engine.models import Position


# ─── 辅助: 构造 bar Series with named timestamp ──────────
def _bar(date: str, open_: float, close: float, name: bool = True) -> pd.Series:
    s = pd.Series({"open": open_, "close": close, "high": close, "low": open_, "volume": 1_000_000})
    if name:
        s.name = pd.Timestamp(date)
    return s


# ─────────────────────────────────────────────
# _price_limit (price_limit helper)
# ─────────────────────────────────────────────
class TestPriceLimit:
    def test_main_board_10pct(self):
        # 6 digit main board codes (600xxx / 000xxx / 002xxx)
        assert _price_limit("600000.SH") == 0.10
        assert _price_limit("000001.SZ") == 0.10
        assert _price_limit("002001.SZ") == 0.10

    def test_chinext_20pct(self):
        # 300xxx = 创业板 (code uses startswith("300") check)
        assert _price_limit("300001.SZ") == 0.20
        assert _price_limit("300750.SZ") == 0.20

    def test_star_20pct(self):
        # 688xxx = 科创板
        assert _price_limit("688001.SH") == 0.20
        assert _price_limit("688999.SH") == 0.20

    def test_bse_30pct(self):
        # 8xxxxx = 北交所
        assert _price_limit("830001.BJ") == 0.30
        assert _price_limit("835001.BJ") == 0.30

    def test_no_suffix(self):
        # Without suffix, still works based on prefix
        assert _price_limit("600000") == 0.10
        assert _price_limit("300001") == 0.20
        assert _price_limit("688001") == 0.20
        assert _price_limit("830001") == 0.30


# ─────────────────────────────────────────────
# _calc_pct_change (pct_chg helper)
# ─────────────────────────────────────────────
class TestCalcPctChange:
    def test_normal_bar(self):
        bar = _bar("2024-01-02", 100.0, 105.0, name=False)
        assert _calc_pct_change(bar) == pytest.approx(0.05)

    def test_negative_change(self):
        bar = _bar("2024-01-02", 100.0, 98.0, name=False)
        assert _calc_pct_change(bar) == pytest.approx(-0.02)

    def test_zero_open_returns_none(self):
        bar = _bar("2024-01-02", 0.0, 100.0, name=False)
        assert _calc_pct_change(bar) is None

    def test_no_open_field(self):
        bar = pd.Series({"close": 100.0, "high": 100.0, "low": 100.0})
        assert _calc_pct_change(bar) is None


# ─────────────────────────────────────────────
# ChinaAEngine init
# ─────────────────────────────────────────────
class TestChinaAEngineInit:
    def test_default_rates(self):
        eng = ChinaAEngine({"codes": ["600000.SH"]})
        assert eng.commission_rate == pytest.approx(0.00025)
        assert eng.commission_min == pytest.approx(5.0)
        assert eng.stamp_tax == pytest.approx(0.0005)
        assert eng.transfer_fee == pytest.approx(0.00001)
        assert eng.slippage_rate == pytest.approx(0.001)

    def test_leverage_force_1(self):
        # 即使 config 给 leverage=10, A 股强制为 1
        eng = ChinaAEngine({"codes": ["600000.SH"], "leverage": 10.0})
        assert eng.default_leverage == 1.0

    def test_custom_rates_from_config(self):
        eng = ChinaAEngine({
            "codes": ["600000.SH"],
            "commission_rate": 0.0003,
            "commission_min": 10.0,
            "stamp_tax": 0.001,
            "slippage": 0.005,
        })
        assert eng.commission_rate == pytest.approx(0.0003)
        assert eng.commission_min == pytest.approx(10.0)
        assert eng.stamp_tax == pytest.approx(0.001)
        assert eng.slippage_rate == pytest.approx(0.005)

    def test_initial_capital_from_config(self):
        eng = ChinaAEngine({"codes": ["600000.SH"], "initial_cash": 500_000.0})
        assert eng.initial_capital == 500_000.0
        assert eng.capital == 500_000.0


# ─────────────────────────────────────────────
# can_execute: T+1, 涨跌停, 禁止做空
# ─────────────────────────────────────────────
class TestCanExecute:
    def test_short_blocked(self):
        eng = ChinaAEngine({"codes": ["600000.SH"]})
        bar = _bar("2024-01-02", 10.0, 10.5)  # +5%, no limit hit
        assert eng.can_execute("600000.SH", -1, bar) is False

    def test_long_allowed_normal(self):
        eng = ChinaAEngine({"codes": ["600000.SH"]})
        bar = _bar("2024-01-02", 10.0, 10.5)  # +5%
        assert eng.can_execute("600000.SH", 1, bar) is True

    def test_close_no_position_allowed(self):
        eng = ChinaAEngine({"codes": ["600000.SH"]})
        bar = _bar("2024-01-02", 10.0, 10.5)
        assert eng.can_execute("600000.SH", 0, bar) is True

    def test_t_plus_1_close_blocked_same_day(self):
        # 当日买入的仓位, 当日不能卖
        eng = ChinaAEngine({"codes": ["600000.SH"]})
        ts = pd.Timestamp("2024-01-02")
        eng.positions["600000.SH"] = Position(
            symbol="600000.SH",
            direction=1,
            entry_price=10.0,
            entry_time=ts,
            size=100,
            leverage=1.0,
            entry_bar_idx=0,
        )
        bar = _bar("2024-01-02", 10.5, 11.0)
        assert eng.can_execute("600000.SH", 0, bar) is False

    def test_t_plus_1_close_allowed_next_day(self):
        eng = ChinaAEngine({"codes": ["600000.SH"]})
        entry_ts = pd.Timestamp("2024-01-02")
        eng.positions["600000.SH"] = Position(
            symbol="600000.SH",
            direction=1,
            entry_price=10.0,
            entry_time=entry_ts,
            size=100,
            leverage=1.0,
        )
        # next day bar
        bar = _bar("2024-01-03", 10.5, 11.0)
        assert eng.can_execute("600000.SH", 0, bar) is True

    def test_up_limit_blocks_buy(self):
        # 涨停时, 不能买 (10% main board)
        eng = ChinaAEngine({"codes": ["600000.SH"]})
        bar = _bar("2024-01-02", 10.0, 11.0)  # exactly +10%
        # boundary: pct_chg >= limit - 0.001 → blocks
        assert eng.can_execute("600000.SH", 1, bar) is False

    def test_chinext_up_limit_blocks_buy(self):
        eng = ChinaAEngine({"codes": ["300001.SZ"]})
        bar = _bar("2024-01-02", 10.0, 12.0)  # +20%
        assert eng.can_execute("300001.SZ", 1, bar) is False

    def test_bse_up_limit_blocks_buy(self):
        eng = ChinaAEngine({"codes": ["830001.BJ"]})
        bar = _bar("2024-01-02", 10.0, 13.0)  # +30%
        assert eng.can_execute("830001.BJ", 1, bar) is False

    def test_down_limit_blocks_sell(self):
        eng = ChinaAEngine({"codes": ["600000.SH"]})
        bar = _bar("2024-01-02", 10.0, 9.0)  # exactly -10%
        assert eng.can_execute("600000.SH", 0, bar) is False

    def test_below_up_limit_allows_buy(self):
        eng = ChinaAEngine({"codes": ["600000.SH"]})
        bar = _bar("2024-01-02", 10.0, 10.05)  # +0.5%
        assert eng.can_execute("600000.SH", 1, bar) is True


# ─────────────────────────────────────────────
# round_size: 100-share lot
# ─────────────────────────────────────────────
class TestRoundSize:
    def test_below_100_rounds_to_zero(self):
        eng = ChinaAEngine({"codes": ["600000.SH"]})
        assert eng.round_size(50, 10.0) == 0

    def test_exact_100(self):
        eng = ChinaAEngine({"codes": ["600000.SH"]})
        assert eng.round_size(100, 10.0) == 100

    def test_150_rounds_down_to_100(self):
        eng = ChinaAEngine({"codes": ["600000.SH"]})
        assert eng.round_size(150, 10.0) == 100

    def test_250_rounds_down_to_200(self):
        eng = ChinaAEngine({"codes": ["600000.SH"]})
        assert eng.round_size(250, 10.0) == 200

    def test_1000(self):
        eng = ChinaAEngine({"codes": ["600000.SH"]})
        assert eng.round_size(1000, 10.0) == 1000

    def test_negative_rounds_to_zero(self):
        eng = ChinaAEngine({"codes": ["600000.SH"]})
        assert eng.round_size(-100, 10.0) == 0


# ─────────────────────────────────────────────
# calc_commission
# ─────────────────────────────────────────────
class TestCalcCommission:
    def test_open_basic(self):
        # notional = 1000 * 10 = 10000
        # comm = 10000 * 0.00025 = 2.5 (below min 5.0 → use 5.0)
        # + transfer 10000 * 0.00001 = 0.1
        # Total = 5.0 + 0.1 = 5.1
        eng = ChinaAEngine({"codes": ["600000.SH"]})
        comm = eng.calc_commission(1000, 10.0, 1, is_open=True)
        assert comm == pytest.approx(5.1)

    def test_open_large_size_no_min(self):
        # notional = 100000 * 10 = 1,000,000
        # comm = 1M * 0.00025 = 250
        # + transfer 1M * 0.00001 = 10
        # Total = 260
        eng = ChinaAEngine({"codes": ["600000.SH"]})
        comm = eng.calc_commission(100_000, 10.0, 1, is_open=True)
        assert comm == pytest.approx(260.0)

    def test_close_includes_stamp_tax(self):
        # close: comm + transfer + stamp_tax
        # 1,000,000 * 0.00025 = 250
        # 1,000,000 * 0.00001 = 10
        # 1,000,000 * 0.0005 = 500 (stamp tax only on close)
        # Total = 760
        eng = ChinaAEngine({"codes": ["600000.SH"]})
        comm = eng.calc_commission(100_000, 10.0, -1, is_open=False)
        assert comm == pytest.approx(760.0)

    def test_open_short_direction_no_extra(self):
        # 禁止做空 but commission still calculated on direction=-1
        eng = ChinaAEngine({"codes": ["600000.SH"]})
        comm_open = eng.calc_commission(100_000, 10.0, -1, is_open=True)
        comm_long = eng.calc_commission(100_000, 10.0, 1, is_open=True)
        # direction only matters for which side, not comm rate
        assert comm_open == comm_long


# ─────────────────────────────────────────────
# apply_slippage
# ─────────────────────────────────────────────
class TestApplySlippage:
    def test_buy_increases_price(self):
        eng = ChinaAEngine({"codes": ["600000.SH"]})  # slippage 0.001
        # direction=1 → price * 1.001
        assert eng.apply_slippage(100.0, 1) == pytest.approx(100.1)

    def test_sell_decreases_price(self):
        eng = ChinaAEngine({"codes": ["600000.SH"]})
        # direction=-1 → price * 0.999
        assert eng.apply_slippage(100.0, -1) == pytest.approx(99.9)

    def test_custom_slippage(self):
        eng = ChinaAEngine({"codes": ["600000.SH"], "slippage": 0.01})
        assert eng.apply_slippage(100.0, 1) == pytest.approx(101.0)


# ─────────────────────────────────────────────
# Integration: full run_backtest
# ─────────────────────────────────────────────
class TestRunBacktestIntegration:
    @staticmethod
    def _make_data(code: str, n_bars: int = 50, start_price: float = 10.0) -> pd.DataFrame:
        dates = pd.bdate_range("2024-01-02", periods=n_bars)
        np.random.seed(42)
        rets = np.random.normal(0.001, 0.01, n_bars)
        prices = start_price * (1 + pd.Series(rets)).cumprod().values
        # Build arrays directly, avoiding index alignment issues
        opens = np.empty(n_bars)
        opens[0] = start_price
        opens[1:] = prices[:-1]
        df = pd.DataFrame({
            "open": opens,
            "high": prices * 1.005,
            "low": prices * 0.995,
            "close": prices,
            "volume": np.full(n_bars, 1_000_000.0),
        }, index=dates)
        df.index.name = "date"
        return df

    def test_simple_long_only_run(self):
        code = "600000.SH"
        eng = ChinaAEngine({"codes": [code], "initial_cash": 1_000_000.0})
        data_map = {code: self._make_data(code)}
        dates = data_map[code].index
        # Always hold long
        signal_map = {code: pd.Series(1.0, index=dates)}
        metrics = eng.run_backtest(data_map, signal_map, [code])
        # Should produce trades
        assert "total_return" in metrics
        assert "sharpe" in metrics
        # Should have opened at least once and force-closed at end
        assert len(eng.trades) >= 1
        # Last trade should be force-close
        assert eng.trades[-1].exit_reason == "end_of_backtest"

    def test_capital_guard(self):
        # Test that position is opened with correct margin + commission
        code = "600000.SH"
        eng = ChinaAEngine({"codes": [code], "initial_cash": 100_000.0})
        data_map = {code: self._make_data(code, n_bars=10)}
        dates = data_map[code].index
        signal_map = {code: pd.Series(1.0, index=dates)}
        eng.run_backtest(data_map, signal_map, [code])
        # Capital must remain >= 0 throughout (cannot go negative)
        assert all(snap.capital >= -1e-6 for snap in eng.equity_snapshots)

    def test_metrics_keys_present(self):
        code = "600000.SH"
        eng = ChinaAEngine({"codes": [code]})
        data_map = {code: self._make_data(code)}
        dates = data_map[code].index
        signal_map = {code: pd.Series(0.5, index=dates)}
        metrics = eng.run_backtest(data_map, signal_map, [code])
        # Standard metrics keys
        for key in ["total_return", "annual_return", "sharpe",
                    "max_drawdown", "win_rate", "trade_count"]:
            assert key in metrics

    def test_short_signal_ignored(self):
        # 禁止做空: -1 signal should not generate short trades
        code = "600000.SH"
        eng = ChinaAEngine({"codes": [code]})
        data_map = {code: self._make_data(code)}
        dates = data_map[code].index
        signal_map = {code: pd.Series(-1.0, index=dates)}
        eng.run_backtest(data_map, signal_map, [code])
        # No trades because short is blocked
        assert len(eng.trades) == 0

    def test_zero_signal_no_trades(self):
        code = "600000.SH"
        eng = ChinaAEngine({"codes": [code]})
        data_map = {code: self._make_data(code)}
        dates = data_map[code].index
        signal_map = {code: pd.Series(0.0, index=dates)}
        eng.run_backtest(data_map, signal_map, [code])
        # 0 signal should generate no trades
        assert len(eng.trades) == 0
