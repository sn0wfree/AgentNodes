"""A5 runtime: stop / budget policy extracted from AgentLoop."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from ..strategy.budget import (
    BudgetLimits,
    BudgetUsage,
    evaluate_budget,
    record_usage,
    usage_from_response,
)

__all__ = [
    "BudgetLimits",
    "BudgetUsage",
    "StopPolicy",
    "evaluate_budget",
    "limits_from_config",
    "record_usage",
    "usage_from_response",
]


def limits_from_config(cfg: Any) -> BudgetLimits:
    """Read A1 budget fields from a LoopConfig-like object."""
    if cfg is None:
        return BudgetLimits()
    return BudgetLimits(
        max_tokens=getattr(cfg, "budget_max_tokens", None),
        max_cost_usd=getattr(cfg, "budget_max_cost_usd", None),
        max_seconds=getattr(cfg, "budget_max_seconds", None),
        price_per_mtok_input=getattr(cfg, "price_per_mtok_input", None),
        price_per_mtok_output=getattr(cfg, "price_per_mtok_output", None),
    )


@dataclass
class StopDecision:
    should_stop: bool
    reason: str | None = None
    kind: str | None = None  # budget | external | None


class StopPolicy:
    """Pure stop semantics for one agent run (A5)."""

    def __init__(
        self,
        limits: BudgetLimits | None = None,
        *,
        max_iterations: int | None = None,
    ) -> None:
        self.limits = limits or BudgetLimits()
        self.max_iterations = max_iterations

    def decide(
        self,
        *,
        tokens: int = 0,
        cost_usd: float = 0.0,
        started_at: float = 0.0,
        iteration: int = 0,
        external_stop: bool = False,
        external_reason: str | None = None,
        now: float | None = None,
    ) -> StopDecision:
        if external_stop:
            return StopDecision(True, external_reason, "external")

        usage = BudgetUsage(
            tokens=tokens,
            cost_usd=cost_usd,
            started_at=started_at or time.perf_counter(),
        )
        exceeded, reason = evaluate_budget(usage, self.limits, now=now)
        if exceeded:
            return StopDecision(True, reason, "budget")

        if (
            self.max_iterations is not None
            and self.max_iterations > 0
            and iteration >= self.max_iterations
        ):
            return StopDecision(True, "max_iter", "max_iter")

        return StopDecision(False, None, None)
