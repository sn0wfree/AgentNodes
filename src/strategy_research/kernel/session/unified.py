"""Unified-DB session listing (B4 D5).

Why this exists: five consumers need "list sessions" — CLI ``session list``,
REPL ``/history``, TUI resume, MCP ``list_sessions``, and the admin views.
B4's investigation found no reusable function among them:

- ``api.routers.web_session.list_sessions`` is a FastAPI endpoint bound to a
  ``Request`` (ownership + DI), not callable from a CLI.
- ``PlatformStore.list_sessions`` only works in per-session-directory mode
  (it raises in single-DB mode).
- ``core.session.db.SessionDB.list_sessions`` reads the *legacy* database
  that B4 retires.

So this is the one function the less-is-more rule permits: a single 5-line
query shared by all of them, pointed at the unified DB (the same file
``resolve_session_db_path`` resolves for everything else).
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any


def list_sessions(
    db_path: Path | None = None,
    *,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Return recent sessions from the unified database, newest first.

    Returns plain dicts (``id``, ``title``, ``updated_at``, ``created_at``)
    rather than a dataclass: the consumers disagree on shape and a dataclass
    would only be re-adapted by each of them.
    """
    path = _resolve(db_path)
    if not path.exists():
        return []
    conn = sqlite3.connect(str(path), timeout=5.0)
    try:
        rows = conn.execute(
            "SELECT id, title, created_at, updated_at FROM sessions "
            "ORDER BY updated_at DESC LIMIT ?",
            (max(1, int(limit)),),
        ).fetchall()
    finally:
        conn.close()
    return [
        {
            "id": r[0],
            "title": r[1],
            "created_at": r[2],
            "updated_at": r[3],
        }
        for r in rows
    ]


def get_session(session_id: str, db_path: Path | None = None) -> dict[str, Any] | None:
    """Return one session row as a dict, or None (B4 Step 3).

    Replaces ``SessionDB.get_session`` for the CLI/TUI resume paths. Those
    consumers used to read ``session.metadata_json`` / ``session.session_id``
    -- attributes the legacy dataclass never had, so they raised
    AttributeError or silently got None. Reading the unified table directly
    returns the columns that actually exist.
    """
    path = _resolve(db_path)
    if not path.exists():
        return None
    conn = sqlite3.connect(str(path), timeout=5.0)
    try:
        row = conn.execute(
            "SELECT id, user_id, title, created_at, updated_at, starred, "
            "tags_json, message_count, archived FROM sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    return {
        "id": row[0],
        "user_id": row[1],
        "title": row[2],
        "created_at": row[3],
        "updated_at": row[4],
        "starred": bool(row[5]),
        "tags_json": row[6],
        "message_count": row[7],
        "archived": bool(row[8]),
    }


def get_messages(
    session_id: str,
    *,
    limit: int = 50,
    db_path: Path | None = None,
) -> list[dict[str, Any]]:
    """Return a session's messages, oldest first (B4 Step 3).

    ``created_at`` (not the legacy ``timestamp``) is the column that exists.
    """
    path = _resolve(db_path)
    if not path.exists():
        return []
    conn = sqlite3.connect(str(path), timeout=5.0)
    try:
        rows = conn.execute(
            "SELECT id, role, content, created_at, message_type, metadata_json "
            "FROM messages WHERE session_id = ? "
            "ORDER BY seq, created_at LIMIT ?",
            (session_id, max(1, int(limit))),
        ).fetchall()
    finally:
        conn.close()
    return [
        {
            "id": r[0],
            "role": r[1],
            "content": r[2],
            "created_at": r[3],
            "message_type": r[4],
            "metadata_json": r[5],
        }
        for r in rows
    ]


def search_messages(
    query: str,
    *,
    limit: int = 20,
    db_path: Path | None = None,
) -> list[dict[str, Any]]:
    """LIKE search across all sessions (B4 Step 3).

    Replaces ``SessionDB.search_messages`` (legacy FTS5 index). The current
    web search endpoint already uses LIKE (search was moved off trigram FTS
    because of tokenizer behaviour), so this matches the live semantics
    instead of re-introducing a second index.
    """
    path = _resolve(db_path)
    if not path.exists() or not query.strip():
        return []
    conn = sqlite3.connect(str(path), timeout=5.0)
    try:
        rows = conn.execute(
            "SELECT id, session_id, role, content, created_at FROM messages "
            "WHERE content LIKE ? ORDER BY created_at DESC LIMIT ?",
            (f"%{query}%", max(1, int(limit))),
        ).fetchall()
    finally:
        conn.close()
    return [
        {
            "id": r[0],
            "session_id": r[1],
            "role": r[2],
            "content": r[3],
            "created_at": r[4],
        }
        for r in rows
    ]


def delete_session(session_id: str, db_path: Path | None = None) -> bool:
    """Delete a session and its messages (B4 Step 3).

    Returns True when a session row existed and was removed. Messages go with
    it via the FK cascade; the explicit DELETE keeps behaviour identical when
    foreign_keys is off on a fresh connection.
    """
    path = _resolve(db_path)
    if not path.exists():
        return False
    conn = sqlite3.connect(str(path), timeout=5.0)
    try:
        existed = conn.execute(
            "SELECT 1 FROM sessions WHERE id = ?", (session_id,),
        ).fetchone()
        if existed is None:
            return False
        conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
        conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        conn.commit()
    finally:
        conn.close()
    return True


def _resolve(db_path: Path | None) -> Path:
    from strategy_research.kernel.agent.memory_manager import resolve_session_db_path

    return Path(db_path) if db_path is not None else resolve_session_db_path()


__all__ = [
    "delete_session",
    "get_messages",
    "get_session",
    "list_sessions",
    "search_messages",
]
