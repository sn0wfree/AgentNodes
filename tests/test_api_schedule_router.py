"""schedule + permission 路由测试（此前 34.2% / 32.4%）。

模式沿用 test_api_goal_router：裸 FastAPI + AuthMiddleware + 前缀挂载，
HMAC token 认证，AGENTNODES_SESSION_DB 指向 tmp，
ScheduledResearchStore 默认路径 monkeypatch 到 tmp。
"""
from __future__ import annotations

import sqlite3

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agentnodes.interfaces.api.middleware import AuthMiddleware
from agentnodes.interfaces.api.routers.permission import router as permission_router
from agentnodes.interfaces.api.routers.schedule import router as schedule_router


def auth_header(user_id: str = "tester"):
    from agentnodes.interfaces.api.auth_tokens import create_token

    return {"Authorization": f"Bearer {create_token(user_id)}"}


@pytest.fixture
def session_db(tmp_path, monkeypatch):
    """统一会话库指向 tmp 并种一个归属 tester 的会话行。"""
    db = str(tmp_path / "sessions.db")
    monkeypatch.setenv("AGENTNODES_SESSION_DB", db)
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS sessions ("
        " id TEXT PRIMARY KEY, user_id TEXT NOT NULL DEFAULT 'anonymous',"
        " title TEXT, created_at REAL, updated_at REAL, starred INTEGER DEFAULT 0,"
        " tags_json TEXT DEFAULT '[]', message_count INTEGER DEFAULT 0,"
        " archived INTEGER DEFAULT 0)"
    )
    for sid in ("sess-1", "sess-2"):
        conn.execute(
            "INSERT OR IGNORE INTO sessions (id, user_id, title, created_at, updated_at)"
            " VALUES (?, 'tester', '', 1.0, 1.0)",
            (sid,),
        )
    conn.commit()
    conn.close()
    return db


@pytest.fixture
def client(tmp_path, monkeypatch, session_db):
    """schedule store 默认路径 → tmp；workspace_path → tmp 真目录。"""
    import agentnodes.apps.scheduled_research.store as srs

    monkeypatch.setattr(
        srs, "_default_db_path", lambda: tmp_path / "jobs.db"
    )
    app = FastAPI()
    app.add_middleware(AuthMiddleware)
    app.include_router(schedule_router, prefix="/api/schedule")
    app.include_router(permission_router, prefix="/api/chat/permission")
    return TestClient(app), tmp_path


WORKSPACE_ARGS = {
    "objective": "每晚跑一次动量因子研究",
    "strategy_name": "momentum_20d",
    "interval_seconds": 3600,
}


# ── schedule CRUD ────────────────────────────────────────────────────


class TestScheduleCreate:
    def test_create_success(self, client):
        client_obj, tmp_path = client
        res = client_obj.post(
            "/api/schedule/create", headers=auth_header(),
            json={"session_id": "sess-1", "workspace_path": str(tmp_path), **WORKSPACE_ARGS},
        )
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "ok" and data["job_id"]

    def test_create_missing_workspace_400(self, client):
        client_obj, _ = client
        res = client_obj.post(
            "/api/schedule/create", headers=auth_header(),
            json={"session_id": "sess-1", "workspace_path": "Z:/no/such/dir", **WORKSPACE_ARGS},
        )
        assert res.status_code == 400
        assert "workspace_path" in res.json()["detail"]

    def test_create_no_trigger_400(self, client, tmp_path):
        client_obj, _ = client
        res = client_obj.post(
            "/api/schedule/create", headers=auth_header(),
            json={"session_id": "sess-1", "workspace_path": str(tmp_path),
                  "objective": "x", "strategy_name": "s",
                  "cron": "", "interval_seconds": 0},
        )
        assert res.status_code == 400
        assert "cron or interval_seconds" in res.json()["detail"]

    def test_create_invalid_cron_400(self, client, tmp_path):
        client_obj, _ = client
        res = client_obj.post(
            "/api/schedule/create", headers=auth_header(),
            json={"session_id": "sess-1", "workspace_path": str(tmp_path),
                  "objective": "x", "strategy_name": "s",
                  "cron": "bad", "interval_seconds": 0},
        )
        assert res.status_code == 400
        assert "invalid cron" in res.json()["detail"]

    def test_create_idor_foreign_session_404(self, client, tmp_path):
        """会话不存在 → _fetch_session_owned 404（IDOR 保护）。"""
        client_obj, _ = client
        res = client_obj.post(
            "/api/schedule/create", headers=auth_header(),
            json={"session_id": "no-such-session", "workspace_path": str(tmp_path), **WORKSPACE_ARGS},
        )
        assert res.status_code == 404


