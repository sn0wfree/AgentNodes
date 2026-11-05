"""Regression tests for the sync chat path + run router traversal guard."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from agentnodes.interfaces.api.app import create_app
from agentnodes.interfaces.api.auth_tokens import create_token
import agentnodes.interfaces.api.session.web_session_db as _wsdb


@pytest.fixture
def auth_headers():
    return {"Authorization": f"Bearer {create_token('admin')}"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Session DB under tmp_path via web_session._get_db_path patch."""
    import agentnodes.interfaces.api.routers.web_session as ws
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
        """send 必须：校验归属、经 Gateway、持久化这轮对话。

        2026-11-05：P5-1c ④ 之后 ``/api/chat/send`` 唯一路径是
        ``_send_via_gateway``（WorkRequest → mailbox → 引擎），
        ``AGENTNODES_TEST_CHAT`` 只对已删除的 SessionService 直连分支生效。
        这里 mock 掉 Gateway 驱动部分（它需要真实 LLM），只验证
        「归属校验 + 落库」这两条本测试钉住的契约。
        """
        headers = {"Authorization": f"Bearer {create_token('admin')}"}
        sid = _new_session(client, headers)

        import agentnodes.interfaces.api.routers.chat as chat_router

        persisted: list[tuple[str, str]] = []

        async def fake_send_via_gateway(session_id: str, content: str, *, wait: bool = False):
            persisted.append((session_id, content))
            # 复现真实路径的落库动作（event_bus → projector）。
            service = chat_router._get_session_service()
            service.event_bus.emit(session_id, "message_received", {
                "message_id": "m-user",
                "role": "user",
                "content": content,
                "created_at": 0.0,
            })
            service.event_bus.emit(session_id, "assistant_message", {
                "message_id": "m-asst",
                "role": "assistant",
                "content": "测试要点",
                "created_at": 0.1,
            })
            return {"status": "completed", "reply": "测试要点"}

        monkeypatch.setattr(chat_router, "_send_via_gateway", fake_send_via_gateway)

        r = client.post("/api/chat/send", json={
            "session_id": sid, "content": "hello sync",
        }, headers=headers)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "completed"
        assert "测试要点" in body["reply"]
        assert persisted == [(sid, "hello sync")]

        # The exchange must be persisted (not a bare AgentLoop run)
        msgs = client.get(f"/api/chat/session/{sid}/messages", headers=headers)
        assert msgs.status_code == 200
        roles = [m["role"] for m in msgs.json()["messages"]]
        assert "user" in roles and "assistant" in roles

    def test_send_rejects_foreign_session(self, client: TestClient) -> None:
        """An authenticated user must not post to a session owned by
        another user (ownership check present in the sync path)."""
        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setenv("AGENTNODES_TEST_CHAT", "1")
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


