"""骨架与 AgentLoop 的绑定层（批次 11 · B3 阶段 1）。

把 AgentLoop 的既有能力（strategy steps / 预算 / trace / git）包装成
骨架消费的 handler/hook——**沿用 ``bind_agent_loop`` 既有协议**，
不复制任何逻辑。

**async-first**：handlers 是 ``async def handle``（StepHandler 协议允许
返回 Awaitable），内部直接 ``await loop._call_step``——与生产路径的
``arun`` 一致。等价性证明见 ``tests/test_skeleton_equivalence.py``。
"""

from __future__ import annotations

import logging
from typing import Any

from ._core_types import StepHandler, StepResult
from .loop import _make_strategy_ctx
from .strategy.loop_context import LoopContext

logger = logging.getLogger(__name__)

__all__ = ["LoopActor", "LoopFinalize", "LoopHooks", "LoopPrepare", "LoopReasoner"]


class LoopReasoner(StepHandler):
    """REASON 位：compaction + inbox drain + injectors(0) + LLM 调用。

    复用 ``strategy.compaction`` 与 ``strategy.llm_call`` 两个 step
    （经 ``_call_step`` 的既有隔离）。产出 = LLM response
    （``None`` = 终止信号，交给 StopSignalDecider / 骨架）。
    """

    def __init__(self, loop: Any) -> None:
        self._loop = loop

    async def handle(self, input: Any, ctx: LoopContext) -> StepResult:
        loop = self._loop

        # compaction step（含 _emit_compaction / _emit_iter_start）
        comp_ctx = _make_strategy_ctx(loop,
            ctx.messages, None, ctx.result, ctx.iteration, ctx.hook_ctx,
        )
        comp_ctx = await loop._call_step(
            loop._strategy.compaction, comp_ctx, async_mode=True,
        )
        ctx.messages = comp_ctx.messages

        # inbox drain + per-iteration injectors（loop.py:855-863 等价）
        loop._drain_inbox(ctx.messages)
        for injector in getattr(loop, "_injectors", []):
            if getattr(injector, "order", 0) == 0:
                try:
                    injector.inject_per_iteration(loop, ctx.messages)
                except Exception:  # noqa: BLE001
                    pass  # injectors must not break the loop

        # LLM call step（内部发 before_iteration / on_error）
        llm_ctx = _make_strategy_ctx(loop,
            ctx.messages, None, ctx.result, ctx.iteration, ctx.hook_ctx,
        )
        llm_ctx = await loop._call_step(
            loop._strategy.llm_call, llm_ctx, async_mode=True,
        )

        # 状态回写（骨架的 ctx 就是 LoopContext，steps 已变异它）
        ctx.response = llm_ctx.response
        if llm_ctx.should_stop:
            ctx.should_stop = True
            ctx.stop_reason = llm_ctx.stop_reason
        if llm_ctx.response is None:
            ctx.should_stop = True
            ctx.stop_reason = ctx.stop_reason or "llm_no_response"
        return StepResult.ok(llm_ctx.response)


class LoopActor(StepHandler):
    """ACT 位：工具执行（复用 ``strategy.tool_execution``）。"""

    def __init__(self, loop: Any) -> None:
        self._loop = loop

    async def handle(self, input: Any, ctx: LoopContext) -> StepResult:
        loop = self._loop
        tool_ctx = _make_strategy_ctx(loop,
            ctx.messages, input, ctx.result, ctx.iteration, ctx.hook_ctx,
        )
        tool_ctx = await loop._call_step(
            loop._strategy.tool_execution, tool_ctx, async_mode=True,
        )
        ctx.messages = tool_ctx.messages
        if tool_ctx.should_stop:
            ctx.should_stop = True
            ctx.stop_reason = tool_ctx.stop_reason
        return StepResult.ok({
            "tool_hashes": tool_ctx.metadata.get("tool_hashes") or [],
        })


class LoopPrepare:
    """运行准备：``_prepare_run`` + 首个 LoopContext + hook_ctx。"""

    def __init__(self, loop: Any) -> None:
        self._loop = loop

    def __call__(self, spec: Any) -> tuple[LoopContext, Any]:
        loop = self._loop
        full_task, result, messages, t0 = loop._prepare_run(
            spec.task, spec.context, spec.history,
        )
        hook_ctx = loop._build_hook_context(0, messages)
        ctx = LoopContext(
            task=full_task,
            context=spec.context,
            history=spec.history,
            messages=messages,
            t0=t0,
        )
        ctx.result = result
        ctx.hook_ctx = hook_ctx
        return ctx, result


class LoopHooks:
    """生命周期事件转发到 ``_afire_hooks``（签名对齐骨架的 _fire）。"""

    def __init__(self, loop: Any) -> None:
        self._loop = loop

    async def before_run(self, spec: Any) -> None:
        pass  # 首轮 before_iteration 已覆盖（对齐 legacy :809 只发一次）

    async def before_iteration(self, ctx: LoopContext, iteration: int) -> None:
        pass  # llm_call step 内部已发 before_iteration（loop.py:866-868）

    async def after_iteration(self, ctx: LoopContext, iteration: int) -> None:
        hook_ctx = getattr(ctx, "hook_ctx", None)
        if hook_ctx is not None:
            await self._loop._afire_hooks("after_iteration", hook_ctx)

    async def after_run(self, ctx: Any, result: Any) -> None:
        hook_ctx = getattr(ctx, "hook_ctx", None)
        if hook_ctx is None:
            hook_ctx = self._loop._build_hook_context(
                getattr(ctx, "iteration", 0), getattr(ctx, "messages", []),
            )
        await self._loop._afire_hooks("after_run", hook_ctx, result)

    async def on_error(self, ctx: Any, error: Any) -> None:
        logger.warning("skeleton on_error: %s", error)


class LoopFinalize:
    """终局：finalization step + git commit + trace close。"""

    def __init__(self, loop: Any) -> None:
        self._loop = loop

    async def __call__(self, ctx: LoopContext, result: Any) -> None:
        loop = self._loop
        fin_ctx = _make_strategy_ctx(loop,
            ctx.messages, None, result, result.iterations,
            getattr(ctx, "hook_ctx", None),
        )
        fin_ctx.t0 = getattr(ctx, "t0", 0.0)
        await loop._call_step(
            loop._strategy.finalization, fin_ctx, async_mode=True,
        )
        # git commit + trace close（阶段 1 直调，阶段 2 收编为 hook）
        import asyncio as _aio

        task = getattr(ctx, "task", "")
        await _aio.to_thread(loop._git_commit, task, result)
        if loop._trace_writer is not None:
            result.trace_path = str(loop._trace_writer.path)
            loop._trace_writer.close()
