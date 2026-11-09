"""AgentLoop → GraphEngine 节点适配器（V3-b）。

**背景**（``docs/design/v3-path-convergence-decision-20261109.md``）：web
chat 的生产执行路径是 ``make_react_node``（路径 B）——手写同步 for 循环，
能力集是路径 A（``AgentLoop``）的子集（实测 9 项 vs 1 项）。V3-a 补齐了
A 缺的最后 1 项（``task_signature`` 裁剪）后，本模块把 A 包成 ``NodeFn``
（``dict -> dict`` 同步契约），``session_controller`` 的默认图换用它；
V3-c 删除 react.py。

## 契约映射

| GraphEngine / react 语义 | AgentLoop 语义 |
|---|---|
| ``state["messages"]`` 进出 | 末条 user 消息 → ``task``，其余 → ``history``（``build_initial_messages`` 原样插回：system + history + user，字节序不变） |
| 异常冒泡 → controller ``run.failed`` | ``result.error`` 非空 → 重新抛出（``DefaultLLMCallStep`` 已把任意 LLM 异常折叠进 ``result.error``） |
| ``state["answer"]`` | ``result.answer`` |
| ``state["usage"]``（RunBudget.snapshot 形状） | loop 的 A1 预算四字段 + ``iterations`` |
| ``state["stop_reason"]`` | ``result.finished_reason``（``stop`` / ``max_iter`` / ``error`` / …） |
| steer 安全点 drain ``state["steer_inbox"]`` | ``loop._inbox_drain_hook``——``_drain_inbox`` 每轮安全点调用，同一合批形状（``"\\n\\n".join``） |
| client 契约 ``chat`` / ``stream`` | 骨架引擎（默认）走 ``achat`` / ``astream`` —— ``wrap_async_capable_client`` 补齐（原生四件套直通不包） |

## 事件词表（V3 决策 §7 W1 的实测修正）

``AgentLoop`` 发的**本来就是产品词表**：``tool_call``{id,name,arguments} /
``tool_result``{id,status,result} / ``text_delta``{text_id} /
``thinking_delta``{think_id}——``event_v2.EventType`` 注释明示「Names match
the existing SSE event names emitted by AgentLoop」，投影器
（``projection_handlers.py``）与前端 handlers 按同一形状消费。需要翻译的
反而是 react 的 ``tool.started``/``tool.completed``——翻译层在 chat.py 的
SSE bridge，V3-b 把 AgentLoop 词表加进直通表；V3-c 删 react 词表映射。

``llm_request`` 的大字段（system_prompt/tools_schema）经
``make_loop_event_sink`` 剥离——转发 sink 的既有约定动作
（``event_vocab.strip_llm_request_fields``），避免事件日志膨胀三个量级。
"""
from __future__ import annotations

import asyncio
from typing import Any, Callable

from agentnodes.foundation.observability.event_vocab import (
    strip_llm_request_fields,
)
from agentnodes.kernel.agent.runtime.sync_bridge import _run_coro_in_sync

EventSink = Callable[[str, dict], None]
NodeFn = Callable[[dict], dict]

__all__ = [
    "make_agent_loop_node",
    "make_loop_event_sink",
    "wrap_async_capable_client",
]


class _AsyncCapableClient:
    """给只有 ``chat``/``stream`` 的 client 补 ``achat``/``astream``。

    AgentLoop 的默认引擎（骨架）是 async-first：非流式走 ``achat``、
    流式走 ``astream``。GraphEngine 节点收到的 client 只承诺 react 契约
    （``chat`` / ``stream``）。生产 client（``OpenAICompatClient``）四件套
    齐全——``wrap_async_capable_client`` 直接放行，不包；测试/脚本的
    假 client 缺 async 方法，这里用 ``asyncio.to_thread`` 桥补齐
    （语义 = 同步调用搬到工作线程，事件循环不被阻塞）。

    ``__getattr__`` 把其余属性（``chat`` / ``stream`` / ``config`` /
    ``parse_response``）原样转发——sync 路径与压缩器的 ``llm_client.chat``
    行为不变。
    """

    def __init__(self, inner: Any) -> None:
        self._inner = inner

    def __getattr__(self, name: str) -> Any:
        # 仅在实例 dict 未命中时触发；_inner 在 __init__ 已落位。
        return getattr(self._inner, name)

    async def achat(self, messages: list, tools: Any = None, **kw: Any) -> Any:
        inner = self._inner
        native = getattr(inner, "achat", None)
        if callable(native):
            return await native(messages, tools=tools, **kw)
        return await asyncio.to_thread(inner.chat, messages, tools=tools, **kw)

    async def astream(self, messages: list, tools: Any = None, **kw: Any) -> Any:
        inner = self._inner
        native = getattr(inner, "astream", None)
        if callable(native):
            async for chunk in native(messages, tools=tools, **kw):
                yield chunk
            return
        stream = getattr(inner, "stream", None)
        if not callable(stream):
            raise AttributeError(
                f"client {type(inner).__name__} has neither astream nor stream"
            )
        it = iter(stream(messages, tools=tools, **kw))
        sentinel = object()
        while True:
            chunk = await asyncio.to_thread(next, it, sentinel)
            if chunk is sentinel:
                return
            yield chunk


