"""风险平价 (Risk Parity) 加权。

复用自 QuantNodes/strategy/momentum_etf_rotation/common/risk_parity.py。
"""
from __future__ import annotations

import numpy as np
from scipy.optimize import minimize


def risk_contribution(weights: np.ndarray, cov: np.ndarray) -> np.ndarray:
    port_var = float(weights @ cov @ weights)
    if port_var <= 0:
        return np.zeros(len(weights))
    return weights * (cov @ weights) / port_var


def risk_parity_objective(weights: np.ndarray, cov: np.ndarray) -> float:
    rc = risk_contribution(weights, cov)
    target = 1.0 / len(weights)
    return float(np.sum((rc - target) ** 2))


def solve_risk_parity(
    cov: np.ndarray,
    max_iter: int = 200,
    tol: float = 1e-8,
    bounds: tuple[float, float] = (0.01, 0.40),
) -> np.ndarray:
    n = cov.shape[0]
    if cov.shape != (n, n):
        raise ValueError(f"协方差矩阵必须方阵: {cov.shape}")
    try:
        np.linalg.cholesky(cov)
    except np.linalg.LinAlgError:
        raise ValueError("协方差矩阵不正定")

    def obj(w):
        return risk_parity_objective(w, cov)

    w0 = np.ones(n) / n
    constraints = [{"type": "eq", "fun": lambda w: np.sum(w) - 1.0}]
    bnds = [bounds] * n

    result = minimize(
        obj, w0, method="SLSQP",
        bounds=bnds, constraints=constraints,
        options={"maxiter": max_iter, "ftol": tol},
    )
    if not result.success:
        vols = np.sqrt(np.diag(cov))
        inv = 1.0 / vols
        return inv / inv.sum()
    weights = np.maximum(result.x, 0)
    return weights / weights.sum()


def solve_max_diversification(
    cov: np.ndarray,
    max_iter: int = 200,
    tol: float = 1e-8,
    bounds: tuple[float, float] = (0.01, 0.40),
) -> np.ndarray:
    n = cov.shape[0]
    vols = np.sqrt(np.diag(cov))
    corr = cov / np.outer(vols, vols)
    corr = np.nan_to_num(corr, nan=0.0)

    def neg_diversification(w):
        port_vol = np.sqrt(w @ cov @ w)
        weighted_avg_vol = w @ vols
        return -port_vol / (weighted_avg_vol + 1e-10)

    w0 = np.ones(n) / n
    constraints = [{"type": "eq", "fun": lambda w: np.sum(w) - 1.0}]
    bnds = [bounds] * n

    result = minimize(
        neg_diversification, w0, method="SLSQP",
        bounds=bnds, constraints=constraints,
        options={"maxiter": max_iter, "ftol": tol},
    )
    if not result.success:
        w = 1.0 / vols
        return w / w.sum()
    weights = np.maximum(result.x, 0)
    return weights / weights.sum()
