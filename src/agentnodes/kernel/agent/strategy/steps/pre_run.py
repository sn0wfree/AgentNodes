"""Default PreRunStep（B2-① T6 已迁 handle 协议 **且已接线**）.

Bound to an AgentLoop via ``bind_agent_loop`` — ``handle`` delegates to
``agent_loop._prepare_run`` so the strategy step has the same effect
as the legacy hard-coded pre-run block.

**B2-① T6 接线**（step-protocol-migration-20261112.md §3.6）：

本 step 此前是**死代码位**——``LoopPrepare`` 直调 ``loop._prepare_run``，
从不走 strategy 槽位。现在 ``LoopPrepare`` 改为委托本 step：

- 引擎路径：``LoopPrepare`` → ``strategy.pre_run.handle`` → ``_prepare_run``
- legacy 路径：``_run_loop_core`` 内联 ``_prepare_run``（U6-2 随 legacy 删除）

两条路径各自单跑、互斥（**不会双跑**）——原 docstring 的 ⚠ 互斥约定
按路径成立，见 §3.6 的 grep 网（``_prepare_run(`` 代码调用恰 3 处：
``loop.py`` 2 + 本文件 1；``adapters`` 归零）。

- **失败契约：保持崩溃**。``_prepare_run`` 异常经 handle 直接向上抛，
  **不经** ``_call_step``（其 catch 会把异常变成 fail-result——那是
  循环体内步骤的语义，prepare 在循环体外，没有 result 对象可降级）。
- **产物走 output**：``{"full_task", "result", "messages", "t0"}``；
  ctx 同步写入（step 自治：prepare 完成 ctx 即就绪，含 ``ctx.task``）。
"""

from __future__ import annotations

from typing import Any

from agentnodes.kernel.agent._core_types import StepHandler, StepResult
from agentnodes.kernel.agent.strategy.loop_context import LoopContext


class DefaultPreRunStep(StepHandler):
    """PreRunStep that delegates to ``agent_loop._prepare_run``."""

    def __init__(self) -> None:
        self._loop: Any = None

    def bind_agent_loop(self, agent_loop: Any) -> None:
        """L7 wiring — AgentLoop injects itself after strategy creation."""
        self._loop = agent_loop

    @property
    def name(self) -> str:
        return "pre_run"

    def handle(self, input: Any, ctx: LoopContext) -> StepResult:
        """同步实现——``_prepare_run`` 是同步的，异常直接向上抛。

        ``handle`` 允许返回裸 ``StepResult``（``_call_step`` 的 duck-type
        分派经 ``maybe_await`` 统一处理 awaitable / 裸返回）。
        """
        loop = self._loop
        if loop is None:
            return StepResult.ok({
                "full_task": ctx.task, "result": None,
                "messages": list(ctx.messages), "t0": ctx.t0,
            })

        full_task, result, messages, t0 = loop._prepare_run(
            ctx.task, ctx.context, ctx.history,
        )
        # step 自治：prepare 完成 ctx 即就绪——ctx.task 换成带前缀的
        # full_task（下游 git_commit / guardrail 读它）。
        ctx.task = full_task
        ctx.messages = list(messages)
        ctx.result = result
        ctx.iteration = 0
        ctx.t0 = t0
        ctx.metadata["full_task"] = full_task
        return StepResult.ok({
            "full_task": full_task,
            "result": result,
            "messages": list(messages),
            "t0": t0,
        })


__all__ = ["DefaultPreRunStep"]
