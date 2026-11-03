"""Bootstrap Sharpe confidence interval (P3-c).

Resamples equity returns to estimate a confidence interval for the
Sharpe ratio. Useful when there are enough bars (>= 5) for a meaningful
resampling.

Adapted from vibe-trading-ai 0.1.11 (MIT License, HKUDS).
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from .utils import _sharpe


def bootstrap_sharpe_ci(
    equity_curve: pd.Series,
    n_bootstrap: int = 1000,
    confidence: float = 0.95,
    bars_per_year: int = 252,
    seed: int = 42,
) -> dict[str, Any]:
    """Resample daily returns to estimate Sharpe confidence interval.

    Args:
        equity_curve: Equity time series (indexed by date or bar).
        n_bootstrap: Number of bootstrap samples.
        confidence: Confidence level (e.g. 0.95 for 95% CI).
        bars_per_year: Annualisation factor.
        seed: Random seed for reproducibility.

    Returns:
        Dict with observed_sharpe, ci_lower, ci_upper, median_sharpe,
        prob_positive.
    """
    returns = equity_curve.pct_change().dropna().values
    if len(returns) < 5:
        return {"error": "need at least 5 return observations", "n_returns": len(returns)}

    observed = _sharpe(returns, bars_per_year)

    rng = np.random.default_rng(seed)
    boot_sharpes = []
    for _ in range(n_bootstrap):
        sample = rng.choice(returns, size=len(returns), replace=True)
        boot_sharpes.append(_sharpe(sample, bars_per_year))

    arr = np.array(boot_sharpes)
    alpha = (1 - confidence) / 2
    lower = float(np.percentile(arr, alpha * 100))
    upper = float(np.percentile(arr, (1 - alpha) * 100))
    prob_pos = float(np.mean(arr > 0))

    return {
        "observed_sharpe": round(observed, 4),
        "ci_lower": round(lower, 4),
        "ci_upper": round(upper, 4),
        "median_sharpe": round(float(np.median(arr)), 4),
        "prob_positive": round(prob_pos, 4),
        "confidence": confidence,
        "n_bootstrap": n_bootstrap,
        "n_returns": len(returns),
        "bars_per_year": bars_per_year,
    }


__all__ = ["bootstrap_sharpe_ci"]
