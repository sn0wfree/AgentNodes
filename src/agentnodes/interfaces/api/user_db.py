"""SQLite-backed user store — replaces in-memory dict for persistence.

Usage:
    from agentnodes.interfaces.api.user_db import get_user_db
    db = get_user_db("/path/to/workspace")
    db.create_user("admin", "Admin", hash_password("admin"))
    user = db.get_user_by_username("admin")
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class UserDB:
    """Thin wrapper around SQLite for user storage."""

    def __init__(self, db_path: Path):
        self._db_path = db_path
        self._conn: Optional[sqlite3.Connection] = None
        self._lock = threading.Lock()
        self._ensure_table()

    def _get_conn(self) -> sqlite3.Connection:
        # check_same_thread=False: FastAPI serves requests on a thread
        # pool; a per-instance cached connection must be usable from any
        # worker thread (writes are serialized via self._lock).
        if self._conn is None:
            self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def _ensure_table(self) -> None:
        conn = self._get_conn()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                username TEXT UNIQUE NOT NULL,
                display_name TEXT,
                password_hash TEXT NOT NULL,
                created_at REAL NOT NULL,
                role TEXT NOT NULL DEFAULT 'user',
                is_active INTEGER NOT NULL DEFAULT 1
            )
        """)
        # Migration: add role / is_active to pre-existing users tables.
        cols = {r[1] for r in conn.execute("PRAGMA table_info(users)")}
        if "role" not in cols:
            conn.execute(
                "ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'user'"
            )
        if "is_active" not in cols:
            conn.execute(
                "ALTER TABLE users ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1"
            )
        conn.commit()

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    # ── CRUD ──────────────────────────────────────────────────

    def create_user(
        self,
        username: str,
        display_name: str,
        password_hash: str,
        user_id: Optional[str] = None,
        created_at: Optional[float] = None,
        role: str = "user",
        is_active: int = 1,
    ) -> dict:
        """Create a new user. Returns the user dict."""
        uid = user_id or str(uuid.uuid4())
        ts = created_at or time.time()
        conn = self._get_conn()
        with self._lock:
            conn.execute(
                "INSERT INTO users "
                "(id, username, display_name, password_hash, created_at, role, is_active) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (uid, username, display_name, password_hash, ts, role, is_active),
            )
            conn.commit()
        return {
            "id": uid,
            "username": username,
            "display_name": display_name,
            "password_hash": password_hash,
            "created_at": ts,
            "role": role,
            "is_active": is_active,
        }

    def update_password(self, user_id: str, new_password_hash: str) -> bool:
        """Set a user's password hash. Returns True if the user exists."""
        conn = self._get_conn()
        with self._lock:
            cur = conn.execute(
                "UPDATE users SET password_hash = ? WHERE id = ?",
                (new_password_hash, user_id),
            )
            conn.commit()
        return cur.rowcount > 0

    def update_user(
        self,
        user_id: str,
        *,
        role: Optional[str] = None,
        display_name: Optional[str] = None,
        is_active: Optional[int] = None,
    ) -> Optional[dict]:
        """Partially update a user's role / display_name / is_active.

        Returns the updated user dict, or None if the user does not exist.
        """
        sets: list[str] = []
        params: list = []
        if role is not None:
            sets.append("role = ?")
            params.append(role)
        if display_name is not None:
            sets.append("display_name = ?")
            params.append(display_name)
        if is_active is not None:
            sets.append("is_active = ?")
            params.append(is_active)
        if not sets:
            return self.get_user_by_id(user_id)
        params.append(user_id)
        conn = self._get_conn()
        with self._lock:
            cur = conn.execute(
                f"UPDATE users SET {', '.join(sets)} WHERE id = ?", params
            )
            conn.commit()
        if cur.rowcount == 0:
            return None
        return self.get_user_by_id(user_id)

    def list_users(
        self, *, limit: int = 100, offset: int = 0
    ) -> list[dict]:
        """List users ordered by created_at, with pagination."""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM users ORDER BY created_at ASC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
        return [dict(r) for r in rows]

    def count_users(self) -> int:
        """Total number of user rows."""
        conn = self._get_conn()
        return conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]

    def get_user_by_username(self, username: str) -> Optional[dict]:
        """Look up a user by username, or None."""
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM users WHERE username = ?", (username,)
        ).fetchone()
        return dict(row) if row else None

    def get_user_by_id(self, user_id: str) -> Optional[dict]:
        """Look up a user by id, or None."""
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        return dict(row) if row else None

    def user_count(self) -> int:
        """Number of users in the store."""
        conn = self._get_conn()
        return conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]


