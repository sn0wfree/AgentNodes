"""Shared SQLite store infrastructure (P2: storage-layer unification).

Single home for the connection/transaction/JSON/id boilerplate that was
copy-pasted across ``GoalStore``, ``StudyStore``, ``HypothesisStore`` and
friends:

- ``now_iso`` / ``new_id`` / ``json_dumps`` / ``json_loads`` — column helpers
- ``resolve_db_path`` — env-override or ``~/.quantnodes-research/<db>``
- ``connect`` — PRAGMA-tuned connection (WAL, busy_timeout, FK, NORMAL)
- ``synchronized`` — per-store RLock decorator for shared connections
- ``write_transaction`` — ``BEGIN IMMEDIATE`` write transaction
- ``table_columns`` / ``ensure_column`` / ``user_version`` / ``set_user_version``
  — lightweight migration primitives
"""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from typing import Any, Callable, Iterator, TypeVar

F = TypeVar("F", bound=Callable)

DEFAULT_DATA_DIR = Path.home() / ".quantnodes-research"


# ── Column / value helpers ────────────────────────────────────────


def now_iso() -> str:
    """UTC ISO-8601 timestamp for ledger columns."""
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str) -> str:
    """Random 12-hex id with a domain prefix (e.g. ``goal_ab12cd34ef56``)."""
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def json_dumps(value: object) -> str:
    """Canonical JSON encoding for ledger JSON columns."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def json_loads(value: str | None, default: object) -> object:
    """Decode a ledger JSON column; missing/empty → ``default``."""
    if not value:
        return default
    return json.loads(value)


# ── Path / connection ─────────────────────────────────────────────


def resolve_db_path(db_name: str, env_var: str) -> Path:
    """Resolve a store's DB path: env override, else ``~/.quantnodes-research/<db>``."""
    raw_path = os.environ.get(env_var, "").strip()
    if raw_path:
        return Path(raw_path).expanduser()
    return DEFAULT_DATA_DIR / db_name


def connect(path: Path) -> sqlite3.Connection:
    """Open a SQLite connection with the standard PRAGMA tuning.

    - ``check_same_thread=False`` so the shared connection can be used
      from the asyncio event loop / worker threads (callers serialize
      access with ``synchronized``).
    - WAL + ``synchronous=NORMAL`` for read/write concurrency.
    - ``foreign_keys=ON`` + ``busy_timeout`` for cross-connection safety.
    """
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


# ── Threading / transactions ──────────────────────────────────────


def synchronized(method: F) -> F:
    """Serialize access to a shared SQLite connection (expects ``self._lock``)."""

    @wraps(method)
    def wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:  # type: ignore[misc]
        with self._lock:
            return method(self, *args, **kwargs)

    return wrapper  # type: ignore[return-value]


@contextmanager
def write_transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """``BEGIN IMMEDIATE`` write transaction with commit/rollback.

    ``BEGIN IMMEDIATE`` takes the write lock up front, which avoids
    ``database is locked`` races when multiple processes/connections
    share the same SQLite file.
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


# ── Migration primitives ──────────────────────────────────────────


def user_version(conn: sqlite3.Connection) -> int:
    """Read ``PRAGMA user_version`` (0 when unversioned)."""
    return int(conn.execute("PRAGMA user_version").fetchone()[0])


def set_user_version(conn: sqlite3.Connection, version: int) -> None:
    """Write ``PRAGMA user_version``."""
    conn.execute(f"PRAGMA user_version = {version}")


def table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    """Column names of a table (empty set when the table does not exist)."""
    try:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    except sqlite3.OperationalError:
        return set()
    return {str(row["name"]) for row in rows}


def ensure_column(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> bool:
    """Add a column to a table if missing. Returns True when added."""
    if column in table_columns(conn, table):
        return False
    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
    return True
