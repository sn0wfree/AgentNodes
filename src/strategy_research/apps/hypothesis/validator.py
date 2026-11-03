"""Hypothesis automatic validation pipeline (P3-D1).

Evaluates a hypothesis against backtest metrics and automatically transitions
its status from ``testing`` to ``validated`` or ``rejected``.

Uses the existing validation toolkit (Monte Carlo, Bootstrap, Walk-Forward).
The hypothesis should provide threshold criteria (Sharpe, max_drawdown, etc.)
either explicitly via ``success_criteria`` or via defaults from the
hypothesis metadata.

Usage:
    from strategy_research.apps.hypothesis.validator import validate_hypothesis

    result = validate_hypothesis(
        hyp=registry.get("hyp_xxx"),
        equity_curve=nav_series,
        trades=trade_list,
        registry=registry,
    )
    # result: ValidationResult with decision + metrics
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from strategy_research.apps.validation.bootstrap import bootstrap_sharpe_ci
from strategy_research.apps.validation.monte_carlo import monte_carlo_test
from strategy_research.apps.validation.walk_forward import walk_forward_analysis

logger = logging.getLogger(__name__)


# Default thresholds for validation decisions.
# These can be overridden per-hypothesis via Hypothesis.success_criteria.
DEFAULT_CRITERIA: dict[str, float] = {
    "min_sharpe": 0.5,
    "max_drawdown_threshold": -0.30,
    "monte_carlo_p_value": 0.05,
    "bootstrap_prob_positive": 0.70,
    "walk_forward_consistency": 0.5,
}


@dataclass
class ValidationResult:
    """Result of a hypothesis validation run."""

    hypothesis_id: str
    decision: str  # "validated" | "rejected" | "inconclusive"
    metrics: dict[str, Any] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)
    raw_results: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "hypothesis_id": self.hypothesis_id,
            "decision": self.decision,
            "metrics": self.metrics,
            "reasons": self.reasons,
            "raw_results": self.raw_results,
        }


def validate_hypothesis(
    *,
    hyp: Any,
    equity_curve: pd.Series,
    trades: list[Any] | None = None,
    registry: Any | None = None,
    criteria: dict[str, float] | None = None,
    auto_transition: bool = True,
    initial_capital: float = 1_000_000.0,
) -> ValidationResult:
    """Run all three validations against a hypothesis's metrics.

    Args:
        hyp: Hypothesis object (must have hypothesis_id, run_cards).
        equity_curve: Equity time series (e.g., NAV).
        trades: Optional list of TradeInput objects.
        registry: Optional HypothesisRegistry — if provided AND auto_transition
            is True, the hypothesis status will be updated based on the decision.
        criteria: Override default validation thresholds.
        auto_transition: If True, update hypothesis status via registry.
        initial_capital: Starting capital for Monte Carlo.

    Returns:
        ValidationResult with decision, metrics, reasons, and raw results.
    """
    from .registry import VALID_TRANSITIONS, Hypothesis

    if not isinstance(hyp, Hypothesis):
        raise TypeError("hyp must be a Hypothesis instance")

    cfg = {**DEFAULT_CRITERIA, **(criteria or {})}
    raw: dict[str, Any] = {}
    reasons: list[str] = []
    metrics: dict[str, Any] = {}

    # ── Monte Carlo ─────────────────────────────
    try:
        mc = monte_carlo_test(
            trades or [],
            initial_capital,
            n_simulations=500,
            seed=42,
            bars_per_year=252,
        )
        raw["monte_carlo"] = mc
        metrics["monte_carlo_actual_sharpe"] = mc.get("actual_sharpe", 0.0)
        metrics["monte_carlo_p_value"] = mc.get("p_value_sharpe", 1.0)
        if mc.get("p_value_sharpe", 1.0) > cfg["monte_carlo_p_value"]:
            reasons.append(
                f"Monte Carlo: p={mc.get('p_value_sharpe', 1.0):.3f} > "
                f"{cfg['monte_carlo_p_value']} (not significant)"
            )
    except Exception as exc:
        logger.debug("Monte Carlo failed for %s: %s", hyp.hypothesis_id, exc)
        raw["monte_carlo"] = {"error": str(exc)}

    # ── Bootstrap Sharpe CI ──────────────────────
    try:
        bs = bootstrap_sharpe_ci(
            equity_curve,
            bars_per_year=252,
            n_bootstrap=500,
            confidence=0.95,
            seed=42,
        )
        raw["bootstrap"] = bs
        metrics["bootstrap_observed_sharpe"] = bs.get("observed_sharpe", 0.0)
        metrics["bootstrap_prob_positive"] = bs.get("prob_positive", 0.0)
        if bs.get("prob_positive", 0.0) < cfg["bootstrap_prob_positive"]:
            reasons.append(
                f"Bootstrap: P(Sharpe>0)={bs.get('prob_positive', 0.0):.2f} < "
                f"{cfg['bootstrap_prob_positive']}"
            )
    except Exception as exc:
        logger.debug("Bootstrap failed for %s: %s", hyp.hypothesis_id, exc)
        raw["bootstrap"] = {"error": str(exc)}

    # ── Walk-Forward ─────────────────────────────
    try:
        wf = walk_forward_analysis(
            equity_curve,
            trades or [],
            n_windows=4,
            bars_per_year=252,
        )
        raw["walk_forward"] = wf
        metrics["wf_consistency"] = wf.get("consistency_rate", 0.0)
        metrics["wf_profitable_windows"] = wf.get("profitable_windows", 0)
        if wf.get("consistency_rate", 0.0) < cfg["walk_forward_consistency"]:
            reasons.append(
                f"Walk-forward: consistency={wf.get('consistency_rate', 0.0):.2f} < "
                f"{cfg['walk_forward_consistency']}"
            )
    except Exception as exc:
        logger.debug("Walk-forward failed for %s: %s", hyp.hypothesis_id, exc)
        raw["walk_forward"] = {"error": str(exc)}

    # ── Decision ────────────────────────────────
    # If 2+ checks pass (no reject reason), validate. If 2+ fail, reject.
    # Inconclusive otherwise.
    fail_count = sum(1 for r in reasons if r)
    if fail_count == 0:
        decision = "validated"
    elif fail_count >= 2:
        decision = "rejected"
    else:
        decision = "inconclusive"

    # ── Auto-transition ──────────────────────────
    if auto_transition and registry is not None:
        new_status = "validated" if decision == "validated" else (
            "rejected" if decision == "rejected" else "exploring"
        )
        if new_status in VALID_TRANSITIONS.get(hyp.status, set()):
            try:
                registry.update(
                    hyp.hypothesis_id,
                    status=new_status,
                    invalidation_notes="; ".join(reasons) if reasons else "all checks passed",
                )
                logger.info(
                    "Hypothesis %s: %s -> %s (reasons: %d)",
                    hyp.hypothesis_id, hyp.status, new_status, len(reasons),
                )
            except ValueError as exc:
                logger.warning("Auto-transition failed: %s", exc)
        else:
            logger.debug(
                "Skipping transition %s -> %s (not allowed)",
                hyp.status, new_status,
            )

    return ValidationResult(
        hypothesis_id=hyp.hypothesis_id,
        decision=decision,
        metrics=metrics,
        reasons=reasons,
        raw_results=raw,
    )


__all__ = ["DEFAULT_CRITERIA", "ValidationResult", "validate_hypothesis"]
