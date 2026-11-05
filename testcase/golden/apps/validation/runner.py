"""Validation runner — orchestrates Monte Carlo / Bootstrap / Walk-Forward (P3-c).

Adapted from vibe-trading-ai 0.1.11 (MIT License, HKUDS).
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from .bootstrap import bootstrap_sharpe_ci
from .market import MarketType, bars_per_year, warn_if_unsupported_market
from .monte_carlo import monte_carlo_test
from .trade_input import TradeInput
from .utils import _json_safe
from .walk_forward import walk_forward_analysis


def run_validation(
    config: dict[str, Any],
    equity_curve: pd.Series,
    trades: list[TradeInput] | None = None,
    initial_capital: float = 1_000_000.0,
    market: MarketType = MarketType.A_SHARE,
) -> dict[str, Any]:
    """Run configured validation checks.

    Reads from ``config["validation"]``:

      - ``monte_carlo``: ``True`` or dict override (n_simulations, seed)
      - ``bootstrap``:   ``True`` or dict override (n_bootstrap, confidence, seed)
      - ``walk_forward``: ``True`` or dict override (n_windows)

    Multi-market support:
      - ``market`` parameter (default A_SHARE).
      - For unsupported markets, emits a UserWarning and uses A_SHARE bars_per_year.
      - Per the P3-c user decision, only A_SHARE / HK_EQUITY / US_EQUITY are
        fully validated; see docs/validation-design.md for the roadmap.

    Args:
        config: Validation config dict (typically from run config.json).
        equity_curve: Equity time series.
        trades: Optional list of completed trades (Monte Carlo / Walk-Forward use it).
        initial_capital: Starting capital for Monte Carlo.
        market: Target market type.

    Returns:
        Dict keyed by validation type with results.
    """
    warn_if_unsupported_market(market)
    bpy = bars_per_year(market)

    v_cfg = config.get("validation", {})
    results: dict[str, Any] = {
        "market": market.value,
        "bars_per_year": bpy,
    }

    if "monte_carlo" in v_cfg:
        mc_cfg = v_cfg["monte_carlo"] if isinstance(v_cfg["monte_carlo"], dict) else {}
        results["monte_carlo"] = _json_safe(monte_carlo_test(
            trades or [],
            initial_capital,
            n_simulations=mc_cfg.get("n_simulations", 1000),
            seed=mc_cfg.get("seed", 42),
            bars_per_year=bpy,
        ))

    if "bootstrap" in v_cfg:
        bs_cfg = v_cfg["bootstrap"] if isinstance(v_cfg["bootstrap"], dict) else {}
        results["bootstrap"] = _json_safe(bootstrap_sharpe_ci(
            equity_curve,
            bars_per_year=bpy,
            n_bootstrap=bs_cfg.get("n_bootstrap", 1000),
            confidence=bs_cfg.get("confidence", 0.95),
            seed=bs_cfg.get("seed", 42),
        ))

    if "walk_forward" in v_cfg:
        wf_cfg = v_cfg["walk_forward"] if isinstance(v_cfg["walk_forward"], dict) else {}
        results["walk_forward"] = _json_safe(walk_forward_analysis(
            equity_curve,
            trades or [],
            n_windows=wf_cfg.get("n_windows", 5),
            bars_per_year=bpy,
        ))

    return results


__all__ = ["run_validation"]
