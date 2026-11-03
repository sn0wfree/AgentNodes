"""Default ContinuationStep — no-op stub (v0.1)."""

from __future__ import annotations

from ..loop_context import LoopContext


class DefaultContinuationStep:
    @property
    def name(self) -> str:
        return "continuation"

    def evaluate(self, ctx: LoopContext) -> tuple[bool, str | None]:
        # v0.1: AgentLoop._check_goal_continuation decides whether to
        # continue; the Step is a hook for future strategies.
        return False, None


__all__ = ["DefaultContinuationStep"]
