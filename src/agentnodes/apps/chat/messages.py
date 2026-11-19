# -*- coding: utf-8 -*-
"""Chat 消息记录用例（L2）——`message_received` payload 单点构造 + 落库。

3a.1 探针（三前端一核心批 3a）：从 ``routers/chat.py`` 抽取最小且真实使用
的消息记录语义。**不碰** ``_send_via_gateway`` 主体，不建第二条队列。

设计约束（方案 §2.3）：四种现存 ``message_received`` 形状字段不同（有的带
assistant_message_id/attempt_id/queue_*，有的没有），**不盲目填同一组字段**——
构造器以 ``**extra`` 表达差异，调用方按现网语义传入；本模块不认识 HTTP、
FastAPI、SSE 缓冲或 API schema。

落库语义：EventStore.emit 失败只记日志（best-effort）——与现网所有发射点
一致；返回值保留 ``(message_id, assistant_message_id)`` 供调用方使用。
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable

logger = logging.getLogger(__name__)

__all__ = ["UserMessageRecord", "build_message_received", "record_user_message"]


@dataclass(frozen=True)
class UserMessageRecord:
    """一次用户消息的记录结果（应用层类型，不含传输字段）。"""

    message_id: str
    user_message_id: str
    assistant_message_id: str = ""


def build_message_received(
    *,
    session_id: str,
    message_id: str,
    content: str,
    user_message_id: str | None = None,
    assistant_message_id: str | None = None,
    role: str = "user",
    created_at: float | None = None,
    **extra: Any,
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    """构造 ``message_received`` 事件：返回 ``(session_id, payload, meta)``。

    - 固定核心字段：message_id / user_message_id / role / content / created_at；
      其中 ``created_at`` 仅在调用方显式要求时写入（现网 slash 命令形状无此字段）。
    - 差异字段通过 ``**extra`` 原样透传（attempt_id / status /
      queue_position / queue_length / assistant_message_id 等）——由调用方
      按业务语义决定，构造器不补默认、不合并形状。
    - ``meta`` 回传调用方需要但仍属于用例层的标识（如 assistant_message_id），
      不塞进 payload（避免改变 wire 字段绝对值）。
    """
    uid = user_message_id or message_id
    payload: dict[str, Any] = {
        "message_id": message_id,
        "user_message_id": uid,
    }
    if assistant_message_id is not None:
        payload["assistant_message_id"] = assistant_message_id
    payload["role"] = role
    payload["content"] = content
    if created_at is not None:
        payload["created_at"] = created_at
    payload.update(extra)
    meta = {"assistant_message_id": assistant_message_id or ""}
    return session_id, payload, meta


def record_user_message(
    emit: Callable[[str, str, dict[str, Any]], None],
    *,
    session_id: str,
    message_id: str,
    content: str,
    **kwargs: Any,
) -> UserMessageRecord:
    """经事件总线记录用户消息（best-effort）。

    ``emit`` 由组合根/接口适配器注入（SessionService.event_bus.emit 或
    fake publisher）——本模块不 import SessionService、不建服务单例。
    """
    sid, payload, meta = build_message_received(
        session_id=session_id, message_id=message_id, content=content, **kwargs,
    )
    try:
        emit(sid, "message_received", payload)
    except Exception:  # noqa: BLE001 —— 落库失败不阻断受理（现网语义）
        logger.warning("record_user_message: event emit failed", exc_info=True)
    return UserMessageRecord(
        message_id=message_id,
        user_message_id=str(payload.get("user_message_id") or message_id),
        assistant_message_id=str(meta.get("assistant_message_id") or ""),
    )
