"""web_session DB layer —— re-export 垫片（L1 → L3）。

**位置（2026-11-16 批次 B2）**：实现已下沉到 ``kernel/session/db.py``（L3 平台层）——
会话库的 schema / 迁移 / 连接 / 行映射都是**会话领域**的东西，不属于接口层私有实现。
此处是 **re-export 垫片**（L1→L3 是合法方向），保证 29 处老 import 零改动。

新代码请用 ``from agentnodes.kernel.session.db import ...``。

**注意（写测试的人）**：这是 re-export，不是别名模块。``monkeypatch.setattr`` 打在
**本模块**上只会新建一个本地名字，**不会**改变 kernel 模块的全局名 —— 而
``_get_db()`` 在 kernel 模块里解析 ``_get_db_path``。需要重定向 DB 路径时，
patch ``agentnodes.kernel.session.db``，不是这里。

**不转出 `_fetch_session_owned`**：它已上移到
``interfaces/api/routers/web_session.py``（HTTP 鉴权语义，B2 决策 2b）。
本模块若转出它就得 import 路由模块，而路由模块正 import 本模块 —— 循环 import。
"""

from agentnodes.kernel.session.db import (  # noqa: F401
    _add_column,
    _db_thread_local,
    _drop_column,
    _ensure_fts_table,
    _ensure_fts_table_impl,
    _ensure_schema,
    _get_db,
    _get_db_at,
    _get_db_path,
    _has_column,
    _migrate_message_types,
    _migrate_text_part_ids,
    _row_to_message,
    _row_to_session,
    _run_schema_migrations,
    _validate_identifier,
    ensure_session_row,
    logger,
)

__all__ = [
    "_add_column",
    "_db_thread_local",
    "_drop_column",
    "_ensure_fts_table",
    "_ensure_fts_table_impl",
    "_ensure_schema",
    "_get_db",
    "_get_db_at",
    "_get_db_path",
    "_has_column",
    "_migrate_message_types",
    "_migrate_text_part_ids",
    "_row_to_message",
    "_row_to_session",
    "_run_schema_migrations",
    "_validate_identifier",
    "ensure_session_row",
    "logger",
]
