"""Tests for auth router and SSE event buffer."""

import sqlite3
import time
import uuid

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from agentnodes.interfaces.api.app import create_app
    from agentnodes.interfaces.api.auth_tokens import create_token

    app = create_app()
    c = TestClient(app)
    # P5-5①: /api/chat/ no longer public
    c.headers.update({"Authorization": f"Bearer {create_token('tester')}"})
    return c


class TestAuth:
    def test_register_disabled(self, client):
        """Registration is disabled — returns 403."""
        res = client.post("/api/auth/register", json={
            "username": "testuser",
            "display_name": "Test User",
            "password": "pass123",
        })
        assert res.status_code == 403

    def test_register_disabled_always(self, client):
        """Even duplicate usernames return 403 (register disabled)."""
        res = client.post("/api/auth/register", json={
            "username": "dup_user",
            "password": "pass",
        })
        assert res.status_code == 403

    def test_login_success(self, client):
        """Default admin/admin is seeded on first startup."""
        res = client.post("/api/auth/login", json={
            "username": "admin",
            "password": "admin",
        })
        assert res.status_code == 200
        assert "access_token" in res.json()
        assert res.json()["user"]["username"] == "admin"

    def test_login_wrong_password(self, client):
        res = client.post("/api/auth/login", json={
            "username": "admin",
            "password": "wrong",
        })
        assert res.status_code == 401

    def test_login_nonexistent(self, client):
        res = client.post("/api/auth/login", json={
            "username": "nonexistent",
            "password": "pass",
        })
        assert res.status_code == 401




class TestWebSession:
    def test_create_session(self, client):
        res = client.post("/api/chat/session", json={"title": "Test Session"})
        assert res.status_code == 200
        data = res.json()
        assert data["title"] == "Test Session"
        assert "id" in data

    def test_list_sessions(self, client):
        client.post("/api/chat/session", json={"title": "S1"})
        client.post("/api/chat/session", json={"title": "S2"})
        res = client.get("/api/chat/session")
        assert res.status_code == 200
        sessions = res.json()["sessions"]
        assert len(sessions) >= 2

    def test_delete_session(self, client):
        res = client.post("/api/chat/session", json={"title": "To Delete"})
        session_id = res.json()["id"]
        res = client.delete(f"/api/chat/session/{session_id}")
        assert res.status_code == 200


class TestChatAPI:
    def _stub_send(self, status="processing"):
        """Stub the session service so send_async never starts a real
        AgentLoop background task (which would block TestClient close)."""
        from unittest.mock import MagicMock, patch

        svc = MagicMock()
        async def _send(*args, **kwargs):
            return {
                "message_id": "m1",
                "user_message_id": "um1",
                "assistant_message_id": "am1",
                "event_id": "e1",
                "status": status,
            }
        svc.send_message = _send
        return patch("agentnodes.interfaces.api.routers.chat._get_session_service",
                     return_value=svc)

    def test_send_async(self, client):
        # Session must exist before sending (ownership check)
        sid = client.post("/api/chat/session", json={"title": "S"}).json()["id"]
        with self._stub_send():
            res = client.post("/api/chat/send_async", json={
                "session_id": sid,
                "content": "Hello",
            })
        assert res.status_code == 200
        data = res.json()
        assert "message_id" in data
        assert "event_id" in data
        assert data["status"] == "processing"

class TestSessionMessages:
    def test_create_session_with_new_fields(self, client):
        """New sessions include starred/tags/message_count/archived defaults."""
        res = client.post("/api/chat/session", json={"title": "新会话"})
        assert res.status_code == 200
        data = res.json()
        assert data["starred"] is False
        assert data["tags"] == []
        assert data["message_count"] == 0
        assert data["archived"] is False

    def test_patch_session_title(self, client):
        res = client.post("/api/chat/session", json={"title": "Old"})
        sid = res.json()["id"]
        res = client.patch(f"/api/chat/session/{sid}", json={"title": "New"})
        assert res.status_code == 200
        assert res.json()["title"] == "New"

    def test_patch_session_starred(self, client):
        res = client.post("/api/chat/session", json={"title": "S"})
        sid = res.json()["id"]
        res = client.patch(f"/api/chat/session/{sid}", json={"starred": True})
        assert res.status_code == 200
        assert res.json()["starred"] is True

    def test_patch_session_tags(self, client):
        res = client.post("/api/chat/session", json={"title": "S"})
        sid = res.json()["id"]
        res = client.patch(f"/api/chat/session/{sid}", json={"tags": ["alpha", "策略"]})
        assert res.status_code == 200
        assert res.json()["tags"] == ["alpha", "策略"]

    def test_patch_session_archived(self, client):
        res = client.post("/api/chat/session", json={"title": "S"})
        sid = res.json()["id"]
        res = client.patch(f"/api/chat/session/{sid}", json={"archived": True})
        assert res.status_code == 200
        assert res.json()["archived"] is True

    def test_patch_multiple_fields(self, client):
        res = client.post("/api/chat/session", json={"title": "S"})
        sid = res.json()["id"]
        res = client.patch(f"/api/chat/session/{sid}", json={
            "title": "T2",
            "starred": True,
            "tags": ["x"],
            "archived": True,
        })
        assert res.status_code == 200
        body = res.json()
        assert body["title"] == "T2"
        assert body["starred"] is True
        assert body["tags"] == ["x"]
        assert body["archived"] is True

    def test_patch_404(self, client):
        res = client.patch(f"/api/chat/session/nonexistent-{uuid.uuid4()}", json={"title": "X"})
        assert res.status_code == 404


