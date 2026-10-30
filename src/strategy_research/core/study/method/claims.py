"""Falsifiable claims tracking — prediction capture, validation, aggregation.

Design (docs/agentquant-research-20260902.md §可证伪声明追踪):
- Researchers MAY attach a ``predictions`` object to their output; missing
  predictions simply skip claims tracking for that round (graceful
  degradation — never fails a round, never triggers a retry).
- Predictions are captured at the novelty gate (pre-backtest) so they are
  credible, persisted via ``GoalStore.append_journal_entry(predictions=…)``.
- After the backtest, ``validate_predictions`` compares each predicted
  metric against the actual value and the outcome is backfilled via
  ``GoalStore.fill_journal_prediction_outcome``.
- ``summarize_prediction_accuracy`` aggregates a rolling, decayed
  calibration view over recent journal entries.

Prediction schema (per metric name)::

    {"direction": "up" | "down", "expected": 0.8, "tolerance": 0.3}

Outcome schema::

    {"actual": 0.55, "abs_error": 0.25, "direction_correct": true,
     "within_tolerance": true}
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Metrics whose "better" direction is DOWN (lower is better). Everything
# else (calmar/sharpe/ann_return/win_rate/…) improves upward.
_DOWN_IS_BETTER = frozenset({"max_dd", "ann_vol", "turnover", "drawdown"})

_ALLOWED_PRED_KEYS = frozenset({"direction", "expected", "tolerance"})
_VALID_DIRECTIONS = frozenset({"up", "down"})


def normalize_predictions(
    predictions: Any,
    known_metrics: list[str] | None = None,
) -> dict[str, dict]:
    """Sanitize a researcher's raw ``predictions`` object.

    Returns only well-formed entries: metric name →
    ``{"direction": str, "expected": float, "tolerance": float}``.
    Anything malformed is dropped (never raises — claims are optional).

    ``known_metrics`` optionally restricts to the study's tracked metrics;
    unknown metric names are dropped to keep the calibration statistics
    comparable across rounds.
    """
    if not isinstance(predictions, dict):
        return {}
    known = {m.lower() for m in known_metrics} if known_metrics else None
    out: dict[str, dict] = {}
    for raw_name, spec in predictions.items():
        if not isinstance(raw_name, str) or not isinstance(spec, dict):
            continue
        name = raw_name.lower()
        if known is not None and name not in known:
            continue
        direction = str(spec.get("direction", "")).lower()
        expected = _to_float(spec.get("expected"))
        tolerance = _to_float(spec.get("tolerance"))
        if direction not in _VALID_DIRECTIONS or expected is None:
            continue
        out[name] = {
            "direction": direction,
            "expected": expected,
            "tolerance": tolerance if tolerance is not None and tolerance > 0 else 0.0,
        }
    return out


def validate_predictions(
    predictions: dict[str, dict],
    metrics: dict | None,
) -> dict[str, dict]:
    """Compare pre-backtest predictions against actual metrics.

    Returns ``{metric: outcome}`` for every predictable metric. Metrics
    missing from ``metrics`` (backtest failed / metric absent) are skipped
    — an untestable prediction is not a wrong prediction.
    """
    if not predictions or not isinstance(metrics, dict):
        return {}
    outcomes: dict[str, dict] = {}
    for name, spec in predictions.items():
        actual = _to_float(metrics.get(name))
        if actual is None:
            continue
        expected = spec.get("expected", 0.0)
        tolerance = spec.get("tolerance", 0.0) or 0.0
        direction = spec.get("direction", "up")
        if direction == "down":
            direction_correct = actual < expected
        else:
            direction_correct = actual > expected
        outcomes[name] = {
            "actual": actual,
            "abs_error": round(abs(actual - expected), 6),
            "direction_correct": bool(direction_correct),
            "within_tolerance": bool(abs(actual - expected) <= tolerance),
        }
    return outcomes


def summarize_prediction_accuracy(
    entries: list,
    decay: float = 0.9,
) -> dict:
    """Aggregate calibration stats over journal entries (newest first).

    Returns::

        {
          "n_predictions": int,        # rounds that carried predictions
          "n_validated": int,          # predictions with an outcome
          "direction_hit_rate": float, # decayed, 0..1 (None if no data)
          "mean_abs_error": float,     # decayed (None if no data)
          "adoption_rate": float|None, # n_predictions / n_entries
        }

    Entries are expected newest-first (``list_journal_entries`` order);
    older entries get geometrically less weight (decay ** index).
    """
    n_entries = len(entries)
    n_predictions = sum(1 for e in entries if getattr(e, "predictions", None))
    direction_hits: list[float] = []
    abs_errors: list[float] = []
    for idx, e in enumerate(entries):
        outcome = getattr(e, "prediction_outcome", None)
        if not outcome:
            continue
        weight = decay ** idx
        for name, o in outcome.items():
            if not isinstance(o, dict):
                continue
            if "direction_correct" in o:
                direction_hits.append(weight if o["direction_correct"] else 0.0)
            if "abs_error" in o:
                abs_errors.append(float(o["abs_error"]) * weight)
    total_w = sum(decay ** idx for idx, e in enumerate(entries)
                  if getattr(e, "prediction_outcome", None))
    return {
        "n_predictions": n_predictions,
        "n_validated": len(direction_hits) or sum(
            1 for e in entries if getattr(e, "prediction_outcome", None)),
        "direction_hit_rate": (
            round(sum(direction_hits) / total_w, 4)
            if total_w > 0 else None
        ),
        "mean_abs_error": (
            round(sum(abs_errors) / total_w, 6)
            if total_w > 0 else None
        ),
        "adoption_rate": round(n_predictions / n_entries, 4) if n_entries else None,
    }


def _to_float(value: Any) -> float | None:
    """Best-effort float coercion mirroring metric_targets semantics."""
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
