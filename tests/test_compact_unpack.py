"""Regression: service.compact_history used to unpack compact_messages'
4-tuple as a 2-tuple, raising ValueError on every /compact invocation.

The user-visible symptom was: typing /compact in chat surfaced a
synthetic assistant message reading "❌ 压缩失败: too many values to
unpack (expected 2)". This test pins that contract so a future
refactor of compact_messages' return signature does not regress
the manual /compact path.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from strategy_research.interfaces.api.app import create_app
from strategy_research.interfaces.api.auth_tokens import create_token


@pytest.fixture
def auth_headers():
    return {"Authorization": f"Bearer {create_token('admin')}"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Session DB under tmp_path via web_session._get_db_path patch.

    Mirrors the fixture in test_chat_send_sync_run_traversal.py so the
    tests in this file share the canonical harness.
    """
    import strategy_research.interfaces.api.routers.web_session as ws
    import strategy_research.interfaces.api.session.web_session_db as _wsdb
    monkeypatch.setattr(_wsdb, "_get_db_path", lambda: str(tmp_path / "sr.db"))
    monkeypatch.setattr(ws, "_get_db_path", lambda: str(tmp_path / "sr.db"))
    return TestClient(create_app())


def _new_session(client: TestClient, headers: dict) -> str:
    r = client.post(
        "/api/chat/session",
        json={"user_id": "anonymous", "title": "t"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    return r.json()["id"]


class TestCompactUnpackFix:
    """Manual /compact via send_async must succeed, not surface the
    pre-existing 'too many values to unpack (expected 2)' error.
    """

    def test_compact_history_directly_does_not_raise_unpack(
        self, client: TestClient,
    ) -> None:
        """Direct unit-level test: service.compact_history() returns
        without raising ValueError even on an empty session (which
        still exercises the tuple unpack). This guards the contract
        at the unit boundary so the API-level test below cannot
        regress silently if someone refactors compact_messages again.
        """
        # Reset the cached session service so the test sees a fresh
        # singleton bound to the TestClient's tmp_path DB.
        import strategy_research.interfaces.api.routers.chat as chat_router
        chat_router._session_service_cache.clear()

        from strategy_research.interfaces.api.routers.chat import _get_session_service

        service = _get_session_service()
        # Empty session — should return cleanly, never raise unpack.
        result = asyncio.run(
            service.compact_history(session_id="ghost-session-id")
        )
        assert isinstance(result, dict)
        assert "layers" in result
        assert "before_tokens" in result
        assert "after_tokens" in result
        assert "summary" in result
        # Empty input -> no layers applied.
        assert result["layers"] == []
        assert result["before_tokens"] == 0
        assert result["after_tokens"] == 0

    def test_compact_via_send_async_does_not_leak_unpack_error(
        self, client: TestClient, auth_headers,
    ) -> None:
        """End-to-end via TestClient: POST /compact must NOT surface
        the pre-existing 'too many values to unpack' error in the
        synthetic assistant message. /compact hits the
        ``_handle_compact_command`` interceptor (Tier A P31) which
        delegates to service.compact_history; before the fix, the
        function raised ValueError before producing any layers, so
        the except branch produced the leak.
        """
        sid = _new_session(client, auth_headers)

        r = client.post(
            "/api/chat/send_async",
            json={"session_id": sid, "content": "/compact"},
            headers=auth_headers,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "done"
        assert body.get("attempt_id") is None

        msgs = client.get(
            f"/api/chat/session/{sid}/messages",
            headers=auth_headers,
        )
        assert msgs.status_code == 200
        rows = msgs.json()["messages"]
        # User echo + synthetic assistant ack.
        assert [m["role"] for m in rows] == ["user", "assistant"]
        compact_reply = rows[-1].get("content") or ""
        # The bug being locked.
        assert "too many values to unpack" not in compact_reply
        # Empty history -> "no compression needed" fallback path.
        assert "无需压缩" in compact_reply
