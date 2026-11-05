"""Default LLMCallStep (L7 v0.4).

Actually drives the LLM call and fires the LLM-lifecycle hooks itself:

- ``before_iteration`` fires before calling ``_get_response``.
- ``on_error`` fires after ``_handle_llm_error`` on failure.

The legacy ``_run_loop_core`` previously fired these; v0.4 moves them
into this Step (the "Step self-triggers hooks" pattern), so a custom
LLMCallStep can vary when/if they fire.

``_get_response`` is ``async def`` — ``execute`` is now ``async`` and
awaits it. ``_call_step`` (the loop's executor) already handles
awaitables, so no caller change is needed.
"""

from __future__ import annotations

from typing import Any

from ..loop_context import LoopContext


class DefaultLLMCallStep:
    """LLMCallStep that delegates to ``agent_loop._get_response``."""

    def __init__(self) -> None:
        self._loop: Any = None

    def bind_agent_loop(self, agent_loop: Any) -> None:
        self._loop = agent_loop

    @property
    def name(self) -> str:
        return "llm_call"

    def should_run(self, ctx: LoopContext) -> bool:
        return True

    async def execute(self, ctx: LoopContext, *, async_mode: bool) -> LoopContext:
        loop = self._loop
        if loop is None:
            return ctx

        # Ensure hook_ctx is available (fall back to building one).
        hook_ctx = ctx.hook_ctx
        if hook_ctx is None:
            hook_ctx = loop._build_hook_context(ctx.iteration, ctx.messages)

        # before_iteration hook (LLM call starts).
        await loop._afire_hooks("before_iteration", hook_ctx)

        try:
            response = await loop._get_response(
                ctx.messages, ctx.iteration, async_mode, hook_ctx, ctx.result,
            )
        except Exception as exc:  # noqa: BLE001 — LLMError included
            # Preserve the legacy error-handling path: record error on
            # the result, fire on_error, set should_stop so the chain
            # breaks.
            loop._handle_llm_error(exc, ctx.iteration, ctx.result)
            await loop._afire_hooks("on_error", hook_ctx, exc)
            ctx.should_stop = True
            ctx.stop_reason = "error"
            return ctx
        if response is None:
            ctx.should_stop = True
            ctx.stop_reason = "llm_none"
            return ctx

        ctx.response = response
        ctx.response_was_tool_call = bool(getattr(response, "tool_calls", None))
        ctx.response_content = getattr(response, "content", "") or ""
        # Mirror _append_assistant_msg so messages carry the response.
        loop._append_assistant_msg(
            response, ctx.messages, ctx.result, ctx.iteration,
        )
        return ctx


__all__ = ["DefaultLLMCallStep"]
