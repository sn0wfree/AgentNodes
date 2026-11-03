"""P5-5② security regression: trace endpoint ownership enforcement.

The trace endpoint exposes a session's full LLM trajectory (system_prompt,
tool schemas). Before this fix ANY caller (including tokenless) could read
ANY session's trace. Ownership semantics mirror the other chat endpoints
(_fetch_session_owned → 404/403).
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import strategy_research.interfaces.api.session.web_session_db as _wsdb
from strategy_research.interfaces.api.app import create_app
from strategy_research.interfaces.api.auth_tokens import create_token


@pytest.fixture
def client(tmp_path, monkeypatch):
    import strategy_research.interfaces.api.routers.web_session as ws
    monkeypatch.setattr(_wsdb, "_get_db_path", lambda: str(tmp_path / "sr.db"))
    monkeypatch.setattr(ws, "_get_db_path", lambda: str(tmp_path / "sr.db"))
    return TestClient(create_app())


def _new_session(client: TestClient, headers: dict) -> str:
    r = client.post("/api/chat/session", json={
        "user_id": "anonymous", "title": "t",
    }, headers=headers)
    assert r.status_code == 200
    return r.json()["id"]


def test_trace_rejects_foreign_user(client: TestClient) -> None:
    """Another authenticated user must not read a session's trace."""
    owner = {"Authorization": f"Bearer {create_token('admin')}"}
    sid = _new_session(client, owner)

    other = {"Authorization": f"Bearer {create_token('bob')}"}
    r = client.get(f"/api/chat/session/{sid}/trace", headers=other)
    assert r.status_code == 403


def test_trace_rejects_tokenless_caller(client: TestClient) -> None:
    """A tokenless caller is rejected at the middleware layer (P5-5①:
    /api/chat/ is no longer a public prefix → 401 before ownership)."""
    owner = {"Authorization": f"Bearer {create_token('admin')}"}
    sid = _new_session(client, owner)

    r = client.get(f"/api/chat/session/{sid}/trace")
    assert r.status_code == 401


def test_trace_allows_owner(client: TestClient) -> None:
    """The owner reads their session's trace (empty trace → 200 + events)."""
    owner = {"Authorization": f"Bearer {create_token('admin')}"}
    sid = _new_session(client, owner)

    r = client.get(f"/api/chat/session/{sid}/trace", headers=owner)
    assert r.status_code == 200
    body = r.json()
    assert body["session_id"] == sid
    assert "events" in body


def test_trace_unknown_session_is_404(client: TestClient) -> None:
    """Unknown session id → 404 (not 500, no existence oracle beyond that)."""
    headers = {"Authorization": f"Bearer {create_token('admin')}"}
    r = client.get("/api/chat/session/session_nonexistent/trace", headers=headers)
    assert r.status_code == 404
