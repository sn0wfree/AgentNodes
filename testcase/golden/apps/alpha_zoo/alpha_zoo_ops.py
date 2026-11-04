"""Alpha Zoo 算子库。

从 vibe-trading/src/factors/base.py 提取的 17 个核心算子。
所有算子操作宽 DataFrame (index=date, columns=instruments)。

NaN 策略: 所有算子传播 NaN，不静默 fillna(0)。
前视禁止: delta(df, d) 要求 d >= 1。
Inf 禁止: 输出不允许 +/- inf。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from typing import cast


def _as_float(df: pd.DataFrame) -> pd.DataFrame:
    """确保 DataFrame 为 float64 类型。"""
    if df.dtypes.eq(np.float64).all():
        return df
    return df.astype(np.float64)


# ============================================================
# 截面算子 (axis=1)
# ============================================================

def rank(df: pd.DataFrame) -> pd.DataFrame:
    """截面百分位排名 (axis=1, ties=average, pct=True)。"""
    return cast(pd.DataFrame, df.rank(axis=1, method="average", pct=True, na_option="keep"))


def zscore(df: pd.DataFrame) -> pd.DataFrame:
    """截面 Z-score (axis=1, sample std)。"""
    df = _as_float(df)
    mean = df.mean(axis=1, skipna=True)
    std = df.std(axis=1, ddof=1, skipna=True)
    result = df.sub(mean, axis=0).div(std.where(std > 0), axis=0)
    return result.replace([np.inf, -np.inf], np.nan)


def scale(df: pd.DataFrame, a: float = 1.0) -> pd.DataFrame:
    """截面 L1 归一化，使绝对值之和等于 a。"""
    df = _as_float(df)
    abs_sum = df.abs().sum(axis=1, skipna=True)
    abs_sum = abs_sum.where(abs_sum > 0)
    return df.mul(a).div(abs_sum, axis=0)


# ============================================================
# 时序算子 (per column, rolling window)
# ============================================================

def ts_rank(df: pd.DataFrame, n: int) -> pd.DataFrame:
    """滚动排名百分位 (最后值在窗口内的排名)。"""
    if n < 1:
        raise ValueError(f"ts_rank window must be >= 1, got {n}")

    def _last_rank(arr: np.ndarray) -> float:
        if np.isnan(arr).all():
            return np.nan
        last = arr[-1]
        if np.isnan(last):
            return np.nan
        valid = arr[~np.isnan(arr)]
        if valid.size == 0:
            return np.nan
        less = (valid < last).sum()
        eq = (valid == last).sum()
        rank_avg = less + 0.5 * (eq + 1)
        return float(rank_avg / valid.size)

    arr = df.to_numpy(dtype=np.float64)
    result = np.full(arr.shape, np.nan)
    for col_idx in range(arr.shape[1]):
        col = arr[:, col_idx]
        for i in range(n - 1, len(col)):
            window = col[i - n + 1: i + 1]
            result[i, col_idx] = _last_rank(window)
    return pd.DataFrame(result, index=df.index, columns=df.columns)


def ts_corr(x: pd.DataFrame, y: pd.DataFrame, n: int) -> pd.DataFrame:
    """滚动 Pearson 相关系数。零方差窗口输出 NaN（而非 inf）。"""
    result = x.rolling(window=n, min_periods=n).corr(y)
    # pandas rolling().corr() 在 float64 精度边界可能产出 inf
    # 替换为 NaN 以保持与 ts_cov/ts_std 一致的 NaN 传播行为
    return cast(pd.DataFrame, result.replace([np.inf, -np.inf], np.nan))


def ts_cov(x: pd.DataFrame, y: pd.DataFrame, n: int) -> pd.DataFrame:
    """滚动样本协方差。零方差窗口输出 NaN（而非 inf）。"""
    result = x.rolling(window=n, min_periods=n).cov(y)
    return cast(pd.DataFrame, result.replace([np.inf, -np.inf], np.nan))


def ts_mean(df: pd.DataFrame, n: int) -> pd.DataFrame:
    """滚动均值。"""
    return cast(pd.DataFrame, df.rolling(window=n, min_periods=n).mean())


def ts_std(df: pd.DataFrame, n: int) -> pd.DataFrame:
    """滚动样本标准差 (ddof=1)。"""
    return cast(pd.DataFrame, df.rolling(window=n, min_periods=n).std())


def ts_max(df: pd.DataFrame, n: int) -> pd.DataFrame:
    """滚动最大值。"""
    return cast(pd.DataFrame, df.rolling(window=n, min_periods=n).max())


def ts_min(df: pd.DataFrame, n: int) -> pd.DataFrame:
    """滚动最小值。"""
    return cast(pd.DataFrame, df.rolling(window=n, min_periods=n).min())


def _argmax_last(arr: np.ndarray) -> float:
    """窗口内最大值的 0-based 位置索引。"""
    if np.isnan(arr).all():
        return np.nan
    return float(np.nanargmax(arr))


def _argmin_last(arr: np.ndarray) -> float:
    """窗口内最小值的 0-based 位置索引。"""
    if np.isnan(arr).all():
        return np.nan
    return float(np.nanargmin(arr))


def ts_argmax(df: pd.DataFrame, n: int) -> pd.DataFrame:
    """滚动最大值位置 (0-based)。"""
    arr = df.to_numpy(dtype=np.float64)
    result = np.full(arr.shape, np.nan)
    for col_idx in range(arr.shape[1]):
        col = arr[:, col_idx]
        for i in range(n - 1, len(col)):
            result[i, col_idx] = _argmax_last(col[i - n + 1: i + 1])
    return pd.DataFrame(result, index=df.index, columns=df.columns)


def ts_argmin(df: pd.DataFrame, n: int) -> pd.DataFrame:
    """滚动最小值位置 (0-based)。"""
    arr = df.to_numpy(dtype=np.float64)
    result = np.full(arr.shape, np.nan)
    for col_idx in range(arr.shape[1]):
        col = arr[:, col_idx]
        for i in range(n - 1, len(col)):
            result[i, col_idx] = _argmin_last(col[i - n + 1: i + 1])
    return pd.DataFrame(result, index=df.index, columns=df.columns)


# ============================================================
# 滞后/差分算子
# ============================================================

def delta(df: pd.DataFrame, d: int) -> pd.DataFrame:
    """滞后差分: df - df.shift(d)。前视禁止: d >= 1。"""
    if d < 1:
        raise ValueError(f"delta requires d >= 1, got {d}")
    return df - df.shift(d)


# ============================================================
# 工具算子
# ============================================================

def decay_linear(df: pd.DataFrame, n: int) -> pd.DataFrame:
    """线性衰减加权移动平均 (权重: n, n-1, ..., 1)。"""
    weights = np.arange(1, n + 1, dtype=float)
    weights = weights / weights.sum()

    def _apply(x: np.ndarray) -> float:
        valid = x[~np.isnan(x)]
        if len(valid) < n:
            return np.nan
        return float(np.dot(valid, weights))

    return cast(pd.DataFrame, df.rolling(window=n, min_periods=n).apply(_apply, raw=True))


def signed_power(df: pd.DataFrame, p: float) -> pd.DataFrame:
    """保留符号的幂运算: sign(df) * |df|^p。"""
    return cast(pd.DataFrame, np.sign(df) * np.abs(df) ** p)


def safe_div(a: pd.DataFrame, b: pd.DataFrame, eps: float = 1e-12) -> pd.DataFrame:
    """安全除法: |b|<eps → NaN, 否则 a/b。"""
    with np.errstate(divide="ignore", invalid="ignore"):
        denom = np.where(np.abs(b.values) < eps, 1.0, b.values)
        result = np.where(np.abs(b.values) < eps, np.nan, a.values / denom)
    return pd.DataFrame(result, index=a.index, columns=a.columns)


def vwap(panel: dict[str, pd.DataFrame], market: str = "equity_cn") -> pd.DataFrame:
    """成交量加权均价。

    A 股: amount / volume
    美股: (high + low + close) / 3 (typical price)
    """
    if market in ("equity_cn", "equity_hk"):
        amount = panel.get("amount")
        volume = panel.get("volume")
        if amount is not None and volume is not None:
            return safe_div(amount, volume)
    # fallback: typical price
    high = panel.get("high")
    low = panel.get("low")
    close = panel.get("close")
    if high is not None and low is not None and close is not None:
        return (high + low + close) / 3.0
    return cast(pd.DataFrame, close)


# ============================================================
# 算子注册表
# ============================================================

ALPHA_ZOO_OPS = {
    "rank": rank,
    "zscore": zscore,
    "scale": scale,
    "ts_rank": ts_rank,
    "ts_corr": ts_corr,
    "ts_cov": ts_cov,
    "ts_mean": ts_mean,
    "ts_std": ts_std,
    "ts_max": ts_max,
    "ts_min": ts_min,
    "ts_argmax": ts_argmax,
    "ts_argmin": ts_argmin,
    "delta": delta,
    "decay_linear": decay_linear,
    "signed_power": signed_power,
    "safe_div": safe_div,
    "vwap": vwap,
}
