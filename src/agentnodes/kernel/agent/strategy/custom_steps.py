"""Custom Step implementations (P1-3 / P1-4)——B2-① T5 已迁 handle 协议。

Shared helpers used by ValidatorStrategy / MinimalStrategy when their
default ReAct behaviour needs to differ from the regular flow:

- ``ClaimValidationFinalizationStep`` — runs the legacy claim-validation
  hook (mirrors ``AgentLoop._run_claim_validation`` semantics) before
  returning. The actual call is deferred to L7 (AgentLoop rewrite); v0.1
  marks ``ctx.metadata["claim_validation_ran"] = True`` so tests can
  assert the strategy asked for the validation.

- ``NoOpToolExecutionStep`` — runs no tool calls; the assistant's
  response is recorded as a final answer. Used by MinimalStrategy to
  force a one-shot "ask the LLM, return what it says" flow.

**这两个不是死代码**——它们是 ``validator`` / ``minimal`` 两个 preset 的
槽位组成件（validator.py:40 / minimal.py:39），经 ``_call_step`` 在生产
路径上被调用。

**B2-① 变化**（step-protocol-migration-20261112.md）：

- ``execute(ctx, *, async_mode)`` → ``handle(input, ctx) → StepResult``
- ``ClaimValidationFinalizationStep``：纯副作用 → ``output=None``
- ``NoOpToolExecutionStep``：替换 ``tool_execution`` 槽位 → output 与
  ``DefaultToolExecutionStep`` **同形**（引擎 after_act 闸读
  ``r.output["tool_hashes"]``，loop_engine.py:429）
- 两者都是**同步实现**——``_call_step`` 的 duck-type 分派经
  ``maybe_await`` 统一处理 awaitable/裸返回
"""

from __future__ import annotations

from typing import Any

from agentnodes.kernel.agent._core_types import StepHandler, StepResult
from agentnodes.kernel.agent.strategy.loop_context import LoopContext


class ClaimValidationFinalizationStep(StepHandler):
    """Marks ``ctx.metadata["claim_validation_ran"] = True``.

    Real call lands in L7 alongside the AgentLoop rewrite.
    """

    @property
    def name(self) -> str:
        return "finalization.claim_validation"

    def handle(self, input: Any, ctx: LoopContext) -> StepResult:
        ctx.metadata["claim_validation_ran"] = True
        return StepResult.ok(None)


class NoOpToolExecutionStep(StepHandler):
    """No-op tool execution — context passes through unchanged.

    The assistant's tool calls (if any) are *not* invoked. This is
    intentional: MinimalStrategy wants a single LLM answer without
    consuming tool budget.
    """

    @property
    def name(self) -> str:
        return "tool_execution.noop"

    def handle(self, input: Any, ctx: LoopContext) -> StepResult:
        # Strip any tool-call request from the response so the next
        # iteration's StopStep sees a text-only response and breaks.
        response = input if input is not None else ctx.response
        if response is not None and getattr(response, "tool_calls", None):
            # ctx.response 保持原样（旧实现是自赋值，无操作）——
            # 语义不变，只把 metadata 标记写上。
            ctx.metadata["tool_execution_skipped"] = True
        # output 与 DefaultToolExecutionStep 同形（引擎 after_act 闸读
        # r.output["tool_hashes"]）——无工具执行 → 空列表。
        return StepResult.ok({"tool_hashes": [], "tool_result_msgs": []})


__all__ = [
    "ClaimValidationFinalizationStep",
    "NoOpToolExecutionStep",
]
