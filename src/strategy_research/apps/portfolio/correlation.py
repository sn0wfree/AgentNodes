"""策略间相关性矩阵。"""

from __future__ import annotations

from typing import Dict, List

import pandas as pd

from .models import CorrelationPair


def correlation_matrix(curves: Dict[str, pd.Series]) -> pd.DataFrame:
    """计算策略间收益率相关性矩阵。

    Parameters
    ----------
    curves : dict[str, pd.Series]
        {strategy_name: equity_curve}，每个 Series 为日权益。

    Returns
    -------
    pd.DataFrame
        相关性矩阵，index 和 columns 为策略名。
    """
    if not curves:
        return pd.DataFrame()

    # 转为日收益率
    returns = {}
    for name, curve in curves.items():
        if len(curve) > 1:
            returns[name] = curve.pct_change().dropna()

    if not returns:
        return pd.DataFrame()

    # 对齐到共同 index
    df = pd.DataFrame(returns)
    df = df.dropna(how="all")

    if df.empty or len(df) < 2:
        return pd.DataFrame(0.0, index=list(returns.keys()), columns=list(returns.keys()))

    return df.corr()


def correlation_pairs(
    curves: Dict[str, pd.Series],
) -> List[CorrelationPair]:
    """返回所有不重复的策略对相关性。"""
    corr = correlation_matrix(curves)
    if corr.empty:
        return []

    pairs = []
    names = list(corr.index)
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            pairs.append(CorrelationPair(
                strategy_a=names[i],
                strategy_b=names[j],
                correlation=float(corr.iloc[i, j]),
                period=int(len(curves.get(names[i], pd.Series()))),
            ))
    return pairs


def avg_correlation(curves: Dict[str, pd.Series]) -> float:
    """所有策略对的平均相关性。"""
    pairs = correlation_pairs(curves)
    if not pairs:
        return 0.0
    return sum(p.correlation for p in pairs) / len(pairs)


__all__ = [
    "correlation_matrix",
    "correlation_pairs",
    "avg_correlation",
]
