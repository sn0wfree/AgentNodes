"""Default FinalizationStep (L7 v0.4).

Actually drives the finalization work + fires ``after_run``:

- ``_finalize_metrics(result, messages, t0)``
- ``_run_claim_validation(result, messages)``
- ``after_run`` hook (normal-end path)

Note: the no-progress early-return path in ``_run_loop_core`` fires
``after_run`` itself (it returns before reaching FinalizationStep) —
that call stays in the skeleton so the hook isn't lost.
"""

from __future__ import annotations

from typing import Any

from ..loop_context import LoopContext


class DefaultFinalizationStep:
    """FinalizationStep that runs metrics + claim validation + after_run."""

    def __init__(self) -> None:
        self._loop: Any = None

    def bind_agent_loop(self, agent_loop: Any) -> None:
        self._loop = agent_loop

    @property
    def name(self) -> str:
        return "finalization"

    async def execute(self, ctx: LoopContext, *, async_mode: bool) -> LoopContext:
        loop = self._loop
        if loop is None:
            return ctx

        loop._finalize_metrics(ctx.result, ctx.messages, ctx.t0)
        loop._run_claim_validation(ctx.result, ctx.messages)

        # after_run hook (normal end).
        hook_ctx = ctx.hook_ctx
        if hook_ctx is None:
            hook_ctx = loop._build_hook_context(
                ctx.iteration, ctx.messages,
            )
        await loop._afire_hooks("after_run", hook_ctx, ctx.result)
        return ctx


__all__ = ["DefaultFinalizationStep"]
