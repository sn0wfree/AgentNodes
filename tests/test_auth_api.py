"""Auth endpoint tests: /me + /change-password (token-bound semantics)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from agentnodes.interfaces.api.app import create_app
from agentnodes.interfaces.api.auth_tokens import create_token
from agentnodes.interfaces.api.routers import auth as auth_router


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Isolated user DB under tmp_path (never touches ~/.quantnodes)."""
    import agentnodes.interfaces.api.user_db as user_db
    from agentnodes.interfaces.api.routers import auth as auth_router
    real_get = user_db.get_user_db
    monkeypatch.setattr(user_db, "get_user_db", lambda *a, **k: real_get(tmp_path))
    monkeypatch.setattr(auth_router, "_user_db", None)
    return TestClient(create_app())


def _login(client: TestClient) -> dict:
    resp = client.post("/api/auth/login", json={
        "username": "admin", "password": "admin",
    })
    assert resp.status_code == 200
    return resp.json()


def test_me_requires_token(client: TestClient) -> None:
    resp = client.get("/api/auth/me")
    assert resp.status_code == 401


def test_me_returns_current_user(client: TestClient) -> None:
    body = _login(client)
    headers = {"Authorization": f"Bearer {body['access_token']}"}
    resp = client.get("/api/auth/me", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["username"] == "admin"


def test_me_rejects_invalid_token(client: TestClient) -> None:
    resp = client.get("/api/auth/me", headers={
        "Authorization": "Bearer forged-token",
    })
    assert resp.status_code == 401


def test_change_password_requires_token(client: TestClient) -> None:
    resp = client.post("/api/auth/change-password", json={
        "old_password": "admin", "new_password": "admin2",
    })
    assert resp.status_code == 401


def test_change_password_token_bound(client: TestClient) -> None:
    """Password change must apply to the token's user only, and the old
    password must match that user's hash (no global hash scan)."""
    body = _login(client)
    headers = {"Authorization": f"Bearer {body['access_token']}"}

    resp = client.post("/api/auth/change-password", headers=headers, json={
        "old_password": "wrong-old", "new_password": "admin2",
    })
    assert resp.status_code == 401

    resp = client.post("/api/auth/change-password", headers=headers, json={
        "old_password": "admin", "new_password": "admin2",
    })
    assert resp.status_code == 200

    # Old password no longer works; new one does
    resp = client.post("/api/auth/login", json={
        "username": "admin", "password": "admin",
    })
    assert resp.status_code == 401
    resp = client.post("/api/auth/login", json={
        "username": "admin", "password": "admin2",
    })
    assert resp.status_code == 200


def test_change_password_second_user_isolated(client: TestClient) -> None:
    """A second user sharing the same password must not be affected by
    the first user's change (regression: previous global hash scan
    updated the FIRST match, which could be the wrong account)."""
    db = auth_router._get_user_db()
    from agentnodes.interfaces.api.user_db import hash_password
    db.create_user("bob", "Bob", hash_password("shared"))
    db.create_user("alice", "Alice", hash_password("shared"))

    body = _login(client)
    headers = {"Authorization": f"Bearer {body['access_token']}"}
    client.post("/api/auth/change-password", headers=headers, json={
        "old_password": "shared", "new_password": "newpass",
    })

    # bob's login must still work with "shared"
    resp = client.post("/api/auth/login", json={
        "username": "bob", "password": "shared",
    })
    assert resp.status_code == 200


def test_change_password_updates_token_users_hash(client: TestClient) -> None:
    """The change applies to the token-bound account specifically."""
    db = auth_router._get_user_db()
    from agentnodes.interfaces.api.user_db import hash_password
    db.create_user("bob", "Bob", hash_password("bobpass"))
    bob = db.get_user_by_username("bob")

    headers = {"Authorization": f"Bearer {create_token(bob['id'])}"}
    client.post("/api/auth/change-password", headers=headers, json={
        "old_password": "bobpass", "new_password": "bobnew",
    })

    resp = client.post("/api/auth/login", json={
        "username": "bob", "password": "bobnew",
    })
    assert resp.status_code == 200
