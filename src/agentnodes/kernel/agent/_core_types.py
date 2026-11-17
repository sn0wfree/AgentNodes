"""Agent loop 核心抽象类型（批次 9 · B1 协议统一）。

四个原语（移植自 llmwikify，签名有一处有意差异，见下）：

- ``StepResult``：StepHandler / StreamingHandler 的统一输出——
  **SSE 事件跟着返回值走**，不靠回调副作用
- ``StepHandler``：无状态、单次调用（``handle(input, ctx)``）
- ``StreamingHandler``：有状态、多次 yield（最后一个 yield 是 StepResult）
- ``Pipeline``：StepHandler 串行组合（fail-fast，events 累积）
- ``maybe_await``：sync/async handle 统一调用

**与 llmwikify 的签名差异（有意）**：``handle(input, ctx)`` 而非
``handle(input, spec, ctx)``——AgentNodes B1 时点没有 spec 对象，
``LoopContext`` 已携带全部状态；B3 引入 ``LoopSpec`` 时其内容走 ctx。
另外 ``handle`` 允许返回 ``StepResult | Awaitable[StepResult]``——
AgentNodes 的大量存量步骤是同步的，强制 async 会逼出无意义的
``asyncio.to_thread``。

**与既有代码的关系**：``strategy/protocol.py`` 的 9 个 Step Protocol
（``execute(ctx, *, async_mode)`` 变异 ctx）是**当前在跑的职责划分**，
本模块是 B2/B3 的目标底座；桥接见 ``step_adapters.py``。
``maybe_await`` 与 ``runtime/sync_bridge.py``、``loop._call_step`` 的
``inspect.isawaitable`` 手法是三处同型实现，B3 引擎隔离时收口为一处。
"""

from __future__ import annotations

import inspect
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Awaitable
from dataclasses import dataclass, field
from typing import Any, Callable, Union

# ─── StepResult — 统一的步骤输出 ─────────────────────────────────────


@dataclass
class StepResult:
    """StepHandler / StreamingHandler 的统一输出。

    - ``output``: 该步骤的产出（类型由具体 step 决定）
    - ``events``: 透传给调用方的 SSE events——**事件随返回值走**，
      不经回调副作用，Pipeline 才能统一组合两种步骤
    - ``success``: 步骤是否成功
    - ``error``: 失败原因（``success=False`` 时）
    """

    output: Any = None
    events: list[dict[str, Any]] = field(default_factory=list)
    success: bool = True
    error: str | None = None

    @staticmethod
    def ok(
        output: Any = None, events: list[dict[str, Any]] | None = None
    ) -> "StepResult":
        return StepResult(output=output, events=events or [])

    @staticmethod
    def fail(
        error: str, events: list[dict[str, Any]] | None = None
    ) -> "StepResult":
        return StepResult(success=False, error=error, events=events or [])


# ─── StepHandler — 无状态步骤接口 ────────────────────────────────────


class StepHandler(ABC):
    """无状态、单次调用的步骤。

    适用于：LLM 调用、提取、校验、字段检查、数据转换、**停止判定**
    （B2 的 deciders 就是 ``handle(...) -> StepResult{output=(stop, reason)}``）。
    不适用于：流式解析、有状态循环、双输出（用 :class:`StreamingHandler`）。

    **sync/async 双栖**：``handle`` 可为普通函数或 ``async def``——
    返回 ``StepResult`` 或 ``Awaitable[StepResult]``，调用方经
    :func:`maybe_await` 统一。
    """

    @abstractmethod
    def handle(self, input: Any, ctx: Any) -> "Union[StepResult, Awaitable[StepResult]]":
        ...


# ─── StreamingHandler — 有状态流式接口 ───────────────────────────────


class StreamingHandler(ABC):
    """有状态、流式的 handler。

    与 :class:`StepHandler` 的区别：有生命周期，内部管理状态，yield 多次。
    适用于：流式 LLM reasoner（token 级 delta）、带微压缩的 tool actor。

    契约：yield 的事件是 ``dict``（SSE 形态），**最后一个 yield 是
    ``StepResult``**（终止信号）——调用方按 ``isinstance`` 分流。
    """

    @abstractmethod
    def stream(
        self, input: Any, ctx: Any
    ) -> AsyncIterator["Union[dict[str, Any], StepResult]"]:
        ...
        yield StepResult()  # pragma: no cover  # type: ignore[misc]


# ─── Pipeline — Steps 串行组合 ───────────────────────────────────────


class Pipeline(StepHandler):
    """步骤流水线 — 串行执行多个 StepHandler。

    上一个 step 的 ``output`` 作为下一个 step 的 ``input``。
    任一 step 失败 → 整体失败（fail-fast）。events 累积。

    **T11（2026-11-17）**：旧形状互操作桥已随旧契约删除——全部 step
    现在都是新式 ``StepHandler``（``handle(input, ctx) → StepResult``）。
    """

    def __init__(self, *steps: StepHandler) -> None:
        self._steps = steps

    async def handle(self, input: Any, ctx: Any) -> StepResult:
        current = input
        all_events: list[dict[str, Any]] = []
        for step in self._steps:
            result = await maybe_await(step.handle(current, ctx))
            all_events.extend(result.events)
            if not result.success:
                return StepResult.fail(result.error or "pipeline step failed", all_events)
            current = result.output
        return StepResult.ok(current, all_events)

    def __repr__(self) -> str:
        names = " → ".join(type(s).__name__ for s in self._steps)
        return f"Pipeline({names})"


# ─── maybe_await — sync/async 统一调用 ───────────────────────────────


async def maybe_await(
    value: "Union[StepResult, Awaitable[StepResult], Callable[[], Any], Any]",
) -> Any:
    """``StepResult | Awaitable[StepResult]`` 统一为 ``StepResult``。

    与 ``loop._call_step``（loop.py:507）、``runtime/sync_bridge.py``
    同型——三处同型实现，B3 引擎隔离时收口为一处。
    """
    if inspect.isawaitable(value):
        return await value
    return value


__all__ = [
    "Pipeline",
    "StepHandler",
    "StepResult",
    "StreamingHandler",
    "maybe_await",
]
