"""Superuser user-management API tests.

Covers the admin_users router: role-gated access, user CRUD, role
promote/demote, disable/enable, reset password, and self-protection.
Uses low-iteration password hashing to keep PBKDF2 cost fast in tests.
"""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from strategy_research.interfaces.api.app import create_app
from strategy_research.interfaces.api.auth_tokens import create_token


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Isolated user DB under tmp_path; low-cost password hashing."""
    import strategy_research.interfaces.api.user_db as user_db
    from strategy_research.interfaces.api.routers import auth as auth_router

    # Fast tests: shrink PBKDF2 iterations.
    monkeypatch.setattr(auth_router, "_DEFAULT_ITERATIONS", 1000)
    monkeypatch.setattr(user_db, "hash_password",
                        lambda pw: auth_router._hash_password(pw))

    real_get = user_db.get_user_db
    monkeypatch.setattr(user_db, "get_user_db", lambda *a, **k: real_get(tmp_path))
    monkeypatch.setattr(auth_router, "_user_db", None)
    return TestClient(create_app())


def _login(client: TestClient, username: str = "admin", password: str = "admin") -> dict:
    resp = client.post("/api/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _auth(body: dict) -> dict:
    return {"Authorization": f"Bearer {body['access_token']}"}


def _create_user(client: TestClient, username: str, password: str = "pw", admin="admin") -> dict:
    resp = client.post("/api/admin/users", headers=_auth(_login(client, admin)),
                       json={"username": username, "password": password,
                             "display_name": username.title()})
    assert resp.status_code == 201, resp.text
    return resp.json()


# ── Authorization gating ─────────────────────────────────────────


def test_non_admin_cannot_list_users(client: TestClient) -> None:
    _create_user(client, "bob")
    bob = _login(client, "bob", "pw")
    resp = client.get("/api/admin/users", headers=_auth(bob))
    assert resp.status_code == 403


def test_anonymous_cannot_list_users(client: TestClient) -> None:
    resp = client.get("/api/admin/users")
    assert resp.status_code == 401


def test_admin_can_list_users(client: TestClient) -> None:
    _create_user(client, "alice")
    resp = client.get("/api/admin/users", headers=_auth(_login(client)))
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] >= 2
    usernames = {u["username"] for u in data["users"]}
    assert "admin" in usernames and "alice" in usernames


# ── CRUD ────────────────────────────────────────────────────────


def test_create_user_then_login(client: TestClient) -> None:
    _create_user(client, "carol")
    body = _login(client, "carol", "pw")
    assert body["user"]["role"] == "user"
    assert body["user"]["is_active"] is True


def test_create_duplicate_username_409(client: TestClient) -> None:
    _create_user(client, "dave")
    resp = client.post("/api/admin/users", headers=_auth(_login(client)),
                       json={"username": "dave", "password": "pw"})
    assert resp.status_code == 409


def test_create_demotes_unknown_role_to_user(client: TestClient) -> None:
    resp = client.post("/api/admin/users", headers=_auth(_login(client)),
                       json={"username": "erin", "password": "pw", "role": "root"})
    assert resp.status_code == 201
    assert resp.json()["role"] == "user"


def test_reset_password(client: TestClient) -> None:
    created = _create_user(client, "frank")
    resp = client.post(
        f"/api/admin/users/{created['id']}/reset-password",
        headers=_auth(_login(client)),
        json={"new_password": "newpass"},
    )
    assert resp.status_code == 200
    _login(client, "frank", "newpass")


# ── Role promote / demote ───────────────────────────────────────


def test_promote_to_admin_can_then_manage(client: TestClient) -> None:
    created = _create_user(client, "grace")
    resp = client.patch(
        f"/api/admin/users/{created['id']}",
        headers=_auth(_login(client)),
        json={"role": "admin"},
    )
    assert resp.status_code == 200
    assert resp.json()["role"] == "admin"

    grace = _login(client, "grace", "pw")
    resp = client.get("/api/admin/users", headers=_auth(grace))
    assert resp.status_code == 200


# ── Disable / enable ────────────────────────────────────────────


def test_disable_blocks_login_and_me(client: TestClient) -> None:
    created = _create_user(client, "hank")
    resp = client.post(
        f"/api/admin/users/{created['id']}/disable",
        headers=_auth(_login(client)),
    )
    assert resp.status_code == 200

    # Login blocked.
    resp = client.post("/api/auth/login", json={"username": "hank", "password": "pw"})
    assert resp.status_code == 403

    # Existing token no longer works (middleware checks is_active).
    token = create_token(created["id"])
    resp = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403

    # Re-enable.
    resp = client.post(
        f"/api/admin/users/{created['id']}/enable",
        headers=_auth(_login(client)),
    )
    assert resp.status_code == 200
    _login(client, "hank", "pw")


# ── Self-protection ─────────────────────────────────────────────


def test_admin_cannot_disable_own_account(client: TestClient) -> None:
    me = _login(client)
    self_id = me["user"]["id"]
    resp = client.post(
        f"/api/admin/users/{self_id}/disable",
        headers=_auth(me),
    )
    assert resp.status_code == 400


def test_admin_cannot_demote_own_role(client: TestClient) -> None:
    me = _login(client)
    self_id = me["user"]["id"]
    resp = client.patch(
        f"/api/admin/users/{self_id}",
        headers=_auth(me),
        json={"role": "user"},
    )
    assert resp.status_code == 400


# ── Data audit view ─────────────────────────────────────────────


def test_user_data_returns_counts(client: TestClient) -> None:
    created = _create_user(client, "iris")
    resp = client.get(
        f"/api/admin/users/{created['id']}/data",
        headers=_auth(_login(client)),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["user_id"] == created["id"]
    assert data["sessions"] == 0 or data["sessions"] is None
    assert data["studies"] == 0 or data["studies"] is None


# ── X-Admin-Token static-token path ────────────────────────────


def test_static_admin_token_can_manage(monkeypatch, client: TestClient) -> None:
    """The static X-Admin-Token (SR_ADMIN_TOKEN) is accepted as admin."""
    monkeypatch.setenv("SR_ADMIN_TOKEN", "static-secret")
    resp = client.get("/api/admin/users", headers={"X-Admin-Token": "static-secret"})
    assert resp.status_code == 200
    assert "users" in resp.json()


def test_static_admin_token_wrong_rejected(client: TestClient, monkeypatch) -> None:
    monkeypatch.setenv("SR_ADMIN_TOKEN", "static-secret")
    resp = client.get("/api/admin/users", headers={"X-Admin-Token": "wrong"})
    assert resp.status_code == 401


def test_static_admin_token_self_protection(
    monkeypatch, client: TestClient
) -> None:
    """Token-based admin (acting='__token__') is exempt from the
    self-protection guard (no single user to protect)."""
    monkeypatch.setenv("SR_ADMIN_TOKEN", "static-secret")
    headers = {"X-Admin-Token": "static-secret"}

    # token admin can create and disable a named user
    resp = client.post(
        "/api/admin/users",
        headers=headers,
        json={"username": "jane", "password": "pw"},
    )
    assert resp.status_code == 201
    created = resp.json()
    resp = client.post(
        f"/api/admin/users/{created['id']}/disable", headers=headers,
    )
    assert resp.status_code == 200


def test_static_admin_token_create_uses_token_actor(
    monkeypatch, client: TestClient
) -> None:
    monkeypatch.setenv("SR_ADMIN_TOKEN", "static-secret")
    resp = client.post(
        "/api/admin/users",
        headers={"X-Admin-Token": "static-secret"},
        json={"username": "kane", "password": "pw", "role": "admin"},
    )
    assert resp.status_code == 201
    assert resp.json()["role"] == "admin"
    _login(client, "kane", "pw")