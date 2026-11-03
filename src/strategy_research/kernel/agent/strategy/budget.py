"""Three-dimensional run budget: tokens / cost / time (A1).

Policy is a pure function so AgentLoop / StopStep / tests share one
source of truth. A dimension is inactive when its limit is None or <= 0.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class BudgetLimits:
    """Hard caps for one agent run. None / <=0 means unlimited."""

    max_tokens: int | None = None
    max_cost_usd: float | None = None
    max_seconds: float | None = None
    # USD per 1M tokens — used only for cost accounting.
    price_per_mtok_input: float | None = None
    price_per_mtok_output: float | None = None

    @staticmethod
    def _active(value: float | int | None) -> bool:
        return value is not None and value > 0


@dataclass
class BudgetUsage:
    """Accumulated spend for the current run."""

    tokens: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    started_at: float = 0.0

    def elapsed(self, now: float | None = None) -> float:
        if self.started_at <= 0:
            return 0.0
        return (now if now is not None else time.perf_counter()) - self.started_at


def usage_from_response(usage: Any) -> tuple[int, int, int]:
    """Extract (total, prompt, completion) from an LLM usage mapping.

    Prefers ``total_tokens``; falls back to prompt+completion. Missing /
    malformed values count as 0 so accounting never crashes the loop.
    """
    if not isinstance(usage, dict):
        return 0, 0, 0

    def _i(key: str) -> int:
        try:
            v = usage.get(key)
            return int(v) if v is not None else 0
        except (TypeError, ValueError):
            return 0

    prompt = _i("prompt_tokens")
    completion = _i("completion_tokens")
    total = _i("total_tokens") or (prompt + completion)
    return total, prompt, completion


def record_usage(
    tracker: BudgetUsage,
    limits: BudgetLimits,
    usage: Any,
) -> BudgetUsage:
    """Add one LLM response's usage to the tracker (mutates + returns)."""
    total, prompt, completion = usage_from_response(usage)
    tracker.tokens += total
    tracker.prompt_tokens += prompt
    tracker.completion_tokens += completion

    price_in = limits.price_per_mtok_input
    price_out = limits.price_per_mtok_output
    if price_in:
        tracker.cost_usd += (prompt / 1_000_000.0) * float(price_in)
    if price_out:
        tracker.cost_usd += (completion / 1_000_000.0) * float(price_out)
    return tracker


def evaluate_budget(
    tracker: BudgetUsage,
    limits: BudgetLimits,
    *,
    now: float | None = None,
) -> tuple[bool, str | None]:
    """Return (exceeded, reason) for the first breached dimension.

    Order: tokens → cost → time. Reason format: ``budget:<dimension>``.
    """
    max_tokens = limits.max_tokens
    if max_tokens is not None and BudgetLimits._active(max_tokens) and tracker.tokens >= int(max_tokens):
        return True, "budget:tokens"
    max_cost = limits.max_cost_usd
    if max_cost is not None and BudgetLimits._active(max_cost) and tracker.cost_usd >= float(max_cost):
        return True, "budget:cost"
    max_seconds = limits.max_seconds
    if max_seconds is not None and BudgetLimits._active(max_seconds):
        elapsed = tracker.elapsed(now)
        if elapsed >= float(max_seconds):
            return True, "budget:time"
    return False, None


__all__ = [
    "BudgetLimits",
    "BudgetUsage",
    "evaluate_budget",
    "record_usage",
    "usage_from_response",
]
