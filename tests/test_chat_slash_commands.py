"""Regression tests for chat slash command intercepts (P31).

The chat router intercepts ``/clear`` and ``/help`` BEFORE the message
reaches the SessionService / AgentLoop, so the LLM is never invoked and
the response is a synthetic assistant message produced by the
corresponding ``_handle_*_command`` helper.

These tests exercise the full HTTP path through ``TestClient`` so the
intercept is locked against future refactors of the routing layer.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

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
    """Session DB under tmp_path via web_session._get_db_path patch.

    Mirrors the fixture in test_chat_send_sync_run_traversal.py so the
    tests in this file share the canonical harness.
    """
    import strategy_research.interfaces.api.routers.web_session as ws
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


def _get_messages(client: TestClient, sid: str, headers: dict) -> list[dict]:
    r = client.get(
        f"/api/chat/session/{sid}/messages",
        headers=headers,
    )
    assert r.status_code == 200
    return r.json()["messages"]


class TestSlashCommandIntercepts:
    """``/clear`` and ``/help`` short-circuit the LLM path.

    Verifies:
    * the response status is ``done`` (no queueing / attempt kicked off)
    * the user + synthetic assistant messages persist via EventStore
      (which writes to ``messages`` through the projector flush)
    * the LLM-bearing path is NOT entered (SessionService.send_message
      is never awaited for the intercepted command)
    """

    def test_clear_command_intercepted(
        self, client: TestClient, auth_headers,
    ) -> None:
        sid = _new_session(client, auth_headers)

        # Patch memory_manager.clear so we can assert it ran once, and
        # patch SessionService.send_message so a regression that lets
        # the request fall through to the LLM would fail loudly.
        clear_mock = AsyncMock(return_value=None)
        send_mock = AsyncMock()

        with patch(
            "strategy_research.kernel.agent.memory_manager.get_default_memory_manager",
            return_value=type(
                "MM",
                (),
                {"clear": clear_mock, "get": AsyncMock(return_value=[])},
            )(),
        ), patch.object(
            client.app.state if hasattr(client.app, "state") else object(),
            "_noop",
            create=True,
        ):
            # Patch the SessionService.send_message on the singleton
            # returned by _get_session_service. We grab the singleton
            # lazily — patch the module attribute before the first call.
            import strategy_research.interfaces.api.routers.chat as chat_router
            cache = chat_router._session_service_cache
            cache.clear()
            original_get = chat_router._get_session_service
            real_service = original_get()
            real_service.send_message = send_mock  # type: ignore[attr-defined]

            r = client.post(
                "/api/chat/send_async",
                json={"session_id": sid, "content": "/clear"},
                headers=auth_headers,
            )

            assert r.status_code == 200, r.text
            body = r.json()
            assert body["status"] == "done"
            assert body.get("attempt_id") is None

            # LLM path was bypassed — send_message never awaited.
            send_mock.assert_not_called()
            # Memory was cleared exactly once with the right session id.
            clear_mock.assert_awaited_once_with(sid)

        # Two rows persisted via EventStore → projector flush.
        rows = _get_messages(client, sid, auth_headers)
        roles = [m["role"] for m in rows]
        assert roles == ["user", "assistant"]
        # Assistant content includes the clear acknowledgement.
        assistant = next(m for m in rows if m["role"] == "assistant")
        assert "已清空" in (assistant.get("content") or "")

    def test_help_command_intercepted(
        self, client: TestClient, auth_headers,
    ) -> None:
        sid = _new_session(client, auth_headers)

        send_mock = AsyncMock()
        import strategy_research.interfaces.api.routers.chat as chat_router
        chat_router._session_service_cache.clear()
        real_service = chat_router._get_session_service()
        real_service.send_message = send_mock  # type: ignore[attr-defined]

        r = client.post(
            "/api/chat/send_async",
            json={"session_id": sid, "content": "/help"},
            headers=auth_headers,
        )

        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "done"
        assert body.get("attempt_id") is None

        send_mock.assert_not_called()

        rows = _get_messages(client, sid, auth_headers)
        assistant = next(m for m in rows if m["role"] == "assistant")
        assert "可用命令" in assistant["content"]
        # The cheat sheet mentions the webui commands.
        # /goal 已随 apps/goal 移除（2026-11-04 批次 2）。
        # /study 已随 apps/study 移除（2026-11-05 批次 3）。
        for cmd in ("/compact", "/clear", "/help"):
            assert cmd in assistant["content"]
        assert "/study" not in assistant["content"]
        assert "/goal" not in assistant["content"]

    def test_clear_with_trailing_args_is_not_matched(
        self, client: TestClient, auth_headers,
    ) -> None:
        """``/clear foo`` must NOT short-circuit — only exact ``/clear``.

        Guards the strict ``content.strip() == "/clear"`` equality check
        against future loosening. The fallback path runs
        ``SessionService.send_message`` (which we mock to raise — the
        point is that ``memory_manager.clear`` was NOT awaited and the
        regular path WAS attempted).
        """
        sid = _new_session(client, auth_headers)

        clear_mock = AsyncMock(return_value=None)
        with patch(
            "strategy_research.kernel.agent.memory_manager.get_default_memory_manager",
            return_value=type(
                "MM",
                (),
                {"clear": clear_mock, "get": AsyncMock(return_value=[])},
            )(),
        ):
            import strategy_research.interfaces.api.routers.chat as chat_router
            chat_router._session_service_cache.clear()
            real_service = chat_router._get_session_service()
            real_service.send_message = AsyncMock(  # type: ignore[attr-defined]
                side_effect=RuntimeError("queue_full_or_llm_unconfigured"),
            )

            # The mock raises inside send_async — TestClient surfaces
            # that as a 500 (Starlette re-raises in this async path).
            # Either way the side-effect assertions below are the real
            # contract: the clear handler must not have run, and the
            # regular send_message path must have been attempted.
            with pytest.raises(RuntimeError, match="queue_full_or_llm_unconfigured"):
                client.post(
                    "/api/chat/send_async",
                    json={"session_id": sid, "content": "/clear foo"},
                    headers=auth_headers,
                )

            clear_mock.assert_not_called()
            real_service.send_message.assert_awaited_once()  # type: ignore[attr-defined]