class TestMessages:
    def _create_session(self, client):
        res = client.post("/api/chat/session", json={"title": "测试"})
        return res.json()["id"]

    def test_list_messages_empty(self, client):
        sid = self._create_session(client)
        res = client.get(f"/api/chat/session/{sid}/messages")
        assert res.status_code == 200
        body = res.json()
        assert body["messages"] == []
        assert body["total"] == 0
        assert body["has_more"] is False

    def test_list_messages_404(self, client):
        res = client.get(f"/api/chat/session/nonexistent-{uuid.uuid4()}/messages")
        assert res.status_code == 404

    def test_persist_message_via_helper(self, client):
        from agentnodes.interfaces.api.routers.web_session import (
            _get_db,
            persist_message,
        )
        sid = self._create_session(client)
        msg_id = persist_message(
            session_id=sid,
            role="user",
            content="测试消息",
        )
        # Verify it's in DB
        conn = _get_db()
        row = conn.execute(
            "SELECT * FROM messages WHERE id = ?", (msg_id,)
        ).fetchone()
        assert row is not None
        assert row["role"] == "user"
        assert row["content"] == "测试消息"
        # Verify session counter incremented
        sess = conn.execute(
            "SELECT message_count FROM sessions WHERE id = ?", (sid,)
        ).fetchone()
        assert sess["message_count"] == 1

    def test_persist_message_with_parts(self, client):
        from agentnodes.interfaces.api.routers.web_session import persist_message
        sid = self._create_session(client)
        parts = [
            {"type": "text", "text": "Hello"},
            {"type": "tool_call", "id": "t1", "name": "fetch", "arguments": "{}"},
        ]
        persist_message(session_id=sid, role='assistant', content='Hello', parts=parts)
        res = client.get(f"/api/chat/session/{sid}/messages")
        msgs = res.json()["messages"]
        assert len(msgs) == 1
        m = msgs[0]
        assert m["role"] == "assistant"
        assert m["content"] == "Hello"
        assert m["parts"] == parts

    def test_delete_session_cascades_messages(self, client):
        """Soft-delete preserves messages (no CASCADE)."""
        from agentnodes.interfaces.api.routers.web_session import persist_message
        sid = self._create_session(client)
        persist_message(session_id=sid, role="user", content="msg1")
        persist_message(session_id=sid, role="assistant", content="reply")
        # Verify 2 messages exist
        res = client.get(f"/api/chat/session/{sid}/messages")
        assert res.json()["total"] == 2
        # Delete session (soft-delete)
        client.delete(f"/api/chat/session/{sid}")
        # Messages should still be there (soft delete preserves them)
        from agentnodes.interfaces.api.routers.web_session import _get_db
        conn = _get_db()
        count = conn.execute(
            "SELECT COUNT(*) AS c FROM messages WHERE session_id = ?", (sid,)
        ).fetchone()["c"]
        assert count == 2

    def test_soft_delete_session_hidden_from_list(self, client):
        """Soft-deleted session is hidden from list_sessions default view."""
        res = client.post("/api/chat/session", json={"title": "To Hide"})
        sid = res.json()["id"]
        # Verify it appears in list
        res = client.get("/api/chat/session")
        ids = [s["id"] for s in res.json()["sessions"]]
        assert sid in ids
        # Soft-delete
        client.delete(f"/api/chat/session/{sid}")
        # Should be hidden from default list
        res = client.get("/api/chat/session")
        ids = [s["id"] for s in res.json()["sessions"]]
        assert sid not in ids

    def test_soft_delete_session_visible_with_include_archived(self, client):
        """Soft-deleted session visible when include_archived=True."""
        res = client.post("/api/chat/session", json={"title": "Archived"})
        sid = res.json()["id"]
        client.delete(f"/api/chat/session/{sid}")
        res = client.get("/api/chat/session?include_archived=true")
        ids = [s["id"] for s in res.json()["sessions"]]
        assert sid in ids

    def test_soft_delete_session_still_readable(self, client):
        """Soft-deleted session is still accessible via GET."""
        res = client.post("/api/chat/session", json={"title": "Readable"})
        sid = res.json()["id"]
        client.delete(f"/api/chat/session/{sid}")
        res = client.get(f"/api/chat/session/{sid}")
        assert res.status_code == 200
        assert res.json()["archived"] is True

    def test_messages_pagination(self, client):
        from agentnodes.interfaces.api.routers.web_session import persist_message
        sid = self._create_session(client)
        for i in range(5):
            persist_message(
                session_id=sid, role="user",
                content=f"msg-{i}",
                created_at=time.time() + i,
            )
        res = client.get(f"/api/chat/session/{sid}/messages?limit=2")
        body = res.json()
        assert len(body["messages"]) == 2
        assert body["has_more"] is True
        assert body["total"] == 5


