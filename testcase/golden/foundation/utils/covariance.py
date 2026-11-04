"""协方差矩阵估计。

复用自 QuantNodes/strategy/momentum_etf_rotation/common/covariance.py。
"""
from __future__ import annotations

from typing import Literal

import numpy as np
import pandas as pd

CovMethod = Literal["sample", "ledoit_wolf", "ewma", "diagonal"]


def sample_covariance(returns: pd.DataFrame) -> np.ndarray:
    return returns.cov().values * 252


def ledoit_wolf_shrinkage(returns: pd.DataFrame) -> tuple[np.ndarray, float]:
    rets = returns.dropna()
    n, p = rets.shape
    if n < 2:
        raise ValueError(f"样本数不足: n={n}")
    X = rets.values
    X_centered = X - X.mean(axis=0)
    S = (X_centered.T @ X_centered) / (n - 1)
    mu_sq = float(np.trace(S) / p)
    F = mu_sq * np.eye(p)
    var_S = np.var(X_centered, axis=0).sum() / n
    off_diag = S - np.diag(np.diag(S))
    var_off = np.sum(off_diag ** 2) / (p * (p - 1)) if p > 1 else 0
    numerator = var_S + var_off
    denominator = np.sum((S - F) ** 2)
    if denominator <= 0:
        alpha = 1.0
    else:
        alpha = float(np.clip(numerator / denominator, 0.0, 1.0))
    shrunk = alpha * F + (1 - alpha) * S
    return shrunk * 252, alpha


def ewma_covariance(returns: pd.DataFrame, halflife: int = 60) -> np.ndarray:
    rets = returns.dropna()
    n, p = rets.shape
    if n < 2:
        raise ValueError(f"样本数不足: n={n}")
    lam = float(np.exp(-np.log(2) / halflife))
    X = rets.values
    cov = np.zeros((p, p))
    weight = 0.0
    for t in range(n - 1, -1, -1):
        x = X[t:t + 1].T
        cov = lam * cov + (1 - lam) * (x @ x.T)
        weight = lam * weight + (1 - lam)
    if weight > 0:
        cov = cov / weight
    return cov * 252


def diagonal_covariance(returns: pd.DataFrame) -> np.ndarray:
    rets = returns.dropna()
    return np.diag(np.asarray(rets.var().to_numpy()) * 252)


def estimate_covariance(returns, method: str = "ledoit_wolf", halflife: int = 60):
    method = method.lower()
    if method == "sample":
        return sample_covariance(returns)
    elif method == "ledoit_wolf":
        cov, _ = ledoit_wolf_shrinkage(returns)
        return cov
    elif method == "ewma":
        return ewma_covariance(returns, halflife=halflife)
    elif method == "diagonal":
        return diagonal_covariance(returns)
    else:
        raise ValueError(f"未知方法: {method}")


def is_positive_definite(matrix: np.ndarray, tol: float = 1e-10) -> bool:
    try:
        eigvals = np.linalg.eigvalsh(matrix)
        return bool(np.all(eigvals > tol))
    except np.linalg.LinAlgError:
        return False


def condition_number(matrix: np.ndarray) -> float:
    try:
        return float(np.linalg.cond(matrix))
    except np.linalg.LinAlgError:
        return float("inf")
