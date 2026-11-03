"""Tests for backtest_metrics.py — bars_per_year + 17-key calc_metrics + trade stats"""

from __future__ import annotations

import pandas as pd
import pytest

from strategy_research.foundation.utils.backtest_metrics import (
    _empty_metrics,
    by_exit_reason_stats,
    by_symbol_stats,
    calc_bars_per_year,
    calc_metrics,
    win_rate_and_stats,
)
from strategy_research.foundation.utils.backtest_models import TradeRecord


def _ts(s: str) -> pd.Timestamp:
    return pd.Timestamp(s)


def _make_trade(
    symbol: str = "AAPL",
    pnl: float = 100.0,
    exit_reason: str = "signal",
    holding_bars: int = 5,
    entry_price: float = 100.0,
    exit_price: float = 110.0,
    size: float = 10.0,
    direction: int = 1,
    entry_time: str = "2024-01-02",
    exit_time: str = "2024-01-10",
) -> TradeRecord:
    return TradeRecord(
        symbol=symbol,
        direction=direction,
        entry_price=entry_price,
        exit_price=exit_price,
        entry_time=_ts(entry_time),
        exit_time=_ts(exit_time),
        size=size,
        leverage=1.0,
        pnl=pnl,
        pnl_pct=pnl / (entry_price * size) if entry_price * size != 0 else 0.0,
        exit_reason=exit_reason,
        holding_bars=holding_bars,
    )


def _make_equity(start: str, n_days: int, slope: float = 0.001) -> pd.Series:
    """Create a synthetic equity curve with constant daily returns."""
    dates = pd.bdate_range(start, periods=n_days)
    initial = 100_000.0
    returns = pd.Series([slope] * n_days, index=dates)
    nav = initial * (1 + returns).cumprod()
    return nav


# ============================================================
# calc_bars_per_year
# ============================================================


class TestCalcBarsPerYear:
    @pytest.mark.parametrize(
        ("interval", "source", "expected"),
        [
            ("1D", "tushare", 252),
            ("1D", "yfinance", 252),
            ("1D", "okx", 365),
            ("1D", "ccxt", 365),
            ("1H", "tushare", 1008),
            ("1H", "okx", 8760),
            ("1H", "yfinance", 1512),
            ("5m", "tushare", 12096),
            ("5m", "okx", 105120),
            ("1m", "tushare", 60480),
            ("1m", "okx", 525600),
        ],
    )
    def test_specific_values(self, interval, source, expected):
        assert calc_bars_per_year(interval, source) == expected

    def test_default_is_daily_tushare(self):
        assert calc_bars_per_year() == 252

    def test_unknown_source_defaults_252_days(self):
        # Unknown source → 252 trading days, 1D → 252
        assert calc_bars_per_year("1D", "unknown_source") == 252

    def test_unknown_interval_returns_1(self):
        # Unknown interval → bars_per_day=1, so result = trading_days
        assert calc_bars_per_year("UNKNOWN", "tushare") == 252

    def test_4h_tushare(self):
        assert calc_bars_per_year("4H", "tushare") == 252

    def test_15m_yfinance(self):
        # 252 * 26 = 6552
        assert calc_bars_per_year("15m", "yfinance") == 6552


# ============================================================
# win_rate_and_stats
# ============================================================