class TestAutoTitle:
    def test_auto_title_from_first_message(self, client):
        from agentnodes.interfaces.api.routers.web_session import (
            _get_db,
            auto_title_session,
        )
        res = client.post("/api/chat/session", json={"title": "新会话"})
        sid = res.json()["id"]
        new_title = auto_title_session(sid, "帮我设计一个 alpha 策略")
        assert new_title is not None
        assert new_title.startswith("帮我设计一个")
        conn = _get_db()
        row = conn.execute("SELECT title FROM sessions WHERE id = ?", (sid,)).fetchone()
        assert row["title"].startswith("帮我设计一个")
        assert row["title"].endswith("…") or len("帮我设计一个 alpha 策略") <= 30

    def test_auto_title_skips_non_default(self, client):
        from agentnodes.interfaces.api.routers.web_session import (
            auto_title_session,
        )
        res = client.post("/api/chat/session", json={"title": "Custom"})
        sid = res.json()["id"]
        new_title = auto_title_session(sid, "any content")
        assert new_title is None

    def test_auto_title_truncates_long(self, client):
        from agentnodes.interfaces.api.routers.web_session import auto_title_session
        res = client.post("/api/chat/session", json={"title": "新会话"})
        sid = res.json()["id"]
        long = "x" * 100
        auto_title_session(sid, long)
        from agentnodes.interfaces.api.routers.web_session import _get_db
        conn = _get_db()
        row = conn.execute("SELECT title FROM sessions WHERE id = ?", (sid,)).fetchone()
        # Should be 29 chars + "…" = 30 chars total (max_len=30)
        assert len(row["title"]) <= 31


class TestFTSSearch:
    def test_search_returns_hits(self, client):
        from agentnodes.interfaces.api.routers.web_session import persist_message
        res = client.post("/api/chat/session", json={"title": "搜索测试"})
        sid = res.json()["id"]
        persist_message(
            session_id=sid, role="user",
            content="请帮我设计一个 alpha 量化策略",
        )
        persist_message(
            session_id=sid, role="assistant",
            content="好的，alpha 策略需要考虑因子选择",
        )
        res = client.post("/api/chat/session/search", json={"query": "alpha"})
        assert res.status_code == 200
        body = res.json()
        assert "hits" in body
        assert len(body["hits"]) >= 1
        for hit in body["hits"]:
            assert "session_id" in hit
            assert "message_id" in hit
            assert "snippet" in hit
            assert "<mark>" in hit["snippet"]

    def test_search_no_results(self, client):
        res = client.post("/api/chat/session/search", json={"query": "完全不存在_xyz123"})
        assert res.status_code == 200
        assert res.json()["hits"] == []

    def test_search_empty_query(self, client):
        res = client.post("/api/chat/session/search", json={"query": ""})
        assert res.status_code == 200
        assert res.json()["hits"] == []

    def test_search_filters_archived(self, client):
        from agentnodes.interfaces.api.routers.web_session import persist_message
        res = client.post("/api/chat/session", json={"title": "归档"})
        sid = res.json()["id"]
        persist_message(session_id=sid, role="user", content="uniquearchword123")
        # Archive the session
        client.patch(f"/api/chat/session/{sid}", json={"archived": True})
        # Search should not find it
        res = client.post("/api/chat/session/search", json={"query": "uniquearchword123"})
        assert res.status_code == 200
        assert res.json()["hits"] == []


