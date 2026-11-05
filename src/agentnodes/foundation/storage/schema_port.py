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
    """调用已注册的初始化器；未注册时退化为兼容 DDL。

    退化分支在「没有任何 L1 装配」时走到：纯内核单元测试、嵌入式使用、
    **以及 pytest-xdist 并行 worker 里的先到者**（每个 worker 是独立进程，
    注册顺序不一致，某个 worker 可能先建库再 import L1）。

    **为什么退化 DDL 必须列全**：历史上这里是「最小可用」列集，结果并行
    跑时先到者建出窄表，后到的 `_ensure_schema` 用 ``CREATE TABLE IF NOT
    EXISTS`` 不会加列，随后投影器/搜索路径撞上
    ``no such column: seq`` / ``table sessions has no column named archived``。
    表结构一旦被窄版本占位就无法自愈。

    因此这里与 L1 的 ``_ensure_schema`` **保持列级兼容**（不必字节相同，
    但每张表的核心列必须齐全，且都带 ``IF NOT EXISTS``）。完整的 FTS 触发器、
    ``message_parts`` 索引等仍由 L1 的注册实现补齐。
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
            updated_at REAL NOT NULL,
            starred INTEGER NOT NULL DEFAULT 0,
            tags_json TEXT NOT NULL DEFAULT '[]',
            message_count INTEGER NOT NULL DEFAULT 0,
            archived INTEGER NOT NULL DEFAULT 0
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
    # message_parts 的列必须与 L1 的 _ensure_schema **逐字一致**：
    #   id / message_id / session_id / type / data_json / seq / time_created
    # 历史故障（2026-11-05）：这里曾用 part_type/content/ordering 的自创列名
    # 建表，真实 DDL 的 CREATE IF NOT EXISTS 不生效，随后
    # `CREATE INDEX ... ON message_parts(message_id, seq)` 报
    # ``no such column: seq`` —— 一个窄表把生产 schema 永久占位。
    conn.execute("""
        CREATE TABLE IF NOT EXISTS message_parts (
            id TEXT PRIMARY KEY,
            message_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            type TEXT NOT NULL,
            data_json TEXT NOT NULL,
            seq INTEGER NOT NULL DEFAULT 0,
            time_created REAL NOT NULL,
            FOREIGN KEY (message_id) REFERENCES messages(id) ON DELETE CASCADE,
            FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS attempts (
            attempt_id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            parent_attempt_id TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            prompt TEXT,
            run_dir TEXT,
            summary TEXT,
            react_trace_json TEXT,
            metrics_json TEXT,
            created_at TEXT,
            completed_at TEXT,
            error TEXT,
            message_id TEXT,
            persona TEXT,
            mode TEXT,
            model_override TEXT,
            thinking TEXT
        )
    """)
    # 列补齐（幂等）：若库已被**更早版本**的窄 DDL 建过，
    # ``CREATE TABLE IF NOT EXISTS`` 不会加列 —— 必须显式 ALTER。
    # 历史故障：并行 worker 里先到者用旧窄表建库，后续读到
    # ``no such column: seq`` / ``table sessions has no column named archived``。
    for table, column, decl in (
        ("sessions", "starred", "INTEGER NOT NULL DEFAULT 0"),
        ("sessions", "tags_json", "TEXT NOT NULL DEFAULT '[]'"),
        ("sessions", "message_count", "INTEGER NOT NULL DEFAULT 0"),
        ("sessions", "archived", "INTEGER NOT NULL DEFAULT 0"),
        ("messages", "metadata_json", "TEXT"),
        ("messages", "message_type", "TEXT"),
        ("messages", "seq", "INTEGER"),
    ):
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
        except sqlite3.OperationalError:
            pass  # 列已存在


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
