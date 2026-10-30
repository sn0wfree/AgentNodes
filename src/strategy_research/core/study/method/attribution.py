"""AEGIS-style attribution system for study rounds.

Classifies each predicted metric target's pass/fail change between
consecutive rounds.  Pure functions with no DB dependencies.
"""
from __future__ import annotations

from enum import Enum


class AttributionOutcome(str, Enum):
    FLIPPED = "flipped"      # F→T (predicted metric improved)
    STILL_F = "still_F"      # F→F (no improvement)
    REGRESSED = "regressed"  # T→F (previously met metric regressed)
    STILL_T = "still_T"      # T→T (already met, no change)
    ABSENT = "absent"        # metric not in current results


def classify_attribution(
    predicted_tasks: list[str],
    passed_before: set[str],
    passed_now: set[str],
) -> dict[str, str]:
    """Classify each predicted task by comparing before/after pass sets.

    Args:
        predicted_tasks: metric names the researcher/strategist predicted
            would be affected by this round's changes.
        passed_before: metric names that met their targets in the previous round.
        passed_now: metric names that met their targets in this round.

    Returns:
        ``{metric_name: AttributionOutcome}`` for each predicted task.
    """
    result: dict[str, str] = {}
    for tid in predicted_tasks:
        was_p = tid in passed_before
        now_p = tid in passed_now

        if was_p and now_p:
            result[tid] = AttributionOutcome.STILL_T
        elif was_p and not now_p:
            result[tid] = AttributionOutcome.REGRESSED
        elif not was_p and now_p:
            result[tid] = AttributionOutcome.FLIPPED
        else:
            result[tid] = AttributionOutcome.STILL_F
    return result


def compute_precision(attribution: dict[str, str]) -> tuple[float, int, int]:
    """Compute attribution precision.

    Returns:
        (precision, hits, total) where:
        - hits = number of FLIPPED metrics
        - total = attributed + side_effects (REGRESSED)
        - precision = hits / total (0.0 if no attributed metrics)
    """
    hits = 0
    attributed = 0
    side_effects = 0

    for outcome in attribution.values():
        if outcome == AttributionOutcome.ABSENT:
            continue
        attributed += 1
        if outcome == AttributionOutcome.FLIPPED:
            hits += 1
        elif outcome == AttributionOutcome.REGRESSED:
            side_effects += 1

    total = attributed + side_effects
    precision = hits / total if total > 0 else 0.0
    return precision, hits, total
