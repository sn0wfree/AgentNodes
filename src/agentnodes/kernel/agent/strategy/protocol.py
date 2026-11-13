"""判定型 Step Protocol（批次 10 起的 9 槽位中，执行型已收敛）。

**B2-① T7 收敛**：本文件原 10 个 Protocol（``Step`` + 6 个执行型 + 4 个判定型）。
执行型全部并入 ``kernel/agent/_core_types.py::StepHandler``——那是 B1 就备好的
统一底座（``handle(input, ctx) → StepResult``）。本文件只留**判定型**四枚：

- ``StopStep`` — 预算 / 外部停止判定（``evaluate(ctx) -> (bool, reason)``）
- ``ContinuationStep`` — 续跑判定（goal 未覆盖 → 续跑注入）
- ``ProgressStep`` — 重复 hash 检测（``record_hash`` / ``is_no_progress``）
- ``ResilienceStep`` — 熔断（``is_open``）

**为什么判定型不并入 StepHandler**：它们不是"变形点"而是**判定点**——
decider 家族（``deciders.py`` 的 ``class Decider(StepHandler)``）包装它们，
把 ``(bool, reason)`` 翻译成 ``Decision`` 四值。形状不同是**职责不同**
的如实反映，不是待收敛的技术债。详见
``docs/design/step-protocol-migration-20261112.md`` §5。

**消费面（AST 实测）**：4 枚判定型只被 ``loop_strategy.py`` 的槽位注解引用
+ ``strategy/__init__.py`` 导出；执行型 Protocol 的引用已在 T7 全部消除。

**历史**：本文件初版（P1-1）定义 9 个执行型 Protocol（``execute(ctx, *,
async_mode)`` 变异 ctx），是 B2 之前的在跑形状。B2-① 逐切片迁移后
（compaction / llm_call / tool_execution / finalization / pre_run +
2 个 custom step 全部改 ``handle``），执行型 Protocol 失去存在理由。
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from .loop_context import LoopContext


# ── 判定型 Protocol（decider 家族的降级适配面）────────────────────


@runtime_checkable
class StopStep(Protocol):
    """Decide whether to break the loop after the LLM call.

    Returns ``(should_stop, reason)``；循环体也读 ``ctx.should_stop``
    （其它判定方置位）。
    """

    def evaluate(self, ctx: LoopContext) -> tuple[bool, str | None]:
        ...


@runtime_checkable
class ContinuationStep(Protocol):
    """Decide whether to keep iterating despite a text-only response.

    Returns ``(should_continue, reason)``. ``False`` means the text
    response is final (loop ends); ``True`` means inject a follow-up
    prompt and re-call the LLM.
    """

    def evaluate(self, ctx: LoopContext) -> tuple[bool, str | None]:
        ...


@runtime_checkable
class ProgressStep(Protocol):
    """Detect no-progress (same tool call repeated N times)."""

    def record_hash(self, ctx: LoopContext, hash_value: str) -> None:
        ...

    def is_no_progress(self, ctx: LoopContext) -> bool:
        ...


@runtime_checkable
class ResilienceStep(Protocol):
    """Circuit breaker / per-tool failure gating."""

    def is_open(self, ctx: LoopContext) -> bool:
        ...

    def record_success(self, ctx: LoopContext, tool_name: str) -> None:
        ...

    def record_failure(self, ctx: LoopContext, tool_name: str) -> None:
        ...


__all__ = [
    "ContinuationStep",
    "ProgressStep",
    "ResilienceStep",
    "StopStep",
]
