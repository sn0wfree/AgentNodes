"""Edge cases for backtest_metrics.calc_metrics beyond the happy path."""
from __future__ import annotations

import pandas as pd
import pytest

from strategy_research.foundation.utils.backtest_metrics import (
    _empty_metrics,
    calc_metrics,
)
from strategy_research.foundation.utils.backtest_models import TradeRecord


def _equity(values, start="2024-01-01") -> pd.Series:
    idx = pd.date_range(start, periods=len(values), freq="D")
    return pd.Series(values, dtype=float, index=idx)


def _trade(pnl: float, symbol: str = "S") -> TradeRecord:
    t0 = pd.Timestamp("2024-01-02")
    return TradeRecord(
        symbol=symbol,
        direction=1,
        entry_price=10.0,
        exit_price=11.0,
        entry_time=t0,
        exit_time=t0 + pd.Timedelta(hours=1),
        size=1.0,
        leverage=1.0,
        pnl=pnl,
        pnl_pct=pnl / 100.0,
        exit_reason="signal",
        holding_bars=1,
    )


class TestEmptyMetricsContract:
    def test_seventeen_keys(self):
        m = _empty_metrics(1000.0)
        assert len(m) == 17
        assert m["final_value"] == 1000.0
        assert m["trade_count"] == 0


class TestDegenerateEquity:
    def test_single_bar_no_crash(self):
        m = calc_metrics(_equity([100.0]), [], initial_cash=100.0)
        assert m["final_value"] == 100.0
        assert m["trade_count"] == 0

    def test_flat_equity_zero_return(self):
        m = calc_metrics(_equity([100.0] * 10), [], initial_cash=100.0)
        assert m["total_return"] == 0.0
        assert m["max_drawdown"] == 0.0
        assert m["calmar"] == 0.0  # no drawdown → calmar defined as 0

    def test_zero_initial_cash_does_not_divide_by_zero(self):
        m = calc_metrics(_equity([10.0, 10.0]), [], initial_cash=0.0)
        assert m["total_return"] == 0.0

    def test_all_keys_present_after_edge_run(self):
        m = calc_metrics(_equity([50.0, 120.0]), [], initial_cash=100.0)
        assert set(m) == set(_empty_metrics(1.0))


class TestBenchmarkPaths:
    def test_bench_shorter_than_equity_aligns(self):
        eq = _equity([100, 101, 102, 103, 104])
        bench = pd.Series([0.01, 0.01], index=eq.index[:2])
        m = calc_metrics(eq, [], initial_cash=100.0, bench_ret=bench)
        assert "excess_return" in m
        assert "information_ratio" in m

    def test_bench_none_keeps_zeros(self):
        m = calc_metrics(_equity([100, 110]), [], initial_cash=100.0, bench_ret=None)
        assert m["benchmark_return"] == 0.0
        assert m["excess_return"] == 0.0


class TestTradeDependentMetrics:
    def test_trade_count_exact(self):
        trades = [_trade(1.0), _trade(-1.0), _trade(2.0)]
        m = calc_metrics(_equity([100, 101, 102]), trades, initial_cash=100.0)
        assert m["trade_count"] == 3

    def test_win_rate_all_win(self):
        trades = [_trade(5.0), _trade(3.0)]
        m = calc_metrics(_equity([100, 101, 102]), trades, initial_cash=100.0)
        assert m["win_rate"] == pytest.approx(1.0)

    def test_turnover_passthrough(self):
        m = calc_metrics(_equity([100, 101]), [], initial_cash=100.0, turnover=0.42)
        assert m["turnover"] == pytest.approx(0.42)


class TestBarsPerYearNone:
    def test_auto_annualization_runs(self):
        eq = _equity([100 + i for i in range(60)])
        m = calc_metrics(eq, [], initial_cash=100.0, bars_per_year=None)
        assert m["annual_return"] == m["annual_return"]  # not NaN

    def test_single_day_fallback(self):
        eq = _equity([100.0], start="2024-01-01")
        m = calc_metrics(eq, [], initial_cash=100.0, bars_per_year=None)
        assert "sharpe" in m
