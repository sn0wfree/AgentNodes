"""组合级指标 — Sharpe / VaR / CVaR / 换手 / 集中度。"""

from __future__ import annotations

from typing import Dict

import numpy as np
import pandas as pd

from .models import PortfolioMetrics


def portfolio_metrics(
    portfolio_curve: pd.Series,
    components: Dict[str, pd.Series],
    weights: Dict[str, float],
    bars_per_year: int = 252,
) -> PortfolioMetrics:
    """计算组合级完整指标。

    Parameters
    ----------
    portfolio_curve : pd.Series
        组合权益曲线。
    components : dict[str, pd.Series]
        {strategy_name: equity_curve}。
    weights : dict[str, float]
        {strategy_name: weight}。
    bars_per_year : int
        年度 bar 数量。
    """
    if len(portfolio_curve) < 2:
        return PortfolioMetrics(n_strategies=len(components))

    rets = portfolio_curve.pct_change().dropna()
    n = len(rets)

    # Annual return
    total_ret = float(portfolio_curve.iloc[-1] / portfolio_curve.iloc[0] - 1)
    years = n / bars_per_year
    ann_ret = float((1 + total_ret) ** (1 / max(years, 1e-9)) - 1) if years > 0 else 0.0

    # Sharpe
    vol = float(rets.std())
    sharpe = float(rets.mean() / (vol + 1e-10) * np.sqrt(bars_per_year))

    # Max drawdown
    peak = portfolio_curve.cummax()
    dd = (portfolio_curve - peak) / peak.replace(0, 1)
    max_dd = float(dd.min())

    # VaR / CVaR (historical, 5%)
    var_95 = float(rets.quantile(0.05))
    cvar_95 = float(rets[rets <= var_95].mean()) if (rets <= var_95).any() else var_95

    # Turnover (sum of weight changes, simplified)
    turnover = 0.0  # Placeholder — actual turnover needs rebalance history

    # Diversification ratio = weighted avg vol / portfolio vol
    component_vols = []
    for name, curve in components.items():
        if name in weights and len(curve) > 1:
            c_rets = curve.pct_change().dropna()
            component_vols.append(weights[name] * float(c_rets.std()))
    weighted_avg_vol = sum(component_vols) if component_vols else vol
    div_ratio = weighted_avg_vol / (vol + 1e-10)

    # Component correlation (avg pairwise)
    from .correlation import avg_correlation
    comp_corr = avg_correlation(components) if len(components) > 1 else 0.0

    return PortfolioMetrics(
        sharpe=sharpe,
        annual_return=ann_ret,
        max_drawdown=max_dd,
        total_return=total_ret,
        var_95=var_95,
        cvar_95=cvar_95,
        turnover=turnover,
        diversification_ratio=div_ratio,
        component_correlation=comp_corr,
        n_strategies=len(components),
    )


__all__ = ["portfolio_metrics"]
