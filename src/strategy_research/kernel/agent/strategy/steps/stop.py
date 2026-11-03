"""Budget-aware StopStep (A1).

Replaces the v0.1 stub: evaluates the three-dimensional run budget
(tokens / cost / time) in addition to an external ``ctx.should_stop``.
"""

from __future__ import annotations

from ..budget import BudgetLimits, BudgetUsage, evaluate_budget
from ..loop_context import LoopContext


class BudgetStopStep:
    """Stop when budget is exhausted or an external step requested stop."""

    def __init__(self, limits: BudgetLimits | None = None) -> None:
        self._limits = limits

    @property
    def name(self) -> str:
        return "stop"

    def bind_agent_loop(self, agent_loop) -> None:  # noqa: ANN001
        """Allow the loop to supply config-derived limits + live usage."""
        self._agent_loop = agent_loop

    def _resolve(self, ctx: LoopContext) -> tuple[BudgetUsage, BudgetLimits]:
        usage = BudgetUsage(
            tokens=getattr(ctx, "tokens_used", 0) or 0,
            cost_usd=getattr(ctx, "cost_usd", 0.0) or 0.0,
            started_at=getattr(ctx, "t0", 0.0) or 0.0,
        )
        if self._limits is not None:
            return usage, self._limits

        loop = getattr(self, "_agent_loop", None)
        cfg = getattr(getattr(loop, "_strategy", None), "config", None)
        if cfg is not None:
            limits = BudgetLimits(
                max_tokens=getattr(cfg, "budget_max_tokens", None),
                max_cost_usd=getattr(cfg, "budget_max_cost_usd", None),
                max_seconds=getattr(cfg, "budget_max_seconds", None),
                price_per_mtok_input=getattr(cfg, "price_per_mtok_input", None),
                price_per_mtok_output=getattr(cfg, "price_per_mtok_output", None),
            )
        else:
            limits = BudgetLimits()
        return usage, limits

    def evaluate(self, ctx: LoopContext) -> tuple[bool, str | None]:
        if ctx.should_stop:
            return True, ctx.stop_reason
        usage, limits = self._resolve(ctx)
        exceeded, reason = evaluate_budget(usage, limits)
        if exceeded:
            ctx.should_stop = True
            ctx.stop_reason = reason
            return True, reason
        return False, None


# Backwards-compatible alias.
DefaultStopStep = BudgetStopStep


__all__ = ["BudgetStopStep", "DefaultStopStep"]
