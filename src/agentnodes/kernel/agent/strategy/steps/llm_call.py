"""Default LLMCallStep（B2-① T3 已迁 handle 协议）.

Actually drives the LLM call and fires the LLM-lifecycle hooks itself:

- ``before_iteration`` fires before calling ``_get_response``.
- ``on_error`` fires after ``_handle_llm_error`` on failure.

**B2-① 变化**（step-protocol-migration-20261112.md §3.2）：

- ``execute(ctx, *, async_mode)`` → ``handle(input, ctx) → StepResult``
- ``_get_response`` 改传字面量 ``True``——U5.5 后 ``async_mode=False``
  生产不可达（§1.3）；``_get_response`` 的形参保留至 U6-2 随 legacy 删除
- 产物走 ``StepResult.output = LLMResponse | None``（None = 空响应，
  **非失败**）；LLM 业务异常 → ``StepResult.fail("error")`` 且**仍调**
  ``_handle_llm_error`` + ``on_error`` hook——result 记账是既有可观测契约
- ctx 写入保留（追加态留 ctx，§2.1 公理）：response / response_content /
  response_was_tool_call / ctx_hash_id 同步 + ``_append_assistant_msg``
- 业务停止信号保留在 ctx（``should_stop``/``stop_reason`` =
  ``"error"``/``"llm_none"``）——StopSignalDecider 通道，B2-② 才收编
"""

from __future__ import annotations

import logging
from typing import Any

from ..._core_types import StepHandler, StepResult
from ..loop_context import LoopContext

logger = logging.getLogger(__name__)


class DefaultLLMCallStep(StepHandler):
    """LLMCallStep that delegates to ``agent_loop._get_response``."""

    def __init__(self) -> None:
        self._loop: Any = None

    def bind_agent_loop(self, agent_loop: Any) -> None:
        self._loop = agent_loop

    @property
    def name(self) -> str:
        return "llm_call"

    async def handle(self, input: Any, ctx: LoopContext) -> StepResult:
        loop = self._loop
        if loop is None:
            return StepResult.ok(None)

        # Ensure hook_ctx is available (fall back to building one).
        hook_ctx = ctx.hook_ctx
        if hook_ctx is None:
            hook_ctx = loop._build_hook_context(ctx.iteration, ctx.messages)

        # before_iteration hook (LLM call starts).
        await loop._afire_hooks("before_iteration", hook_ctx)

        try:
            # async_mode 字面量 True——U5.5 后 False 生产不可达（§1.3）
            response = await loop._get_response(
                ctx.messages, ctx.iteration, True, hook_ctx, ctx.result,
            )
        except Exception as exc:  # noqa: BLE001 — LLMError included
            # Preserve the legacy error-handling path: record error on
            # the result, fire on_error, set should_stop so the chain
            # breaks.
            loop._handle_llm_error(exc, ctx.iteration, ctx.result)
            await loop._afire_hooks("on_error", hook_ctx, exc)
            ctx.should_stop = True
            ctx.stop_reason = "error"
            return StepResult.fail("error")
        if response is None:
            ctx.should_stop = True
            ctx.stop_reason = "llm_none"
            return StepResult.ok(None)

        ctx.response = response
        ctx.response_was_tool_call = bool(getattr(response, "tool_calls", None))
        ctx.response_content = getattr(response, "content", "") or ""
        # 上下文指纹（统一引擎 U6）：`_get_response` 内部已算好并挂在
        # loop._current_ctx_hash_id（每轮更新）——同步到 ctx 与 result，
        # 使二者始终反映**当前状态**。
        _ctx_hash_id = getattr(loop, "_current_ctx_hash_id", "") or ""
        if _ctx_hash_id:
            ctx.ctx_hash_id = _ctx_hash_id
            if ctx.result is not None:
                try:
                    ctx.result.ctx_hash_id = _ctx_hash_id
                except Exception:  # noqa: BLE001 — 冻结 result 不阻断
                    pass
        # Mirror _append_assistant_msg so messages carry the response.
        #
        # **产物有效 ≠ 成功**（B2-① T3 实测）：_append_assistant_msg 在
        # 构造 assistant msg 时访问 ``response.finish_reason``——替身响应
        # 缺该字段时抛 AttributeError。此时 **LLM 调用已成功**（response
        # 有效），崩的只是"消息记录"这一步。旧实现让异常冒到 _call_step，
        # 而 response 早已写进 ctx——调用点从 ctx 读到有效响应，TextFinal
        # 判终答，答案保住。
        #
        # 新协议下 output 是权威通道：若 fail 时不带 output，产物就丢了
        # （引擎拿到 None → "无响应" → 答案消失）。故此处**带产物 fail**：
        # output=response（有效），success=False（收尾失败）。
        try:
            loop._append_assistant_msg(
                response, ctx.messages, ctx.result, ctx.iteration,
            )
        except Exception as exc:  # noqa: BLE001 — 记录失败但保住产物
            logger.warning("llm_call: append assistant message failed: %s", exc)
            return StepResult(
                output=response, success=False,
                error=f"step_llm_call: {exc}",
            )
        return StepResult.ok(response)


__all__ = ["DefaultLLMCallStep"]
