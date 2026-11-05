"""Web session API — create/list/update/delete + messages + FTS5 search.

Sessions and messages are persisted to SQLite (same DB as users).

Schema:
  sessions:       id, user_id, title, created_at, updated_at,
                  starred, tags_json, message_count, archived
  messages:       id, session_id (FK CASCADE), role, content,
                  parts_json, created_at, metadata_json
  messages_fts:   FTS5 virtual table over messages(content, role)
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter()


class WebSessionCreate(BaseModel):
    title: str = "新会话"
    id: Optional[str] = None  # explicit id (DAG-orchestrator sessions: "dag:{name}")


class WebSessionUpdate(BaseModel):
    title: Optional[str] = None
    starred: Optional[bool] = None
    archived: Optional[bool] = None
    tags: Optional[list[str]] = None


class SearchRequest(BaseModel):
    query: str
    limit: int = 20


# ─────────────────────────────────────────────────────────────────────────────
# DB helpers
# ─────────────────────────────────────────────────────────────────────────────


from ..session.web_session_db import (  # noqa: E402,F401  (re-exports for old import paths)
    _add_column,
    _drop_column,
    _ensure_fts_table,
    _ensure_fts_table_impl,
    _ensure_schema,
    _fetch_session_owned,
    _get_db,
    _get_db_path,  # noqa: F401  (re-export: tests patch this name)
    _has_column,
    _migrate_message_types,
    _migrate_text_part_ids,
    _row_to_message,
    _row_to_session,
    _run_schema_migrations,
    _validate_identifier,
)


def persist_message(
    session_id: str,
    role: str,
    content: str,
    parts: Optional[list[dict[str, Any]]] = None,
    metadata: Optional[dict[str, Any]] = None,
    message_id: Optional[str] = None,
    created_at: Optional[float] = None,
    tool_call_id: Optional[str] = None,
    message_type: str = "assistant",
    seq: int | None = None,
) -> str:
    """Insert a message and bump session counters. Returns message id.

    Safe to call from background tasks — does not raise on failure (logs only).

    Args:
        message_type: One of 'user' | 'assistant' | 'tool' | 'compaction' | 'error'.
            Defaults to 'assistant' for backward compat.
        seq: Per-session monotonic sequence number (Level 1, opencode-aligned).
            If None, falls back to 0 (the column default). Callers SHOULD
            pass an explicit seq via the SeqGenerator for new messages.
    """
    logger.debug("[DB] persist_message session=%s role=%s type=%s content_len=%d seq=%s",
                session_id, role, message_type, len(content), seq)
    msg_id = message_id or str(uuid.uuid4())
    ts = created_at or time.time()
    parts_json = json.dumps(parts, ensure_ascii=False) if parts is not None else None
    metadata_json = json.dumps(metadata, ensure_ascii=False) if metadata is not None else None
    try:
        conn = _get_db()
        row = conn.execute("SELECT id FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if not row:
            logger.warning("[DB] persist_message: session %s not found", session_id)
            return msg_id

        # S3 idempotency guard: the projector may have already projected
        # the same event into messages (message_received / assistant_message
        # both carry a message_id). Re-inserting would violate UNIQUE(id)
        # and — worse — double-count the session message_count.
        existing = conn.execute(
            "SELECT 1 FROM messages WHERE id = ?", (msg_id,),
        ).fetchone()
        if existing is not None:
            logger.debug(
                "[DB] persist_message: id=%s already present (projector "
                "wrote it first); skipping", msg_id,
            )
            return msg_id

        # Level 2 / Phase 2 commit 6: parts_json and tool_call_id
        # columns were dropped. Build the INSERT dynamically based on
        # which columns are present, so this function works on both
        # pre- and post-migration DBs during the cleanup window.
        has_parts_json = _has_column(conn, "messages", "parts_json")
        has_tool_call_id = _has_column(conn, "messages", "tool_call_id")

        cols = ["id", "session_id", "role", "content", "created_at", "metadata_json", "message_type", "seq"]
        placeholders = ["?", "?", "?", "?", "?", "?", "?", "?"]
        vals: list[Any] = [msg_id, session_id, role, content, ts, metadata_json, message_type, seq or 0]
        if has_parts_json:
            cols.append("parts_json")
            placeholders.append("?")
            vals.append(parts_json)
        if has_tool_call_id:
            cols.append("tool_call_id")
            placeholders.append("?")
            vals.append(tool_call_id)

        sql = (
            f"INSERT INTO messages ({','.join(cols)}) "
            f"VALUES ({','.join(placeholders)})"
        )
        conn.execute(sql, vals)
        logger.debug("[DB] persisted id=%s", msg_id)

        # Dual-write: also insert parts into message_parts (Level 2, Phase 2
        # commit 2). The old parts_json column remains the source of truth
        # until commit 5 switches the read path. This commit only adds
        # the parallel write so commits 3-4 (migration + read switch) can
        # proceed without losing data.
        if parts:
            for i, p in enumerate(parts):
                if not isinstance(p, dict):
                    continue
                part_id = str(uuid.uuid4())
                part_type = p.get("type", "text")
                part_data_json = json.dumps(p, ensure_ascii=False)
                conn.execute(
                    "INSERT INTO message_parts "
                    "(id, message_id, session_id, type, data_json, seq, time_created) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (part_id, msg_id, session_id, part_type, part_data_json, i, ts),
                )

        conn.execute(
            "UPDATE sessions SET message_count = message_count + 1, updated_at = ? WHERE id = ?",
            (ts, session_id),
        )
        conn.commit()
    except Exception as exc:
        logger.error("persist_message failed for session %s: %s", session_id, exc, exc_info=True)
    return msg_id


def auto_title_session(session_id: str, content: str, max_len: int = 30) -> Optional[str]:
    """Set session title from first user message if title is still default.

    Returns the new title if changed, None otherwise.
    """
    if not content or not content.strip():
        return None
    title = content.strip().replace("\n", " ")
    if len(title) > max_len:
        title = title[:max_len].rstrip() + "…"
    try:
        conn = _get_db()
        row = conn.execute(
            "SELECT title, message_count FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        if not row:
            return None
        if row["title"] != "新会话":
            return None
        conn.execute(
            "UPDATE sessions SET title = ?, updated_at = ? WHERE id = ?",
            (title, time.time(), session_id),
        )
        conn.commit()
        return title
    except Exception as exc:
        logger.error("auto_title_session failed: %s", exc)
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Session CRUD endpoints
# ─────────────────────────────────────────────────────────────────────────────


@router.post("")
async def create_session(body: WebSessionCreate, request: Request):
    """Create a new web session.

    ``body.id`` is optional: DAG-orchestrator sessions pass an explicit
    ``dag:{name}`` id (bound to the definition); normal sessions leave it
    unset and get a server-generated uuid. If the id already exists, the
    existing session is returned unchanged (idempotent upsert).
    """
    user_id = getattr(request.state, "user_id", "anonymous")
    session_id = body.id or str(uuid.uuid4())
    now = time.time()
    conn = _get_db()
    existing = conn.execute(
        "SELECT * FROM sessions WHERE id = ?", (session_id,)
    ).fetchone()
    if existing is not None:
        if existing["user_id"] != user_id:
            raise HTTPException(status_code=403, detail="Forbidden")
        return _row_to_session(existing)
    conn.execute(
        "INSERT INTO sessions (id, user_id, title, created_at, updated_at, "
        "starred, tags_json, message_count, archived) "
        "VALUES (?, ?, ?, ?, ?, 0, '[]', 0, 0)",
        (session_id, user_id, body.title, now, now),
    )
    conn.commit()
    return {
        "id": session_id,
        "user_id": user_id,
        "title": body.title,
        "created_at": now,
        "updated_at": now,
        "starred": False,
        "tags": [],
        "message_count": 0,
        "archived": False,
    }


@router.get("")
async def list_sessions(request: Request, limit: int = 50, include_archived: bool = False):
    """List sessions for current user, most recent first."""
    user_id = getattr(request.state, "user_id", "anonymous")
    conn = _get_db()
    sql = (
        "SELECT * FROM sessions WHERE user_id = ? "
        + ("" if include_archived else "AND archived = 0 ")
        + "ORDER BY updated_at DESC LIMIT ?"
    )
    rows = conn.execute(sql, (user_id, limit)).fetchall()
    return {"sessions": [_row_to_session(r) for r in rows]}


@router.get("/{session_id}")
async def get_session(session_id: str, request: Request):
    """Get a single session."""
    user_id = getattr(request.state, "user_id", "anonymous")
    conn = _get_db()
    row = _fetch_session_owned(conn, session_id, user_id)
    return _row_to_session(row)


@router.patch("/{session_id}")
async def update_session(session_id: str, body: WebSessionUpdate, request: Request):
    """Update session metadata: title, starred, archived, tags."""
    user_id = getattr(request.state, "user_id", "anonymous")
    conn = _get_db()
    row = _fetch_session_owned(conn, session_id, user_id)
    now = time.time()
    fields: dict[str, Any] = {"updated_at": now}
    if body.title is not None:
        fields["title"] = body.title
    if body.starred is not None:
        fields["starred"] = 1 if body.starred else 0
    if body.archived is not None:
        fields["archived"] = 1 if body.archived else 0
    if body.tags is not None:
        fields["tags_json"] = json.dumps(body.tags, ensure_ascii=False)

    if len(fields) > 1:  # has at least one user-supplied change
        set_clause = ", ".join(f"{k} = ?" for k in fields)
        params = list(fields.values()) + [session_id]
        conn.execute(f"UPDATE sessions SET {set_clause} WHERE id = ?", params)
        conn.commit()

    # Re-read
    row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
    return _row_to_session(row)


@router.delete("/{session_id}")
async def delete_session(session_id: str, request: Request):
    """Soft-delete a session (mark as archived).

    Messages and event_log are preserved so the projector can continue
    flushing and SSE stays functional for active studies.  The session
    disappears from ``list_sessions`` (which filters ``archived=0``)
    but remains readable via ``GET /{session_id}``.
    """
    user_id = getattr(request.state, "user_id", "anonymous")
    conn = _get_db()
    _fetch_session_owned(conn, session_id, user_id)
    conn.execute("UPDATE sessions SET archived = 1 WHERE id = ?", (session_id,))
    conn.commit()
    _invalidate_projection(session_id, request)
    return {"status": "ok", "deleted_id": session_id}


def _invalidate_projection(session_id: str, request: Request | None = None) -> None:
    """Drop the projector's in-memory state for a deleted session.

    The projector cache lives on the EventStore held by the process-wide
    SessionService. Prefers the DI container (attached by ``create_app``);
    falls back to the process-wide service cache for tests/scripts.
    """
    try:
        if request is not None:
            container = getattr(request.app.state, "_container", None)
            if container is not None:
                bus = container.session_service.event_bus
                invalidate = getattr(bus, "invalidate", None)
                if callable(invalidate):
                    invalidate(session_id)
                return
        from .chat import _session_service_cache
        for service in _session_service_cache.values():
            bus = getattr(service, "event_bus", None)
            invalidate = getattr(bus, "invalidate", None)
            if callable(invalidate):
                invalidate(session_id)
    except Exception as exc:
        logger.debug("projection invalidation skipped: %s", exc)


def delete_messages(session_id: str, message_ids: list[str]) -> None:
    """Delete specific messages by ID from a session."""
    if not message_ids:
        return
    try:
        conn = _get_db()
        placeholders = ",".join("?" for _ in message_ids)
        cur = conn.execute(
            f"DELETE FROM messages WHERE session_id = ? AND id IN ({placeholders})",
            [session_id, *message_ids],
        )
        # Only decrement by the number of rows actually deleted, so
        # message_count can never go negative when some ids don't exist.
        deleted = cur.rowcount
        conn.execute(
            "UPDATE sessions SET message_count = message_count - ?, updated_at = ? WHERE id = ?",
            (deleted, time.time(), session_id),
        )
        conn.commit()
    except Exception as exc:
        logger.error("delete_messages failed for session %s: %s", session_id, exc)



# ─────────────────────────────────────────────────────────────────────────────
# Messages endpoint
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/{session_id}/messages")
async def list_messages(
    session_id: str,
    request: Request,
    limit: int = 200,
    before: Optional[float] = None,
):
    """List messages for a session, oldest first.

    Optional ``before`` cursor (unix ts) for pagination. ``has_more=True`` if
    there are additional older messages beyond ``limit``.

    Order key: ``seq`` (Level 1, opencode-aligned). Per-session monotonic
    counter that's invariant under clock skew. ``before`` continues to
    accept a unix ts; we map it to the maximum seq seen before that ts
    via a subquery for cursor-pagination compatibility.
    """
    user_id = getattr(request.state, "user_id", "anonymous")
    conn = _get_db()
    _fetch_session_owned(conn, session_id, user_id)
    params: list[Any] = [session_id]
    cursor_sql = ""
    if before is not None:
        # Convert unix-ts cursor into a seq cursor by finding the
        # smallest seq with created_at > before. This preserves the
        # external API while using seq internally for ordering.
        cursor_sql = (
            "AND seq > COALESCE("
            "(SELECT MIN(seq) - 1 FROM messages "
            "WHERE session_id = ? AND created_at >= ?), 0)"
        )
        params.extend([session_id, before])
    params.append(limit + 1)  # +1 to detect has_more
    rows = conn.execute(
        f"SELECT * FROM messages WHERE session_id = ? {cursor_sql} "
        "ORDER BY seq ASC LIMIT ?",
        params,
    ).fetchall()
    has_more = len(rows) > limit

    # Batch-fetch parts for all messages in one query (avoids N+1).
    # Level 2 / Phase 2 commit 4: parts are now in the message_parts
    # table. Read from there; fall back to parts_json only if no
    # message_parts rows exist (pre-migration rows).
    if rows:
        message_ids = [r["id"] for r in rows[:limit]]
        placeholders = ",".join("?" * len(message_ids))
        parts_rows = conn.execute(
            f"SELECT message_id, data_json FROM message_parts "
            f"WHERE message_id IN ({placeholders}) ORDER BY message_id, seq",
            message_ids,
        ).fetchall()
        # Group by message_id, preserving seq order. Only include
        # messages that have at least one message_parts row; missing
        # keys (via .get) signal to _row_to_message that the caller
        # didn't pre-fetch (so it falls back to parts_json).
        parts_by_msg: dict[str, list[Any]] = {}
        for mid, data_json in parts_rows:
            try:
                part = json.loads(data_json)
                if isinstance(part, dict):
                    parts_by_msg.setdefault(mid, []).append(part)
            except (json.JSONDecodeError, TypeError):
                pass
    else:
        parts_by_msg = {}
    messages = [
        _row_to_message(r, parts=parts_by_msg.get(r["id"]))
        for r in rows[:limit]
    ]
    total = conn.execute(
        "SELECT COUNT(*) AS c FROM messages WHERE session_id = ?", (session_id,)
    ).fetchone()["c"]
    return {"messages": messages, "has_more": has_more, "total": total}


# ─────────────────────────────────────────────────────────────────────────────
# Session state (B13): backfill agent / DAG / goal panels after reload.
# goal / workflow 面板已随 apps/goal 等移除（2026-11-04 批次 2）：
# state 端点保留，goal/workflow 恒为 null，agents 恒为空。
# ─────────────────────────────────────────────────────────────────────────────


from ..session.web_session_state import (  # noqa: F401  (re-exports)
    _shape_agents_for_frontend,
)


@router.get("/{session_id}/state")
async def get_session_state(session_id: str, request: Request):
    """Backfill panels after a page reload (B13).

    goal / workflow 子面板已随 apps/goal 移除（批次 2）：恒返回 null，
    前端显示空态；agents 列表恒为空（SSE 流负责刷新 chat 主面板）。
    """
    user_id = getattr(request.state, "user_id", "anonymous")
    conn = _get_db()
    _fetch_session_owned(conn, session_id, user_id)

    return {
        "goal": None,
        "workflow": None,
        "agents": [],
    }


# ─────────────────────────────────────────────────────────────────────────────
# FTS5 search endpoint
# ─────────────────────────────────────────────────────────────────────────────


def _make_snippet(content: str, query: str, max_len: int = 120) -> str:
    """Extract a snippet around the first match, escaping HTML and wrapping matches in <mark>."""
    import html

    if not content:
        return ""
    if not query:
        return html.escape(content[:max_len])
    # Find first occurrence of any query token (whitespace-split)
    tokens = [t for t in query.split() if t]
    lower = content.lower()
    first_pos = -1
    for tok in tokens:
        pos = lower.find(tok.lower())
        if pos != -1 and (first_pos == -1 or pos < first_pos):
            first_pos = pos
    if first_pos == -1:
        snippet = content[:max_len]
    else:
        # Window around the match
        start = max(0, first_pos - 30)
        end = min(len(content), first_pos + max_len - 30)
        snippet = content[start:end]
        if start > 0:
            snippet = "…" + snippet
        if end < len(content):
            snippet = snippet + "…"
    escaped = html.escape(snippet)
    # Highlight tokens (case-insensitive)
    for tok in tokens:
        if not tok:
            continue
        pattern = html.escape(tok)
        # Use simple find/replace with case-insensitive matching via lowered scan
        out_parts = []
        cursor = 0
        low = escaped.lower()
        while True:
            idx = low.find(pattern.lower(), cursor)
            if idx == -1:
                out_parts.append(escaped[cursor:])
                break
            out_parts.append(escaped[cursor:idx])
            out_parts.append(f"<mark>{escaped[idx:idx + len(pattern)]}</mark>")
            cursor = idx + len(pattern)
        escaped = "".join(out_parts)
    return escaped


@router.post("/search")
async def search_sessions(body: SearchRequest, request: Request):
    """Full-text search across all messages.

    Returns hits with session metadata + highlighted snippet. Joins with
    sessions table to filter by current user_id.
    """
    user_id = getattr(request.state, "user_id", "anonymous")
    conn = _get_db()
    if not body.query or not body.query.strip():
        return {"hits": [], "query": body.query}

    # 用 LIKE 子串匹配，不用 FTS。
    #
    # 为什么不走 FTS5/trigram（保留 messages_fts 表但搜索不再依赖它）：
    # - trigram 分词器要求查询串 >= 3 字符，中文双字词（策略/回测/动量/因子）
    #   一律零命中——中文是这个产品的主要语言；
    # - 这不是大规模文档检索：实测真实库 190 条消息 LIKE 全表扫描 0.07ms，
    #   20 万条也只有 50ms；FTS 的索引反而占 208% 额外空间、还需三个触发器维护；
    # - 用户要的是「包含这个词」，不是相关度排序，BM25 的排序价值在这里为零。
    # 若将来真的出现百万级单会话，再引入带中文分词器的 FTS 也不迟。
    tokens = [t.strip() for t in body.query.split() if t.strip()]
    if not tokens:
        return {"hits": [], "query": body.query}

    # 多关键词 = 全部命中（AND 语义，与此前 FTS 行为一致）。
    # ESCAPE 处理 % / _ 通配符，避免用户输入被当成模式。
    like_clauses = []
    params: list[Any] = []
    for tok in tokens:
        escaped = tok.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        like_clauses.append("m.content LIKE ? ESCAPE '\\'")
        params.append(f"%{escaped}%")

    rows = conn.execute(
        f"""
        SELECT
            m.id AS message_id,
            m.session_id,
            m.role,
            m.content,
            m.created_at,
            s.title AS session_title
        FROM messages m
        JOIN sessions s ON s.id = m.session_id
        WHERE {' AND '.join(like_clauses)}
          AND s.user_id = ?
          AND s.archived = 0
        ORDER BY m.created_at DESC
        LIMIT ?
        """,
        (*params, user_id, body.limit),
    ).fetchall()

    hits = []
    for r in rows:
        hits.append(
            {
                "session_id": r["session_id"],
                "session_title": r["session_title"],
                "message_id": r["message_id"],
                "role": r["role"],
                "snippet": _make_snippet(r["content"], body.query),
                # LIKE 无相关度分数；保留字段以维持响应契约
                "score": 0.0,
                "created_at": r["created_at"],
            }
        )
    return {"hits": hits, "query": body.query}
