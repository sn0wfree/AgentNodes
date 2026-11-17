"""流式 handler 适配器（批次 9 引入；T11 后只剩 ``FnStreamingHandler``）。

**T11（2026-11-17）**：旧形状 ``execute(ctx, *, async_mode)`` 适配器已按
契约删除（零生产调用者），``loop._call_step`` 的旧形状分支同步退役
（见 ``test_step_contract`` 的 4 条断言转正）。新式代码只面向
``StreamingHandler``。
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from typing import Any

from ._core_types import StreamingHandler

__all__ = ["FnStreamingHandler"]


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