# ── Module-level singleton per workspace ───────────────────────

_dbs: dict[str, UserDB] = {}


def get_user_db(workspace_path: Optional[Path] = None) -> UserDB:
    """Get or create a UserDB for the given workspace.

    If workspace_path is None, uses a default location under ~/.quantnodes/.
    """
    if workspace_path is None:
        workspace_path = Path.home() / ".quantnodes"
    workspace_path = Path(workspace_path)
    key = str(workspace_path.resolve())

    if key not in _dbs:
        db_dir = workspace_path
        db_dir.mkdir(parents=True, exist_ok=True)
        _dbs[key] = UserDB(db_dir / "quantnodes_agentnodes_user.db")

    return _dbs[key]


def hash_password(password: str) -> str:
    """SEC-1: PBKDF2-HMAC-SHA256 password hash (260k iterations + salt)."""
    import hashlib as _hl
    import os as _os
    salt = _os.urandom(16)
    dk = _hl.pbkdf2_hmac("sha256", password.encode(), salt, 260_000)
    return f"pbkdf2:260000:{salt.hex()}:{dk.hex()}"


def seed_admin_if_empty(db: UserDB) -> None:
    """Insert the first admin account if the users table is empty.

    This runs on every startup. It's idempotent — only inserts if count == 0.
    The seeded admin account is a superuser (role='admin').

    SEC-4 (P5-5④): the seed password is no longer the known constant
    ``admin``. Resolution order:
        1. ``SR_ADMIN_PASSWORD`` env (ops/tests opt-in to a known value);
        2. otherwise a random ``secrets.token_urlsafe`` password, persisted
           once to ``~/.quantnodes/admin_password.txt`` (0600 where
           supported) and logged once at first start — the standard
           initial-admin-password pattern (Jenkins/Grafana).

    Existing deployments with a seeded admin are untouched (count > 0
    short-circuits before any password logic).
    """
    if db.user_count() > 0:
        return
    import os as _os
    password = _os.environ.get("SR_ADMIN_PASSWORD", "")
    if password:
        source = "SR_ADMIN_PASSWORD"
    else:
        import secrets as _secrets
        password = _secrets.token_urlsafe(18)
        source = "generated"
        _persist_admin_password(password)
    db.create_user(
        username="admin",
        display_name="Admin",
        password_hash=hash_password(password),
        role="admin",
    )
    if source == "generated":
        logger.warning(
            "SEC-4: first-start admin account created with a RANDOM "
            "password — retrieve it from ~/.quantnodes/admin_password.txt "
            "(it is not 'admin'; change it after first login). Set "
            "SR_ADMIN_PASSWORD to control the seed explicitly.",
        )


def _persist_admin_password(password: str) -> None:
    """Best-effort one-time persistence of the generated seed password."""
    try:
        pw_file = Path.home() / ".quantnodes" / "admin_password.txt"
        pw_file.parent.mkdir(parents=True, exist_ok=True)
        pw_file.write_text(password + "\n", encoding="utf-8")
        try:
            pw_file.chmod(0o600)
        except OSError:
            pass  # POSIX perms unavailable (Windows) — best effort
    except OSError:
        logger.warning("could not persist admin seed password file")
