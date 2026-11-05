"""Backtest 公共 metrics — 借鉴自 vibe-trading

含 17 keys calc_metrics + bars_per_year 表 + by_symbol/by_exit_reason stats。
所有函数接受 TradeRecord 列表作为 trades 参数。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from .backtest_models import TradeRecord

# ─── Bars/year 表（7 源 × 7 周期）───

_TRADING_DAYS = {
    "tushare": 252,
    "yfinance": 252,
    "okx": 365,
    "akshare": 252,
    "ccxt": 365,
    "mootdx": 252,
    "futu": 252,
}

_BARS_PER_DAY = {
    "1m": {
        "tushare": 240,
        "okx": 1440,
        "yfinance": 390,
        "akshare": 240,
        "ccxt": 1440,
        "mootdx": 240,
        "futu": 240,
    },
    "5m": {
        "tushare": 48,
        "okx": 288,
        "yfinance": 78,
        "akshare": 48,
        "ccxt": 288,
        "mootdx": 48,
        "futu": 48,
    },
    "15m": {
        "tushare": 16,
        "okx": 96,
        "yfinance": 26,
        "akshare": 16,
        "ccxt": 96,
        "mootdx": 16,
        "futu": 16,
    },
    "30m": {
        "tushare": 8,
        "okx": 48,
        "yfinance": 13,
        "akshare": 8,
        "ccxt": 48,
        "mootdx": 8,
        "futu": 8,
    },
    "1H": {
        "tushare": 4,
        "okx": 24,
        "yfinance": 6,
        "akshare": 4,
        "ccxt": 24,
        "mootdx": 4,
        "futu": 4,
    },
    "4H": {
        "tushare": 1,
        "okx": 6,
        "yfinance": 1,
        "akshare": 1,
        "ccxt": 6,
        "mootdx": 1,
        "futu": 1,
    },
    "1D": {
        "tushare": 1,
        "okx": 1,
        "yfinance": 1,
        "akshare": 1,
        "ccxt": 1,
        "mootdx": 1,
        "futu": 1,
    },
}


def calc_bars_per_year(interval: str = "1D", source: str = "tushare") -> int:
    """Bars per year for the given interval and data source.

    Examples: 252 (daily A-share), 8760 (1h crypto), 525600 (1m crypto).
    """
    trading_days = _TRADING_DAYS.get(source, 252)
    bars_per_day = _BARS_PER_DAY.get(interval, {}).get(source, 1)
    return trading_days * bars_per_day


# ─── Trade-level stats ───


def win_rate_and_stats(trades: List[TradeRecord]) -> Dict[str, float]:
    """Win rate / profit_loss_ratio / profit_factor / max_consec / avg_holding."""
    if not trades:
        return {
            "win_rate": 0.0,
            "profit_loss_ratio": 0.0,
            "profit_factor": 0.0,
            "max_consecutive_loss": 0,
            "avg_holding_bars": 0.0,
        }

    wins = [t.pnl for t in trades if t.pnl > 0]
    losses = [t.pnl for t in trades if t.pnl < 0]

    win_rate = len(wins) / len(trades)

    avg_win = float(np.mean(wins)) if wins else 0.0
    avg_loss = abs(float(np.mean(losses))) if losses else 1e-10
    profit_loss_ratio = avg_win / avg_loss if avg_loss > 1e-10 else 0.0

    gross_profit = sum(wins) if wins else 0.0
    gross_loss = abs(sum(losses)) if losses else 1e-10
    profit_factor = gross_profit / gross_loss if gross_loss > 1e-10 else 0.0

    max_consec = 0
    cur_consec = 0
    for t in trades:
        if t.pnl < 0:
            cur_consec += 1
            max_consec = max(max_consec, cur_consec)
        else:
            cur_consec = 0

    hold_bars = [t.holding_bars for t in trades if t.holding_bars > 0]
    avg_holding = float(np.mean(hold_bars)) if hold_bars else 0.0

    return {
        "win_rate": round(win_rate, 4),
        "profit_loss_ratio": round(profit_loss_ratio, 4),
        "profit_factor": round(profit_factor, 4),
        "max_consecutive_loss": max_consec,
        "avg_holding_bars": round(avg_holding, 1),
    }


def by_symbol_stats(trades: List[TradeRecord]) -> Dict[str, Dict[str, Any]]:
    """{symbol: {count, win_rate, total_pnl, avg_pnl}}."""
    result: Dict[str, Dict[str, Any]] = {}
    for t in trades:
        if t.symbol not in result:
            result[t.symbol] = {"count": 0, "wins": 0, "total_pnl": 0.0}
        result[t.symbol]["count"] += 1
        result[t.symbol]["total_pnl"] += t.pnl
        if t.pnl > 0:
            result[t.symbol]["wins"] += 1

    stats: Dict[str, Dict[str, Any]] = {}
    for sym, d in result.items():
        count = d["count"]
        stats[sym] = {
            "count": count,
            "win_rate": round(d["wins"] / count, 4) if count > 0 else 0.0,
            "total_pnl": round(d["total_pnl"], 4),
            "avg_pnl": round(d["total_pnl"] / count, 4) if count > 0 else 0.0,
        }
    return stats


def by_exit_reason_stats(trades: List[TradeRecord]) -> Dict[str, Dict[str, Any]]:
    """{reason: {count, total_pnl}}."""
    result: Dict[str, Dict[str, Any]] = {}
    for t in trades:
        reason = t.exit_reason
        if reason not in result:
            result[reason] = {"count": 0, "total_pnl": 0.0}
        result[reason]["count"] += 1
        result[reason]["total_pnl"] += t.pnl

    return {k: {"count": v["count"], "total_pnl": round(v["total_pnl"], 4)} for k, v in result.items()}


# ─── 17-key calc_metrics ───


def _empty_metrics(initial_cash: float) -> Dict[str, Any]:
    """空 equity 返回零值 dict。"""
    return {
        "final_value": initial_cash,
        "total_return": 0.0,
        "annual_return": 0.0,
        "max_drawdown": 0.0,
        "sharpe": 0.0,
        "calmar": 0.0,
        "sortino": 0.0,
        "win_rate": 0.0,
        "profit_loss_ratio": 0.0,
        "profit_factor": 0.0,
        "max_consecutive_loss": 0,
        "avg_holding_days": 0.0,
        "trade_count": 0,
        "benchmark_return": 0.0,
        "excess_return": 0.0,
        "information_ratio": 0.0,
        "turnover": 0.0,
    }


def calc_metrics(
    equity_curve: pd.Series,
    trades: List[TradeRecord],
    initial_cash: float,
    bars_per_year: Optional[int] = 252,
    bench_ret: Optional[pd.Series] = None,
    turnover: Optional[float] = 0.0,
) -> Dict[str, Any]:
    """17-key 完整 metrics，cross-market 用 bars_per_year=None 自动按日历日年化。

    Parameters
    ----------
    equity_curve : pd.Series
        权益曲线（每个 bar 的总资产），index 为 DatetimeIndex
    trades : list[TradeRecord]
        已完成的 round-trip 交易列表
    initial_cash : float
        初始资金
    bars_per_year : int, optional
        年度 bar 数量；None 时自动计算
    bench_ret : pd.Series, optional
        基准每日收益率（用于 excess_return 和 information_ratio）
    turnover : float, optional
        换手率（total turnover / 2）
    """
    if len(equity_curve) == 0:
        return _empty_metrics(initial_cash)

    n = len(equity_curve)

    # Calendar-day annualization for cross-market
    if bars_per_year is None:
        first = pd.Timestamp(equity_curve.index[0])
        last = pd.Timestamp(equity_curve.index[-1])
        calendar_days = (last - first).days
        years = calendar_days / 365.25 if calendar_days > 0 else 1.0
        bpy = int(n / years) if years > 0 else 252
    else:
        bpy = bars_per_year

    port_ret = equity_curve.pct_change().fillna(0.0)

    final_value = float(equity_curve.iloc[-1])
    total_ret = float(final_value / initial_cash - 1) if initial_cash else 0.0
    ann_ret = float((1 + total_ret) ** (bpy / max(n, 1)) - 1)
    vol = float(port_ret.std())
    sharpe = float(port_ret.mean() / (vol + 1e-10) * np.sqrt(bpy))

    peak = equity_curve.cummax()
    dd = (equity_curve - peak) / peak.replace(0, 1)
    max_dd = float(dd.min())

    calmar = ann_ret / abs(max_dd) if abs(max_dd) > 1e-10 else 0.0

    downside = port_ret[port_ret < 0]
    downside_std = float(downside.std()) if len(downside) > 1 else 1e-10
    sortino = float(port_ret.mean() / (downside_std + 1e-10) * np.sqrt(bpy))

    trade_stats = win_rate_and_stats(trades)

    # Benchmark comparison
    bench_return = 0.0
    excess = 0.0
    ir = 0.0
    if bench_ret is not None and len(bench_ret) > 0:
        bench_return = float((1 + bench_ret).prod() - 1)
        excess = total_ret - bench_return
        active_ret = port_ret - bench_ret.reindex(port_ret.index).fillna(0.0)
        active_std = float(active_ret.std())
        ir = float(active_ret.mean() / (active_std + 1e-10) * np.sqrt(bpy))

    return {
        "final_value": round(final_value, 2),
        "total_return": round(total_ret, 6),
        "annual_return": round(ann_ret, 6),
        "max_drawdown": round(max_dd, 6),
        "sharpe": round(sharpe, 4),
        "calmar": round(calmar, 4),
        "sortino": round(sortino, 4),
        "win_rate": trade_stats["win_rate"],
        "profit_loss_ratio": trade_stats["profit_loss_ratio"],
        "profit_factor": trade_stats["profit_factor"],
        "max_consecutive_loss": trade_stats["max_consecutive_loss"],
        "avg_holding_days": trade_stats["avg_holding_bars"],
        "trade_count": len(trades),
        "benchmark_return": round(bench_return, 6),
        "excess_return": round(excess, 6),
        "information_ratio": round(ir, 4),
        "turnover": round(turnover, 4) if turnover is not None else 0.0,
    }


__all__ = [
    "calc_bars_per_year",
    "win_rate_and_stats",
    "by_symbol_stats",
    "by_exit_reason_stats",
    "calc_metrics",
    "_empty_metrics",
]