def wrap_async_capable_client(client: Any) -> Any:
    """原生有 ``achat``+``astream`` 的 client 原样返回；否则包兼容壳。"""
    if hasattr(client, "achat") and hasattr(client, "astream"):
        return client
    return _AsyncCapableClient(client)


def make_loop_event_sink(sink: EventSink) -> EventSink:
    """包一层 ``llm_request`` 大字段剥离（转发 sink 的约定动作）。

    其余事件原样透传（``strip_llm_request_fields`` 对非 ``llm_request``
    返回原 dict，零拷贝）。
    """

    def wrapped(kind: str, data: dict) -> None:
        sink(kind, strip_llm_request_fields(kind, data or {}))

    return wrapped


def make_agent_loop_node(loop: Any) -> NodeFn:
    """把 ``AgentLoop`` 包成 GraphEngine 节点（同步 ``dict -> dict``）。

    :param loop: 已装配好的 ``AgentLoop``（client / 权限闸 / 系统提示 /
        ``task_signature`` 由构造方接线；节点只负责 state 进出与错误契约）。
    :return: ``NodeFn``——注册名沿用 ``"react"``（``test_react_node_receives_assembled_prompt``
        钉住节点名；语义不变，只是实现换了引擎）。
    """

    def agent_loop_node(state: dict) -> dict:
        # ── messages 拆分：末条 user → task，其余 → history ──
        # controller._inject_context 已把 hub 内容前置进末条 user 的
        # content（P5-4 契约），所以这里的 task 自带上下文前缀——与
        # react「整列表直发」给 LLM 看到的序列一致。
        messages = list(state.get("messages") or [])
        task = ""
        history: list[dict] = []
        if messages:
            task = str(messages[-1].get("content") or "")
            history = messages[:-1]

        # steer 注入通道（R2.2）：react 在每次 LLM 调用前 drain；
        # AgentLoop 的对应安全点是 ``_drain_inbox``（每轮 compaction 后、
        # LLM 前）——钩子在 loop.py，合批形状与 react 逐字一致。
        inbox = state.get("steer_inbox")
        if inbox is not None:
            loop._inbox_drain_hook = inbox.drain

        result = _run_coro_in_sync(loop.arun(task, history=history))
        if result.error:
            # react 语义：异常冒泡 → controller 捕获 → run.failed + settled。
            # ``DefaultLLMCallStep`` 已把任意 LLM 异常折叠成
            # ``result.error = "{type}: {msg}"``，这里恢复冒泡形状。
            raise RuntimeError(result.error)

        updated = dict(state)
        updated["messages"] = result.messages
        updated["answer"] = result.answer
        # ``usage`` 保持 react 的 RunBudget.snapshot 形状（fanout join 与
        # 测试读它）；数值来自 loop 的 A1 预算累计。
        updated["usage"] = {
            "tokens": int(getattr(loop, "_budget_tokens", 0) or 0),
            "prompt_tokens": int(getattr(loop, "_budget_prompt_tokens", 0) or 0),
            "completion_tokens": int(getattr(loop, "_budget_completion_tokens", 0) or 0),
            "cost_usd": float(getattr(loop, "_budget_cost_usd", 0.0) or 0.0),
            "iterations": result.iterations,
        }
        updated["stop_reason"] = result.finished_reason
        return updated

    return agent_loop_node
