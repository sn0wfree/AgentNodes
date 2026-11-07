"""新旧步骤接口的适配器（批次 9 · B1 互操作桥）。

**换底座不换职责**：现有 9 个 ``strategy/steps/`` 实现用
``execute(ctx, *, async_mode)`` 变异 ctx 的旧形态，且正在生产路径上跑。
本模块让它们**不做任何修改**就能进新底座（Pipeline / 未来的 skeleton）；
新式代码从此只面向 ``StepHandler`` / ``StreamingHandler``。

B2 把 ``ctx.should_stop`` 副作用收编为 ``StepResult`` 返回值之后，
适配器可逐步退役——它是桥，不是终点。
"""

from __future__ import annotations

import inspect
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from ._core_types import StepHandler, StepResult, StreamingHandler

__all__ = ["FnStreamingHandler", "LegacyStepAdapter"]


class LegacyStepAdapter(StepHandler):
    """把旧式 step（``execute(ctx, *, async_mode)`` 变异 ctx）包成
    :class:`StepHandler`。

    语义映射（**诚实标注**）：

    - ``output`` = 步骤返回的 ctx（legacy 契约就是变异后返回它）
    - ``success`` = 未抛异常；**legacy 步骤用 ``ctx.should_stop`` 副作用
      表达停止/失败**——本适配器只映射异常，``should_stop`` 的读取归
      B2 的 deciders（那正是 B2 收编副作用的原因）
    - ``events`` = 空（legacy 步骤的事件经 ``_fire`` 回调副作用走，
      B2/B3 才收编进 StepResult）
    - ``async_mode`` 恒传 ``True``：legacy 步骤按 awaitable 优先，
      同步实现返回裸 ctx，``inspect.isawaitable`` 兜底——与
      ``loop._call_step`` 的分派手法一致
    """

    def __init__(self, step: Any, *, name: str | None = None) -> None:
        self._step = step
        self._name = name or getattr(step, "name", type(step).__name__)

    @property
    def name(self) -> str:
        return self._name

    @property
    def legacy_step(self) -> Any:
        """被包装的原始 step（测试/调试用）。"""
        return self._step

    async def handle(self, input: Any, ctx: Any) -> StepResult:
        try:
            result = self._step.execute(ctx, async_mode=True)
            if inspect.isawaitable(result):
                result = await result
            return StepResult.ok(result)
        except Exception as exc:  # noqa: BLE001 — 与 _call_step 同口径隔离
            return StepResult.fail(f"{self._name}: {exc}")


class FnStreamingHandler(StreamingHandler):
    """把 async 生成器函数包成 :class:`StreamingHandler`。

    B2/B3 接 ``AgentLoop._astream_chat`` 时的现成壳：loop 的流式方法
    是 bound async-gen，本类把它委托出来，事件透传、``StepResult``
    透传——契约（最后一个 yield 是 StepResult）由被包装方保证。
    """

    def __init__(
        self,
        fn: Callable[..., AsyncIterator[Any]],
        *,
        name: str = "fn_streaming",
    ) -> None:
        self._fn = fn
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    def stream(self, input: Any, ctx: Any) -> AsyncIterator[Any]:
        return self._fn(input, ctx)
