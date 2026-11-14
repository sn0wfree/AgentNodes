"""Default ToolExecutionStep（B2-① T4 已迁 handle 协议）.

Actually runs the assistant's tool calls (via AgentLoop's async batch
method) and fires the tool lifecycle hooks itself — this is the
"Step self-triggers hooks" pattern (v0.3 scope, framework decision #1):

- ``before_execute_tools`` fires before dispatching.
- ``on_tool_error`` / ``after_tool_executed`` fire per tool result.

The round-level ``after_iteration`` hook lives outside this Step
(engine-side, cross-step); the tool-specific hooks live here so a
custom ToolExecutionStep can vary when/if they fire.

**B2-① 变化**（step-protocol-migration-20261112.md §3.3）：

- ``execute(ctx, *, async_mode)`` → ``handle(input, ctx) → StepResult``
- ``input`` = LLMResponse（工具调用来源）；``ctx.response`` 兜底
- 恒走 async 孪生 ``_aexecute_tool_batch``——U5.5 后同步分支生产不可达（§1.3）
- 产物走 ``StepResult.output = {"tool_hashes", "tool_result_msgs"}``；
  ``metadata["tool_hashes"]`` / ``metadata["tool_result_msgs"]`` 双写保留
  （legacy 读 metadata；``tool_result_msgs`` 实为只写不读的死变量，见 §1.6，
  归 U6-2）
- **产物有效 ≠ 成功**（T3 教训）：``_append_tool_results`` 失败时 fail
  **带产物**——工具已执行完、hashes 已算出，不能因记录失败丢产物
"""

from __future__ import annotations

import logging
from typing import Any

from ..._core_types import StepHandler, StepResult
from ..loop_context import LoopContext

logger = logging.getLogger(__name__)


class DefaultToolExecutionStep(StepHandler):
    """Runs tools and fires tool lifecycle hooks."""

    def __init__(self) -> None:
        self._loop: Any = None

    def bind_agent_loop(self, agent_loop: Any) -> None:
        self._loop = agent_loop

    @property
    def name(self) -> str:
        return "tool_execution"

    async def handle(self, input: Any, ctx: LoopContext) -> StepResult:
        loop = self._loop
        if loop is None:
            return StepResult.ok({"tool_hashes": [], "tool_result_msgs": []})

        response = input if input is not None else ctx.response
        tool_calls = getattr(response, "tool_calls", None)
        if not tool_calls:
            return StepResult.ok({"tool_hashes": [], "tool_result_msgs": []})

        # Hook: before_execute_tools
        await loop._afire_hooks("before_execute_tools", ctx.hook_ctx)

        # 恒走 async 孪生（U5.5 后同步分支生产不可达，§1.3）
        tool_result_msgs = await loop._aexecute_tool_batch(tool_calls, ctx.result)

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
        tool_hashes = loop._collect_tool_hashes(tool_calls, tool_result_msgs)
        ctx.metadata["tool_hashes"] = tool_hashes
        ctx.metadata["tool_result_msgs"] = list(tool_result_msgs)
        output = {
            "tool_hashes": tool_hashes,
            "tool_result_msgs": list(tool_result_msgs),
        }
        try:
            loop._append_tool_results(
                tool_calls, tool_result_msgs, ctx.messages, ctx.result,
            )
        except Exception as exc:  # noqa: BLE001 — 工具已执行，产物必须保留
            logger.warning("tool_execution: append tool results failed: %s", exc)
            return StepResult(
                output=output, success=False,
                error=f"step_tool_execution: {exc}",
            )
        return StepResult.ok(output)


__all__ = ["DefaultToolExecutionStep"]
