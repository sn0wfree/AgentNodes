"""截面算子库 — 宽 DataFrame (index=date, columns=instruments) 的通用变换。

**来历**：这批算子原先住在 ``apps/alpha_zoo_ops.py``（Alpha Zoo 因子库的
配套工具）。2026-11-04 做减法批次 1 移除 alpha_zoo 时，算子本身被留下——
它们是**与因子/量化无关的纯数值工具**，任何需要"按日横截面标准化"的场景
都用得上（假设检验、风险暴露、组合权重都依赖它们）。

**契约**（沿用原实现，未改动语义）：
- 输入输出都是宽 DataFrame：行=日期，列=标的
- NaN 传播：不静默 ``fillna(0)``——那会让"数据缺失"看起来像"值为零"
- 禁止 +/- inf：输出里的 inf 一律归为 NaN
- ``delta`` 要求 ``d >= 1``（前视禁止）

完整算子集（含时序算子 ``ts_rank`` / ``decay_linear`` 等）见
``testcase/golden/apps/alpha_zoo/alpha_zoo_ops.py``。
"""

from __future__ import annotations

from typing import cast

import numpy as np
import pandas as pd


def _as_float(df: pd.DataFrame) -> pd.DataFrame:
    """确保 DataFrame 为 float64 类型（整数列 rank/std 会退化成错误结果）。"""
    if df.dtypes.eq(np.float64).all():
        return df
    return df.astype(np.float64)


# ── 截面算子（axis=1，同一时点跨标的） ──────────────────────────────


def rank(df: pd.DataFrame) -> pd.DataFrame:
    """截面百分位排名（axis=1, ties=average, pct=True）。

    返回 [0, 1] 的分位值；NaN 保持 NaN（``na_option="keep"``）。
    """
    return cast(
        pd.DataFrame,
        df.rank(axis=1, method="average", pct=True, na_option="keep"),
    )


def zscore(df: pd.DataFrame) -> pd.DataFrame:
    """截面 Z-score（axis=1，样本标准差 ddof=1）。

    std=0 的时点（全列同值）返回 NaN 而不是 0——「无区分度」与
    「恰好等于均值」是两件事。
    """
    df = _as_float(df)
    mean = df.mean(axis=1, skipna=True)
    std = df.std(axis=1, ddof=1, skipna=True)
    result = df.sub(mean, axis=0).div(std.where(std > 0), axis=0)
    return result.replace([np.inf, -np.inf], np.nan)


def scale(df: pd.DataFrame, a: float = 1.0) -> pd.DataFrame:
    """截面 L1 归一化：使每行绝对值之和等于 ``a``。"""
    df = _as_float(df)
    abs_sum = df.abs().sum(axis=1, skipna=True)
    abs_sum = abs_sum.where(abs_sum > 0)
    return df.mul(a).div(abs_sum, axis=0)


# ── 逐元素算子 ──────────────────────────────────────────────────────


def safe_div(
    a: pd.DataFrame, b: pd.DataFrame, eps: float = 1e-12
) -> pd.DataFrame:
    """逐元素除法，分母接近 0 时返回 NaN（不返回 inf）。

    ``eps`` 是绝对阈值：``|b| < eps`` 视为不可除。这比直接
    ``replace(inf, nan)`` 更早拦住问题——inf 已经算出来了，只是被藏起来。
    """
    a = _as_float(a)
    b = _as_float(b)
    denom = b.where(b.abs() >= eps)
    return a.div(denom).replace([np.inf, -np.inf], np.nan)


__all__ = [
    "rank",
    "safe_div",
    "scale",
    "zscore",
]
