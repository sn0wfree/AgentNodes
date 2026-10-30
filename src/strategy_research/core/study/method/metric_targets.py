"""Metric target comparison utilities.

Extracted from runner.py to enable reuse across engines and tests.
"""
from __future__ import annotations

from typing import Any


def meets_metric_targets(metrics: dict[str, Any], targets: list[dict]) -> bool:
    """Check if all metric targets are met."""
    for t in targets:
        name = t.get("name")
        op = t.get("op", ">=")
        value = t.get("value")
        if name is None or value is None:
            return False
        actual = metrics.get(name)
        if actual is None:
            return False
        try:
            a, v = float(actual), float(value)
        except (TypeError, ValueError):
            return False
        if op == ">=":
            if not ((a >= v)):
                return False
        elif op == "<=":
            if not ((a <= v)):
                return False
        elif op == ">":
            if not ((a > v)):
                return False
        elif op == "<":
            if not ((a < v)):
                return False
        elif op == "==":
            if not ((a == v)):
                return False
        else:
            return False
    return True


def target_failures(metrics: dict[str, Any], targets: list[dict]) -> list[str]:
    """Human-readable descriptions of unmet targets (for drift reports).

    Mirrors the comparison semantics of :func:`meets_metric_targets` but
    collects every failure instead of short-circuiting.
    """
    failures: list[str] = []
    for t in targets:
        name = t.get("name")
        op = t.get("op", ">=")
        value = t.get("value")
        if name is None or value is None:
            failures.append(f"invalid target spec: {t!r}")
            continue
        actual = metrics.get(name)
        if actual is None:
            failures.append(f"{name} {op} {value}: metric missing")
            continue
        try:
            a, v = float(actual), float(value)
        except (TypeError, ValueError):
            failures.append(f"{name} {op} {value}: non-numeric value {actual!r}")
            continue
        ok = (
            (op == ">=" and a >= v) or (op == "<=" and a <= v)
            or (op == ">" and a > v) or (op == "<" and a < v)
            or (op == "==" and a == v)
        )
        if not ok:
            failures.append(
                f"{name} {op} {value:g}: got {a:g}" if isinstance(a, float) else
                f"{name} {op} {value}: got {actual}"
            )
    return failures


def metric_pass_set(metrics: dict, targets: list[dict]) -> set[str]:
    """Return set of metric names that meet their targets."""
    passed = set()
    for t in targets:
        name = t.get("name")
        op = t.get("op", ">=")
        value = t.get("value")
        if name is None or value is None:
            continue
        actual = metrics.get(name)
        if actual is None:
            continue
        try:
            a, v = float(actual), float(value)
        except (TypeError, ValueError):
            continue
        if ((op == ">=" and a >= v) or (op == "<=" and a <= v)
                or (op == ">" and a > v) or (op == "<" and a < v)
                or (op == "==" and a == v)):
            passed.add(name)
    return passed


def acceptance_config_from_targets(
    targets: list[dict] | None,
) -> Any:
    """Map ``metric_targets`` to an ``AcceptanceConfig`` override.

    Called lazily (imports strategy_acceptance) so importing this module
    does not eagerly load the acceptance module.
    """
    from strategy_research.core.strategy_acceptance import DEFAULT_CONFIG

    if not targets:
        return DEFAULT_CONFIG
    overrides: dict[str, Any] = {}
    for t in targets:
        name = t.get("name")
        value = t.get("value")
        if name is None or value is None:
            continue
        if name == "calmar":
            overrides["hard_calmar_min"] = float(value)
        elif name == "sharpe":
            overrides["hard_sharpe_min"] = float(value)
        elif name == "max_dd":
            overrides["hard_max_dd_min"] = float(value)
    return DEFAULT_CONFIG.with_overrides(**overrides) if overrides else DEFAULT_CONFIG
