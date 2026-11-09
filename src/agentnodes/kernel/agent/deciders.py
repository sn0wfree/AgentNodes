"""Deciders — 停止/继续判定的可配置对象（批次 10 · B2）。

**包装而非重编码**（批次 10 设计 §1）：每个默认 decider 持有现有
strategy step 的引用，`handle` 调用其 `evaluate`/`is_open`/
`is_no_progress` 并把结论翻译为四值 `Decision`——等价性由**构造**
保证（同一函数被调用），映射表测试（``tests/test_deciders.py``）
只需验证形状适配，不需验证逻辑复刻。

## Decision 四值（二值装不下的实测证据）

现有循环的检查点结论有四种（loop.py:884-998）：

- `PROCEED`——正常进入下一步骤（ACT 或 OBSERVE）
- `CONTINUE`——跳过剩余步骤直接下一轮（goal 续跑 :917 / 熔断开 :942）
- `STOP`——结束循环走 finalize（文本终答 :930 / 预算 :896 /
  工具失败 :956 / 上限 :998）
- `EXIT`——结束整个 run，自带 after_run（no-progress 拒绝 :996
  的 ``return result``）

## 与骨架的关系

B3 的 skeleton 在三个闸位（precheck / after_reason / after_act）按序
调用 deciders，任一非 `PROCEED` 即按 `Decision` 分派。当前旧循环体
**不消费**本模块（接线随 B3 重写循环体一并完成——在旧体上接线是
一次性代码）。
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from ._core_types import StepHandler, StepResult
from .strategy.loop_context import LoopContext

__all__ = [
    "BudgetDecider",
    "CircuitBreakerDecider",
    "Decision",
    "Decider",
    "GoalContinuationDecider",
    "MaxIterationsDecider",
    "NoProgressDecider",
    "StopSignalDecider",
    "TextFinalDecider",
]


class Decision(str, Enum):
    """检查点结论（批次 10 设计 §2——四值，二值装不下）。"""

    PROCEED = "proceed"    # 进入下一步骤（ACT 或 OBSERVE）
    CONTINUE = "continue"  # 跳过剩余步骤，直接下一轮迭代
    STOP = "stop"          # 结束循环，走 finalize
    EXIT = "exit"          # 结束整个 run（自带 after_run，跳过主 finalize）


class Decider(StepHandler):
    """停止/继续判定器。

    ``handle(input, ctx)`` 的 ``input`` 是检查点上下文载荷
    （如 LLM response、tool hashes），返回
    ``StepResult{output=(Decision, reason)}``。

    **子类契约**：只读判定，不得变异 ``ctx``——副作用（预算记账、
    hash 窗口维护、事件发射）仍属现有步骤/循环，B3 随骨架收编。
    """

    @property
    def name(self) -> str:  # pragma: no cover - 子类覆盖
        return type(self).__name__

    def handle(self, input: Any, ctx: Any) -> StepResult:
        decision, reason = self.decide(input, ctx)
        return StepResult.ok((decision, reason))

    def decide(
        self, input: Any, ctx: Any
    ) -> tuple[Decision, str | None]:  # pragma: no cover - 子类覆盖
        raise NotImplementedError


class StopSignalDecider(Decider):
    """外部 stop 信号（LLM 错误 / 工具失败经 ``ctx.should_stop`` 置位）。

    对应 loop.py:875（``llm_ctx.should_stop`` → break）与 :954
    （``tool_ctx.should_stop`` → break）。reason 透传置位方给的。
    """

    def __init__(self, *, name: str = "stop_signal") -> None:
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    def decide(self, input: Any, ctx: LoopContext) -> tuple[Decision, str | None]:
        if getattr(ctx, "should_stop", False):
            return Decision.STOP, getattr(ctx, "stop_reason", None) or "stop_signal"
        return Decision.PROCEED, None


class BudgetDecider(Decider):
    """预算耗尽（包装 ``BudgetStopStep.evaluate``——tokens/cost/time 三维）。"""

    def __init__(self, stop_step: Any) -> None:
        self._stop_step = stop_step

    @property
    def name(self) -> str:
        return "budget"

    def decide(self, input: Any, ctx: LoopContext) -> tuple[Decision, str | None]:
        exceeded, reason = self._stop_step.evaluate(ctx)
        if exceeded:
            return Decision.STOP, reason
        return Decision.PROCEED, None


class MaxIterationsDecider(Decider):
    """迭代上限（对应 for-else → ``_handle_max_iter``，loop.py:997-998）。"""

    def __init__(self, max_iterations: int) -> None:
        self._max = max_iterations

    @property
    def name(self) -> str:
        return "max_iterations"

    def decide(self, input: Any, ctx: LoopContext) -> tuple[Decision, str | None]:
        iteration = getattr(ctx, "iteration", 0) or 0
        # legacy 语义（for-else）：跑完第 N 轮**之后**才停——
        # `>` 而非 `>=`（第 N 轮仍执行，PRECHECK 在 N+1 轮开头拦）。
        if iteration > self._max:
            return Decision.STOP, "max_iterations"
        return Decision.PROCEED, None


class TextFinalDecider(Decider):
    """文本终答（无 tool_calls 即停）。

    对应 loop.py:898 的内联 ``if not response.has_tool_calls()``——
    该判定原本没有 step 可包（5 行内联），这是唯一一处新实现；
    映射表测试用同一 ``has_tool_calls`` 桩对齐。
    """

    @property
    def name(self) -> str:
        return "text_final"

    def decide(self, input: Any, ctx: LoopContext) -> tuple[Decision, str | None]:
        response = input if input is not None else getattr(ctx, "response", None)
        if response is not None and not response.has_tool_calls():
            return Decision.STOP, "final_text"
        return Decision.PROCEED, None


class GoalContinuationDecider(Decider):
    """goal 续跑（包装 ``strategy.continuation.evaluate``）。

    True = goal 有未覆盖判据 → 注入续跑提示、跳过本轮剩余步骤。
    """

    def __init__(self, continuation_step: Any) -> None:
        self._step = continuation_step

    @property
    def name(self) -> str:
        return "goal_continuation"

    def decide(self, input: Any, ctx: LoopContext) -> tuple[Decision, str | None]:
        should_continue, reason = self._step.evaluate(ctx)
        if should_continue:
            return Decision.CONTINUE, reason
        return Decision.PROCEED, None


class CircuitBreakerDecider(Decider):
    """熔断闸（包装 ``strategy.resilience.is_open``）。

    **检查点 = pre_act**（工具执行**前**，loop.py:936）：开 → 追加错误
    工具结果、跳过本轮 ACT 进下一轮。这是对评估文档
    「after_act」命名的修正（批次 10 设计 §3）。
    """

    def __init__(self, resilience_step: Any) -> None:
        self._step = resilience_step

    @property
    def name(self) -> str:
        return "circuit_breaker"

    def decide(self, input: Any, ctx: LoopContext) -> tuple[Decision, str | None]:
        if self._step.is_open(ctx):
            return Decision.CONTINUE, "circuit_breaker_open"
        return Decision.PROCEED, None


class NoProgressDecider(Decider):
    """no-progress（包装 ``strategy.progress.record_hash + is_no_progress``）。

    **检查点 = after_act**（工具结果已入消息，loop.py:971-996）。
    触发 → ``_check_no_progress`` 询问用户：拒绝/超时 → **EXIT 整个
    run**（return result，自带 after_run）；批准 → PROCEED 续跑。
    ``input`` = 本轮 tool hashes 列表。
    """

    def __init__(self, progress_step: Any, *, approved_continuation: bool = False) -> None:
        self._step = progress_step
        # 批准续跑由用户交互决定；decider 只表达「检测到无进展」半边，
        # 交互半边归骨架的 ``_check_no_progress``（B3 收编）。
        self._approved = approved_continuation

    @property
    def name(self) -> str:
        return "no_progress"

    def record(self, ctx: LoopContext, hash_value: str) -> None:
        """透传 hash 记录（窗口维护仍是 step 的职责）。"""
        self._step.record_hash(ctx, hash_value)

    def decide(self, input: Any, ctx: LoopContext) -> tuple[Decision, str | None]:
        if self._step.is_no_progress(ctx):
            if self._approved:
                return Decision.PROCEED, "no_progress_approved"
            return Decision.EXIT, "no_progress"
        return Decision.PROCEED, None