class TestWinRateAndStats:
    def test_empty_trades(self):
        result = win_rate_and_stats([])
        assert result["win_rate"] == 0.0
        assert result["profit_loss_ratio"] == 0.0
        assert result["profit_factor"] == 0.0
        assert result["max_consecutive_loss"] == 0
        assert result["avg_holding_bars"] == 0.0

    def test_all_wins(self):
        trades = [_make_trade(pnl=100) for _ in range(5)]
        result = win_rate_and_stats(trades)
        assert result["win_rate"] == 1.0
        assert result["max_consecutive_loss"] == 0

    def test_all_losses(self):
        trades = [_make_trade(pnl=-50) for _ in range(4)]
        result = win_rate_and_stats(trades)
        assert result["win_rate"] == 0.0
        assert result["max_consecutive_loss"] == 4

    def test_mixed_trades(self):
        trades = [
            _make_trade(pnl=200),
            _make_trade(pnl=-100),
            _make_trade(pnl=300),
            _make_trade(pnl=-50),
            _make_trade(pnl=150),
        ]
        result = win_rate_and_stats(trades)
        assert result["win_rate"] == 0.6
        # avg_win = (200+300+150)/3 = 216.67, avg_loss = (100+50)/2 = 75
        assert result["profit_loss_ratio"] == pytest.approx(216.67 / 75.0, rel=1e-2)
        assert result["profit_factor"] == pytest.approx(650.0 / 150.0, rel=1e-2)
        assert result["max_consecutive_loss"] == 1

    def test_max_consecutive_loss_streak(self):
        trades = [
            _make_trade(pnl=-10),
            _make_trade(pnl=-20),
            _make_trade(pnl=-30),
            _make_trade(pnl=100),
            _make_trade(pnl=-5),
            _make_trade(pnl=-15),
        ]
        result = win_rate_and_stats(trades)
        assert result["max_consecutive_loss"] == 3

    def test_avg_holding_bars(self):
        trades = [_make_trade(holding_bars=b) for b in [2, 4, 6, 8]]
        result = win_rate_and_stats(trades)
        assert result["avg_holding_bars"] == 5.0


# ============================================================
# by_symbol_stats
# ============================================================


class TestBySymbolStats:
    def test_empty(self):
        assert by_symbol_stats([]) == {}

    def test_single_symbol(self):
        trades = [_make_trade(symbol="AAPL", pnl=100) for _ in range(3)]
        result = by_symbol_stats(trades)
        assert result["AAPL"]["count"] == 3
        assert result["AAPL"]["win_rate"] == 1.0
        assert result["AAPL"]["total_pnl"] == 300.0

    def test_multiple_symbols(self):
        trades = [
            _make_trade(symbol="AAPL", pnl=100),
            _make_trade(symbol="AAPL", pnl=-50),
            _make_trade(symbol="TSLA", pnl=200),
        ]
        result = by_symbol_stats(trades)
        assert result["AAPL"]["count"] == 2
        assert result["AAPL"]["win_rate"] == 0.5
        assert result["TSLA"]["count"] == 1
        assert result["TSLA"]["win_rate"] == 1.0


# ============================================================
# by_exit_reason_stats
# ============================================================


class TestByExitReasonStats:
    def test_empty(self):
        assert by_exit_reason_stats([]) == {}

    def test_mixed_reasons(self):
        trades = [
            _make_trade(exit_reason="signal", pnl=100),
            _make_trade(exit_reason="signal", pnl=50),
            _make_trade(exit_reason="liquidation", pnl=-200),
        ]
        result = by_exit_reason_stats(trades)
        assert result["signal"]["count"] == 2
        assert result["signal"]["total_pnl"] == 150.0
        assert result["liquidation"]["count"] == 1
        assert result["liquidation"]["total_pnl"] == -200.0


# ============================================================
# _empty_metrics
# ============================================================


class TestEmptyMetrics:
    def test_returns_17_keys(self):
        result = _empty_metrics(100_000.0)
        expected_keys = {
            "final_value",
            "total_return",
            "annual_return",
            "max_drawdown",
            "sharpe",
            "calmar",
            "sortino",
            "win_rate",
            "profit_loss_ratio",
            "profit_factor",
            "max_consecutive_loss",
            "avg_holding_days",
            "trade_count",
            "benchmark_return",
            "excess_return",
            "information_ratio",
            "turnover",
        }
        assert set(result.keys()) == expected_keys

    def test_final_value_is_initial(self):
        assert _empty_metrics(50_000.0)["final_value"] == 50_000.0

    def test_all_zeros_except_final(self):
        result = _empty_metrics(100_000.0)
        for k, v in result.items():
            if k == "final_value":
                continue
            if isinstance(v, float):
                assert v == 0.0, f"{k} should be 0.0 but got {v}"
            elif isinstance(v, int):
                assert v == 0, f"{k} should be 0 but got {v}"


# ============================================================
# calc_metrics
# ============================================================


