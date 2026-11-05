# -*- coding: utf-8 -*-
"""对话导出格式（markdown / json）—— 表现层策略，L2。

**为什么在 L2**：「导出成什么格式、字段怎么排、compaction 与 error 消息
怎么渲染」是产品决策。L1 只负责把结果包成 HTTP 响应（``PlainTextResponse``
的 media_type 属于传输细节，留在 L1）。

**设计要点**：本模块**不认识 ORM 对象**，只收 duck-typed 的 message
（有 ``role`` / ``content`` / ``message_type`` / ``created_at`` /
``message_id`` 属性即可）。这样 L2 不依赖 L1 的 ``SessionStore``
返回类型，也不依赖 L3 的投影模型。
"""
from __future__ import annotations

import json
from typing import Any, Iterable

#: 支持的导出格式。
FORMATS = frozenset({"markdown", "json"})


def to_json_rows(messages: Iterable[Any]) -> list[dict[str, Any]]:
    """把消息序列化为前端可下载的 JSON 行。"""
    return [
        {
            "role": m.role,
            "content": m.content,
            "message_type": m.message_type,
            "created_at": m.created_at,
            "message_id": m.message_id,
        }
        for m in messages
    ]


def to_json_text(messages: Iterable[Any]) -> str:
    """JSON 导出正文（缩进 2，保留中文）。"""
    return json.dumps(to_json_rows(messages), ensure_ascii=False, indent=2)


def to_markdown(messages: Iterable[Any]) -> str:
    """Markdown 导出正文。

    - ``compaction`` → 分隔线包裹的摘要（截断 200 字，避免把整轮历史
      塞进导出文件）
    - ``error`` → 加粗 Error 行
    - 其余按 role 渲染
    """
    lines: list[str] = []
    for m in messages:
        if m.message_type == "compaction":
            lines.append(f"\n---\n*{m.content[:200]}*\n---\n")
            continue
        if m.message_type == "error":
            lines.append(f"**Error:** {m.content}\n")
            continue
        if m.role == "user":
            lines.append(f"**User:** {m.content}\n")
        elif m.role == "assistant":
            lines.append(f"**Assistant:** {m.content}\n")
        else:
            lines.append(f"**{m.role.title()}:** {m.content}\n")
    return "\n".join(lines)


def render(messages: Iterable[Any], fmt: str = "markdown") -> tuple[str, str]:
    """Return ``(body, media_type)`` for the requested format.

    :param fmt: ``"markdown"`` 或 ``"json"``
    :raises ValueError: 未知格式
    """
    fmt = fmt.strip().lower()
    if fmt == "json":
        return to_json_text(messages), "application/json"
    if fmt == "markdown":
        return to_markdown(messages), "text/plain; charset=utf-8"
    raise ValueError(f"unsupported export format: {fmt!r} (expected one of {sorted(FORMATS)})")


__all__ = [
    "FORMATS",
    "render",
    "to_json_rows",
    "to_json_text",
    "to_markdown",
]
