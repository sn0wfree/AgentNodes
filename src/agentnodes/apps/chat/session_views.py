# -*- coding: utf-8 -*-
"""会话视图用例（L2）——各前端共用「看 / 查 / 删会话」的领域数据面。

M3（三前端一核心批 3c，§9-5 裁决：会话视图先归 apps/chat，不新建
``apps/session_views``）：CLI ``session`` 命令与 REPL ``/history`` /
``/search`` 此前各自直调 ``kernel.session.reader``——取数职责已单点
（reader），但「哪个前端拿什么数据」没有 L2 词汇。本模块给四个薄用例：

- 前端（L1）调用它们拿**领域行数据**（plain dict），渲染参数（截断 /
  列宽 / 时间格式）留在前端——原则 1 明确不统一渲染；
- 数据来自 ``kernel.session.reader``（L3 只读浏览单源），本层不做
  第二份查询。

与 :mod:`apps.chat` 其他模块同判据：L1 调用时不需要知道 reader 细节，
L3 不认识本层。
"""
from __future__ import annotations

from typing import Any

__all__ = [
    "delete_session_by_id",
    "list_recent_sessions",
    "search_session_messages",
    "session_detail",
    "session_messages",
]


def list_recent_sessions(limit: int = 50) -> list[dict[str, Any]]:
    """最近会话（新→旧），每行 ``id/title/created_at/updated_at``。"""
    from agentnodes.kernel.session.reader import list_sessions

    return list_sessions(limit=limit)


def session_detail(session_id: str) -> dict[str, Any] | None:
    """单个会话行；不存在返回 ``None``（存在性检查也用它）。"""
    from agentnodes.kernel.session.reader import get_session

    return get_session(session_id)


def session_messages(session_id: str, limit: int = 50) -> list[dict[str, Any]]:
    """会话内消息行（按时间正序）。"""
    from agentnodes.kernel.session.reader import get_messages

    return get_messages(session_id, limit=limit)


def search_session_messages(query: str, limit: int = 50) -> list[dict[str, Any]]:
    """LIKE 搜索消息（与 Web 端同一语义，不引入第二套索引）。"""
    from agentnodes.kernel.session.reader import search_messages

    return search_messages(query, limit=limit)


def delete_session_by_id(session_id: str) -> bool:
    """软删会话（archived=1，与 Web 一致；数据保留）。"""
    from agentnodes.kernel.session.reader import delete_session

    return delete_session(session_id)
