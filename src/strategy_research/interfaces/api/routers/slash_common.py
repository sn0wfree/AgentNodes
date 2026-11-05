"""Chat 通用 slash 命令处理器（``/compact`` / ``/clear`` / ``/help``）。

**历史**：本文件原为 ``routers/slash_commands.py``，同时含 ``/study`` 系列
（669 行，其中 ~400 行是 study 命令）。2026-11-05 做减法批次 3 随
``apps/study`` 移除后，只剩这三个通用命令——它们与 study 无关：

| 命令 | 依赖 | 为什么保留 |
|---|---|---|
| ``/compact`` | ``SessionService.compact_history`` | 通用上下文压缩 |
| ``/clear`` | ``MemoryManager.clear`` | 通用会话重置 |
| ``/help`` | 静态文本 | 通用帮助 |

**改名**：``slash_commands.py`` → ``slash_common.py``，让文件名不再暗示
「所有 slash 命令都在这」——study 命令已不存在，将来若要加新命令域，
各自成文件即可。

**SSE 协议**：三条命令都走同一套 3 步文本协议
（``text.started`` → ``text_delta`` → ``text.ended``）+ ``message_received``
/ ``assistant_message`` / ``agent_done``。与 agent 正常回复同一条链路，
前端不需要区分来源。
"""

from __future__ import annotations

import logging
import uuid

from strategy_research.interfaces.api.schemas.chat import (
    ChatMessageRequest as ChatMessage,
    SendMessageResponse,
)

logger = logging.getLogger(__name__)


def _get_session_service():
    """Container-backed SessionService (imported lazily to avoid a cycle)."""
    from strategy_research.interfaces.api.routers.chat import _get_session_service as _svc

    return _svc()


def _build_llm_config():
    """LLMConfig from env (shared helper lives in chat.py)."""
    from strategy_research.interfaces.api.routers.chat import _build_llm_config as _cfg

    return _cfg()


def _emit_text_response(
    event_bus,
    session_id: str,
    text: str,
    *,
    user_content: str,
) -> tuple[str, str]:
    """发一轮完整的「用户消息 + 助手回复」事件序列。

    返回 ``(user_msg_id, assistant_msg_id)``。抽成函数是因为三条命令的
    事件序列逐字相同——复制三份只会让改协议时要改三处。
    """
    user_msg_id = str(uuid.uuid4())
    assistant_msg_id = str(uuid.uuid4())
    text_id = str(uuid.uuid4())

    event_bus.emit(session_id, "message_received", {
        "message_id": user_msg_id,
        "user_message_id": user_msg_id,
        "assistant_message_id": assistant_msg_id,
        "content": user_content,
        "role": "user",
        "status": "done",
    })
    event_bus.emit(session_id, "text.started", {
        "text_id": text_id,
        "message_id": assistant_msg_id,
    })
    event_bus.emit(session_id, "text_delta", {
        "text": text,
        "text_id": text_id,
        "message_id": assistant_msg_id,
    })
    event_bus.emit(session_id, "text.ended", {
        "text_id": text_id,
        "text": text,
        "message_id": assistant_msg_id,
    })
    event_bus.emit(session_id, "assistant_message", {
        "message_id": assistant_msg_id,
        "content": text,
        "message_type": "assistant",
    })
    event_bus.emit(session_id, "agent_done", {
        "message_id": assistant_msg_id,
        "status": "completed",
    })
    return user_msg_id, assistant_msg_id


# ── /compact ────────────────────────────────────────────────────────


async def _handle_compact_command(body: ChatMessage) -> SendMessageResponse:
    """Handle /compact command — compress session history in-place."""
    service = _get_session_service()
    cfg = _build_llm_config()

    try:
        result = await service.compact_history(
            session_id=body.session_id,
            config=cfg.compact_config if cfg else None,
        )
        layers = result.get("layers", [])
        before = result.get("before_tokens", 0)
        after = result.get("after_tokens", 0)
        summary = result.get("summary", "")

        if layers:
            response_text = f"✅ 上下文已压缩: {', '.join(layers)}（{before} → {after} tokens）"
            if summary:
                response_text += f"\n\n{summary}"
        else:
            response_text = "ℹ️ 上下文无需压缩，当前 token 使用量在阈值以下。"
    except Exception as exc:
        logger.exception("compact_history failed")
        response_text = f"❌ 压缩失败: {exc}"

    user_msg_id, assistant_msg_id = _emit_text_response(
        service.event_bus, body.session_id, response_text,
        user_content=body.content,
    )
    return SendMessageResponse(
        message_id=user_msg_id,
        user_message_id=user_msg_id,
        assistant_message_id=assistant_msg_id,
        event_id="",
        status="done",
    )


# ── /clear ──────────────────────────────────────────────────────────


async def _handle_clear_command(body: ChatMessage) -> SendMessageResponse:
    """Handle ``/clear`` — drop the LLM-visible history for this session.

    Implementation: calls ``MemoryManager.clear`` which truncates the
    session's memory backend rows (the buffer the AgentLoop reads at
    attempt start). The persisted message log (the ``messages`` table
    populated by the projector) is intentionally NOT touched so the
    user can still scroll their conversation. The UI sees a synthetic
    assistant acknowledgement via the same text-event flow as
    ``/compact``.
    """
    try:
        from strategy_research.kernel.agent.memory_manager import (
            get_default_memory_manager,
        )
        mm = get_default_memory_manager()
        await mm.clear(body.session_id)
        response_text = "✅ 已清空当前会话的上下文。历史消息保留可见。"
    except Exception as exc:
        logger.exception("clear failed")
        response_text = f"❌ 清空失败: {exc}"

    service = _get_session_service()
    user_msg_id, assistant_msg_id = _emit_text_response(
        service.event_bus, body.session_id, response_text,
        user_content=body.content,
    )
    return SendMessageResponse(
        message_id=user_msg_id,
        user_message_id=user_msg_id,
        assistant_message_id=assistant_msg_id,
        event_id="",
        status="done",
    )


# ── /help ───────────────────────────────────────────────────────────


_HELP_TEXT = (
    "## 可用命令\n"
    "\n"
    "- `/compact` — 压缩当前会话的上下文\n"
    "- `/clear` — 清空当前会话的 LLM 上下文（保留历史消息）\n"
    "- `/help` — 显示本帮助\n"
    "\n"
    "## 快捷键\n"
    "\n"
    "- ⌘K — 搜索会话\n"
    "- ⌘P — 打开命令面板\n"
    "- ⌘T — 新建会话\n"
    "- ⌘B — 切换右栏\n"
    "- ⌘1–9 — 切换会话 tab\n"
    "- Enter — 发送 · Shift+Enter — 换行\n"
)


async def _handle_help_command(body: ChatMessage) -> SendMessageResponse:
    """Handle ``/help`` — return the static cheat-sheet as an assistant message."""
    service = _get_session_service()
    user_msg_id, assistant_msg_id = _emit_text_response(
        service.event_bus, body.session_id, _HELP_TEXT,
        user_content=body.content,
    )
    return SendMessageResponse(
        message_id=user_msg_id,
        user_message_id=user_msg_id,
        assistant_message_id=assistant_msg_id,
        event_id="",
        status="done",
    )


__all__ = [
    "_handle_clear_command",
    "_handle_compact_command",
    "_handle_help_command",
]
