"""17 个业绩指标。

复用自 QuantNodes/strategy/momentum_etf_rotation/common/extended_metrics.py。
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def _ann_return(nav: pd.Series, freq: int = 252) -> float:
    if nav.empty or len(nav) < 2:
        return 0.0
    rets = nav.pct_change(fill_method=None).dropna()
    if rets.empty:
        return 0.0
    n_years = len(rets) / freq
    total_ret = nav.iloc[-1] / nav.iloc[0] - 1
    return float((1 + total_ret) ** (1 / max(n_years, 1e-9)) - 1)


def _ann_vol(nav: pd.Series, freq: int = 252) -> float:
    if nav.empty or len(nav) < 2:
        return 0.0
    rets = nav.pct_change(fill_method=None).dropna()
    return float(rets.std() * np.sqrt(freq)) if not rets.empty else 0.0


def _sharpe(nav: pd.Series, freq: int = 252, risk_free_rate: float = 0.0) -> float:
    """年化 Sharpe = (ann_return − rf) / ann_vol。

    ``risk_free_rate`` 为年化无风险利率(0.05 = 5%)。默认 0.0 保持
    向后兼容,但显式传入才避免把无风险收益错记为策略超额收益。
    """
    vol = _ann_vol(nav, freq)
    if vol == 0:
        return 0.0
    return (_ann_return(nav, freq) - risk_free_rate) / vol


def _sortino(nav: pd.Series, freq: int = 252, risk_free_rate: float = 0.0) -> float:
    """年化 Sortino = (ann_return − rf) / 下行波动。"""
    if nav.empty:
        return 0.0
    rets = nav.pct_change(fill_method=None).dropna()
    if rets.empty:
        return 0.0
    downside = rets[rets < 0]
    dd = float(downside.std() * np.sqrt(freq)) if not downside.empty else 0.0
    if dd == 0:
        return 0.0
    return (_ann_return(nav, freq) - risk_free_rate) / dd


def _max_drawdown(nav: pd.Series) -> tuple[float, int]:
    if nav.empty or len(nav) < 2:
        return 0.0, 0
    cummax = nav.cummax()
    dd = (nav / cummax - 1)
    is_dd = dd < 0
    max_dd = float(dd.min())

    # 最大回撤天数: 最长连续回撤段
    max_run = 0
    cur_run = 0
    for v in is_dd.values:
        if v:
            cur_run += 1
            max_run = max(max_run, cur_run)
        else:
            cur_run = 0
    return max_dd, max_run


def _calmar(nav: pd.Series, freq: int = 252) -> float:
    md, _ = _max_drawdown(nav)
    if md >= 0:
        return 0.0
    return _ann_return(nav, freq) / abs(md)


def _info_ratio(nav: pd.Series, bench: pd.Series | None = None, freq: int = 252) -> float:
    """Info ratio vs benchmark."""
    if nav.empty or bench is None or bench.empty:
        return 0.0
    rets_n = nav.pct_change(fill_method=None).dropna()
    rets_b = bench.pct_change(fill_method=None).dropna()
    common = rets_n.index.intersection(rets_b.index)
    if len(common) < 2:
        return 0.0
    excess = rets_n.loc[common] - rets_b.loc[common]
    if excess.std() == 0:
        return 0.0
    return float(excess.mean() / excess.std() * np.sqrt(freq))


def _downside_dev(nav: pd.Series, freq: int = 252) -> float:
    if nav.empty:
        return 0.0
    rets = nav.pct_change(fill_method=None).dropna()
    if rets.empty:
        return 0.0
    downside = rets[rets < 0]
    return float(downside.std() * np.sqrt(freq)) if not downside.empty else 0.0


def _var_cvar(nav: pd.Series, alpha: float = 0.05) -> tuple[float, float]:
    """历史法 VaR / CVaR."""
    if nav.empty or len(nav) < 2:
        return 0.0, 0.0
    rets = nav.pct_change(fill_method=None).dropna()
    if rets.empty:
        return 0.0, 0.0
    var = float(rets.quantile(alpha))
    cvar = float(rets[rets <= var].mean()) if (rets <= var).any() else var
    return var, cvar


def _win_rate(nav: pd.Series) -> float:
    if nav.empty:
        return 0.0
    rets = nav.pct_change(fill_method=None).dropna()
    if rets.empty:
        return 0.0
    return float((rets > 0).mean())


def _profit_loss_ratio(nav: pd.Series) -> float:
    if nav.empty:
        return 0.0
    rets = nav.pct_change(fill_method=None).dropna()
    if rets.empty:
        return 0.0
    wins = rets[rets > 0]
    losses = rets[rets < 0]
    if len(wins) == 0 or len(losses) == 0:
        return 0.0
    return float(wins.mean() / abs(losses.mean()))


def _resample_monthly(nav: pd.Series) -> pd.Series:
    """按月取最后值。pandas ≥2.2 用 'ME',旧版用 'M'(别名已被移除)。"""
    try:
        return nav.resample("ME").last()
    except ValueError:
        return nav.resample("M").last()


def _max_monthly_loss(nav: pd.Series) -> float:
    """最大月度损失 (worst negative monthly return)."""
    if nav.empty:
        return 0.0
    monthly = _resample_monthly(nav).pct_change(fill_method=None).dropna()
    if monthly.empty:
        return 0.0
    losses = monthly[monthly < 0]
    if losses.empty:
        return 0.0  # 无亏损月
    return float(losses.min())


def _profit_months_ratio(nav: pd.Series) -> float:
    if nav.empty:
        return 0.0
    monthly = _resample_monthly(nav).pct_change(fill_method=None).dropna()
    if monthly.empty:
        return 0.0
    return float((monthly > 0).mean())


def _avg_dd(nav: pd.Series) -> float:
    """平均回撤深度."""
    if nav.empty or len(nav) < 2:
        return 0.0
    cummax = nav.cummax()
    dd = (nav / cummax - 1)
    is_dd = dd < 0
    if not is_dd.any():
        return 0.0

    # 提取每段回撤的平均深度
    cur_dds = []
    depths = []
    for v, flag in zip(dd.values, is_dd.values):
        if flag:
            cur_dds.append(v)
        else:
            if cur_dds:
                depths.append(np.mean(cur_dds))
                cur_dds = []
    if cur_dds:
        depths.append(np.mean(cur_dds))
    return float(np.mean(depths)) if depths else 0.0


def extended_metrics(
    nav: pd.Series,
    benchmark_nav: pd.Series | None = None,
    rebalance_dates: list | None = None,
    freq: int = 252,
    risk_free_rate: float = 0.0,
) -> dict:
    """17 个业绩指标。

    ``risk_free_rate``: 年化无风险利率,进入 sharpe/sortino 的超额收益
    分子(默认 0.0 = 与历史行为一致)。
    """
    if nav.empty or len(nav) < 2:
        return {}

    md, max_dd_days = _max_drawdown(nav)
    var_95, cvar_95 = _var_cvar(nav, alpha=0.05)
    avg_dd = _avg_dd(nav)

    calmar_avg_dd = (
        _calmar(nav, freq) / abs(avg_dd) if avg_dd < 0 else 0.0
    )

    return {
        "ann_return": _ann_return(nav, freq),
        "ann_vol": _ann_vol(nav, freq),
        "sharpe": _sharpe(nav, freq, risk_free_rate),
        "max_drawdown": md,
        "calmar": _calmar(nav, freq),
        "sortino": _sortino(nav, freq, risk_free_rate),
        "downside_dev": _downside_dev(nav, freq),
        "info_ratio": _info_ratio(nav, benchmark_nav, freq),
        "win_rate": _win_rate(nav),
        "profit_loss_ratio": _profit_loss_ratio(nav),
        "max_dd_duration": max_dd_days,
        "calmar_avg_dd": calmar_avg_dd,
        "var_95": var_95,
        "cvar_95": cvar_95,
        "ann_turnover": 0.0,  # 由引擎根据 weights_history 覆盖填充
        "max_monthly_loss": _max_monthly_loss(nav),
        "profit_months_ratio": _profit_months_ratio(nav),
        "risk_free_rate": float(risk_free_rate),
    }


__all__ = ["extended_metrics"]