class TestScheduleCrud:
    def _create(self, client_obj, tmp_path, session_id="sess-1"):
        res = client_obj.post(
            "/api/schedule/create", headers=auth_header(),
            json={"session_id": session_id, "workspace_path": str(tmp_path), **WORKSPACE_ARGS},
        )
        assert res.status_code == 200
        return res.json()["job_id"]

    def test_list_shows_own_jobs(self, client):
        client_obj, tmp_path = client
        job_id = self._create(client_obj, tmp_path)
        res = client_obj.get("/api/schedule/list", headers=auth_header(),
                             params={"session_id": "sess-1"})
        assert res.status_code == 200
        jobs = res.json()["jobs"]
        assert any(j["job_id"] == job_id for j in jobs)
        assert jobs[0]["owner_session_id"] == "sess-1"

    def test_show_and_shape(self, client):
        client_obj, tmp_path = client
        job_id = self._create(client_obj, tmp_path)
        res = client_obj.get(f"/api/schedule/show/{job_id}", headers=auth_header(),
                             params={"session_id": "sess-1"})
        assert res.status_code == 200
        job = res.json()["job"]
        assert job["strategy_name"] == "momentum_20d"
        assert job["target"] == "study"
        assert job["interval_ms"] == 3600 * 1000

    def test_show_missing_job_404(self, client):
        client_obj, _ = client
        res = client_obj.get("/api/schedule/show/nope", headers=auth_header(),
                             params={"session_id": "sess-1"})
        assert res.status_code == 404

    def test_cancel_marks_cancelled(self, client):
        client_obj, tmp_path = client
        job_id = self._create(client_obj, tmp_path)
        res = client_obj.post(f"/api/schedule/cancel/{job_id}", headers=auth_header(),
                              params={"session_id": "sess-1"})
        assert res.status_code == 200
        res2 = client_obj.get(f"/api/schedule/show/{job_id}", headers=auth_header(),
                              params={"session_id": "sess-1"})
        assert res2.json()["job"]["status"] == "cancelled"

    def test_delete_removes_job(self, client):
        client_obj, tmp_path = client
        job_id = self._create(client_obj, tmp_path)
        res = client_obj.post(f"/api/schedule/delete/{job_id}", headers=auth_header(),
                              params={"session_id": "sess-1"})
        assert res.status_code == 200
        res2 = client_obj.get(f"/api/schedule/show/{job_id}", headers=auth_header(),
                              params={"session_id": "sess-1"})
        assert res2.status_code == 404

    def test_idor_other_session_403(self, client):
        """别的会话（存在但不归属）不能改我的 job——403 而非 404。"""
        client_obj, tmp_path = client
        job_id = self._create(client_obj, tmp_path, session_id="sess-1")
        res = client_obj.post(f"/api/schedule/cancel/{job_id}", headers=auth_header(),
                              params={"session_id": "sess-2"})
        assert res.status_code == 403
        assert "another session" in res.json()["detail"]


# ── permission respond ───────────────────────────────────────────────


