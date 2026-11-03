"""SEC-4 (P5-5④): first-start admin seed no longer uses the known
'admin' password — SR_ADMIN_PASSWORD env opt-in, otherwise random +
one-time persistence to ~/.quantnodes/admin_password.txt.

The conftest autouse fixture sets SR_ADMIN_PASSWORD=admin for the test
suite; these tests override it locally to exercise the production paths.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from strategy_research.interfaces.api.routers.auth import _verify_password
from strategy_research.interfaces.api.user_db import UserDB, seed_admin_if_empty


@pytest.fixture
def db(tmp_path) -> UserDB:
    return UserDB(tmp_path / "users.db")


def test_random_seed_when_no_env(db: UserDB, monkeypatch) -> None:
    """Production path: random password, never 'admin', persisted once."""
    monkeypatch.delenv("SR_ADMIN_PASSWORD", raising=False)

    seed_admin_if_empty(db)

    user = db.get_user_by_username("admin")
    assert not _verify_password("admin", user["password_hash"]), (
        "the known constant must not authenticate after SEC-4",
    )
    pw_file = Path.home() / ".quantnodes" / "admin_password.txt"
    assert pw_file.exists(), "generated password must be retrievable"
    stored = pw_file.read_text(encoding="utf-8").strip()
    assert stored and stored != "admin"
    assert _verify_password(stored, user["password_hash"])


def test_env_opt_in_seed(db: UserDB, monkeypatch) -> None:
    """SR_ADMIN_PASSWORD controls the seed explicitly (ops/tests)."""
    monkeypatch.setenv("SR_ADMIN_PASSWORD", "s3cret-op")

    seed_admin_if_empty(db)

    user = db.get_user_by_username("admin")
    assert _verify_password("s3cret-op", user["password_hash"])
    assert not _verify_password("admin", user["password_hash"])


def test_seed_is_idempotent(db: UserDB, monkeypatch) -> None:
    """Existing deployments are untouched (count > 0 short-circuits)."""
    monkeypatch.setenv("SR_ADMIN_PASSWORD", "first")

    seed_admin_if_empty(db)
    first = db.get_user_by_username("admin")

    monkeypatch.setenv("SR_ADMIN_PASSWORD", "second")
    seed_admin_if_empty(db)

    again = db.get_user_by_username("admin")
    assert first["id"] == again["id"]
    assert db.user_count() == 1
    assert _verify_password("first", again["password_hash"])
