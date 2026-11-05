"""Utility helpers shared by the validation tools (P3-c).

Includes ``_json_safe`` to coerce NaN / inf into JSON-null, and
``_sharpe`` which is the canonical sharpe-ratio formula used by the
Monte Carlo / Bootstrap tools.

Adapted from vibe-trading-ai 0.1.11 (MIT License, HKUDS).
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np


def _json_safe(value: Any) -> Any:
    """Return a JSON-strict copy of validation results.

    NaN / inf floats are converted to ``None`` so the output can be
    serialized with ``allow_nan=False``.
    """
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, np.floating):
        as_float = float(value)
        return as_float if math.isfinite(as_float) else None
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, np.bool_):
        return bool(value)
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def _sharpe(returns: np.ndarray, bars_per_year: int = 252) -> float:
    """Annualized Sharpe ratio from a returns array."""
    std = returns.std()
    return float(returns.mean() / (std + 1e-10) * np.sqrt(bars_per_year))


__all__ = ["_json_safe", "_sharpe"]
