"""E2E test for Web UI — full user flow."""

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from strategy_research.api.app import create_app
    from strategy_research.api.auth_tokens import create_token

    app = create_app()
    c = TestClient(app)
    # P5-5①: /api/chat/ endpoints require a bearer token now
    c.headers.update({"Authorization": f"Bearer {create_token('tester')}"})
    return c


class TestE2EFlow:
    """End-to-end test: register → login → create session → send message → receive events."""

    def test_full_user_flow(self, client):
        # 1. Login (registration is disabled; admin/admin is seeded)
        res = client.post("/api/auth/login", json={
            "username": "admin",
            "password": "admin",
        })
        assert res.status_code == 200
        res.json()['access_token']

        # 2. Create session
        res = client.post("/api/chat/session", json={"title": "E2E Session"})
        assert res.status_code == 200
        session_id = res.json()["id"]

        # 3. Send async message (stub the session service so no real
        # AgentLoop background task blocks TestClient shutdown)
        from unittest.mock import MagicMock, patch

        async def _send(*args, **kwargs):
            return {
                "message_id": "m1",
                "user_message_id": "um1",
                "assistant_message_id": "am1",
                "event_id": "e1",
                "status": "processing",
            }

        svc = MagicMock()
        svc.send_message = _send
        with patch("strategy_research.api.routers.chat._get_session_service",
                   return_value=svc):
            res = client.post("/api/chat/send_async", json={
                "session_id": session_id,
                "content": "Hello, E2E test!",
            })
        assert res.status_code == 200
        data = res.json()
        assert "message_id" in data
        assert "event_id" in data

        # 4. Verify session list contains created session
        res = client.get("/api/chat/session")
        assert res.status_code == 200
        sessions = res.json()["sessions"]
        session_ids = [s["id"] for s in sessions]
        assert session_id in session_ids

        # 5. Update session title
        res = client.patch(f"/api/chat/session/{session_id}", json={
            "title": "Updated E2E Session",
        })
        assert res.status_code == 200
        assert res.json()["title"] == "Updated E2E Session"

        # 6. Delete session
        res = client.delete(f"/api/chat/session/{session_id}")
        assert res.status_code == 200

        # 7. Verify deleted
        res = client.get("/api/chat/session")
        sessions = res.json()["sessions"]
        session_ids = [s["id"] for s in sessions]
        assert session_id not in session_ids

    def test_concurrent_sessions(self, client):
        """Create multiple sessions and verify they're isolated."""
        session_ids = []
        for i in range(5):
            res = client.post("/api/chat/session", json={"title": f"Concurrent {i}"})
            assert res.status_code == 200
            session_ids.append(res.json()["id"])

        # All IDs should be unique
        assert len(set(session_ids)) == 5

        # List should contain all
        res = client.get("/api/chat/session")
        sessions = res.json()["sessions"]
        listed_ids = [s["id"] for s in sessions]
        for sid in session_ids:
            assert sid in listed_ids

    def test_event_buffer_replay_chain(self, tmp_path):
        """Verify SSE event replay chain works across multiple events.

        历史注记：旧实现走 api.sse_buffer（A2 已退役——SSE 单轨归
        EventStore.subscribe）。重放语义等价改写为对 EventStore 的验证：
        push 3 个事件 → 从中间 id 回放应得到其后的事件。
        """
        from pathlib import Path

        from strategy_research.core.agent.event_store import EventStore

        store = EventStore(db_path=tmp_path / "replay.db")
        eid1 = store.emit("replay-test", "text_delta", {"text": "a"}).id
        eid2 = store.emit("replay-test", "text_delta", {"text": "b"}).id
        eid3 = store.emit("replay-test", "text_delta", {"text": "c"}).id

        # seq 语义：from_seq 严格大于（1-based，三个事件 seq=1,2,3）。
        # Replay from eid1 (seq=1) → should return eid2 and eid3
        events = store.replay("replay-test", from_seq=1)
        assert [e.id for e in events] == [eid2, eid3]

        # Replay from eid2 (seq=2) → should return only eid3
        events = store.replay("replay-test", from_seq=2)
        assert [e.id for e in events] == [eid3]

        store._backend.close()  # Windows：释放文件句柄（WAL 附属文件随库清理）

    def test_get_nonexistent_session(self, client):
        """Getting non-existent session returns 404."""
        res = client.get("/api/chat/session/nonexistent-id")
        assert res.status_code == 404

    def test_delete_nonexistent_session(self, client):
        """Deleting non-existent session returns 404 (ownership check)."""
        res = client.delete("/api/chat/session/nonexistent-id")
        assert res.status_code == 404


class TestStaticFiles:
    """Test that the FastAPI app serves the built frontend."""

    def test_static_index_served(self, client):
        """Index page served when static files exist."""
        from pathlib import Path
        static_dir = Path(__file__).parent.parent / "webui" / "static"
        if not static_dir.exists():
            pytest.skip("Static files not built (run `npm run build` first)")
        res = client.get("/")
        assert res.status_code == 200
        assert "html" in res.headers.get("content-type", "").lower() or "<!DOCTYPE" in res.text

    def test_static_assets_served(self, client):
        """Static assets served with correct content type."""
        from pathlib import Path
        static_dir = Path(__file__).parent.parent / "webui" / "static"
        if not (static_dir / "assets").exists():
            pytest.skip("Static assets not built")
        # Try to get the first asset
        assets_dir = static_dir / "assets"
        first_asset = list(assets_dir.iterdir())[0] if assets_dir.iterdir() else None
        if not first_asset:
            pytest.skip("No assets found")
        res = client.get(f"/assets/{first_asset.name}")
        assert res.status_code == 200
