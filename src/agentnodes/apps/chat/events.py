# -*- coding: utf-8 -*-
"""Chat 产品事件词表（L2）——前端语义的稳定引用 + 引擎事件翻译。

3a.2（三前端一核心批 3a）：方案 §2.1「apps/chat 定义产品事件词表」的落地。
**双源防线**：所有 wire 名从 kernel ``EventType`` 取值，不在本模块复制
字符串字面量——kernel 是事件名的唯一事实源；本模块只声明「chat 产品
用哪些事件」及「引擎名→前端名」的翻译规则。

边界：本模块不 import interfaces / FastAPI / SSE 缓冲；kernel 不 import 本
模块（apps→kernel 合法，反向禁止）。API adapter 负责把产品语义编码为
SSE/EventStore payload，TUI 只映射双方共有的语义。
"""
from __future__ import annotations

from typing import Final

from agentnodes.kernel.events.event_v2 import EventType

__all__ = [
    "ChatEvent",
    "ENGINE_TO_PRODUCT",
    "STREAM_PASSTHROUGH_KINDS",
    "translate_engine_event",
]


class ChatEvent:
    """chat 产品关心的前端事件名（从 EventType 取值的稳定别名）。"""

    MESSAGE_RECEIVED: Final[str] = EventType.MESSAGE_RECEIVED
    ASSISTANT_MESSAGE: Final[str] = EventType.ASSISTANT_MESSAGE
    AGENT_DONE: Final[str] = EventType.AGENT_DONE
    AGENT_ERROR: Final[str] = EventType.AGENT_ERROR
    TEXT_STARTED: Final[str] = EventType.TEXT_STARTED
    TEXT_DELTA: Final[str] = EventType.TEXT_DELTA
    TEXT_ENDED: Final[str] = EventType.TEXT_ENDED
    THINKING_START: Final[str] = EventType.THINKING_START
    THINKING_DELTA: Final[str] = EventType.THINKING_DELTA
    THINKING_DONE: Final[str] = EventType.THINKING_DONE
    THINKING_END: Final[str] = EventType.THINKING_END
    TOOL_CALL: Final[str] = EventType.TOOL_CALL
    TOOL_RESULT: Final[str] = EventType.TOOL_RESULT
    # attempt 生命周期：wire 名从 EventType 取（单一事实源，3a.3 决策 6）
    ATTEMPT_STARTED: Final[str] = EventType.ATTEMPT_STARTED
    ATTEMPT_COMPLETED: Final[str] = EventType.ATTEMPT_COMPLETED
    ATTEMPT_FAILED: Final[str] = EventType.ATTEMPT_FAILED


# 引擎事件名 → 产品事件名（bridge 现网 mapping；名字相同 = 直通）。
# 值统一从 ChatEvent 取，杜绝字符串漂移。
ENGINE_TO_PRODUCT: dict[str, str] = {
    "tool_call": ChatEvent.TOOL_CALL,
    "tool_result": ChatEvent.TOOL_RESULT,
}

# 流式事件直通集合（AgentLoop 的 3-step / thinking / stream.failed 协议）。
STREAM_PASSTHROUGH_KINDS: frozenset[str] = frozenset({
    "text_delta", "thinking_delta", "thinking_done", "stream.failed",
    "text.started", "text.ended",
    "thinking_start", "thinking_end",
})


def translate_engine_event(kind: str) -> str | None:
    """把引擎事件名翻译为前端产品事件名；无对应语义返回 None（不转发）。

    翻译规则：显式映射优先 → 流式直通集合 → None。行为与现网 bridge
    ``mapping`` + 直通分支逐字一致（3a.2 只挪规则、不改 wire 输出）。
    """
    mapped = ENGINE_TO_PRODUCT.get(kind)
    if mapped is not None:
        return mapped
    if kind in STREAM_PASSTHROUGH_KINDS:
        return kind
    return None
