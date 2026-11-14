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

## 与引擎的关系

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
    "ExternalTerminationDecider",
    "GoalContinuationDecider",
    "InjectorContinuationDecider",
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
    EXIT = "exit"          # 结束整个 run（U1 后也走 FINALIZE 漏斗；
                           # 旧注释"跳过主 finalize"已过时）


class Decider(StepHandler):
    """停止/继续判定器。

    ``handle(input, ctx)`` 的 ``input`` 是检查点上下文载荷
    （如 LLM response、tool hashes），返回
    ``StepResult{output=(Decision, reason)}``。

    **子类契约**：只读判定，不得变异 ``ctx``——副作用（预算记账、
    hash 窗口维护、事件发射）仍属现有步骤/循环，B3 随引擎收编。
    """

    #: 崩溃 fail-safe 方向（B2-①b）：闸位 runner 对 decider 本体异常逐个隔离，
    #: 按本属性合成保守裁决 ``StepResult.ok((crash_policy, f"decider_{name}"))``。
    #: - 守护型（默认 ``STOP``）：守护崩了还放行 = 失控比中断危险
    #:   （预算不封顶、熔断失效、no-progress 失灵）。
    #: - 放行型（续跑类覆盖 ``PROCEED``）：裁判崩了还 CONTINUE = 无限续跑；
    #:   放行由 TextFinal 终答 / max_iter 兜底（有界）。
    #: 注入依赖的降级方向是各子类既有语义（halt 失败 → PROCEED、
    #: approval gate 失败 → EXIT），与本属性正交——它们管依赖，本属性管本体。
    crash_policy: Decision = Decision.STOP

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

    # ── 组合子（统一引擎 U4）─────────────────────────────────────────
    #
    # AutoGen 的终止条件支持 `&`/`|` 组合、Vercel AI SDK 的 stopWhen 是
    # 条件数组——业界把「循环控制」当**可组合条件**而不是固定检查点。
    # 这里提供显式方法（不用运算符重载：`&`/`|` 与位运算习惯冲突且在
    # `StepHandler` 协议上语义不清）。

    def and_(self, other: "Decider") -> "Decider":
        """两者都 PROCEED 才 PROCEED；否则取**第一个非 PROCEED** 的结论。

        与闸内「按序判定，首个非 PROCEED 生效」短路语义一致——
        ``d1.and_(d2)`` 在闸里等价于 ``[d1, d2]`` 相邻放置。
        """
        return _CombinationDecider(self, other, op="and")

    def or_(self, other: "Decider") -> "Decider":
        """第一个非 PROCEED 即返回；都 PROCEED 才 PROCEED。"""
        return _CombinationDecider(self, other, op="or")


class _CombinationDecider(Decider):
    """组合器：纯转发，两个子 decider 的 decide 结果按 op 合成。"""

    def __init__(self, first: Decider, second: Decider, *, op: str) -> None:
        self._first = first
        self._second = second
        self._op = op

    @property
    def name(self) -> str:
        op = " and " if self._op == "and" else " or "
        return f"({self._first.name}{op}{self._second.name})"

    def decide(self, input: Any, ctx: Any) -> tuple[Decision, str | None]:
        d1, r1 = self._first.decide(input, ctx)
        if self._op == "or":
            if d1 is not Decision.PROCEED:
                return d1, r1
            return self._second.decide(input, ctx)
        # and：第一个非 PROCEED 即短路；否则看第二个
        if d1 is not Decision.PROCEED:
            return d1, r1
        return self._second.decide(input, ctx)


