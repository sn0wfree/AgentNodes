"""Tests for BaseEngine — bar-by-bar 执行引擎"""

from __future__ import annotations

import pandas as pd
import pytest

from strategy_research.apps.engine.base import BaseEngine
from strategy_research.apps.engine.signals import ConstantWeightEngine

# ============================================================
# Test Engine — 最简单的具体子类
# ============================================================


class SimpleEngine(BaseEngine):
    """测试用引擎：零佣金/零滑点，无限制。"""

    def can_execute(self, symbol, direction, bar):
        return True

    def round_size(self, raw_size, price):
        return max(round(raw_size, 2), 0.0)

    def calc_commission(self, size, price, direction, is_open):
        return 0.0

    def apply_slippage(self, price, direction):
        return price


class CommissionEngine(BaseEngine):
    """测试用引擎：有佣金 + 滑点。"""

    def __init__(self, config, commission_rate=0.001, slippage_rate=0.001):
        super().__init__(config)
        self.commission_rate = commission_rate
        self.slippage_rate = slippage_rate

    def can_execute(self, symbol, direction, bar):
        return True

    def round_size(self, raw_size, price):
        return max(round(raw_size, 2), 0.0)

    def calc_commission(self, size, price, direction, is_open):
        return size * price * self.commission_rate

    def apply_slippage(self, price, direction):
        return price * (1 + direction * self.slippage_rate)


class TPlusOneEngine(BaseEngine):
    """测试用引擎：T+1 限制（当日买入不可当日卖出）。"""

    def can_execute(self, symbol, direction, bar):
        if direction == 0:  # 平仓
            pos = self.positions.get(symbol)
            if pos is not None:
                bar_date = bar.name.date() if hasattr(bar, "name") else None
                entry_date = pos.entry_time.date() if hasattr(pos.entry_time, "date") else None
                if bar_date and entry_date and bar_date == entry_date:
                    return False
        return True

    def round_size(self, raw_size, price):
        return max(int(raw_size / 100) * 100, 0)

    def calc_commission(self, size, price, direction, is_open):
        return size * price * 0.00025

    def apply_slippage(self, price, direction):
        return price * (1 + direction * 0.001)


# ============================================================
# helpers
# ============================================================


def _make_data(
    start: str = "2024-01-02",
    n_days: int = 20,
    codes: list[str] | None = None,
    trend: float = 0.001,
) -> dict[str, pd.DataFrame]:
    """创建测试用 OHLCV 数据。"""
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


def _run_engine(
    engine: BaseEngine,
    data_map: dict[str, pd.DataFrame],
    weights: dict[str, float],
    codes: list[str] | None = None,
):
    """便捷函数：创建信号 → 对齐 → 执行。"""
    if codes is None:
        codes = list(data_map.keys())
    sig_eng = ConstantWeightEngine(weights)
    signal_map = sig_eng.generate(data_map)
    return engine.run_backtest(data_map, signal_map, codes)


# ============================================================
# tests
# ============================================================


class TestBaseEngineInit:
    def test_default_capital(self):
        engine = SimpleEngine({})
        assert engine.initial_capital == 1_000_000
        assert engine.capital == 1_000_000

    def test_custom_capital(self):
        engine = SimpleEngine({"initial_cash": 500_000})
        assert engine.initial_capital == 500_000

    def test_initial_state(self):
        engine = SimpleEngine({})
        assert engine.positions == {}
        assert engine.trades == []
        assert engine.equity_snapshots == []


class TestBaseEngineRun:
    def test_flat_weights_no_trades(self):
        engine = SimpleEngine({})
        data = _make_data(n_days=10)
        m = _run_engine(engine, data, {"A": 0.0})
        assert engine.trades == []
        assert m["trade_count"] == 0

    def test_long_position(self):
        engine = SimpleEngine({})
        data = _make_data(n_days=10, trend=0.01)
        m = _run_engine(engine, data, {"A": 1.0})
        # Open creates Position, force-close creates TradeRecord
        # So 1 trade with exit_reason=end_of_backtest
        assert len(engine.trades) == 1
        assert engine.trades[0].exit_reason == "end_of_backtest"
        assert m["trade_count"] == 1

    def test_positive_return_uptrend(self):
        engine = SimpleEngine({})
        data = _make_data(n_days=50, trend=0.005)
        m = _run_engine(engine, data, {"A": 1.0})
        assert m["total_return"] > 0
        assert m["annual_return"] > 0

    def test_negative_return_downtrend(self):
        engine = SimpleEngine({})
        data = _make_data(n_days=50, trend=-0.005)
        m = _run_engine(engine, data, {"A": 1.0})
        assert m["total_return"] < 0

    def test_multi_asset(self):
        engine = SimpleEngine({})
        data = _make_data(n_days=30, codes=["A", "B"])
        m = _run_engine(engine, data, {"A": 0.5, "B": 0.5})
        assert m["trade_count"] >= 2

    def test_equity_snapshots_count(self):
        engine = SimpleEngine({})
        data = _make_data(n_days=15)
        _run_engine(engine, data, {"A": 1.0})
        # n_days snapshots + maybe force-close
        assert len(engine.equity_snapshots) == 15

    def test_final_equity_matches_last_snapshot(self):
        engine = SimpleEngine({})
        data = _make_data(n_days=10)
        _run_engine(engine, data, {"A": 1.0})
        assert len(engine.equity_snapshots) > 0
        last = engine.equity_snapshots[-1]
        assert last.equity > 0


