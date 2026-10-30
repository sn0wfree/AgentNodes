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
    from ..agent.memory_manager import resolve_session_db_path

    path = Path(db_path) if db_path is not None else resolve_session_db_path()
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


__all__ = ["list_sessions"]
