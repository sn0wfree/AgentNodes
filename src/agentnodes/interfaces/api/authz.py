"""HTTP 鉴权原语：会话归属校验（IDOR 防护），供各路由共用。

**位置（2026-11-20 M11a · 三前端一核心批 3c · WP-9 M11 第一段）**：
``_fetch_session_owned`` 原住 ``routers/web_session.py``（B2 自 DB 层上移）。
chat/goal/permission/schedule/workflow 五路由此前横向 import 该路由模块的
私名；本模块把它提到 ``interfaces/api/`` 顶层作共用鉴权原语，路由改从
这里取，消除横向 import。

依赖方向：fastapi + kernel（L1→L3 向下合法），**不 import 任何路由模块**
（routers.web_session 保留同名 re-export 作过渡面，M11b 删）。
"""
from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import HTTPException


def enforce_session_owned(request: Any, session_id: str) -> sqlite3.Row:
    """请求侧的会话归属校验 adapter（第三轮复审候选 S1）。

    独占「request.state.user_id 提取 → 连接获取 → 归属校验」三步；21 个
    调用点此前各自重复这三行，且 user_id 口径分裂为 ``"anonymous"`` 与
    ``None or "anonymous"`` 两种。统一为本 helper 一种实现（与
    ``auth.get_current_user_id`` 一致）。特例（goal 旁路开关 / chat
    study-404 重建 / permission 条件检查 / workflow live-runner 短路）
    留在各自本地 wrapper，本 helper 只拥有公共三步。

    ``_fetch_session_owned`` 位置不动；本 helper 包装它。
    """
    from agentnodes.kernel.session.db import _get_db

    user_id = getattr(request.state, "user_id", None) or "anonymous"
    return _fetch_session_owned(_get_db(), session_id, user_id)


def _fetch_session_owned(
    conn: sqlite3.Connection, session_id: str, user_id: str
) -> sqlite3.Row:
    """Fetch a session row, enforcing ownership (IDOR protection).

    Returns the row, or raises 404 (not found) / 403 (other user's).

    **位置（2026-11-16 批次 B2）**：本函数原住 ``session/web_session_db.py``
    （DB 层）。它抛的是 ``HTTPException`` —— 那是 **HTTP 鉴权语义**（状态码 +
    归属校验），不是会话存储。下沉 DB 层到 ``kernel/session/db.py`` 时把它留在
    L1 这里，内核因此保持零 web 框架依赖（方案 §4-B2 决策 2b）。

    全仓 15 处调用方都经由 ``routers.web_session`` 取它，故上移零改动。
    """
    row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Session not found")
    if row["user_id"] != user_id:
        raise HTTPException(status_code=403, detail="Forbidden")
    return row
