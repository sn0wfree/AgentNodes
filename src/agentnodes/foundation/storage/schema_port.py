# -*- coding: utf-8 -*-
"""Schema 初始化端口（L4 声明，L1 实现）— 消除 L3→L1 反向依赖。

**背景**：``kernel/agent/memory_manager.SQLiteStore._init_schema`` 原先
``from agentnodes.interfaces.api.session.web_session_db import _ensure_schema``
—— 内核反向依赖接口层。原 docstring 的辩护是「event_store 也这么干，
所以不算新增方向」，但「别人也违规」不构成合规理由（两个违规仍是两个
违规，且合起来构成 SCC 环）。

**为什么不用下沉 DDL 的方式**：``_ensure_schema`` 是 ~200 行完整 DDL +
版本化迁移 + FTS 触发器重建，与 ``web_session_db`` 的 ``_add_column`` 等
辅助函数深度交织。把它整体搬到 foundation 需要同时搬 5 个辅助函数并
改动所有调用点，风险高于收益。

**采用端口注册**（与 ProjectionSink 同思路）：
- L4 声明「怎么初始化 schema」的契约与本模块级注册表
- L1 的 ``web_session_db`` 在 import 时把自己注册进来
- L3 的 ``memory_manager`` 调用端口，不认识实现

依赖方向因此变成：L1 → L4 注册；L3 → L4 消费。两者都朝内。
"""
from __future__ import annotations

import sqlite3
from typing import Callable

#: schema 初始化器签名：接收一个已连接的 sqlite3.Connection，就地建表/迁移。
SchemaInitializer = Callable[[sqlite3.Connection], None]

_initializer: SchemaInitializer | None = None


def register_schema_initializer(fn: SchemaInitializer) -> None:
    """注册唯一的 schema 初始化器（由 L1 的 web_session_db 在 import 时调用）。

    幂等：后注册者覆盖先注册者——装配顺序由组合根决定，这里不做仲裁。
    """
    global _initializer
    _initializer = fn


def ensure_schema(conn: sqlite3.Connection) -> None:
    """调用已注册的初始化器；未注册时退化为最小可用 DDL。

    退化分支只在「没有任何 L1 装配」的纯内核场景（单元测试、嵌入式
    使用）走到。**注意**：这里的列必须与 L1 的 `_ensure_schema` 保持
    兼容——`SQLiteStore.list_messages()` 会 SELECT ``metadata_json`` /
    ``message_type`` / ``seq``，缺列会直接 OperationalError。完整表集
    （message_parts / attempts / FTS）仍由 L1 的 ``_ensure_schema`` 负责。
    """
    if _initializer is not None:
        _initializer(conn)
        return
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL DEFAULT 'anonymous',
            title TEXT NOT NULL DEFAULT '',
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL DEFAULT '',
            created_at REAL NOT NULL,
            metadata_json TEXT,
            message_type TEXT,
            seq INTEGER
        )
    """)
    # 投影器（Projector.flush）会写 message_parts；缺表会让整个 flush
    # 失败而不是降级——所以退化路径必须把它建出来。完整字段集（含
    # part_type / ordering 等）仍以 L1 的 _ensure_schema 为准。
    conn.execute("""
        CREATE TABLE IF NOT EXISTS message_parts (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            message_id TEXT NOT NULL,
            part_type TEXT NOT NULL DEFAULT 'text',
            content TEXT NOT NULL DEFAULT '',
            ordering INTEGER NOT NULL DEFAULT 0,
            created_at REAL NOT NULL
        )
    """)


def has_initializer() -> bool:
    """Whether a real initializer has been registered (used by tests)."""
    return _initializer is not None


def reset_for_tests() -> None:
    """Clear the registration (test isolation)."""
    global _initializer
    _initializer = None


__all__ = [
    "SchemaInitializer",
    "ensure_schema",
    "has_initializer",
    "register_schema_initializer",
    "reset_for_tests",
]
