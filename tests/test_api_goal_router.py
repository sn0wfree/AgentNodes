"""Tests for api/routers/goal.py — Goal API routes.

覆盖：
- POST /start — 创建 goal
- GET /status — 查询当前 goal
- GET /list — 列出 goals（带过滤）
- POST /evidence — 添加 evidence（无 active goal → 404）
- POST /complete — 完成 goal（无 active → 404，invalid outcome → 400）
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from strategy_research.api.middleware import AuthMiddleware
from strategy_research.api.routers.goal import router as goal_router


@pytest.fixture
def app(tmp_path, monkeypatch):
    """构造测试 app，goal DB 指向临时目录。"""
    import sqlite3

    db_path = str(tmp_path / "goals.db")
    session_db = str(tmp_path / "sessions.db")
    monkeypatch.setenv("SR_SESSIONS_DB", session_db)
    conn = sqlite3.connect(session_db)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS sessions ("
        " id TEXT PRIMARY KEY, user_id TEXT NOT NULL DEFAULT 'anonymous',"
        " title TEXT, created_at REAL, updated_at REAL, starred INTEGER DEFAULT 0,"
        " tags_json TEXT DEFAULT '[]', message_count INTEGER DEFAULT 0,"
        " archived INTEGER DEFAULT 0)"
    )
    user_id = "tester"
    for sid in (
        "sessions", "sess-0", "sess-1", "sess-2", "sess-3", "sess-bad",
        "sess-status", "sess-A", "sess-B", "sess-ev", "sess-done",
        "sess-wf", "x", "s", "no-active", "sess-empty", "sess-stale",
    ):
        now = 1.0
        conn.execute(
            "INSERT OR IGNORE INTO sessions "
            "(id, user_id, title, created_at, updated_at, starred, tags_json, message_count, archived) "
            "VALUES (?, ?, '', ?, ?, 0, '[]', 0, 0)",
            (sid, user_id, now, now),
        )
    conn.commit()
    conn.close()
    app = FastAPI()
    app.add_middleware(AuthMiddleware)
    app.state.goal_db_path = db_path
    app.include_router(goal_router, prefix="/api/goal")
    return app


@pytest.fixture
def client(app):
    return TestClient(app)


def auth_header(user_id: str = "tester"):
    """生成有效 HMAC-SHA256 签名 token（AuthMiddleware 要求的格式）。"""
    from strategy_research.api.auth_tokens import create_token

    return {"Authorization": f"Bearer {create_token(user_id)}"}


# ────────────────────────── /start ──────────────────────────


class TestStart:
    def test_start_goal_success(self, client):
        res = client.post(
            "/api/goal/start",
            headers=auth_header(),
            json={
                "session_id": "sess-1",
                "objective": "Find alpha factor with Sharpe > 1.5",
            },
        )
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "ok"
        assert "goal_id" in data

    def test_start_with_custom_criteria(self, client):
        res = client.post(
            "/api/goal/start",
            headers=auth_header(),
            json={
                "session_id": "sess-2",
                "objective": "Build a momentum strategy",
                "criteria": ["IC > 0.05", "Sharpe > 1.0", "Max DD < 20%"],
            },
        )
        assert res.status_code == 200

    def test_start_with_risk_tier(self, client):
        """risk_tier 必须能转换为 RiskTier enum。"""
        res = client.post(
            "/api/goal/start",
            headers=auth_header(),
            json={
                "session_id": "sess-3",
                "objective": "Test",
                "risk_tier": "research_general",
            },
        )
        assert res.status_code == 200

    def test_start_invalid_risk_tier_returns_500(self, client):
        """无效 risk_tier → RiskTier() 抛 ValueError → 500。"""
        res = client.post(
            "/api/goal/start",
            headers=auth_header(),
            json={
                "session_id": "sess-bad",
                "objective": "Test",
                "risk_tier": "invalid_tier",
            },
        )
        # 500 because unhandled ValueError in route
        assert res.status_code == 500


# ────────────────────────── /status ──────────────────────────


class TestStatus:
    def test_no_active_goal(self, client):
        res = client.get(
            "/api/goal/status?session_id=sess-empty",
            headers=auth_header(),
        )
        assert res.status_code == 200
        assert res.json()["status"] == "no_goal"

    def test_status_after_start(self, client):
        """创建 goal 后查询 status。"""
        # Start a goal first
        client.post(
            "/api/goal/start",
            headers=auth_header(),
            json={"session_id": "sess-status", "objective": "Test"},
        )

        res = client.get(
            "/api/goal/status?session_id=sess-status",
            headers=auth_header(),
        )
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "ok"
        assert data["session_id"] == "sess-status"
        assert "goal_status" in data
        assert "objective" in data


# ────────────────────────── /list ──────────────────────────


class TestList:
    def test_list_empty(self, client):
        res = client.get(
            "/api/goal/list",
            headers=auth_header(),
        )
        assert res.status_code == 200
        assert res.json()["goals"] == []

    def test_list_after_create(self, client):
        # Create multiple goals
        for i in range(3):
            client.post(
                "/api/goal/start",
                headers=auth_header(),
                json={"session_id": f"sess-{i}", "objective": f"Goal {i}"},
            )

        res = client.get("/api/goal/list", headers=auth_header())
        data = res.json()
        assert len(data["goals"]) == 3

    def test_list_filter_by_session(self, client):
        """session_id 过滤。"""
        client.post(
            "/api/goal/start",
            headers=auth_header(),
            json={"session_id": "sess-A", "objective": "A"},
        )
        client.post(
            "/api/goal/start",
            headers=auth_header(),
            json={"session_id": "sess-B", "objective": "B"},
        )

        res = client.get(
            "/api/goal/list?session_id=sess-A",
            headers=auth_header(),
        )
        data = res.json()
        assert len(data["goals"]) == 1
        assert data["goals"][0]["session_id"] == "sess-A"

    def test_list_limit(self, client):
        """limit 参数限制返回数量。"""
        for i in range(5):
            client.post(
                "/api/goal/start",
                headers=auth_header(),
                json={"session_id": f"sess-{i}", "objective": f"G{i}"},
            )

        res = client.get(
            "/api/goal/list?limit=2",
            headers=auth_header(),
        )
        assert len(res.json()["goals"]) == 2


# ────────────────────────── /evidence ──────────────────────────


class TestEvidence:
    def test_evidence_without_active_goal_returns_404(self, client):
        res = client.post(
            "/api/goal/evidence",
            headers=auth_header(),
            json={"session_id": "no-active", "evidence": "Some finding"},
        )
        assert res.status_code == 404
        assert "No active goal" in res.json()["detail"]

    def test_evidence_success(self, client):
        """创建 goal → 添加 evidence。"""
        client.post(
            "/api/goal/start",
            headers=auth_header(),
            json={"session_id": "sess-ev", "objective": "Test"},
        )

        res = client.post(
            "/api/goal/evidence",
            headers=auth_header(),
            json={
                "session_id": "sess-ev",
                "evidence": "Collected 5 years of AAPL data with 1d granularity",
                "source": "api",
            },
        )
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "ok"
        assert "evidence_id" in data


# ────────────────────────── /complete ──────────────────────────


class TestComplete:
    def test_complete_without_active_goal_returns_404(self, client):
        res = client.post(
            "/api/goal/complete",
            headers=auth_header(),
            json={"session_id": "no-active", "outcome": "complete"},
        )
        assert res.status_code == 404

    def test_complete_invalid_outcome_returns_400(self, client):
        """无效 outcome 枚举 → 400。"""
        client.post(
            "/api/goal/start",
            headers=auth_header(),
            json={"session_id": "sess-bad", "objective": "Test"},
        )

        res = client.post(
            "/api/goal/complete",
            headers=auth_header(),
            json={"session_id": "sess-bad", "outcome": "invalid_outcome_xyz"},
        )
        assert res.status_code == 400
        assert "Invalid outcome" in res.json()["detail"]

    def test_complete_success(self, client):
        """正常完成（cancelled — 不需要所有 criterion 满足）。"""
        client.post(
            "/api/goal/start",
            headers=auth_header(),
            json={"session_id": "sess-done", "objective": "Test"},
        )

        res = client.post(
            "/api/goal/complete",
            headers=auth_header(),
            json={"session_id": "sess-done", "outcome": "cancelled", "summary": "User aborted"},
        )
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "ok"
        assert data["new_status"] == "cancelled"


# ────────────────────────── Auth required ──────────────────────────


class TestAuthRequired:
    def test_goal_endpoints_require_auth(self, app):
        """所有 goal 端点都需要 JWT（/api/goal/* 不在 PUBLIC_PREFIXES 中）。"""
        client = TestClient(app)

        res = client.post("/api/goal/start", json={"session_id": "x", "objective": "y"})
        assert res.status_code == 401

        res = client.get("/api/goal/status?session_id=x")
        assert res.status_code == 401

        res = client.get("/api/goal/list")
        assert res.status_code == 401

        res = client.post("/api/goal/evidence", json={"session_id": "x", "evidence": "y"})
        assert res.status_code == 401

        res = client.post("/api/goal/complete", json={"session_id": "x"})
        assert res.status_code == 401

class TestListWorkflowId:
    def test_list_returns_workflow_id_for_workflow_goals(self, app, client):
        """goal list 应透出 workflow_id（工作流启动的 goal 需用于恢复）。"""
        from strategy_research.apps.goal import GoalStore

        with GoalStore(db_path=app.state.goal_db_path) as store:
            store.replace_goal(
                session_id="sess-wf",
                objective="带工作流的目标",
                criteria=["Sharpe > 1.0"],
                workflow_id="factor_research",
            )
            store.replace_goal(
                session_id="sess-wf",
                objective="普通目标",
                criteria=["Sharpe > 1.0"],
            )

        res = client.get("/api/goal/list?session_id=sess-wf", headers=auth_header())
        assert res.status_code == 200
        goals = {g["objective"]: g for g in res.json()["goals"]}
        assert goals["带工作流的目标"]["workflow_id"] == "factor_research"
        assert goals["普通目标"]["workflow_id"] is None

    def test_list_workflow_id_survives_status_filter(self, app, client):
        """状态过滤后 workflow_id 仍保留。"""
        from strategy_research.apps.goal import GoalStore

        with GoalStore(db_path=app.state.goal_db_path) as store:
            store.replace_goal(
                session_id="sess-wf2",
                objective="工作流目标",
                criteria=["x"],
                workflow_id="risk_assessment",
            )

        res = client.get(
            "/api/goal/list?session_id=sess-wf2&status=active",
            headers=auth_header(),
        )
        data = res.json()
        assert len(data["goals"]) == 1
        assert data["goals"][0]["workflow_id"] == "risk_assessment"


class TestErrorBranches:
    """500/409 兜底分支：端点在底层抛非预期异常时的保护。"""

    def test_status_internal_error_returns_500(self, app, client, monkeypatch):
        """status 端点底层抛错时，端点兜底为 500 而非裸异常。"""
        import strategy_research.apps.goal as goal_module

        class _BoomStore:
            def __init__(self, *a, **k):
                raise RuntimeError("status boom")
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False

        monkeypatch.setattr(goal_module, "GoalStore", _BoomStore)

        res = client.get("/api/goal/status?session_id=sess-1", headers=auth_header())
        assert res.status_code == 500
        assert "status boom" in res.json()["detail"]

    def test_list_internal_error_returns_500(self, app, client, monkeypatch):
        import strategy_research.apps.goal as goal_module

        class _BoomStore:
            def __init__(self, *a, **k):
                raise RuntimeError("list boom")
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False

        monkeypatch.setattr(goal_module, "GoalStore", _BoomStore)

        res = client.get("/api/goal/list", headers=auth_header())
        assert res.status_code == 500
        assert "list boom" in res.json()["detail"]

    def test_evidence_internal_error_returns_500(self, app, client, monkeypatch):
        import strategy_research.apps.goal as goal_module

        class _BoomStore:
            def __init__(self, *a, **k):
                raise RuntimeError("evidence boom")
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False

        monkeypatch.setattr(goal_module, "GoalStore", _BoomStore)

        res = client.post(
            "/api/goal/evidence",
            headers=auth_header(),
            json={"session_id": "s", "evidence": "x"},
        )
        assert res.status_code == 500
        assert "evidence boom" in res.json()["detail"]

    def test_complete_internal_error_returns_500(self, app, client, monkeypatch):
        import strategy_research.apps.goal as goal_module

        class _BoomStore:
            def __init__(self, *a, **k):
                raise RuntimeError("complete boom")
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False

        monkeypatch.setattr(goal_module, "GoalStore", _BoomStore)

        res = client.post(
            "/api/goal/complete",
            headers=auth_header(),
            json={"session_id": "s", "outcome": "complete"},
        )
        assert res.status_code == 500
        assert "complete boom" in res.json()["detail"]

    def test_complete_stale_goal_returns_409(self, app, client, monkeypatch):
        """StaleGoalError（expected_goal_id 不匹配）→ 409。"""
        import strategy_research.apps.goal as goal_module
        from strategy_research.apps.goal.models import StaleGoalError

        class FakeStore:
            def __init__(self, *a, **k):
                pass
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False
            def get_current_goal(self, session_id):
                from strategy_research.apps.goal.models import GoalRecord, GoalStatus, RiskTier
                return GoalRecord(
                    goal_id="current-goal",
                    session_id=session_id,
                    objective="x",
                    status=GoalStatus.ACTIVE,
                    ui_summary="",
                    source="api",
                    protocol="thesis_review",
                    risk_tier=RiskTier.RESEARCH_GENERAL,
                )
            def update_status(self, *a, **k):
                raise StaleGoalError("expected current-goal but caller passed stale-goal")

        monkeypatch.setattr(goal_module, "GoalStore", FakeStore)

        res = client.post(
            "/api/goal/complete",
            headers=auth_header(),
            json={
                "session_id": "sess-stale",
                "outcome": "complete",
            },
        )
        assert res.status_code == 409
        assert "stale" in res.json()["detail"].lower()


# ────────────────────────── IDOR isolation ──────────────────────────


class TestGoalIsolation:
    def test_other_users_session_denied(self, app, client):
        """非所属用户的 session 上读写 goal → 403。

        第一条 session 属于默认 tester；创建一条属于 other 的 session，
        确认 tester 无法在它上面 start/status/evidence/complete。
        """
        import sqlite3

        from strategy_research.api.routers.web_session import _get_db_path

        with sqlite3.connect(str(_get_db_path())) as conn:
            now = 1.0
            conn.execute(
                "INSERT OR IGNORE INTO sessions "
                "(id, user_id, title, created_at, updated_at, starred, tags_json, message_count, archived) "
                "VALUES ('other-sess', 'other', '', ?, ?, 0, '[]', 0, 0)",
                (now, now),
            )
            conn.commit()

        r = client.post(
            "/api/goal/start",
            headers=auth_header(),
            json={"session_id": "other-sess", "objective": "prying"},
        )
        assert r.status_code == 403
        r2 = client.get(
            "/api/goal/status?session_id=other-sess", headers=auth_header()
        )
        assert r2.status_code == 403
        r3 = client.post(
            "/api/goal/evidence",
            headers=auth_header(),
            json={"session_id": "other-sess", "evidence": "x"},
        )
        assert r3.status_code == 403
        r4 = client.post(
            "/api/goal/complete",
            headers=auth_header(),
            json={"session_id": "other-sess", "outcome": "complete"},
        )
        assert r4.status_code == 403

    def test_list_scopes_to_owner(self, app, client):
        """无 session_id 的全局 list 只返回本用户的 goals。"""
        client.post(
            "/api/goal/start",
            headers=auth_header(),
            json={"session_id": "sess-1", "objective": "mine"},
        )
        # other 用户在自己的 session 建 goal
        import sqlite3

        from strategy_research.api.routers.web_session import _get_db_path

        with sqlite3.connect(str(_get_db_path())) as conn:
            now = 1.0
            conn.execute(
                "INSERT OR IGNORE INTO sessions "
                "(id, user_id, title, created_at, updated_at, starred, tags_json, message_count, archived) "
                "VALUES ('other-sess2', 'other', '', ?, ?, 0, '[]', 0, 0)",
                (now, now),
            )
            conn.commit()
        client.post(
            "/api/goal/start",
            headers=auth_header("other"),
            json={"session_id": "other-sess2", "objective": "theirs"},
        )

        res = client.get("/api/goal/list", headers=auth_header())
        assert res.status_code == 200
        goals = res.json()["goals"]
        assert all(g["session_id"] != "other-sess2" for g in goals)
        assert any(g["objective"] == "mine" for g in goals)