class TestCalcMetrics:
    def test_empty_equity(self):
        result = calc_metrics(pd.Series(dtype=float), [], 100_000.0)
        assert result["final_value"] == 100_000.0
        assert result["trade_count"] == 0

    def test_perfect_uptrend(self):
        # 100 bars, each +0.01 (1% daily)
        dates = pd.bdate_range("2024-01-02", periods=100)
        equity = pd.Series(
            [100_000 * (1.01 ** i) for i in range(100)],
            index=dates,
        )
        result = calc_metrics(equity, [], 100_000.0, bars_per_year=252)
        assert result["final_value"] > 100_000
        assert result["total_return"] > 0
        assert result["annual_return"] > 0
        assert result["max_drawdown"] == 0.0  # perfect uptrend
        assert result["sharpe"] > 0

    def test_monotonic_decline(self):
        dates = pd.bdate_range("2024-01-02", periods=100)
        equity = pd.Series(
            [100_000 * (0.99 ** i) for i in range(100)],
            index=dates,
        )
        result = calc_metrics(equity, [], 100_000.0, bars_per_year=252)
        assert result["total_return"] < 0
        assert result["max_drawdown"] < 0
        assert result["sharpe"] < 0

    def test_flat_equity(self):
        dates = pd.bdate_range("2024-01-02", periods=50)
        equity = pd.Series([100_000.0] * 50, index=dates)
        result = calc_metrics(equity, [], 100_000.0, bars_per_year=252)
        assert result["total_return"] == pytest.approx(0.0, abs=1e-6)
        assert result["sharpe"] == pytest.approx(0.0, abs=1e-2)

    def test_with_trades(self):
        dates = pd.bdate_range("2024-01-02", periods=50)
        equity = pd.Series(
            [100_000 * (1.005 ** i) for i in range(50)],
            index=dates,
        )
        trades = [
            _make_trade(pnl=500, holding_bars=5),
            _make_trade(pnl=-200, holding_bars=3),
            _make_trade(pnl=800, holding_bars=10),
        ]
        result = calc_metrics(equity, trades, 100_000.0, bars_per_year=252)
        assert result["trade_count"] == 3
        assert result["win_rate"] == pytest.approx(2 / 3, abs=0.01)
        assert result["profit_factor"] > 1.0
        assert result["max_consecutive_loss"] == 1

    def test_with_benchmark(self):
        dates = pd.bdate_range("2024-01-02", periods=50)
        equity = pd.Series(
            [100_000 * (1.005 ** i) for i in range(50)],
            index=dates,
        )
        bench_ret = pd.Series([0.001] * 50, index=dates)
        result = calc_metrics(equity, [], 100_000.0, bars_per_year=252, bench_ret=bench_ret)
        assert result["benchmark_return"] > 0
        assert "excess_return" in result
        assert "information_ratio" in result

    def test_cross_market_auto_annualization(self):
        # bars_per_year=None triggers calendar-day calculation
        dates = pd.bdate_range("2024-01-02", periods=252)
        equity = pd.Series(
            [100_000 * (1.001 ** i) for i in range(252)],
            index=dates,
        )
        result = calc_metrics(equity, [], 100_000.0, bars_per_year=None)
        assert result["annual_return"] > 0
        assert result["sharpe"] > 0

    def test_with_turnover(self):
        dates = pd.bdate_range("2024-01-02", periods=50)
        equity = pd.Series(
            [100_000 * (1.001 ** i) for i in range(50)],
            index=dates,
        )
        result = calc_metrics(equity, [], 100_000.0, bars_per_year=252, turnover=0.5)
        assert result["turnover"] == 0.5

    def test_all_17_keys_present(self):
        dates = pd.bdate_range("2024-01-02", periods=10)
        equity = pd.Series([100_000] * 10, index=dates)
        result = calc_metrics(equity, [], 100_000.0)
        assert len(result) == 17
        expected_keys = {
            "final_value", "total_return", "annual_return", "max_drawdown",
            "sharpe", "calmar", "sortino", "win_rate", "profit_loss_ratio",
            "profit_factor", "max_consecutive_loss", "avg_holding_days",
            "trade_count", "benchmark_return", "excess_return",
            "information_ratio", "turnover",
        }
        assert set(result.keys()) == expected_keys

    def test_final_value_matches_last_equity(self):
        dates = pd.bdate_range("2024-01-02", periods=20)
        equity = pd.Series(
            [100_000 * (1.002 ** i) for i in range(20)],
            index=dates,
        )
        result = calc_metrics(equity, [], 100_000.0)
        assert result["final_value"] == pytest.approx(equity.iloc[-1], rel=1e-4)
