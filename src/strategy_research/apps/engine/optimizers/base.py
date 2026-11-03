"""Portfolio optimizers — 基于 QuantOPT 的 5 个优化器。

统一接口：
  optimize_weights(ret_df, pos_df, dates, method) → pd.DataFrame

5 个优化器：
  1. equal_volatility  — inverse-volatility (自写 numpy)
  2. risk_parity       — QuantOPT RiskParity
  3. mean_variance     — QuantOPT MVO (Markowitz)
  4. max_diversification — QuantOPT MaxIR (adapted)
  5. turnover_aware    — QuantOPT MaxRiskAdjReturn

增强特性（v0.3）：
  - 滚动窗口优化（lookback 参数）
  - Sign preservation（保留信号方向，支持多空）
  - Turnover 跟踪（return_turnover 参数）
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

try:
    from QuantOPT.models.model_MaxIR import MaxIR
    from QuantOPT.models.model_MaxRiskAdjReturn import MaxRiskAdjReturn
    from QuantOPT.models.model_MVO import MVO
    from QuantOPT.models.model_RiskParity import RiskParity
    _HAS_QUANTOPT = True
except ImportError:
    _HAS_QUANTOPT = False


# ============================================================
# 1. Equal Volatility (inverse-vol, numpy 直接计算)
# ============================================================

def _equal_volatility_optimize(codes, cov):
    """inverse-volatility 权重。"""
    vols = np.sqrt(np.diag(cov))
    vols = np.where(vols < 1e-10, 1e-10, vols)
    inv_vols = 1.0 / vols
    weights = inv_vols / inv_vols.sum()
    return weights


# ============================================================
# 2. Risk Parity (QuantOPT)
# ============================================================

def _risk_parity_optimize(codes, cov):  # pragma: no cover - needs QuantOPT
    """QuantOPT RiskParity。本机未安装 QuantOPT，调用方已由 ImportError 守卫。"""
    port_std = np.sqrt(np.diag(cov))
    res = RiskParity.run_opt(
        stockpool=codes, port_std=port_std, cov=cov,
        bounds=[(0.0, 1.0)] * len(codes), constraints=[],
    )
    return np.array(res.x)


# ============================================================
# 3. Mean Variance (QuantOPT MVO)
# ============================================================

def _mean_variance_optimize(codes, cov, mu, risk_aversion=1.0):  # pragma: no cover
    """QuantOPT MVO。同 ``_risk_parity_optimize``：依赖未安装时不可达。"""
    res = MVO.run_opt(
        stockpool=codes, sigma2=cov, stock_ret=mu * risk_aversion,
        bounds=[(0.0, 1.0)] * len(codes), constraints=[],
    )
    return np.array(res.x)


# ============================================================
# 4. Max Diversification (QuantOPT MaxIR adapted)
# ============================================================

def _max_diversification_optimize(codes, cov, mu):  # pragma: no cover
    """QuantOPT MaxIR。同前：依赖未安装时不可达。"""
    res = MaxIR.run_opt(
        stockpool=codes, sigma2=cov, portfolio_returns=mu,
        lambda_r=1.0,
        bounds=[(0.0, 1.0)] * len(codes), constraints=[],
    )
    return np.array(res.x)


# ============================================================
# 5. Turnover Aware (QuantOPT MaxRiskAdjReturn)
# ============================================================

def _turnover_aware_optimize(codes, cov, mu, current_weights, lambda_r=1.0):  # pragma: no cover
    """QuantOPT MaxRiskAdjReturn。同前：依赖未安装时不可达。"""
    res = MaxRiskAdjReturn.run_opt(
        stockpool=codes, sigma2=cov, portfolio_returns=mu,
        lambda_r=lambda_r,
        bounds=[(0.0, 1.0)] * len(codes), constraints=[],
    )
    return np.array(res.x)


# ============================================================
# 单次优化（内部）
# ============================================================

def _single_optimize(
    codes: list[str],
    ret_window: pd.DataFrame,
    pos_row: np.ndarray | None,
    method: str,
    **kwargs,
) -> np.ndarray | None:
    """对单个时间切片执行优化，返回权重数组。失败返回 None。"""
    n = len(codes)
    cov = ret_window.cov().values
    if np.any(np.isnan(cov)):
        cov = np.nan_to_num(cov, nan=0.0)

    # 检查协方差矩阵是否退化（全零或条件数过大）
    if np.allclose(cov, 0):
        return None

    mu = ret_window.mean().values

    try:
        if method == "equal_volatility":
            return _equal_volatility_optimize(codes, cov)
        elif method == "risk_parity":
            if not _HAS_QUANTOPT:
                raise ImportError("QuantOPT required for risk_parity")
            return _risk_parity_optimize(codes, cov)
        elif method == "mean_variance":
            if not _HAS_QUANTOPT:
                raise ImportError("QuantOPT required for mean_variance")
            return _mean_variance_optimize(codes, cov, mu, kwargs.get("risk_aversion", 1.0))
        elif method == "max_diversification":
            if not _HAS_QUANTOPT:
                raise ImportError("QuantOPT required for max_diversification")
            return _max_diversification_optimize(codes, cov, mu)
        elif method == "turnover_aware":
            if not _HAS_QUANTOPT:
                raise ImportError("QuantOPT required for turnover_aware")
            current_w = pos_row if pos_row is not None else np.ones(n) / n
            return _turnover_aware_optimize(codes, cov, mu, current_w, kwargs.get("lambda_r", 1.0))
    except Exception as e:
        logger.debug("Optimizer '%s' failed at single step: %s", method, e)
        return None

    return None


# ============================================================
# Sign preservation
# ============================================================

def _apply_sign_preservation(weights: np.ndarray, pos_row: np.ndarray) -> np.ndarray:
    """将优化后的权重与原始信号方向对齐。

    做多信号（pos > 0）保持正权重，做空信号（pos < 0）取负权重。
    无信号（pos == 0）的资产保持优化权重不变。
    """
    signs = np.sign(pos_row)
    has_signal = signs != 0
    if not np.any(has_signal):
        # 无信号时不做方向调整，保持原始权重
        return weights
    # 有信号的资产：sign * |weight|；无信号的资产：保持原权重
    result = weights.copy()
    result[has_signal] = signs[has_signal] * np.abs(weights[has_signal])
    return result


# ============================================================
# 统一接口
# ============================================================

_METHOD_MAP = {
    "equal_volatility": "equal_volatility",
    "risk_parity": "risk_parity",
    "mean_variance": "mean_variance",
    "max_diversification": "max_diversification",
    "turnover_aware": "turnover_aware",
}


def optimize_weights(
    ret_df: pd.DataFrame,
    pos_df: pd.DataFrame,
    dates: pd.DatetimeIndex,
    method: str = "equal_volatility",
    *,
    lookback: int = 0,
    sign_preservation: bool = True,
    return_turnover: bool = False,
    **kwargs,
) -> pd.DataFrame | tuple[pd.DataFrame, dict]:
    """统一优化接口。

    Args:
        ret_df: 收益率矩阵 (T, N)
        pos_df: 当前权重矩阵 (同 shape)，负值表示做空信号
        dates: 统一日期索引
        method: 优化方法名
        lookback: 滚动窗口大小。0 = 静态模式（单次优化，向后兼容）。
                  >0 = 逐日滚动优化，使用前 lookback 天的收益率估计参数。
        sign_preservation: 是否保留信号方向（多空）。默认 True。
        return_turnover: 是否额外返回换手率统计 dict。默认 False。
        **kwargs: 额外参数 (risk_aversion, lambda_r 等)

    Returns:
        优化后的权重矩阵 (T, N)。
        若 return_turnover=True，返回 (DataFrame, dict) 元组。
    """
    codes = list(ret_df.columns)
    n = len(codes)
    if n == 0:
        if return_turnover:
            return pos_df.copy(), {"turnover": [], "avg_turnover": 0.0}
        return pos_df.copy()

    if method not in _METHOD_MAP:
        raise ValueError(f"Unknown method: {method}. Choose from {list(_METHOD_MAP.keys())}")

    # ── 静态模式 (lookback=0): 向后兼容 ──
    if lookback <= 0:
        weights = _single_optimize(codes, ret_df, None, method, **kwargs)
        if weights is None:
            logger.warning("Optimizer '%s' failed, falling back to equal weights", method)
            weights = np.ones(n) / n

        # Sign preservation
        if sign_preservation and len(pos_df) > 0:
            last_pos = pos_df.iloc[-1].values
            weights = _apply_sign_preservation(weights, last_pos)

        result = pd.DataFrame(
            np.tile(weights, (len(dates), 1)),
            index=dates,
            columns=codes,
        )

        if return_turnover:
            return result, {"turnover": [], "avg_turnover": 0.0}
        return result

    # ── 滚动窗口模式 (lookback > 0) ──
    weight_rows: list[np.ndarray] = []
    turnover_list: list[float] = []
    prev_weights: np.ndarray | None = None

    for i in range(len(dates)):
        # 切片：取 [i-lookback, i) 的收益率窗口
        start = max(0, i - lookback)
        ret_window = ret_df.iloc[start:i]

        # 窗口不足时跳过优化，沿用上一期权重
        if len(ret_window) < max(2, lookback // 2):
            if prev_weights is not None:
                weight_rows.append(prev_weights)
            else:
                weight_rows.append(np.ones(n) / n)
            continue

        # 取当前信号行
        pos_row = pos_df.iloc[i].values if i < len(pos_df) else None

        # 优化
        w = _single_optimize(codes, ret_window, pos_row, method, **kwargs)
        if w is None:
            # 失败时沿用上一期
            w = prev_weights if prev_weights is not None else np.ones(n) / n

        # Sign preservation
        if sign_preservation and pos_row is not None:
            w = _apply_sign_preservation(w, pos_row)

        # Turnover 计算
        if prev_weights is not None:
            turnover = float(np.sum(np.abs(w - prev_weights)))
            turnover_list.append(turnover)

        prev_weights = w.copy()
        weight_rows.append(w)

    result = pd.DataFrame(
        np.array(weight_rows),
        index=dates,
        columns=codes,
    )

    if return_turnover:
        avg_turnover = float(np.mean(turnover_list)) if turnover_list else 0.0
        stats = {
            "turnover": turnover_list,
            "avg_turnover": avg_turnover,
            "total_turnover": float(np.sum(turnover_list)),
            "rebalance_count": len(turnover_list),
        }
        return result, stats

    return result


__all__ = ["optimize_weights"]
