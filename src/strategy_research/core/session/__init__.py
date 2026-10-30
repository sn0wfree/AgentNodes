"""Session persistence.

B4 Step 4-5 (2026-10-30): the legacy stack (``db`` / ``manager`` / ``metrics``
/ ``models`` / ``rate_limiter``) was retired. The unified DB — the same
``sessions`` / ``messages`` tables everyone else writes through the projector —
is the only session store now; ``unified`` is its read/write surface.
"""
from __future__ import annotations

from .unified import (
    delete_session,
    get_messages,
    get_session,
    list_sessions,
    search_messages,
)

__all__ = [
    "delete_session",
    "get_messages",
    "get_session",
    "list_sessions",
    "search_messages",
]
