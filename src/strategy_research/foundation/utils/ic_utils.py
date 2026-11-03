"""IC 计算工具。

复用自 QuantNodes/strategy/momentum_etf_rotation/rd_utils.py。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.stats import pearsonr, spearmanr


def compute_cross_sectional_ic(
    X_panel: np.ndarray,
    Y: pd.DataFrame,
    factor_idx: int,
    min_obs: int = 10,
    start_t: int = 52,
) -> list[float]:
    """单因子的截面 Spearman IC 时间序列.

    Parameters:
        X_panel: (T, N, K) 因子面板
        Y: (T, N) 周频收益 DataFrame
        factor_idx: 因子索引
        min_obs: 最小有效资产数
        start_t: 起始时间步

    Returns:
        list[float]: IC 值列表
    """
    T = len(Y)
    Y_shifted = Y.shift(-1).iloc[:-1].values
    X_shifted = X_panel[:-1]

    ic_list = []
    for t in range(start_t, T - 1):
        x_t = X_shifted[t, :, factor_idx]
        y_t = Y_shifted[t]
        valid = ~np.isnan(x_t) & ~np.isnan(y_t)
        if valid.sum() > min_obs:
            corr, _ = spearmanr(x_t[valid], y_t[valid])
            ic_list.append(corr)
    return ic_list


def compute_ic_summary(ic_list: list[float]) -> dict:
    """IC 统计摘要: mean, std, ICIR, 正IC占比."""
    if not ic_list:
        return dict(ic_mean=0, ic_std=0, icir=0, pct_positive=0, n_obs=0)
    arr = np.array(ic_list)
    ic_mean = float(np.mean(arr))
    ic_std = float(np.std(arr))
    icir = ic_mean / ic_std if ic_std > 0 else 0.0
    pct_pos = float(np.mean(arr > 0))
    return dict(ic_mean=ic_mean, ic_std=ic_std, icir=icir, pct_positive=pct_pos, n_obs=len(ic_list))


def compute_time_series_ic(
    factor_ts: np.ndarray,
    market_ts: np.ndarray,
) -> tuple[float, float]:
    """时序因子 IC: Pearson 相关 + p-value."""
    valid = ~np.isnan(factor_ts) & ~np.isnan(market_ts)
    if valid.sum() < 10:
        return 0.0, 1.0
    corr, pval = pearsonr(factor_ts[valid], market_ts[valid])
    return float(corr), float(pval)
