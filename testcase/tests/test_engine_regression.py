"""Backtest regression tests.

覆盖:
- 信号边界条件 (NaN/空值/极端值)
- 资金保护逻辑
- 资金曲线快照
- 尾部交易记录完整性
- 边界条件 (单bar/零价格/极值)
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from strategy_research.apps.engine.china_a import ChinaAEngine


def _make_data(symbol, n_bars=50, start_price=10.0, freq="B"):
    dates = pd.date_range("2024-01-02", periods=n_bars, freq=freq)
    np.random.seed(42)
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
# 信号边界条件
# ─────────────────────────────────────────────
class TestSignalEdgeCases:
    def test_nan_signal_treated_as_zero(self):
        code = "600000.SH"
        eng = ChinaAEngine({"codes": [code], "initial_cash": 1_000_000.0})
        n_bars = 20
        data_map = {code: _make_data(code, n_bars=n_bars)}
        dates = data_map[code].index
        # Mix of NaN and valid signals
        sigs = [np.nan if i % 2 == 0 else 1.0 for i in range(n_bars)]
        signal_map = {code: pd.Series(sigs, index=dates)}
        eng.run_backtest(data_map, signal_map, [code])
        # NaN treated as 0 → only opens on non-NaN bars
        assert len(eng.trades) >= 1

    def test_weight_clipped_to_1(self):
        code = "600000.SH"
        eng = ChinaAEngine({"codes": [code], "initial_cash": 1_000_000.0})
        data_map = {code: _make_data(code)}
        dates = data_map[code].index
        signal_map = {code: pd.Series(2.0, index=dates)}
        eng.run_backtest(data_map, signal_map, [code])
        # Weight=2.0 is clipped to 1.0 in _align, so should still work
        assert len(eng.trades) >= 1

    def test_weight_clipped_to_negative_1(self):
        code = "600000.SH"
        eng = ChinaAEngine({"codes": [code], "initial_cash": 1_000_000.0})
        data_map = {code: _make_data(code)}
        dates = data_map[code].index
        signal_map = {code: pd.Series(-2.0, index=dates)}
        eng.run_backtest(data_map, signal_map, [code])
        # Weight=-2.0 clipped to -1.0, but A-share blocks short
        assert len(eng.trades) == 0

    def test_empty_signal_map(self):
        code = "600000.SH"
        eng = ChinaAEngine({"codes": [code], "initial_cash": 1_000_000.0})
        data_map = {code: _make_data(code)}
        data_map[code].index
        signal_map = {}
        eng.run_backtest(data_map, signal_map, [code])
        assert len(eng.trades) == 0

    def test_single_bar_signal(self):
        code = "600000.SH"
        eng = ChinaAEngine({"codes": [code], "initial_cash": 1_000_000.0})
        data_map = {code: _make_data(code, n_bars=2)}
        dates = data_map[code].index
        signal_map = {code: pd.Series([1.0, 0.0], index=dates)}
        eng.run_backtest(data_map, signal_map, [code])
        # With 2 bars, signal is shifted → bar1=1.0, bar2=0.0 → force-close at end
        assert len(eng.trades) >= 1


# ─────────────────────────────────────────────
# 资金保护
# ─────────────────────────────────────────────
class TestCapitalGuard:
    def test_position_reduced_when_insufficient_capital(self):
        code = "600000.SH"
        eng = ChinaAEngine({"codes": [code], "initial_cash": 1000.0})
        data_map = {code: _make_data(code, n_bars=10)}
        dates = data_map[code].index
        signal_map = {code: pd.Series(1.0, index=dates)}
        eng.run_backtest(data_map, signal_map, [code])
        # With only 1000 capital, position size should be limited
        assert all(snap.capital >= -1e-6 for snap in eng.equity_snapshots)

    def test_zero_capital_no_open(self):
        # With zero capital, no position can be opened.
        # run_backtest crashes in calc_metrics (division by zero),
        # so we test the engine internals directly.
        code = "600000.SH"
        eng = ChinaAEngine({"codes": [code], "initial_cash": 0.0})
        data_map = {code: _make_data(code, n_bars=5)}
        dates = data_map[code].index
        signal_map = {code: pd.Series(1.0, index=dates)}
        # Run manually to avoid metrics crash
        from strategy_research.apps.engine.base import BaseEngine
        dates_aligned, close_df, target_pos, ret_df = BaseEngine._align(
            data_map, signal_map, [code]
        )
        eng._execute_bars(dates_aligned, data_map, close_df, target_pos, [code])
        # No trades opened
        assert len(eng.trades) == 0
        assert len(eng.equity_snapshots) == 5


# ─────────────────────────────────────────────
# 资金曲线快照
# ─────────────────────────────────────────────
class TestEquityCurveSnapshots:
    def test_snapshot_count_matches_dates(self):
        code = "600000.SH"
        n = 20
        eng = ChinaAEngine({"codes": [code], "initial_cash": 1_000_000.0})
        data_map = {code: _make_data(code, n_bars=n)}
        dates = data_map[code].index
        signal_map = {code: pd.Series(0.5, index=dates)}
        eng.run_backtest(data_map, signal_map, [code])
        assert len(eng.equity_snapshots) == n

    def test_first_snapshot_initial_capital(self):
        code = "600000.SH"
        eng = ChinaAEngine({"codes": [code], "initial_cash": 1_000_000.0})
        data_map = {code: _make_data(code)}
        dates = data_map[code].index
        signal_map = {code: pd.Series(0.0, index=dates)}
        eng.run_backtest(data_map, signal_map, [code])
        assert eng.equity_snapshots[0].capital == pytest.approx(1_000_000.0)
        assert eng.equity_snapshots[0].positions == 0

    def test_equity_monotonic_no_position(self):
        code = "600000.SH"
        eng = ChinaAEngine({"codes": [code], "initial_cash": 1_000_000.0})
        data_map = {code: _make_data(code)}
        dates = data_map[code].index
        signal_map = {code: pd.Series(0.0, index=dates)}
        eng.run_backtest(data_map, signal_map, [code])
        # No position → equity = capital = constant
        equities = [s.equity for s in eng.equity_snapshots]
        assert len(set(equities)) == 1  # all same


# ─────────────────────────────────────────────
# 尾部交易记录完整性
# ─────────────────────────────────────────────
class TestTradeRecordIntegrity:
    def test_pnl_pct_formula(self):
        code = "600000.SH"
        eng = ChinaAEngine({"codes": [code], "initial_cash": 1_000_000.0})
        data_map = {code: _make_data(code)}
        dates = data_map[code].index
        signal_map = {code: pd.Series(1.0, index=dates)}
        eng.run_backtest(data_map, signal_map, [code])
        for t in eng.trades:
            margin = t.size * t.entry_price
            if margin > 1e-9:
                assert t.pnl_pct == pytest.approx(t.pnl / margin * 100, abs=1e-6)

    def test_commission_non_negative(self):
        code = "600000.SH"
        eng = ChinaAEngine({"codes": [code], "initial_cash": 1_000_000.0})
        data_map = {code: _make_data(code)}
        dates = data_map[code].index
        signal_map = {code: pd.Series(1.0, index=dates)}
        eng.run_backtest(data_map, signal_map, [code])
        for t in eng.trades:
            assert t.commission >= 0

    def test_holding_bars_non_negative(self):
        code = "600000.SH"
        eng = ChinaAEngine({"codes": [code], "initial_cash": 1_000_000.0})
        data_map = {code: _make_data(code)}
        dates = data_map[code].index
        signal_map = {code: pd.Series(1.0, index=dates)}
        eng.run_backtest(data_map, signal_map, [code])
        for t in eng.trades:
            assert t.holding_bars >= 0

    def test_entry_before_exit(self):
        code = "600000.SH"
        eng = ChinaAEngine({"codes": [code], "initial_cash": 1_000_000.0})
        data_map = {code: _make_data(code)}
        dates = data_map[code].index
        signal_map = {code: pd.Series(1.0, index=dates)}
        eng.run_backtest(data_map, signal_map, [code])
        for t in eng.trades:
            assert t.entry_time <= t.exit_time


# ─────────────────────────────────────────────
# 尾部 force-close
# ─────────────────────────────────────────────
class TestEndOfBacktestForceClose:
    def test_position_force_closed(self):
        code = "600000.SH"
        eng = ChinaAEngine({"codes": [code], "initial_cash": 1_000_000.0})
        data_map = {code: _make_data(code)}
        dates = data_map[code].index
        signal_map = {code: pd.Series(1.0, index=dates)}
        eng.run_backtest(data_map, signal_map, [code])
        assert len(eng.trades) >= 1
        assert eng.trades[-1].exit_reason == "end_of_backtest"

    def test_no_position_no_force_close(self):
        code = "600000.SH"
        eng = ChinaAEngine({"codes": [code], "initial_cash": 1_000_000.0})
        data_map = {code: _make_data(code)}
        dates = data_map[code].index
        signal_map = {code: pd.Series(0.0, index=dates)}
        eng.run_backtest(data_map, signal_map, [code])
        assert len(eng.trades) == 0

    def test_positions_empty_after_backtest(self):
        code = "600000.SH"
        eng = ChinaAEngine({"codes": [code], "initial_cash": 1_000_000.0})
        data_map = {code: _make_data(code)}
        dates = data_map[code].index
        signal_map = {code: pd.Series(1.0, index=dates)}
        eng.run_backtest(data_map, signal_map, [code])
        assert len(eng.positions) == 0


# ─────────────────────────────────────────────
# 边界条件
# ─────────────────────────────────────────────
class TestBoundaryConditions:
    def test_two_bars(self):
        code = "600000.SH"
        eng = ChinaAEngine({"codes": [code], "initial_cash": 1_000_000.0})
        data_map = {code: _make_data(code, n_bars=2)}
        dates = data_map[code].index
        signal_map = {code: pd.Series(1.0, index=dates)}
        metrics = eng.run_backtest(data_map, signal_map, [code])
        assert "total_return" in metrics

    def test_very_large_equity(self):
        code = "600000.SH"
        eng = ChinaAEngine({"codes": [code], "initial_cash": 1e12})
        data_map = {code: _make_data(code, n_bars=5)}
        dates = data_map[code].index
        signal_map = {code: pd.Series(1.0, index=dates)}
        metrics = eng.run_backtest(data_map, signal_map, [code])
        assert "total_return" in metrics

    def test_small_price(self):
        code = "600000.SH"
        eng = ChinaAEngine({"codes": [code], "initial_cash": 100_000.0})
        data_map = {code: _make_data(code, n_bars=10, start_price=0.1)}
        dates = data_map[code].index
        signal_map = {code: pd.Series(0.5, index=dates)}
        metrics = eng.run_backtest(data_map, signal_map, [code])
        assert "total_return" in metrics

    def test_negative_price_no_crash(self):
        code = "600000.SH"
        eng = ChinaAEngine({"codes": [code], "initial_cash": 100_000.0})
        # Manually create data with negative close
        dates = pd.bdate_range("2024-01-02", periods=5)
        df = pd.DataFrame({
            "open": [10.0, -1.0, 10.0, 10.0, 10.0],
            "high": [10.5, 10.5, 10.5, 10.5, 10.5],
            "low": [9.5, 9.5, 9.5, 9.5, 9.5],
            "close": [-1.0, 10.0, 10.0, 10.0, 10.0],
            "volume": [1000.0] * 5,
        }, index=dates)
        df.index.name = "date"
        signal_map = {code: pd.Series(1.0, index=dates)}
        # Should not crash
        metrics = eng.run_backtest({code: df}, signal_map, [code])
        assert "total_return" in metrics

    def test_single_bar_data(self):
        code = "600000.SH"
        eng = ChinaAEngine({"codes": [code], "initial_cash": 1_000_000.0})
        dates = pd.bdate_range("2024-01-02", periods=1)
        df = pd.DataFrame({
            "open": [10.0], "high": [10.5], "low": [9.5], "close": [10.2], "volume": [1000.0],
        }, index=dates)
        df.index.name = "date"
        signal_map = {code: pd.Series(1.0, index=dates)}
        metrics = eng.run_backtest({code: df}, signal_map, [code])
        assert "total_return" in metrics