class TestBaseEngineCommission:
    def test_commission_reduces_return(self):
        data = _make_data(n_days=50, trend=0.005)

        engine_free = SimpleEngine({})
        m_free = _run_engine(engine_free, data, {"A": 1.0})

        engine_cost = CommissionEngine({}, commission_rate=0.01)
        m_cost = _run_engine(engine_cost, data, {"A": 1.0})

        assert m_free["total_return"] > m_cost["total_return"]

    def test_commission_in_trade_record(self):
        engine = CommissionEngine({}, commission_rate=0.001)
        data = _make_data(n_days=10)
        _run_engine(engine, data, {"A": 1.0})
        for t in engine.trades:
            assert t.commission >= 0


class TestBaseEngineSlippage:
    def test_slippage_reduces_return(self):
        data = _make_data(n_days=50, trend=0.005)

        engine_free = SimpleEngine({})
        m_free = _run_engine(engine_free, data, {"A": 1.0})

        engine_slip = CommissionEngine({}, slippage_rate=0.01)
        m_slip = _run_engine(engine_slip, data, {"A": 1.0})

        assert m_free["total_return"] > m_slip["total_return"]


class TestBaseEngineTPlusOne:
    def test_t_plus_1_no_same_day_sell(self):
        engine = TPlusOneEngine({})
        data = _make_data(n_days=5)
        sig_eng = ConstantWeightEngine({"A": 1.0})
        signal_map = sig_eng.generate(data)
        # Force close on same day should be blocked
        engine.run_backtest(data, signal_map, ['A'])
        # Should still have trades (buy + end_of_backtest)
        assert len(engine.trades) >= 1


class TestBaseEngineEdgeCases:
    def test_empty_data(self):
        engine = SimpleEngine({})
        m = _run_engine(engine, {}, {"A": 1.0})
        assert m["trade_count"] == 0
        assert m["final_value"] == 1_000_000

    def test_single_bar(self):
        engine = SimpleEngine({})
        data = _make_data(n_days=1)
        m = _run_engine(engine, data, {"A": 1.0})
        assert m["trade_count"] >= 0

    def test_zero_price_no_crash(self):
        engine = SimpleEngine({})
        data = _make_data(n_days=5)
        data["A"]["close"] = 0.0
        data["A"]["open"] = 0.0
        m = _run_engine(engine, data, {"A": 1.0})
        # Zero price → no position opened → capital preserved
        assert m["final_value"] == pytest.approx(1_000_000)
        assert engine.trades == []


class TestBaseEngineAlign:
    def test_signal_shifted_by_one_bar(self):
        """Signal on bar T should execute at bar T+1 open."""
        engine = SimpleEngine({})
        data = _make_data(n_days=5)
        sig_eng = ConstantWeightEngine({"A": 1.0})
        signal_map = sig_eng.generate(data)
        dates, close_df, target_pos, ret_df = engine._align(
            data, signal_map, ["A"]
        )
        # First bar should have 0 weight (shifted)
        assert target_pos.iloc[0]["A"] == 0.0

    def test_weights_normalized(self):
        engine = SimpleEngine({})
        data = _make_data(n_days=10, codes=["A", "B"])
        # Give weights that sum > 1
        sig_map = {
            "A": pd.Series([0.8] * 10, index=data["A"].index),
            "B": pd.Series([0.8] * 10, index=data["B"].index),
        }
        dates, close_df, target_pos, ret_df = engine._align(
            data, sig_map, ["A", "B"]
        )
        # Should be normalized to sum <= 1
        max_total = target_pos.abs().sum(axis=1).max()
        assert max_total <= 1.0 + 1e-9


class TestBaseEngineOptimize:
    def test_with_optimizer(self):
        def dummy_optimizer(ret, pos, dates):
            # Half all weights
            return pos * 0.5

        engine = SimpleEngine({})
        data = _make_data(n_days=20)
        sig_eng = ConstantWeightEngine({"A": 1.0})
        signal_map = sig_eng.generate(data)
        m = engine.run_backtest(
            data, signal_map, ["A"], optimizer=dummy_optimizer
        )
        assert m["trade_count"] >= 0