class ExternalTerminationDecider(Decider):
    """外部终止信号：全局 halt（跨进程 kill-switch）。

    对标 AutoGen ``ExternalTermination``。halt 是**用户/外部明确要求终止
    整个 run** 的信号 → 返回 **EXIT**（与 no-progress 拒绝同型）——经 U1
    的 FINALIZE 漏斗收尾（finished_reason 落 "exit"）。

    挂 **precheck** 闸：halt 是全局信号，命中时不该再烧一轮 LLM 调用。

    **分层纪律（R-001）**：halt 的实现在 ``interfaces/cli/halt.py``（L4），
    kernel（L2）**不得 import 它**。这里只接受注入的 ``halt_check``
    callable——装配层（``modes.deciders_for`` 由 L2 内部调用，但实际注入
    发生在 L3/L4 的装配点，或由 L2 侧传 ``None`` 降级为"永不终止"）。
    与仓库既有模式一致（``_make_approval_gate`` 也是闭包注入）。
    """

    def __init__(self, halt_check: Any | None = None) -> None:
        #: ``() -> bool``；None = 无外部终止源（恒 PROCEED，零行为变化）
        self._halt_check = halt_check

    @property
    def name(self) -> str:
        return "external_termination"

    def decide(self, input: Any, ctx: LoopContext) -> tuple[Decision, str | None]:
        if self._halt_check is None:
            return Decision.PROCEED, None
        try:
            halted = bool(self._halt_check())
        except Exception:  # noqa: BLE001 — 外部信号故障不阻断循环
            return Decision.PROCEED, None
        if halted:
            return Decision.EXIT, "halt"
        return Decision.PROCEED, None


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

    #: 放行型（B2-①b）：裁判崩了还 CONTINUE = 无限续跑；
    #: 放行由 TextFinal 终答 / max_iter 兜底（有界）。
    crash_policy = Decision.PROCEED

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


class InjectorContinuationDecider(Decider):
    """post-response injector 请求续跑（``ctx.metadata["injector_continued"]``）。

    **批次 13（S3-a）新增**。legacy 在循环体内直接问 injector 的返回值
    并 ``continue``；引擎把它表达成 CONTINUE 决策，语义一致：
    continuation → injector → stop。

    ``LoopReasoner`` 负责调用 ``inject_post_response`` 并把结论写进
    ``ctx.metadata``——「谁问 injector」与「谁按结论分派」分离，符合
    decider 只读的契约。
    """

    #: 放行型（B2-①b）：读 metadata 的判定崩了放行，由 TextFinal / 上限兜底。
    crash_policy = Decision.PROCEED

    @property
    def name(self) -> str:
        return "injector_continuation"

    def decide(self, input: Any, ctx: LoopContext) -> tuple[Decision, str | None]:
        meta = getattr(ctx, "metadata", None) or {}
        if meta.get("injector_continued"):
            return Decision.CONTINUE, "injector_continued"
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

    **检查点 = after_act**（工具结果已入消息，loop.py:1035-1055）。
    触发 → 若注入了 ``approval_gate``（AgentLoop 的
    ``_check_no_progress``），先走 approval 交互再定论：
    - gate 返回 True（拒绝/超时拒绝）→ **EXIT 整个 run**
    - gate 返回 False（批准/超时继续）→ PROCEED 续跑
    无 gate（纯检测场景）→ 直接 EXIT。

    ``input`` = 本轮 tool hashes 列表。
    """

    def __init__(
        self,
        progress_step: Any,
        *,
        approved_continuation: bool = False,
        approval_gate: Any | None = None,
        context_provider: Any | None = None,
    ) -> None:
        self._step = progress_step
        self._approved = approved_continuation
        #: ``(tool_hashes) -> bool``——True = 应退出。绑定 AgentLoop 的
        #: ``_check_no_progress`` 包装（批次 12 S1 收编 approval gate）。
        self._gate = approval_gate
        #: 提供 (response, result, iteration) 给 gate —— 闭包注入，
        #: decider 自身不持有 loop。
        self._ctx_provider = context_provider

    @property
    def name(self) -> str:
        return "no_progress"

    def record(self, ctx: LoopContext, hash_value: str) -> None:
        """透传 hash 记录（窗口维护仍是 step 的职责）。"""
        self._step.record_hash(ctx, hash_value)

    def decide(self, input: Any, ctx: LoopContext) -> tuple[Decision, str | None]:
        if not self._step.is_no_progress(ctx):
            return Decision.PROCEED, None

        # approval gate（批次 12 S1）：有 gate 时让 legacy 的
        # _check_no_progress 决定——它处理「用户批准/拒绝/超时」全部语义
        # 并写好 result.finished_reason（user_rejected /
        # approval_timeout_rejected）。decider 只翻译成 Decision。
        if self._gate is not None:
            tool_hashes = input if isinstance(input, list) else []
            try:
                should_exit = self._gate(tool_hashes)
            except Exception:  # noqa: BLE001 —— gate 失败按退出处理（保守）
                return Decision.EXIT, "no_progress"
            if should_exit:
                return Decision.EXIT, "no_progress"
            return Decision.PROCEED, "no_progress_approved"

        if self._approved:
            return Decision.PROCEED, "no_progress_approved"
        return Decision.EXIT, "no_progress"