class TestFTSMigration:
    """Auto-heal logic in _ensure_schema: detect wrong tokenizer and rebuild."""

    def _create_db_with_unicode61(self, tmp_db_path) -> None:
        """Build a fresh DB with messages_fts using the unicode61 tokenizer
        (the legacy/wrong one)."""
        conn = sqlite3.connect(str(tmp_db_path))
        conn.execute("""
            CREATE TABLE messages (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL DEFAULT '',
                parts_json TEXT,
                created_at REAL NOT NULL,
                metadata_json TEXT
            )
        """)
        conn.execute("""
            CREATE VIRTUAL TABLE messages_fts USING fts5(
                content,
                role,
                content='messages',
                content_rowid='rowid',
                tokenize='unicode61'
            )
        """)
        conn.execute("""
            INSERT INTO messages(id, session_id, role, content, created_at)
            VALUES ('m1', 's1', 'user', '帮我设计一个 alpha 量化策略', 1700000000.0)
        """)
        conn.commit()
        conn.close()

    def _get_fts_sql(self, tmp_db_path) -> str:
        conn = sqlite3.connect(str(tmp_db_path))
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='messages_fts'"
        ).fetchone()
        conn.close()
        return (row[0] or "") if row else ""

    def _query_fts(self, tmp_db_path, query: str) -> int:
        conn = sqlite3.connect(str(tmp_db_path))
        try:
            rows = conn.execute(
                "SELECT rowid FROM messages_fts WHERE messages_fts MATCH ?",
                (query,),
            ).fetchall()
            return len(rows)
        finally:
            conn.close()

    def test_rebuilds_when_wrong_tokenizer(self, tmp_path, monkeypatch):
        """DB with unicode61 tokenizer → _ensure_schema rebuilds as trigram."""
        from agentnodes.interfaces.api.routers import web_session

        db_path = tmp_path / "test_malformed.db"
        self._create_db_with_unicode61(db_path)

        # Redirect the module to use this DB
        monkeypatch.setattr(web_session, "_get_db_path", lambda: db_path)

        # Run ensure_schema — should detect unicode61 and rebuild
        conn = sqlite3.connect(str(db_path))
        try:
            web_session._ensure_schema(conn)
            conn.commit()
        finally:
            conn.close()

        # After rebuild, tokenizer must be trigram
        sql = self._get_fts_sql(db_path)
        assert "tokenize='trigram'" in sql.lower(), \
            f"Expected trigram tokenizer after rebuild, got: {sql}"
        # The legacy unicode61 tokenize option must be gone
        assert "tokenize='unicode61'" not in sql.lower(), \
            f"unicode61 tokenizer still present after rebuild: {sql}"

    def test_backfills_messages_after_rebuild(self, tmp_path, monkeypatch):
        """Existing messages must be searchable after the rebuild."""
        from agentnodes.interfaces.api.routers import web_session

        db_path = tmp_path / "test_backfill.db"
        self._create_db_with_unicode61(db_path)

        monkeypatch.setattr(web_session, "_get_db_path", lambda: db_path)

        conn = sqlite3.connect(str(db_path))
        try:
            web_session._ensure_schema(conn)
            conn.commit()
        finally:
            conn.close()

        # Trigram tokenizer breaks "帮我设计一个 alpha 量化策略" into
        # overlapping 3-byte windows. "alpha" is the most reliable query
        # because its trigrams ("alp", "lph", "pha") stay contiguous in
        # UTF-8, unlike pure Chinese where each char spans 3 bytes and
        # trigrams bleed across character boundaries.
        n = self._query_fts(db_path, "alpha")
        assert n >= 1, "Expected 'alpha' to match the backfilled message"

    def test_idempotent_when_already_trigram(self, tmp_path, monkeypatch, caplog):
        """Calling _ensure_schema twice must not rebuild a healthy FTS table."""
        from agentnodes.interfaces.api.routers import web_session

        db_path = tmp_path / "test_idempotent.db"
        self._create_db_with_unicode61(db_path)

        monkeypatch.setattr(web_session, "_get_db_path", lambda: db_path)

        # First call → rebuild
        conn = sqlite3.connect(str(db_path))
        try:
            web_session._ensure_schema(conn)
            conn.commit()
        finally:
            conn.close()

        # Capture sql right after first run
        first_sql = self._get_fts_sql(db_path)

        # Second call → must not drop/recreate
        caplog.clear()
        import logging
        caplog.set_level(logging.WARNING, logger="agentnodes.interfaces.api.routers.web_session")
        conn = sqlite3.connect(str(db_path))
        try:
            web_session._ensure_schema(conn)
            conn.commit()
        finally:
            conn.close()

        # SQL unchanged
        second_sql = self._get_fts_sql(db_path)
        assert first_sql == second_sql, \
            "FTS schema changed between idempotent calls"
        # No "auto-rebuilding" warning logged on the second call
        assert "auto-rebuilding" not in caplog.text, \
            f"Unexpected rebuild warning on idempotent call: {caplog.text}"