class TestPermissionRespond:
    def test_no_gateway_is_noop(self, client, monkeypatch):
        """网关不可用 → no-op，前端不重试循环。

        实测：`_get_permission_gateway` 是 lazy-init 进程单例，裸 app 也会
        拿到网关——该防御分支用注入 None 触发。
        """
        client_obj, _ = client
        from agentnodes.interfaces.api.routers import chat as chat_mod

        monkeypatch.setattr(chat_mod, "_get_permission_gateway", lambda request: None)
        res = client_obj.post(
            "/api/chat/permission/respond", headers=auth_header(),
            json={"tool_call_id": "tc-1", "action": "allow"},
        )
        assert res.status_code == 200
        assert res.json() == {"status": "no_gateway"}

    def test_allow_resolves(self, client, monkeypatch):
        client_obj, _ = client
        from agentnodes.kernel.permission import PermissionAction

        seen: dict = {}

        class StubGateway:
            def owner_session_of(self, tool_call_id):
                seen["owner"] = tool_call_id
                return None  # 无归属会话 → 跳过所有权校验分支

            def respond(self, tool_call_id, response):
                seen["resp"] = response
                return True

        from agentnodes.interfaces.api.routers import chat as chat_mod

        monkeypatch.setattr(chat_mod, "_get_permission_gateway",
                            lambda request: StubGateway())
        res = client_obj.post(
            "/api/chat/permission/respond", headers=auth_header(),
            json={"tool_call_id": "tc-2", "action": "allow", "permanent": True},
        )
        assert res.status_code == 200
        assert res.json() == {"status": "ok"}
        assert seen["resp"].action == PermissionAction.ALLOW
        assert seen["resp"].permanent is True

    def test_invalid_action(self, client, monkeypatch):
        client_obj, _ = client

        class StubGateway:
            def owner_session_of(self, tool_call_id):
                return None

            def respond(self, tool_call_id, response):
                return True

        from agentnodes.interfaces.api.routers import chat as chat_mod

        monkeypatch.setattr(chat_mod, "_get_permission_gateway",
                            lambda request: StubGateway())
        res = client_obj.post(
            "/api/chat/permission/respond", headers=auth_header(),
            json={"tool_call_id": "tc-3", "action": "nonsense"},
        )
        assert res.json() == {"status": "invalid_action"}

    def test_expired_when_gateway_says_not_resolved(self, client, monkeypatch):
        client_obj, _ = client

        class StubGateway:
            def owner_session_of(self, tool_call_id):
                return None

            def respond(self, tool_call_id, response):
                return False

        from agentnodes.interfaces.api.routers import chat as chat_mod

        monkeypatch.setattr(chat_mod, "_get_permission_gateway",
                            lambda request: StubGateway())
        res = client_obj.post(
            "/api/chat/permission/respond", headers=auth_header(),
            json={"tool_call_id": "tc-4", "action": "deny"},
        )
        assert res.json() == {"status": "expired"}

    def test_ownership_enforced_on_owned_request(self, client, monkeypatch):
        """归属会话存在 → 走 _fetch_session_owned；他人回答 → 404/403。"""
        client_obj, _ = client

        class StubGateway:
            def owner_session_of(self, tool_call_id):
                return "sess-2"  # 归属 sess-2

            def respond(self, tool_call_id, response):
                return True

        from agentnodes.interfaces.api.routers import chat as chat_mod

        monkeypatch.setattr(chat_mod, "_get_permission_gateway",
                            lambda request: StubGateway())
        # tester 只拥有 sess-1/sess-2 → 允许
        res = client_obj.post(
            "/api/chat/permission/respond", headers=auth_header(user_id="tester"),
            json={"tool_call_id": "tc-5", "action": "allow"},
        )
        assert res.json() == {"status": "ok"}
        # 另一用户不拥有 sess-2 → 403
        res2 = client_obj.post(
            "/api/chat/permission/respond", headers=auth_header(user_id="mallory"),
            json={"tool_call_id": "tc-5", "action": "allow"},
        )
        assert res2.status_code == 403
