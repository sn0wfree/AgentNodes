"""Hard threshold rule for strategy acceptance (P6 Step 0).

This is the deterministic, no-LLM layer of the dual-layer decision. It
inspects the numeric fields of ``metrics.json`` and returns a per-check
``{passed: bool, detail: {metric: bool}}`` breakdown.

Rules (all configurable via ``AcceptanceConfig``):

    calmar >= hard_calmar_min
    sharpe >= hard_sharpe_min
    max_dd >= hard_max_dd_min           (max_dd is negative; e.g. -0.10 > -0.15)
    ann_return >= hard_ann_return_min   (disabled if threshold = 0.0)
    trades >= hard_trades_min

Two modes:

    cfg.require_all_hard = True   → ALL checks must pass
    cfg.require_all_hard = False  → AT LEAST ONE check must pass (lenient)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


@dataclass(frozen=True)
class RuleResult:
    """Output of a single rule check."""

    passed: bool
    detail: dict[str, bool] = field(default_factory=dict)
    notes: str = ""


class HardThresholdRule:
    """Pure-Python check against metrics.json numeric fields."""

    name: str = "hard_threshold"

    def check(
        self,
        metrics: Mapping[str, Any],
        cfg: Any,
    ) -> RuleResult:
        """Run the rule.

        Args:
            metrics: Numeric metric dict (already coerced to float/int by
                ``__init__.py._coerce_metrics``).
            cfg: ``AcceptanceConfig`` instance (typed as Any to avoid
                circular import; only attribute reads happen here).

        Returns:
            RuleResult with per-check pass/fail breakdown.
        """
        detail: dict[str, bool] = {}

        calmar_ok = self._check_calmar(metrics.get("calmar", 0.0), cfg.hard_calmar_min)
        sharpe_ok = self._check_sharpe(metrics.get("sharpe", 0.0), cfg.hard_sharpe_min)
        max_dd_ok = self._check_max_dd(metrics.get("max_dd", 0.0), cfg.hard_max_dd_min)
        ann_ret_ok = self._check_ann_return(
            metrics.get("ann_return", 0.0), cfg.hard_ann_return_min,
        )
        trades_ok = self._check_trades(metrics.get("trades", 0), cfg.hard_trades_min)

        detail["calmar"] = calmar_ok
        detail["sharpe"] = sharpe_ok
        detail["max_dd"] = max_dd_ok
        detail["ann_return"] = ann_ret_ok
        detail["trades"] = trades_ok

        if cfg.require_all_hard:
            passed = all(detail.values())
            notes = self._format_notes(detail, mode="all")
        else:
            passed = any(detail.values())
            notes = self._format_notes(detail, mode="any")

        return RuleResult(passed=passed, detail=detail, notes=notes)

    # ── Individual checks ────────────────────────────────────────

    @staticmethod
    def _check_calmar(value: float, threshold: float) -> bool:
        return value >= threshold

    @staticmethod
    def _check_sharpe(value: float, threshold: float) -> bool:
        return value >= threshold

    @staticmethod
    def _check_max_dd(value: float, threshold: float) -> bool:
        # max_dd is negative (e.g. -0.10); threshold is also negative
        # (e.g. -0.15). -0.10 > -0.15 → passes.
        return value >= threshold

    @staticmethod
    def _check_ann_return(value: float, threshold: float) -> bool:
        # Threshold of 0.0 disables this check (returns True).
        if threshold == 0.0:
            return True
        return value >= threshold

    @staticmethod
    def _check_trades(value: int, threshold: int) -> bool:
        return value >= threshold

    # ── Notes formatting ─────────────────────────────────────────

    @staticmethod
    def _format_notes(detail: dict[str, bool], mode: str) -> str:
        if mode == "all":
            failed = [k for k, v in detail.items() if not v]
            return f"all required; failed: {', '.join(failed) or '<none>'}"
        passed = [k for k, v in detail.items() if v]
        return f"any sufficient; passed: {', '.join(passed) or '<none>'}"
