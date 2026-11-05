"""Security regression tests (Phase 1 hardening).

Covers:
    - SPA static path traversal blocked
    - Token forgery rejected (HMAC signature required)
    - Mutating /api/system/* requires auth; GET stays public
    - Session IDOR: owner-only read/update/delete/list_messages
    - SSE /chat/events ownership
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def app():
    from agentnodes.interfaces.api.app import create_app
    return create_app(workspace_path=None)


@pytest.fixture
def client(app):
    return TestClient(app)


@pytest.fixture
def auth_headers(client):
    resp = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "admin"},
    )
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


# ── Path traversal ──────────────────────────────────────────────────


class TestPathTraversal:
    def test_dotdot_segments_rejected(self, client, tmp_path: Path):
        """GET /../ etc must never leak files outside the static dir.

        Traversal that escapes the dir → 404. Variants that don't exist
        as real paths fall through to the SPA index.html (200) — the
        security property is that no file contents are served.
        """
        for evil in (
            "..%2f..%2f..%2fetc%2fpasswd",
            "%2e%2e%2f%2e%2e%2f%2e%2e%2fetc%2fpasswd",
            "..%5c..%5cetc%5cpasswd",
        ):
            resp = client.get(f"/{evil}")
            assert resp.status_code in (200, 404), (evil, resp.status_code)
            assert "root:" not in resp.text, f"passwd leaked via {evil}"
            assert resp.status_code == 404 or resp.text.lstrip().lower().startswith(
                "<!doctype html>"
            ), evil

    def test_encoded_traversal_does_not_leak_env(
        self, client, tmp_path: Path
    ):
        """Regression: %2e%2e must not read ~/.quantnodes/.env."""
        import os
        env_path = Path(os.path.expanduser("~/.quantnodes/.env"))
        if not env_path.exists():
            pytest.skip("no ~/.quantnodes/.env on this machine")
        resp = client.get(
            "/%2e%2e/%2e%2e/%2e%2e/%2e%2e/%2e%2e/.quantnodes/.env"
        )
        assert resp.status_code == 404


# ── Token forgery ───────────────────────────────────────────────────


class TestTokenForgery:
    def test_unsigned_token_rejected(self, client):
        """Old bare-base64 tokens must no longer authenticate."""
        import base64

        forged = base64.urlsafe_b64encode(
            json.dumps({"sub": "admin", "exp": 9999999999}).encode()
        ).decode()
        resp = client.get("/api/goal/status", headers={
            "Authorization": f"Bearer {forged}",
        })
        assert resp.status_code == 401

    def test_tampered_signature_rejected(self, client):
        resp = client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "admin"},
        )
        token = resp.json()["access_token"]
        payload, sig = token.rsplit(".", 1)
        bad_sig = ("A" if sig[-1] != "A" else "B") + sig[1:]
        resp2 = client.get("/api/goal/status", headers={
            "Authorization": f"Bearer {payload}.{bad_sig}",
        })
        assert resp2.status_code == 401

    def test_expired_token_rejected(self, client):

        from agentnodes.interfaces.api.auth_tokens import create_token

        expired = create_token("admin", ttl=-100)
        resp = client.get("/api/goal/status", headers={
            "Authorization": f"Bearer {expired}",
        })
        assert resp.status_code == 401

    def test_signed_token_accepted(self, client, auth_headers):
        resp = client.get("/api/goal/status", headers=auth_headers)
        # 401 = auth failed; 422 = missing query params (auth PASSED)
        assert resp.status_code != 401


# ── /api/system/ mutation requires auth ─────────────────────────────


class TestSystemMutationAuth:
    def test_put_llm_requires_token(self, client):
        resp = client.put("/api/system/llm", json={
            "provider": "nvidia", "model": "z-ai/glm-5.2",
        })
        assert resp.status_code == 401

    def test_get_llm_stays_public(self, client):
        resp = client.get("/api/system/llm")
        assert resp.status_code == 200


# ── Session IDOR ────────────────────────────────────────────────────


class TestSessionIDOR:
    def _make_two_users(self, client, auth_headers) -> tuple[str, str]:
        """Create session for 'admin' + a second user's session."""
        import sqlite3
        import time
        import uuid

        from agentnodes.interfaces.api.user_db import get_user_db, hash_password
        db = get_user_db()
        with sqlite3.connect(str(db._db_path)) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO users "
                "(id, username, display_name, password_hash, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (str(uuid.uuid4()), "alice", "Alice",
                 hash_password("alicepw"), time.time()),
            )

        # admin's session
        resp = client.post("/api/chat/session", headers=auth_headers, json={
            "title": "admin-session",
        })
        admin_sid = resp.json()["id"]

        # alice logs in and creates her session
        alice = client.post(
            "/api/auth/login",
            json={"username": "alice", "password": "alicepw"},
        )
        alice_h = {"Authorization": f"Bearer {alice.json()['access_token']}"}
        resp = client.post("/api/chat/session", headers=alice_h, json={
            "title": "alice-session",
        })
        alice_sid = resp.json()["id"]
        return admin_sid, alice_sid, alice_h

    def test_admin_cannot_read_alice_session(self, client, auth_headers):
        _, alice_sid, _ = self._make_two_users(client, auth_headers)
        resp = client.get(f"/api/chat/session/{alice_sid}",
                          headers=auth_headers)
        assert resp.status_code == 403

    def test_admin_cannot_patch_alice_session(self, client, auth_headers):
        _, alice_sid, _ = self._make_two_users(client, auth_headers)
        resp = client.patch(f"/api/chat/session/{alice_sid}",
                            headers=auth_headers, json={"title": "hijack"})
        assert resp.status_code == 403

    def test_admin_cannot_delete_alice_session(self, client, auth_headers):
        _, alice_sid, _ = self._make_two_users(client, auth_headers)
        resp = client.delete(f"/api/chat/session/{alice_sid}",
                             headers=auth_headers)
        assert resp.status_code == 403

    def test_admin_cannot_list_alice_messages(self, client, auth_headers):
        _, alice_sid, _ = self._make_two_users(client, auth_headers)
        resp = client.get(f"/api/chat/session/{alice_sid}/messages",
                          headers=auth_headers)
        assert resp.status_code == 403

    def test_owner_can_access_own_session(self, client, auth_headers):
        admin_sid, _, _ = self._make_two_users(client, auth_headers)
        resp = client.get(f"/api/chat/session/{admin_sid}",
                          headers=auth_headers)
        assert resp.status_code == 200

    def test_anonymous_cannot_read_admin_session(self, client, auth_headers):
        admin_sid, _, _ = self._make_two_users(client, auth_headers)
        resp = client.get(f"/api/chat/session/{admin_sid}")
        assert resp.status_code in (401, 403)

    def test_sse_events_ownership(self, client, auth_headers):
        _, alice_sid, _ = self._make_two_users(client, auth_headers)
        with client.stream(
            "GET",
            f"/api/chat/events?session_id={alice_sid}",
            headers=auth_headers,
        ) as resp:
            assert resp.status_code == 403
