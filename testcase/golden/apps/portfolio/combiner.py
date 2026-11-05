"""组合权重计算 — equal_weight / risk_parity / sharpe_weight。"""

from __future__ import annotations

from typing import Dict, List

import numpy as np
import pandas as pd


def equal_weight(names: List[str]) -> Dict[str, float]:
    """等权重分配。"""
    n = len(names)
    if n == 0:
        return {}
    w = 1.0 / n
    return {name: w for name in names}


def risk_parity(curves: Dict[str, pd.Series]) -> Dict[str, float]:
    """风险平价：权重反比于波动率。

    Parameters
    ----------
    curves : dict[str, pd.Series]
        {strategy_name: equity_curve}，每个 Series 为日收益率或权益曲线。
    """
    if not curves:
        return {}

    vols = {}
    for name, curve in curves.items():
        rets = curve.pct_change().dropna() if len(curve) > 1 else pd.Series([0.0])
        vols[name] = float(rets.std()) if len(rets) > 0 else 1e-10

    inv_vols = {name: 1.0 / max(v, 1e-10) for name, v in vols.items()}
    total = sum(inv_vols.values())
    return {name: iv / total for name, iv in inv_vols.items()}


def sharpe_weight(curves: Dict[str, pd.Series], risk_free: float = 0.0) -> Dict[str, float]:
    """Sharpe 加权：权重正比于 Sharpe ratio。

    Parameters
    ----------
    curves : dict[str, pd.Series]
        {strategy_name: equity_curve}
    risk_free : float
        无风险利率（年化）
    """
    if not curves:
        return {}

    sharpes = {}
    for name, curve in curves.items():
        rets = curve.pct_change().dropna() if len(curve) > 1 else pd.Series([0.0])
        if len(rets) < 2:
            sharpes[name] = 0.0
            continue
        mean_ret = float(rets.mean())
        vol = float(rets.std())
        annual_factor = np.sqrt(252)
        sharpe = (mean_ret * 252 - risk_free) / (vol * annual_factor + 1e-10)
        sharpes[name] = sharpe

    # 将负 Sharpe clip 到 0
    sharpes_clipped = {name: max(s, 0.0) for name, s in sharpes.items()}
    total = sum(sharpes_clipped.values())
    if total <= 0:
        return equal_weight(list(curves.keys()))
    return {name: s / total for name, s in sharpes_clipped.items()}


def combine_equity_curves(
    curves: Dict[str, pd.Series],
    weights: Dict[str, float],
) -> pd.Series:
    """加权组合权益曲线，自动对齐到共同 index。

    Parameters
    ----------
    curves : dict[str, pd.Series]
        {strategy_name: equity_curve}，每个 Series index 为 DatetimeIndex。
    weights : dict[str, float]
        {strategy_name: weight}，权重之和应为 1.0。
    """
    if not curves:
        return pd.Series(dtype=float)

    # 将所有 equity curves 转为日收益率
    returns_dict = {}
    for name, curve in curves.items():
        if name in weights and len(curve) > 1:
            returns_dict[name] = curve.pct_change().fillna(0.0)

    if not returns_dict:
        return pd.Series(dtype=float)

    # 对齐到共同 index
    combined_index = None
    for rets in returns_dict.values():
        if combined_index is None:
            combined_index = rets.index
        else:
            combined_index = combined_index.intersection(rets.index)

    if combined_index is None or len(combined_index) == 0:
        return pd.Series(dtype=float)

    # 加权求和
    weighted_returns = pd.Series(0.0, index=combined_index)
    for name, rets in returns_dict.items():
        w = weights.get(name, 0.0)
        aligned = rets.reindex(combined_index).fillna(0.0)
        weighted_returns += w * aligned

    # 从收益率重建权益曲线
    initial = sum(
        weights.get(name, 0.0) * curves[name].iloc[0]
        for name in curves
        if name in weights and len(curves[name]) > 0
    )
    equity = initial * (1 + weighted_returns).cumprod()
    equity.name = "portfolio"
    return equity


__all__ = [
    "equal_weight",
    "risk_parity",
    "sharpe_weight",
    "combine_equity_curves",
]
