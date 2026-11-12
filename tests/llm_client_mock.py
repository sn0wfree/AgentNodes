"""LLM 客户端的测试替身工具。

**为什么需要它**（V4-2）：

批次 12 S1 把默认引擎翻成 skeleton 之后，``AgentLoop._get_response`` 在
**async 模式**下走 ``client.achat(...)``（``loop.py:1299``），流式下走
``client.astream(...)``。而 V4-2 让**公开的 sync 入口** ``run()`` 也走
skeleton —— 于是 ``run()`` 也变成 async 客户端的调用方。

后果：只 mock 了 ``loop.client.chat`` 的测试会**打到真实 HTTP**
（``client.achat`` 仍然是真客户端 → 真网络请求 + 重试），表现为
"答案变空"或整条用例超时——而不是干脆的失败。

所以本模块提供两个入口的挂载方式：

- :func:`patch_llm_client` —— 同步 + 异步两个入口挂同一个替身；
- :func:`as_async_gen` —— 把同步生成器包成异步生成器（流式用）。

替身对象**不被包装**：调用计数、``side_effect``、``assert_called``
全都落在原对象上。
"""

from __future__ import annotations

from typing import Any


def as_async(sync_impl: Any) -> Any:
    """把同步可调用包成异步可调用（调用仍落在原对象上）。"""

    async def _call(*args: Any, **kwargs: Any) -> Any:
        return sync_impl(*args, **kwargs)

    _call.__wrapped__ = sync_impl  # type: ignore[attr-defined]
    return _call


def as_async_gen(sync_gen: Any) -> Any:
    """把同步生成器函数包成异步生成器函数。

    ``sync_gen`` 可以是生成器函数（``yield`` 的函数）或返回迭代器的
    普通函数——两种都按"逐个产出"处理。
    """

    async def _gen(*args: Any, **kwargs: Any) -> Any:
        for item in sync_gen(*args, **kwargs):
            yield item

    _gen.__wrapped__ = sync_gen  # type: ignore[attr-defined]
    return _gen


def patch_llm_client(loop: Any, chat_impl: Any) -> Any:
    """把 LLM 替身挂到 ``loop.client`` 的 **chat + achat** 两个入口。

    参数 ``chat_impl`` 可以是普通函数、``Mock``、``MagicMock``——
    调用计数与断言都落在它自己身上。

    返回 ``chat_impl``，方便链式写法。

    **R-008 修正（2026-11-12）**：LLM 客户端实有 **4 个入口**
    （chat/achat/stream/astream，openai_client.py:321/343/365/470）——
    ``stream_mode=True``（默认）走 ``astream``，只挂 chat/achat 仍会
    打到真实 HTTP（实测复现过 120s 挂起）。流式替换身时必须用
    :func:`patch_llm_client_full`。
    """
    loop.client.chat = chat_impl
    loop.client.achat = as_async(chat_impl)
    return chat_impl


def patch_llm_client_full(loop: Any, *, chat_impl: Any, stream_impl: Any) -> Any:
    """把替身挂满 **全部 4 个入口**（chat/achat/stream/astream）。

    - ``chat_impl``：非流式响应（同步函数即可，内部包 async）；
    - ``stream_impl``：流式生成器函数（``yield`` chunk dict），
      同步生成器会被 :func:`as_async_gen` 包装成异步生成器。
    """
    loop.client.chat = chat_impl
    loop.client.achat = as_async(chat_impl)
    loop.client.stream = stream_impl
    loop.client.astream = as_async_gen(stream_impl)
    return chat_impl


__all__ = ["as_async", "as_async_gen", "patch_llm_client", "patch_llm_client_full"]
