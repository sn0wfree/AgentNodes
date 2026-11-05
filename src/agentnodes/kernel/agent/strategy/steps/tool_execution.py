"""Default ToolExecutionStep (L7 v0.3).

Actually runs the assistant's tool calls (via AgentLoop's batch
methods) and fires the tool lifecycle hooks itself — this is the
"Step self-triggers hooks" pattern (v0.3 scope, framework decision #1):

- ``before_execute_tools`` fires before dispatching.
- ``on_tool_error`` / ``after_tool_executed`` fire per tool result.

The legacy ``_run_loop_core`` retains the ``after_iteration`` hook
(cross-step, round-level) but removes the tool-specific hooks here —
they now live in this Step so a custom ToolExecutionStep can vary
when/if they fire.
"""

from __future__ import annotations

from typing import Any

from ..loop_context import LoopContext


class DefaultToolExecutionStep:
    """Runs tools and fires tool lifecycle hooks."""

    def __init__(self) -> None:
        self._loop: Any = None

    def bind_agent_loop(self, agent_loop: Any) -> None:
        self._loop = agent_loop

    @property
    def name(self) -> str:
        return "tool_execution"

    async def execute(self, ctx: LoopContext, *, async_mode: bool) -> LoopContext:
        loop = self._loop
        if loop is None:
            return ctx

        tool_calls = getattr(ctx.response, "tool_calls", None)
        if not tool_calls:
            return ctx

        # Hook: before_execute_tools
        await loop._afire_hooks("before_execute_tools", ctx.hook_ctx)

        # Dispatch (sync/async twin).
        if async_mode:
            tool_result_msgs = await loop._aexecute_tool_batch(tool_calls, ctx.result)
        else:
            tool_result_msgs = loop._execute_tool_batch(tool_calls, ctx.result)

        # Per-tool hooks (mirror legacy _fire_tool_result_hooks).
        for tc, msg in zip(tool_calls, tool_result_msgs):
            content = msg.get("content", "")
            if isinstance(content, str) and content.startswith('{"status": "error"'):
                await loop._afire_hooks(
                    "on_tool_error", ctx.hook_ctx, tc, RuntimeError(content),
                )
            else:
                await loop._afire_hooks(
                    "after_tool_executed", ctx.hook_ctx, tc, msg,
                )

        # Collect hashes + append results (mirror legacy block).
        ctx.metadata["tool_hashes"] = loop._collect_tool_hashes(
            tool_calls, tool_result_msgs,
        )
        loop._append_tool_results(
            tool_calls, tool_result_msgs, ctx.messages, ctx.result,
        )
        ctx.metadata["tool_result_msgs"] = list(tool_result_msgs)
        return ctx


__all__ = ["DefaultToolExecutionStep"]
