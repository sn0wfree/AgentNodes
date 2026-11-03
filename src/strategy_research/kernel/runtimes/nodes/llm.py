"""llm 节点（阶段 1a + P5-1b 流式）—— 一次 LLM 调用，把回答追加进消息历史。

预算（A5 INV-9）：调用前必须过 RunBudget.plan_call——审计门①发现原实现
绕过预算后的修正；调用后 record 记账。

依赖注入：client 由构造方传入（测试用 Fake，生产用 OpenAICompatClient）。
节点不认识 mailbox/控制器——它只管「给消息历史，还更新后的历史」。

流式（P5-1b）：``stream=True`` 时走 ``client.stream()``，每 chunk 发
``text_delta`` / ``thinking_delta`` 事件；**返回值与非流式完全同形**，
下游节点与测试无需分支。
"""
from __future__ import annotations

from typing import Any, Callable, Protocol

from ..run_budget import RunBudget

EventSink = Callable[[str, dict], None]


class ChatClient(Protocol):
    """最小客户端协议（OpenAICompatClient 与测试 Fake 均满足）。"""

    def chat(self, messages: list[dict[str, Any]], **kwargs: Any) -> Any: ...


def make_llm_node(
    client: ChatClient,
    *,
    system: str | None = None,
    budget: RunBudget | None = None,
    stream: bool = False,
    sink: EventSink | None = None,
) -> Callable[[dict], dict]:
    """构造 llm 节点。state 约定：进入时含 `messages`（user 消息已就位），
    退出时追加 `{"role": "assistant", "content": ...}` 并记录 `usage`。

    ``stream=True``：逐 chunk 经 ``sink`` 发 ``text_delta`` / ``thinking_delta``；
    累加后的 content 与 usage 写回 state，形状与 ``stream=False`` 一致。
    """
    run_budget = budget or RunBudget()
    emit = sink or (lambda kind, data: None)

    def llm_node(state: dict) -> dict:
        allowed, reason = run_budget.plan_call()
        if not allowed:
            updated = dict(state)
            updated["answer"] = f"[预算耗尽：{reason}]"
            updated["stop_reason"] = f"budget:{reason}"
            updated["budget"] = run_budget.snapshot()
            return updated

        messages = list(state.get("messages") or [])
        if system and not any(m.get("role") == "system" for m in messages):
            messages = [{"role": "system", "content": system}] + messages

        run_id = str(state.get("run_id", ""))
        if stream and hasattr(client, "stream"):
            answer, usage = _consume_stream(client, messages, run_id, emit)
        else:
            response = client.chat(messages)
            answer = getattr(response, "content", "")
            usage = getattr(response, "usage", None) or {}

        run_budget.record(usage)

        updated = dict(state)
        updated["messages"] = messages + [{"role": "assistant", "content": answer}]
        updated["answer"] = answer
        updated["usage"] = dict(usage)
        updated["budget"] = run_budget.snapshot()
        return updated

    return llm_node


def _consume_stream(
    client: Any,
    messages: list[dict[str, Any]],
    run_id: str,
    emit: EventSink,
) -> tuple[str, dict]:
    """消费流式 chunk，发事件并聚合出 (answer, usage)。

    与 parser.StreamChunk 的字段对齐：delta_content / delta_thinking / usage。
    非法 chunk（无字段）被忽略，不中断流。
    """
    parts: list[str] = []
    thinking_parts: list[str] = []
    usage: dict = {}
    try:
        for chunk in client.stream(messages):
            delta = getattr(chunk, "delta_content", "") or ""
            if delta:
                parts.append(delta)
                emit("text_delta", {"run_id": run_id, "delta": delta})
            think = getattr(chunk, "delta_thinking", "") or ""
            if think:
                thinking_parts.append(think)
                emit("thinking_delta", {"run_id": run_id, "delta": think})
            chunk_usage = getattr(chunk, "usage", None)
            if isinstance(chunk_usage, dict) and chunk_usage:
                usage = chunk_usage
    except Exception as exc:  # noqa: BLE001 —— 流中断保留已收内容
        emit("stream.failed", {"run_id": run_id, "error": str(exc)})

    answer = "".join(parts)
    if thinking_parts:
        emit("thinking_done", {"run_id": run_id, "chars": len("".join(thinking_parts))})
    return answer, usage
