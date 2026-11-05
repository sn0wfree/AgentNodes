"""Default CompactionStep (L7 v0.5).

Drives context compression at the start of each iteration. Wraps the
existing ``_maybe_compact`` / ``_amaybe_compact`` methods (which
themselves delegate to the compaction engine in ``compact.py``).

After compaction, emits ``_emit_compaction`` / ``_emit_iter_start``.
Per-iteration context injection (todos, goal continuation, etc.) is
handled by the ``ContextInjector`` chain in ``AgentLoop._injectors``.
"""

from __future__ import annotations

from typing import Any

from ..loop_context import LoopContext


class DefaultCompactionStep:
    """Context compression + per-iteration observability."""

    def __init__(self) -> None:
        self._loop: Any = None

    def bind_agent_loop(self, agent_loop: Any) -> None:
        self._loop = agent_loop

    @property
    def name(self) -> str:
        return "compaction"

    def should_run(self, ctx: LoopContext) -> bool:
        # Always run (compaction checks threshold internally).
        return True

    async def execute(self, ctx: LoopContext, *, async_mode: bool) -> LoopContext:
        loop = self._loop
        if loop is None:
            return ctx

        # Delegate to the compaction engine (sync / async twin).
        if async_mode:
            messages, applied = await loop._amaybe_compact(ctx.messages)
        else:
            messages, applied = loop._maybe_compact(ctx.messages)

        if applied:
            loop._emit_compaction(applied, ctx.iteration, ctx.result)
        loop._emit_iter_start(ctx.iteration, messages)

        ctx.messages = messages
        ctx.metadata["compaction_applied"] = applied
        return ctx


__all__ = ["DefaultCompactionStep"]
