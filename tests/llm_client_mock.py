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

import inspect
from typing import Any


def as_async(sync_impl: Any) -> Any:
    """把同步可调用包成异步可调用（调用仍落在原对象上）。

    **已经是异步的**（``async def`` / 返回 awaitable）则**直接返回**——
    否则会出现「coroutine object has no attribute 'content'」（2026-11-12
    实测：把 ``SlowAsyncMockLLM.achat`` 这种 async 方法交给本函数会踩）。
    """
    if inspect.iscoroutinefunction(sync_impl):
        return sync_impl

    async def _call(*args: Any, **kwargs: Any) -> Any:
        return sync_impl(*args, **kwargs)

    _call.__wrapped__ = sync_impl  # type: ignore[attr-defined]
    return _call


def as_async_gen(sync_gen: Any) -> Any:
    """把同步生成器函数包成异步生成器函数。

    ``sync_gen`` 可以是生成器函数（``yield`` 的函数）或返回迭代器的
    普通函数——两种都按"逐个产出"处理。

    **已经是异步生成器**（``async def`` + ``yield``）则**直接返回**——
    否则 async 生成器会被当同步迭代器用（2026-11-12 实测踩过）。
    """
    if inspect.isasyncgenfunction(sync_gen):
        return sync_gen

    async def _gen(*args: Any, **kwargs: Any) -> Any:
        for item in sync_gen(*args, **kwargs):
            yield item

    _gen.__wrapped__ = sync_gen  # type: ignore[attr-defined]
    return _gen


def patch_llm_client(loop: Any, chat_impl: Any) -> Any:
    """把 LLM 替身挂到 ``loop.client`` 的 **全部 4 个入口**。

    参数 ``chat_impl`` 可以是普通函数、``Mock``、``MagicMock``——
    调用计数与断言都落在它自己身上。**也接受 async 函数**（自动识别，
    见 :func:`as_async`）。

    返回 ``chat_impl``，方便链式写法。

    **R-008 修正（2026-11-12）**：LLM 客户端实有 **4 个入口**
    （chat/achat/stream/astream，openai_client.py:321/343/365/470）——
    ``stream_mode=True``（默认）走 ``astream``，只挂 chat/achat 仍会
    打到真实 HTTP（实测复现过 120s 挂起）。**本函数默认挂满 4 个入口**，
    流式入口用「单个完整响应」包装成生成器（够用于非流式断言；
    要断言 chunk 级行为请用 :func:`patch_llm_client_full` 自备流式替身）。
    """
    loop.client.chat = chat_impl
    loop.client.achat = as_async(chat_impl)
    loop.client.stream = _as_text_stream(chat_impl)
    loop.client.astream = as_async_gen(_as_text_stream(chat_impl))
    return chat_impl


def _as_text_stream(chat_impl: Any) -> Any:
    """把非流式响应包装成「一个 chunk 的流」生成器（sync）。

    用于让流式入口在测试里**不落到真网络**——chunk 内部结构不做断言，
    只保证「能返回一个响应」。

    **chunk 必须是 ``StreamChunk`` 对象**（不是 dict）——消费侧按属性访问
    （``chunk.delta_content`` / ``chunk.delta_thinking`` / …）。2026-11-12
    实测：用 dict 会让消费侧 AttributeError → 走 error 路径，表现为
    「事件序列不对」而非「mock 挂了」，极难定位。
    """
    from agentnodes.foundation.llm.parser import StreamChunk

    def _gen(*args: Any, **kwargs: Any):
        resp = chat_impl(*args, **kwargs)
        # async 替身的返回值是 coroutine——同步流入口拿不到内容，
        # 退化为空 chunk（内容断言请走 async 入口）。
        if inspect.isawaitable(resp):
            resp.close() if hasattr(resp, "close") else None
            content = ""
        else:
            content = getattr(resp, "content", "") or ""
        yield StreamChunk(
            delta_content=content,
            delta_thinking="",
            finish_reason="stop",
        )

    return _gen


def patch_client_achat(client: Any, chat_impl: Any) -> Any:
    """给**自定义 client 对象**补 async 入口（保留已有 sync 入口）。

    用于「测试自建 client 再挂到 loop」的场景——那种挂法绕过了
    :func:`patch_llm_client`，async 路径下会落到真网络（2026-11-12
    实测：``test_agent_executor`` / ``test_b1c_react_compaction`` 踩过）。

    与 :func:`patch_llm_client` 的区别：**不碰 stream/astream**
    （自定义 client 通常没有流式入口）。
    """
    client.achat = as_async(chat_impl)
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


__all__ = ["as_async", "as_async_gen", "patch_client_achat", "patch_llm_client",
           "patch_llm_client_full"]
