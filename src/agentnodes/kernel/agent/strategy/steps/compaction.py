"""Default CompactionStep（B2-① T2 已迁 handle 协议）.

Drives context compression at the start of each iteration. Wraps the
async compaction entry ``_amaybe_compact``（内部 ``to_thread`` 跑同步核心，
并持有 per-session 压缩锁）.

After compaction, emits ``_emit_compaction`` / ``_emit_iter_start``.
Per-iteration context injection (todos, goal continuation, etc.) is
handled by the ``ContextInjector`` chain in ``AgentLoop._injectors``.

**B2-① 变化**（step-protocol-migration-20261112.md §3.1）：

- ``execute(ctx, *, async_mode)`` → ``handle(input, ctx) → StepResult``
- 恒走 async 孪生——U5.5 统一入口后同步分支生产不可达（§1.3），已删除
- 产物走 ``StepResult.output = {"messages", "applied"}``——**替换态不再写
  ``ctx.messages``**（由调用方写回；追加态才留 ctx，见 §2.1 公理）
- ``metadata["compaction_applied"]`` 双写保留（兼容既有读取方）
"""

from __future__ import annotations

from typing import Any

from ..._core_types import StepHandler, StepResult
from ..loop_context import LoopContext


class DefaultCompactionStep(StepHandler):
    """Context compression + per-iteration observability."""

    def __init__(self) -> None:
        self._loop: Any = None

    def bind_agent_loop(self, agent_loop: Any) -> None:
        self._loop = agent_loop

    @property
    def name(self) -> str:
        return "compaction"

    async def handle(self, input: Any, ctx: LoopContext) -> StepResult:
        loop = self._loop
        if loop is None:
            return StepResult.ok({"messages": list(ctx.messages), "applied": []})

        # 恒走 async 孪生（U5.5 后同步分支生产不可达，§1.3）
        messages, applied = await loop._amaybe_compact(ctx.messages)

        if applied:
            loop._emit_compaction(applied, ctx.iteration, ctx.result)
        loop._emit_iter_start(ctx.iteration, messages)

        ctx.metadata["compaction_applied"] = applied
        return StepResult.ok({"messages": messages, "applied": applied})


__all__ = ["DefaultCompactionStep"]
