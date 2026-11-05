"""Direct unit tests for the UserDB store (api/user_db.py).

These cover the storage layer in isolation (no HTTP): schema creation,
role / is_active columns + legacy-table migration, CRUD, pagination,
partial updates, and the seed_admin_if_empty helper.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from agentnodes.interfaces.api.user_db import (
    UserDB,
    get_user_db,
    hash_password,
    seed_admin_if_empty,
)


@pytest.fixture
def db(tmp_path: Path) -> UserDB:
    return UserDB(tmp_path / "users.db")


# ── Schema / migration ───────────────────────────────────────────


def test_schema_has_role_and_is_active_columns(db: UserDB) -> None:
    cols = {r[1] for r in db._get_conn().execute("PRAGMA table_info(users)")}
    assert "role" in cols
    assert "is_active" in cols
    assert "username" in cols
    assert "password_hash" in cols


def test_legacy_table_gets_role_is_active_migrated(tmp_path: Path) -> None:
    """A pre-existing users table WITHOUT role/is_active gets them added."""
    path = tmp_path / "users.db"
    conn = sqlite3.connect(str(path))
    conn.execute(
        "CREATE TABLE users ("
        " id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL, display_name TEXT,"
        " password_hash TEXT NOT NULL, created_at REAL NOT NULL)"
    )
    conn.execute(
        "INSERT INTO users (id, username, display_name, password_hash, created_at) "
        "VALUES ('u1', 'legacy', 'Legacy', 'hash', 1.0)"
    )
    conn.commit()
    conn.close()

    db = UserDB(path)
    user = db.get_user_by_username("legacy")
    assert user is not None
    assert user["role"] == "user"
    assert user["is_active"] == 1


# ── CRUD ────────────────────────────────────────────────────────


def test_create_and_get_by_username(db: UserDB) -> None:
    user = db.create_user("alice", "Alice", "hash", role="admin")
    assert user["username"] == "alice"
    fetched = db.get_user_by_username("alice")
    assert fetched["display_name"] == "Alice"
    assert fetched["role"] == "admin"
    assert fetched["is_active"] == 1


def test_create_default_role_is_user(db: UserDB) -> None:
    user = db.create_user("bob", "Bob", "hash")
    assert user["role"] == "user"


def test_get_user_by_id(db: UserDB) -> None:
    user = db.create_user("carol", "Carol", "hash")
    assert db.get_user_by_id(user["id"])["username"] == "carol"
    assert db.get_user_by_id("nope") is None


def test_get_user_by_username_missing(db: UserDB) -> None:
    assert db.get_user_by_username("ghost") is None


def test_duplicate_username_raises(db: UserDB) -> None:
    db.create_user("dave", "Dave", "hash")
    with pytest.raises(sqlite3.IntegrityError):
        db.create_user("dave", "Dave2", "hash2")


# ── update_password ─────────────────────────────────────────────


def test_update_password_changes_hash(db: UserDB) -> None:
    user = db.create_user("eve", "Eve", "old-hash")
    assert db.update_password(user["id"], "new-hash") is True
    assert db.get_user_by_id(user["id"])["password_hash"] == "new-hash"


def test_update_password_unknown_user_returns_false(db: UserDB) -> None:
    assert db.update_password("missing", "hash") is False


# ── update_user (partial) ───────────────────────────────────────


def test_update_user_role(db: UserDB) -> None:
    user = db.create_user("frank", "Frank", "hash")
    updated = db.update_user(user["id"], role="admin")
    assert updated["role"] == "admin"
    assert db.get_user_by_id(user["id"])["role"] == "admin"


def test_update_user_display_name(db: UserDB) -> None:
    user = db.create_user("grace", "Grace", "hash")
    updated = db.update_user(user["id"], display_name="G")
    assert updated["display_name"] == "G"


def test_update_user_disable(db: UserDB) -> None:
    user = db.create_user("hank", "Hank", "hash")
    updated = db.update_user(user["id"], is_active=0)
    assert updated["is_active"] == 0
    updated2 = db.update_user(user["id"], is_active=1)
    assert updated2["is_active"] == 1


def test_update_user_no_fields_returns_current(db: UserDB) -> None:
    user = db.create_user("iris", "Iris", "hash")
    updated = db.update_user(user["id"])
    assert updated["username"] == "iris"


def test_update_user_unknown_return_none(db: UserDB) -> None:
    assert db.update_user("missing", role="admin") is None


# ── list / count / pagination ───────────────────────────────────


def test_list_users_ordered_and_paginated(db: UserDB) -> None:
    for i in range(5):
        db.create_user(f"u{i}", f"U{i}", f"hash{i}")
    all_users = db.list_users(limit=100)
    assert [u["username"] for u in all_users] == [f"u{i}" for i in range(5)]

    page = db.list_users(limit=2, offset=1)
    assert [u["username"] for u in page] == ["u1", "u2"]


def test_count_users(db: UserDB) -> None:
    assert db.count_users() == 0
    db.create_user("a1", "A1", "h")
    db.create_user("a2", "A2", "h")
    assert db.count_users() == 2


def test_user_count(db: UserDB) -> None:
    assert db.user_count() == 0
    db.create_user("b1", "B1", "h")
    assert db.user_count() == 1


# ── get_user_db singleton ───────────────────────────────────────#


def test_get_user_db_singleton_per_workspace(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    db1 = get_user_db(ws)
    db2 = get_user_db(ws)
    assert db1 is db2
    assert (ws / "quantnodes_agentnodes_user.db").exists()


def test_get_user_db_different_workspaces_isolated(tmp_path: Path) -> None:
    a = get_user_db(tmp_path / "a")
    b = get_user_db(tmp_path / "b")
    assert a is not b


# ── seed_admin_if_empty ─────────────────────────────────────────#


def test_seed_admin_when_empty(db: UserDB) -> None:
    seed_admin_if_empty(db)
    admin = db.get_user_by_username("admin")
    assert admin is not None
    assert admin["role"] == "admin"
    assert admin["is_active"] == 1


def test_seed_admin_idempotent(db: UserDB) -> None:
    seed_admin_if_empty(db)
    seed_admin_if_empty(db)
    assert db.get_user_by_username("admin") is not None
    assert db.count_users() == 1


def test_seed_admin_skips_when_nonempty(db: UserDB) -> None:
    db.create_user("zed", "Zed", "hash")
    seed_admin_if_empty(db)
    # No admin created because the table was not empty.
    assert db.get_user_by_username("admin") is None


# ── hash_password format (SEC-1) ────────────────────────────────#


def test_hash_password_format() -> None:
    hashed = hash_password("secret")
    assert hashed.startswith("pbkdf2:260000:")
    parts = hashed.split(":")
    assert len(parts) == 4

    import hashlib
    iterations = int(parts[1])
    salt = bytes.fromhex(parts[2])
    expected = bytes.fromhex(parts[3])
    dk = hashlib.pbkdf2_hmac("sha256", b"secret", salt, iterations)
    assert dk == expected


def test_hash_password_is_salted() -> None:
    h1 = hash_password("same")
    h2 = hash_password("same")
    assert h1 != h2