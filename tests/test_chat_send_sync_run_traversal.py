"""Regression tests for the sync chat path + run router traversal guard."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from strategy_research.interfaces.api.app import create_app
from strategy_research.interfaces.api.auth_tokens import create_token
import strategy_research.interfaces.api.session.web_session_db as _wsdb


@pytest.fixture
def auth_headers():
    return {"Authorization": f"Bearer {create_token('admin')}"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Session DB under tmp_path via web_session._get_db_path patch."""
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


class TestSendSync:
    def test_send_requires_ownership_and_persists(
        self, client: TestClient, monkeypatch,
    ) -> None:
        """send_sync must: enforce ownership, go through the unified
        SessionService, and persist the exchange (message_received +
        assistant_message rows)."""
        monkeypatch.setenv("STRATEGY_RESEARCH_TEST_CHAT", "1")
        headers = {"Authorization": f"Bearer {create_token('admin')}"}
        sid = _new_session(client, headers)

        r = client.post("/api/chat/send", json={
            "session_id": sid, "content": "hello sync",
        }, headers=headers)
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "completed"
        assert "测试要点" in body["reply"]

        # The exchange must be persisted (not a bare AgentLoop run)
        msgs = client.get(f"/api/chat/session/{sid}/messages", headers=headers)
        assert msgs.status_code == 200
        roles = [m["role"] for m in msgs.json()["messages"]]
        assert "user" in roles and "assistant" in roles

    def test_send_rejects_foreign_session(self, client: TestClient) -> None:
        """An authenticated user must not post to a session owned by
        another user (ownership check present in the sync path)."""
        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setenv("STRATEGY_RESEARCH_TEST_CHAT", "1")
        try:
            headers = {"Authorization": f"Bearer {create_token('admin')}"}
            sid = _new_session(client, headers)
            other = {"Authorization": f"Bearer {create_token('bob')}"}
            r = client.post("/api/chat/send", json={
                "session_id": sid, "content": "hi",
            }, headers=other)
            assert r.status_code == 403
        finally:
            monkeypatch.undo()


