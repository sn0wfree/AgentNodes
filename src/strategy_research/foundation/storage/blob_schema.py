"""Canonical ``blob_refs`` schema (P0-1 C3).

Tracks every sidecar blob written by ``_offload_large_fields`` so the
TTL-based cleanup has a single source of truth for ``first_seen`` /
``last_access`` / ``ref_count``. The blob files themselves live under
``<event-db-parent>/trace-blobs/``; the rows here are pure metadata.

v0.1 simplification: ``ref_count`` is monotonically incremented on every
offload (since the event_log is append-only). Cleanup is purely a
``last_access + TTL`` query — no decrement path. This is consistent
with the financial-compliance audit posture: an "active" event_log row
implicitly keeps its blob alive forever.
"""

from __future__ import annotations

import sqlite3

__all__ = [
    "BLOB_REFS_DDL",
    "ensure_blob_refs_schema",
    "record_blob_offload",
    "list_stale_blobs",
]

# Reference-count + last-access tracker for sidecar blobs.
# Stale-bucket candidates are rows whose last_access is older than
# ``SR_BLOB_TTL_DAYS`` (default 365 — financial compliance).
BLOB_REFS_DDL = """
CREATE TABLE IF NOT EXISTS blob_refs (
    blob_path TEXT PRIMARY KEY,
    ref_count INTEGER NOT NULL DEFAULT 0,
    first_seen REAL NOT NULL,
    last_access REAL NOT NULL
)
"""

BLOB_REFS_INDEX_DDL = (
    "CREATE INDEX IF NOT EXISTS idx_blob_refs_last_access "
    "ON blob_refs(last_access)"
)


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    try:
        cur = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        )
        return cur.fetchone() is not None
    except sqlite3.Error:
        return False


def ensure_blob_refs_schema(conn: sqlite3.Connection) -> None:
    """Create the ``blob_refs`` table and its index. Idempotent."""
    conn.execute(BLOB_REFS_DDL)
    conn.execute(BLOB_REFS_INDEX_DDL)
    conn.commit()


def record_blob_offload(
    conn: sqlite3.Connection,
    blob_path: str,
    now: float | None = None,
) -> None:
    """Record an offload event: increment ``ref_count`` and stamp
    ``last_access``. ``first_seen`` is set on the first write.

    Must run inside the same transaction that writes the offloaded
    event_log row, so the ref_count + event stay consistent.
    """
    import time as _time

    when = now if now is not None else _time.time()
    if not _table_exists(conn, "blob_refs"):
        ensure_blob_refs_schema(conn)
    conn.execute(
        "INSERT INTO blob_refs (blob_path, ref_count, first_seen, last_access) "
        "VALUES (?, 1, ?, ?) "
        "ON CONFLICT(blob_path) DO UPDATE SET "
        "ref_count = blob_refs.ref_count + 1, "
        "last_access = excluded.last_access",
        (blob_path, when, when),
    )


def list_stale_blobs(
    conn: sqlite3.Connection,
    *,
    ttl_days: int = 365,
    now: float | None = None,
    limit: int | None = None,
) -> list[dict]:
    """Return blob_refs rows whose ``last_access`` is older than TTL.

    v0.1: cleanup candidates only — actual file deletion is a separate
    audit-logged step (see ``scripts/cleanup_blobs.py``). The script
    should: list candidates, write an audit row, then ``os.unlink``
    each blob file + DELETE the blob_refs row.
    """
    import time as _time

    if not _table_exists(conn, "blob_refs"):
        return []
    when = now if now is not None else _time.time()
    threshold = when - ttl_days * 86400
    sql = (
        "SELECT blob_path, ref_count, first_seen, last_access "
        "FROM blob_refs WHERE last_access < ? "
        "ORDER BY last_access ASC"
    )
    if limit is not None:
        sql += " LIMIT ?"
        params: tuple = (threshold, limit)
    else:
        params = (threshold,)
    rows = conn.execute(sql, params).fetchall()
    return [
        {
            "blob_path": r[0],
            "ref_count": r[1],
            "first_seen": r[2],
            "last_access": r[3],
        }
        for r in rows
    ]
