"""Backtest validation toolkit (P3-c).

Three independent statistical checks:
  - Monte Carlo permutation test (path significance)
  - Bootstrap Sharpe confidence interval (Sharpe stability)
  - Walk-Forward analysis (regime consistency)

Plus a multi-market registry (MarketType) for future per-market algorithm
support. In v0.3.0 only A_SHARE / HK_EQUITY / US_EQUITY are validated;
other markets emit a UserWarning and fall back to A_SHARE defaults.

Adapted from vibe-trading-ai 0.1.11 (MIT License, HKUDS).
"""

from __future__ import annotations

from .bootstrap import bootstrap_sharpe_ci
from .market import (
    SUPPORTED_MARKETS,
    MarketType,
    bars_per_year,
    warn_if_unsupported_market,
)
from .monte_carlo import monte_carlo_test
from .runner import run_validation
from .trade_input import TradeInput
from .utils import _json_safe, _sharpe
from .walk_forward import walk_forward_analysis

__all__ = [
    "MarketType",
    "SUPPORTED_MARKETS",
    "TradeInput",
    "_json_safe",
    "_sharpe",
    "bars_per_year",
    "bootstrap_sharpe_ci",
    "monte_carlo_test",
    "run_validation",
    "walk_forward_analysis",
    "warn_if_unsupported_market",
]
