"""Smoke tests for the ``/api/study/*`` HTTP routes — shape-only.

The scheduler's end-to-end loop (submit → executor → complete) is
covered by ``test_study_scheduler.py``; here we verify the HTTP layer
parses requests, validates workspace/strategy, returns 404 for unknown
control targets, and the list endpoint works without a session filter.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest


def _build_asgi_app():
    from fastapi import FastAPI

    from strategy_research.interfaces.api.middleware import AuthMiddleware
    from strategy_research.interfaces.api.routers import chat, study
    from strategy_research.interfaces.api.routers.web_session import router as session_router

    app = FastAPI()
    app.add_middleware(AuthMiddleware)
    app.include_router(chat.router, prefix="/api/chat")
    app.include_router(session_router, prefix="/api/chat/session")
    app.include_router(study.router, prefix="/api/study")
    return app


@pytest.fixture
def _app_env(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("QUANTNODES_RESEARCH_GOAL_DB_PATH", str(tmp_path / "goals.db"))
    monkeypatch.setenv("QUANTNODES_RESEARCH_HYPOTHESES_PATH", str(tmp_path / "hyp.json"))
    # Sessions DB is consulted by /api/study/* for ownership checks
    # (A2); point it at a fresh per-test file and seed a "sess-1" row
    # owned by "tester" (the user_id encoded by _bearer below).
    sessions_db = tmp_path / "sessions.db"
    monkeypatch.setenv("SR_SESSIONS_DB", str(sessions_db))
    import sqlite3
    conn = sqlite3.connect(str(sessions_db))
    conn.executescript(
        """
        CREATE TABLE sessions (
          id TEXT PRIMARY KEY,
          user_id TEXT NOT NULL,
          title TEXT,
          created_at TEXT,
          updated_at TEXT,
          starred INTEGER NOT NULL DEFAULT 0,
          tags_json TEXT NOT NULL DEFAULT '[]',
          message_count INTEGER NOT NULL DEFAULT 0,
          archived INTEGER NOT NULL DEFAULT 0
        );
        """
    )
    now = "2026-08-01T10:00:00"
    for sid in ("sess-1", "sess-A", "sess-other", "sess-stale"):
        conn.execute(
            "INSERT INTO sessions (id, user_id, title, created_at, updated_at) "
            "VALUES (?, 'tester', 't', ?, ?)",
            (sid, now, now),
        )
    conn.commit()
    conn.close()
    ws = tmp_path / "ws"
    strat_dir = ws / "strategies" / "demo_strategy"
    strat_dir.mkdir(parents=True)
    (strat_dir / "strategy.py").write_text(
        "PARAMS = {}\nFACTOR_EXPRS = []\nFACTOR_WEIGHT_METHOD = 'equal'\n",
        encoding="utf-8",
    )
    (ws / "acceptance.yaml").write_text("llm_enabled: false\n", encoding="utf-8")
    return ws


def _bearer(user_id: str = "tester") -> dict:
    from strategy_research.interfaces.api.auth_tokens import create_token

    return {"Authorization": f"Bearer {create_token(user_id)}"}


@pytest.mark.asyncio
async def test_start_rejects_missing_workspace(_app_env):
    app = _build_asgi_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers=_bearer(),
    ) as client:
        body = {
            "session_id": "no-such", "objective": "x",
            "workspace_path": "/tmp/no-such-ws-xyz", "strategy_name": "demo",
        }
        r = await client.post("/api/study/start", json=body)
        # A2: session ownership fires first (404 for unknown session).
        assert r.status_code == 404
        assert "session not found" in r.json()["detail"].lower()


@pytest.mark.asyncio
async def test_start_auto_creates_strategy(_app_env):
    """When strategy dir doesn't exist, it should be auto-created.

    Note: We can't fully test the start endpoint because it triggers the
    scheduler in the background. Instead, test the auto-creation logic directly.
    """
    from strategy_research.apps.study.bootstrap import _create_minimal_strategy

    strat_dir = _app_env / "strategies" / "auto_created_strat"
    assert not strat_dir.exists()
    strat_dir.mkdir(parents=True)
    _create_minimal_strategy(strat_dir, "auto_created_strat")
    assert (strat_dir / "strategy.py").exists()
    content = (strat_dir / "strategy.py").read_text(encoding="utf-8")
    assert "auto_created_strat" in content


@pytest.mark.asyncio
async def test_cancel_unknown_returns_404(_app_env):
    app = _build_asgi_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers=_bearer(),
    ) as client:
        r = await client.post("/api/study/unknown_study/pause")
        assert r.status_code == 404


@pytest.mark.asyncio
async def test_list_returns_all_empty(_app_env):
    app = _build_asgi_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers=_bearer(),
    ) as client:
        r = await client.get("/api/study/list")
        assert r.status_code == 200
        assert "studies" in r.json()
        assert r.json()["studies"] == []


@pytest.mark.asyncio
async def test_list_invalid_status_returns_400(_app_env):
    app = _build_asgi_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers=_bearer(),
    ) as client:
        r = await client.get("/api/study/list?status=bogus")
        assert r.status_code == 400


@pytest.mark.asyncio
async def test_status_no_study(_app_env):
    app = _build_asgi_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers=_bearer(),
    ) as client:
        # sess-1 exists but has no study row → no_study
        r = await client.get("/api/study/status?session_id=sess-1")
        assert r.status_code == 200
        assert r.json()["status"] == "no_study"


@pytest.mark.asyncio
async def test_status_unknown_session_returns_404(_app_env):
    """A2: unknown session → 404 (ownership before status lookup)."""
    app = _build_asgi_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers=_bearer(),
    ) as client:
        r = await client.get("/api/study/status?session_id=ghost-session")
        assert r.status_code == 404


@pytest.mark.asyncio
async def test_summary_other_users_study_denied(_app_env, tmp_path, monkeypatch):
    """SDOR: study-id-derived endpoints enforce owner-session matching.
    A different user cannot read another user's study summary (403)."""
    from strategy_research.apps.study import StudyStore

    db_path = tmp_path / "goals.db"
    with StudyStore(db_path=db_path) as store:
        study = store.create_study(
            owner_session_id="sess-1",  # owned by 'tester'
            goal_id=None,
            objective="tester's study",
            workspace_path=str(_app_env),
            strategy_name="demo_strategy",
            executor_type="autoresearch",
            max_rounds=5,
        )
        study_id = study.study_id

    monkeypatch.setenv("QUANTNODES_RESEARCH_GOAL_DB_PATH", str(db_path))
    app = _build_asgi_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers=_bearer("bob"),  # different user
    ) as client:
        r = await client.get(f"/api/study/{study_id}/summary")
        assert r.status_code == 403  # isolated: bob does not own this study


@pytest.mark.asyncio
async def test_status_other_users_session_returns_403(_app_env, tmp_path, monkeypatch):
    """A2: caller cannot read another user's session."""
    app = _build_asgi_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        # bob owns nothing in fixture (sessions are all owned by 'tester').
        headers=_bearer("bob"),
    ) as client:
        r = await client.get("/api/study/status?session_id=sess-1")
        assert r.status_code == 403


@pytest.mark.asyncio
async def test_status_unauthenticated_returns_401(_app_env):
    """A2: missing token → 401."""
    app = _build_asgi_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        r = await client.get("/api/study/status?session_id=sess-1")
        assert r.status_code == 401


@pytest.mark.asyncio
async def test_summary_returns_strategy_and_round_fields(_app_env, tmp_path, monkeypatch):
    """summary 端点应返回 strategy_name/workspace_path/timestamps，
    且不因 round 记录缺 factor_failures 而 500。"""
    from strategy_research.apps.study import StudyStore

    db_path = tmp_path / "goals.db"
    with StudyStore(db_path=db_path) as store:
        study = store.create_study(
            owner_session_id="sess-1",
            goal_id=None,
            objective="test objective",
            workspace_path=str(_app_env),
            strategy_name="demo_strategy",
            executor_type="autoresearch",
            max_rounds=5,
        )
        store.append_round(
            study.study_id, 1, "run_0001",
            metrics={"sharpe": 1.5}, verdict="keep",
        )
        study_id = study.study_id

    monkeypatch.setenv("QUANTNODES_RESEARCH_GOAL_DB_PATH", str(tmp_path / "goals.db"))
    monkeypatch.setenv("QUANTNODES_RESEARCH_HYPOTHESES_PATH", str(tmp_path / "hyp.json"))

    app = _build_asgi_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers=_bearer(),
    ) as client:
        r = await client.get(f"/api/study/{study_id}/summary")
        assert r.status_code == 200
        data = r.json()
        assert data["strategy_name"] == "demo_strategy"
        assert data["workspace_path"] == str(_app_env)
        assert data["created_at"]
        assert data["updated_at"]
        assert len(data["recent_rounds"]) == 1
        assert data["recent_rounds"][0]["run_name"] == "run_0001"
        assert data["recent_rounds"][0]["metrics"] == {"sharpe": 1.5}


def _seed_study(_app_env, tmp_path, monkeypatch, *, objective="test objective",
                goal_id=None):
    """Create a study row in the goals.db used by the API layer."""
    from strategy_research.apps.study import StudyStore

    db_path = tmp_path / "goals.db"
    with StudyStore(db_path=db_path) as store:
        study = store.create_study(
            owner_session_id="sess-1",
            goal_id=goal_id,
            objective=objective,
            workspace_path=str(_app_env),
            strategy_name="demo_strategy",
            executor_type="autoresearch",
            max_rounds=5,
        )
        return study.study_id


def _api_client():
    from strategy_research.interfaces.api.auth_tokens import create_token

    return httpx.AsyncClient(transport=httpx.ASGITransport(app=_build_asgi_app()),
                             base_url="http://test",
                             headers={"Authorization": f"Bearer {create_token('tester')}"})


@pytest.mark.asyncio
async def test_directive_success_returns_directive_id(_app_env, tmp_path, monkeypatch):
    monkeypatch.setenv("QUANTNODES_RESEARCH_GOAL_DB_PATH", str(tmp_path / "goals.db"))
    monkeypatch.setenv("QUANTNODES_RESEARCH_HYPOTHESES_PATH", str(tmp_path / "hyp.json"))
    study_id = _seed_study(_app_env, tmp_path, monkeypatch)

    async with _api_client() as client:
        r = await client.post(
            f"/api/study/{study_id}/directive",
            json={"content": "  加大动量权重  ", "issued_by": "tester"},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "ok"
        assert data["directive_id"].startswith("dir_")
        assert data["created_at"]

        # Round-trip through the list endpoint (content is trimmed).
        r2 = await client.get(f"/api/study/{study_id}/directives")
        assert r2.status_code == 200
        directives = r2.json()["directives"]
        assert len(directives) == 1
        assert directives[0]["content"] == "加大动量权重"
        assert directives[0]["issued_by"] == "tester"
        assert directives[0]["consumed_at"] is None


@pytest.mark.asyncio
async def test_directive_empty_content_returns_400(_app_env, tmp_path, monkeypatch):
    monkeypatch.setenv("QUANTNODES_RESEARCH_GOAL_DB_PATH", str(tmp_path / "goals.db"))
    monkeypatch.setenv("QUANTNODES_RESEARCH_HYPOTHESES_PATH", str(tmp_path / "hyp.json"))
    study_id = _seed_study(_app_env, tmp_path, monkeypatch)

    async with _api_client() as client:
        r = await client.post(
            f"/api/study/{study_id}/directive",
            json={"content": "   ", "issued_by": "tester"},
        )
        assert r.status_code == 400
        assert "must not be empty" in r.json()["detail"]


@pytest.mark.asyncio
async def test_directive_unknown_study_returns_404(_app_env, tmp_path, monkeypatch):
    monkeypatch.setenv("QUANTNODES_RESEARCH_GOAL_DB_PATH", str(tmp_path / "goals.db"))
    monkeypatch.setenv("QUANTNODES_RESEARCH_HYPOTHESES_PATH", str(tmp_path / "hyp.json"))

    async with _api_client() as client:
        r = await client.post(
            "/api/study/ghost-study/directive",
            json={"content": "hi", "issued_by": "tester"},
        )
        assert r.status_code == 404


@pytest.mark.asyncio
async def test_directives_list_unknown_study_returns_404(_app_env, tmp_path, monkeypatch):
    monkeypatch.setenv("QUANTNODES_RESEARCH_GOAL_DB_PATH", str(tmp_path / "goals.db"))
    monkeypatch.setenv("QUANTNODES_RESEARCH_HYPOTHESES_PATH", str(tmp_path / "hyp.json"))

    async with _api_client() as client:
        r = await client.get("/api/study/ghost-study/directives")
        assert r.status_code == 404


@pytest.mark.asyncio
async def test_summary_goal_snapshot_and_scoreboard(_app_env, tmp_path, monkeypatch):
    """summary 应带 goal_snapshot（进度/标准/证据数）与 journal 派生的 scoreboard。"""
    from strategy_research.apps.goal import GoalStore
    from strategy_research.apps.goal.models import EvidenceInput
    from strategy_research.apps.goal.store import RiskTier

    db_path = tmp_path / "goals.db"
    with GoalStore(db_path=db_path) as gs:
        goal = gs.replace_goal(
            session_id="sess-1",
            objective="Sharpe 提升目标",
            criteria=["Sharpe > 1.0", "回撤 < 20%"],
            risk_tier=RiskTier.RESEARCH_GENERAL,
        )
        gs.append_evidence(
            session_id="sess-1",
            goal_id=goal.goal_id,
            expected_goal_id=goal.goal_id,
            evidence=EvidenceInput(
                text="动量因子提升 Sharpe",
                artifact_path="/tmp/ws/strategies/demo/strategy.py",
                assumptions={"sharpe": 1.3},
            ),
        )
        gs.append_journal_entry(
            goal_id=goal.goal_id, session_id="sess-1", round_num=1,
            hypothesis_id="hyp-1", label="动量假设",
            levers=["momentum"],
        )
        gs.fill_journal_attribution(
            goal_id=goal.goal_id, session_id="sess-1", round_num=1,
            outcome="accepted", attribution={"sharpe": 1.3},
        )
        gs.append_journal_entry(
            goal_id=goal.goal_id, session_id="sess-1", round_num=2,
            hypothesis_id="hyp-2", label="反转假设",
            levers=["reversal"],
        )
        gs.fill_journal_attribution(
            goal_id=goal.goal_id, session_id="sess-1", round_num=2,
            outcome="reverted", attribution={},
        )
        goal_id = goal.goal_id

    monkeypatch.setenv("QUANTNODES_RESEARCH_GOAL_DB_PATH", str(db_path))
    monkeypatch.setenv("QUANTNODES_RESEARCH_HYPOTHESES_PATH", str(tmp_path / "hyp.json"))
    study_id = _seed_study(_app_env, tmp_path, monkeypatch, goal_id=goal_id)

    async with _api_client() as client:
        r = await client.get(f"/api/study/{study_id}/summary")
        assert r.status_code == 200
        data = r.json()

        snap = data["goal_snapshot"]
        assert snap["goal_id"] == goal_id
        assert snap["objective"] == "Sharpe 提升目标"
        assert snap["evidence_count"] == 1
        assert snap["progress_percent"] >= 0

        scoreboard = data["scoreboard"]
        by_lever = {row["lever"]: row for row in scoreboard}
        assert by_lever["momentum"]["attempts"] == 1
        assert by_lever["momentum"]["accepted"] == 1
        assert by_lever["momentum"]["precision_mean"] == 1.0
        assert by_lever["reversal"]["attempts"] == 1
        assert by_lever["reversal"]["reverted"] == 1
        assert by_lever["reversal"]["precision_mean"] == 0.0


@pytest.mark.asyncio
async def test_list_filter_by_session_id(_app_env, tmp_path, monkeypatch):
    """list?session_id= 应只返回该 session 的 study。"""
    from strategy_research.apps.study import StudyStore

    db_path = tmp_path / "goals.db"
    with StudyStore(db_path=db_path) as store:
        s_a = store.create_study(
            owner_session_id="sess-A", goal_id=None, objective="A obj",
            workspace_path=str(_app_env), strategy_name="demo",
            executor_type="autoresearch", max_rounds=3,
        )
        store.create_study(
            owner_session_id="sess-B", goal_id=None, objective="B obj",
            workspace_path=str(_app_env), strategy_name="demo",
            executor_type="autoresearch", max_rounds=3,
        )
        study_a_id = s_a.study_id

    monkeypatch.setenv("QUANTNODES_RESEARCH_GOAL_DB_PATH", str(db_path))
    monkeypatch.setenv("QUANTNODES_RESEARCH_HYPOTHESES_PATH", str(tmp_path / "hyp.json"))

    async with _api_client() as client:
        r = await client.get("/api/study/list?session_id=sess-A")
        assert r.status_code == 200
        data = r.json()
        assert len(data["studies"]) == 1
        assert data["studies"][0]["study_id"] == study_a_id
        # v2 single identity: session_id == study_id
        assert data["studies"][0]["session_id"] == study_a_id


@pytest.mark.asyncio
async def test_list_filter_by_status_returns_matching_only(
    _app_env, tmp_path, monkeypatch
):
    """list?status=running 应只返回该 execution_status 的 study。"""
    from strategy_research.apps.study import StudyStatus, StudyStore

    db_path = tmp_path / "goals.db"
    with StudyStore(db_path=db_path) as store:
        s_queued = store.create_study(
            owner_session_id="sess-1", goal_id=None, objective="queued obj",
            workspace_path=str(_app_env), strategy_name="demo",
            executor_type="autoresearch", max_rounds=3,
        )
        s_running = store.create_study(
            owner_session_id="sess-1", goal_id=None, objective="running obj",
            workspace_path=str(_app_env), strategy_name="demo",
            executor_type="autoresearch", max_rounds=3,
        )
        store.update_execution_status(s_running.study_id, StudyStatus.RUNNING)
        store.update_execution_status(
            s_queued.study_id, StudyStatus.COMPLETE,
            last_metrics={"sharpe": 1.2}, last_verdict="keep",
        )

    monkeypatch.setenv("QUANTNODES_RESEARCH_GOAL_DB_PATH", str(db_path))
    monkeypatch.setenv("QUANTNODES_RESEARCH_HYPOTHESES_PATH", str(tmp_path / "hyp.json"))

    async with _api_client() as client:
        # status=running → 只有 s_running
        r = await client.get("/api/study/list?status=running")
        assert r.status_code == 200
        rows = r.json()["studies"]
        assert len(rows) == 1
        assert rows[0]["execution_status"] == "running"
        assert rows[0]["last_verdict"] is None

        # status=complete → 只有 s_queued（已 complete）
        r2 = await client.get("/api/study/list?status=complete")
        rows2 = r2.json()["studies"]
        assert len(rows2) == 1
        assert rows2[0]["execution_status"] == "complete"
        assert rows2[0]["last_verdict"] == "keep"


@pytest.mark.asyncio
async def test_list_limit_caps_results(_app_env, tmp_path, monkeypatch):
    """list?limit=N 应最多返回 N 条 study（且限定当前用户的 sessions）。"""
    from strategy_research.apps.study import StudyStore

    db_path = tmp_path / "goals.db"
    with StudyStore(db_path=db_path) as store:
        for i in range(5):
            store.create_study(
                owner_session_id=f"sess-{i}", goal_id=None,
                objective=f"obj {i}",
                workspace_path=str(_app_env), strategy_name="demo",
                executor_type="autoresearch", max_rounds=3,
            )

    monkeypatch.setenv("QUANTNODES_RESEARCH_GOAL_DB_PATH", str(db_path))
    monkeypatch.setenv("QUANTNODES_RESEARCH_HYPOTHESES_PATH", str(tmp_path / "hyp.json"))

    # Seed the 5 owner sessions as owned by 'tester' (the caller) so the
    # IDOR-scoped list returns them.
    import sqlite3
    sessions_db = tmp_path / "sessions.db"
    conn = sqlite3.connect(str(sessions_db))
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS sessions (
          id TEXT PRIMARY KEY,
          user_id TEXT NOT NULL,
          title TEXT,
          created_at TEXT,
          updated_at TEXT,
          starred INTEGER NOT NULL DEFAULT 0,
          tags_json TEXT NOT NULL DEFAULT '[]',
          message_count INTEGER NOT NULL DEFAULT 0,
          archived INTEGER NOT NULL DEFAULT 0
        );
        """
    )
    now = "2026-08-01T10:00:00"
    for i in range(5):
        conn.execute(
            "INSERT OR REPLACE INTO sessions (id, user_id, title, created_at, updated_at) "
            "VALUES (?, 'tester', 't', ?, ?)",
            (f"sess-{i}", now, now),
        )
    conn.commit()
    conn.close()

    async with _api_client() as client:
        r = await client.get("/api/study/list?limit=2")
        assert r.status_code == 200
        assert len(r.json()["studies"]) == 2


@pytest.mark.asyncio
async def test_start_autoresearch_returns_study_id(_app_env, monkeypatch):
    """autoresearch 路径：mock scheduler.submit，期望返回 study_id + goal_id + queued。"""
    from unittest.mock import AsyncMock, MagicMock

    from strategy_research.interfaces.api.routers import study as study_router

    sched = MagicMock()
    sched.submit = AsyncMock()
    monkeypatch.setattr(study_router, "_get_study_scheduler", lambda: sched)

    monkeypatch.setenv("QUANTNODES_RESEARCH_GOAL_DB_PATH", str(_app_env / "goals.db"))
    monkeypatch.setenv("QUANTNODES_RESEARCH_HYPOTHESES_PATH", str(_app_env / "hyp.json"))

    body = {
        "session_id": "sess-1", "objective": "Sharpe > 1.5",
        "workspace_path": str(_app_env), "strategy_name": "demo_strategy",
        "executor_type": "autoresearch", "max_rounds": 3,
    }
    async with _api_client() as client:
        r = await client.post("/api/study/start", json=body)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["status"] == "ok"
        assert data["execution_status"] == "queued"
        assert data["executor_type"] == "autoresearch"
        assert data["study_id"].startswith("study_")
        assert data["goal_id"].startswith("goal_")
        sched.submit.assert_awaited_once()


@pytest.mark.asyncio
async def test_start_invalid_objective_returns_400(_app_env, monkeypatch):
    """空 objective → GoalStore ValueError → 400。"""
    from unittest.mock import MagicMock

    from strategy_research.interfaces.api.routers import study as study_router

    sched = MagicMock()
    sched.submit = MagicMock()
    monkeypatch.setattr(study_router, "_get_study_scheduler", lambda: sched)

    monkeypatch.setenv("QUANTNODES_RESEARCH_GOAL_DB_PATH", str(_app_env / "goals.db"))
    monkeypatch.setenv("QUANTNODES_RESEARCH_HYPOTHESES_PATH", str(_app_env / "hyp.json"))

    body = {
        "session_id": "sess-1", "objective": "",
        "workspace_path": str(_app_env), "strategy_name": "demo_strategy",
        "executor_type": "autoresearch", "max_rounds": 3,
    }
    async with _api_client() as client:
        r = await client.post("/api/study/start", json=body)
        assert r.status_code == 400


@pytest.mark.asyncio
async def test_start_internal_error_returns_500(_app_env, monkeypatch):
    """GoalStore 抛非 HTTP 异常 → 500。"""
    from unittest.mock import MagicMock, patch

    from strategy_research.interfaces.api.routers import study as study_router

    sched = MagicMock()
    sched.submit = MagicMock()
    monkeypatch.setattr(study_router, "_get_study_scheduler", lambda: sched)

    monkeypatch.setenv("QUANTNODES_RESEARCH_GOAL_DB_PATH", str(_app_env / "goals.db"))
    monkeypatch.setenv("QUANTNODES_RESEARCH_HYPOTHESES_PATH", str(_app_env / "hyp.json"))

    body = {
        "session_id": "sess-1", "objective": "obj",
        "workspace_path": str(_app_env), "strategy_name": "demo_strategy",
        "executor_type": "autoresearch", "max_rounds": 3,
    }
    with patch(
        "strategy_research.apps.goal.GoalStore.replace_goal",
        side_effect=RuntimeError("simulated"),
    ):
        async with _api_client() as client:
            r = await client.post("/api/study/start", json=body)
            assert r.status_code == 500


@pytest.mark.asyncio
async def test_status_returns_no_study_when_session_has_none(_app_env, monkeypatch):
    """GET /status for a session that has no active study → 'no_study'。"""
    monkeypatch.setenv("QUANTNODES_RESEARCH_GOAL_DB_PATH", str(_app_env / "goals.db"))
    monkeypatch.setenv("QUANTNODES_RESEARCH_HYPOTHESES_PATH", str(_app_env / "hyp.json"))

    async with _api_client() as client:
        # sess-other exists in the fixture but has no study row.
        r = await client.get("/api/study/status?session_id=sess-other")
        assert r.status_code == 200
        assert r.json() == {"status": "no_study", "session_id": "sess-other"}


@pytest.mark.asyncio
async def test_status_returns_active_study_for_session(_app_env, tmp_path, monkeypatch):
    """GET /status?session_id= 应返回该 session 的 active study。"""
    from strategy_research.apps.study import StudyStatus, StudyStore

    db_path = tmp_path / "goals.db"
    with StudyStore(db_path=db_path) as store:
        s = store.create_study(
            owner_session_id="sess-1", goal_id=None, objective="找 alpha",
            workspace_path=str(_app_env), strategy_name="demo",
            executor_type="autoresearch", max_rounds=5,
        )
        store.update_execution_status(s.study_id, StudyStatus.RUNNING)
        study_id = s.study_id

    monkeypatch.setenv("QUANTNODES_RESEARCH_GOAL_DB_PATH", str(db_path))
    monkeypatch.setenv("QUANTNODES_RESEARCH_HYPOTHESES_PATH", str(tmp_path / "hyp.json"))

    async with _api_client() as client:
        r = await client.get("/api/study/status?session_id=sess-1")
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "ok"
        assert data["study_id"] == study_id
        assert data["execution_status"] == "running"
        assert data["objective"] == "找 alpha"
        assert data["strategy_name"] == "demo"
        assert data["workspace_path"] == str(_app_env)


@pytest.mark.asyncio
async def test_status_returns_study_by_id(_app_env, tmp_path, monkeypatch):
    """GET /status?study_id= 直接查询某个 study（不依赖 session 过滤）。"""
    from strategy_research.apps.study import StudyStore

    db_path = tmp_path / "goals.db"
    with StudyStore(db_path=db_path) as store:
        s = store.create_study(
            owner_session_id="sess-other", goal_id=None, objective="其他 session",
            workspace_path=str(_app_env), strategy_name="demo",
            executor_type="autoresearch", max_rounds=3,
        )
        study_id = s.study_id

    monkeypatch.setenv("QUANTNODES_RESEARCH_GOAL_DB_PATH", str(db_path))
    monkeypatch.setenv("QUANTNODES_RESEARCH_HYPOTHESES_PATH", str(tmp_path / "hyp.json"))

    async with _api_client() as client:
        # Use sess-other (the study's parent session); study_id is also
        # passed for explicit lookup. A2 ensures we can't read another
        # user's study by ID alone.
        r = await client.get(f"/api/study/status?session_id=sess-other&study_id={study_id}")
        assert r.status_code == 200
        assert r.json()["study_id"] == study_id

    # IDOR: tester cannot read sess-other's study via study_id alone.
    async with _api_client() as client:
        r = await client.get(f"/api/study/status?session_id=sess-1&study_id={study_id}")
        assert r.status_code == 403


@pytest.mark.asyncio
async def test_status_includes_goal_snapshot_when_goal_linked(_app_env, tmp_path, monkeypatch):
    """study.goal_id 存在时，summary 应包含 goal_snapshot。"""
    from strategy_research.apps.goal import GoalStore
    from strategy_research.apps.goal.store import RiskTier
    from strategy_research.apps.study import StudyStore

    db_path = tmp_path / "goals.db"
    with GoalStore(db_path=db_path) as gs:
        goal = gs.replace_goal(
            session_id="sess-1", objective="with goal",
            criteria=["Sharpe > 1"], risk_tier=RiskTier.RESEARCH_GENERAL,
        )
        goal_id = goal.goal_id

    with StudyStore(db_path=db_path) as store:
        store.create_study(
            owner_session_id="sess-1", goal_id=goal_id, objective="with goal",
            workspace_path=str(_app_env), strategy_name="demo",
            executor_type="autoresearch", max_rounds=3,
        )

    monkeypatch.setenv("QUANTNODES_RESEARCH_GOAL_DB_PATH", str(db_path))
    monkeypatch.setenv("QUANTNODES_RESEARCH_HYPOTHESES_PATH", str(tmp_path / "hyp.json"))

    async with _api_client() as client:
        r = await client.get("/api/study/status?session_id=sess-1")
        assert r.status_code == 200
        data = r.json()
        snap = data["goal_snapshot"]
        assert snap["goal_id"] == goal_id
        assert snap["objective"] == "with goal"
        assert isinstance(snap["criteria"], list)


@pytest.mark.asyncio
async def test_resume_interrupted_study_calls_resume_interrupted(
    _app_env, tmp_path, monkeypatch
):
    """INTERRUPTED 状态的 study 应走 sched.resume_interrupted 路径。"""
    from unittest.mock import AsyncMock, MagicMock

    from strategy_research.interfaces.api.routers import study as study_router
    from strategy_research.apps.study import StudyStatus, StudyStore

    db_path = tmp_path / "goals.db"
    with StudyStore(db_path=db_path) as store:
        s = store.create_study(
            owner_session_id="sess-1", goal_id=None, objective="interrupted obj",
            workspace_path=str(_app_env), strategy_name="demo",
            executor_type="autoresearch", max_rounds=3,
        )
        store.update_execution_status(s.study_id, StudyStatus.INTERRUPTED)
        study_id = s.study_id

    sched = MagicMock()
    sched.resume_interrupted = AsyncMock(return_value=True)
    sched.resume = MagicMock()
    monkeypatch.setattr(study_router, "_get_study_scheduler", lambda: sched)

    monkeypatch.setenv("QUANTNODES_RESEARCH_GOAL_DB_PATH", str(db_path))
    monkeypatch.setenv("QUANTNODES_RESEARCH_HYPOTHESES_PATH", str(tmp_path / "hyp.json"))

    async with _api_client() as client:
        r = await client.post(f"/api/study/{study_id}/resume")
        assert r.status_code == 200
        assert r.json()["action"] == "resumed_from_interrupted"
        sched.resume_interrupted.assert_awaited_once_with(study_id)
        sched.resume.assert_not_called()


@pytest.mark.asyncio
async def test_resume_interrupted_failure_returns_400(_app_env, tmp_path, monkeypatch):
    """resume_interrupted 返回 False → 400。"""
    from unittest.mock import AsyncMock, MagicMock

    from strategy_research.interfaces.api.routers import study as study_router
    from strategy_research.apps.study import StudyStatus, StudyStore

    db_path = tmp_path / "goals.db"
    with StudyStore(db_path=db_path) as store:
        s = store.create_study(
            owner_session_id="sess-1", goal_id=None, objective="interrupted",
            workspace_path=str(_app_env), strategy_name="demo",
            executor_type="autoresearch", max_rounds=3,
        )
        store.update_execution_status(s.study_id, StudyStatus.INTERRUPTED)
        study_id = s.study_id

    sched = MagicMock()
    sched.resume_interrupted = AsyncMock(return_value=False)
    monkeypatch.setattr(study_router, "_get_study_scheduler", lambda: sched)

    monkeypatch.setenv("QUANTNODES_RESEARCH_GOAL_DB_PATH", str(db_path))
    monkeypatch.setenv("QUANTNODES_RESEARCH_HYPOTHESES_PATH", str(tmp_path / "hyp.json"))

    async with _api_client() as client:
        r = await client.post(f"/api/study/{study_id}/resume")
        assert r.status_code == 400
        assert "failed to resume" in r.json()["detail"]


@pytest.mark.asyncio
async def test_resume_unknown_study_returns_404(_app_env, monkeypatch):
    """resume 不存在的 study_id → 404。"""
    from unittest.mock import MagicMock

    from strategy_research.interfaces.api.routers import study as study_router

    sched = MagicMock()
    sched.resume = MagicMock()
    sched.resume_interrupted = MagicMock()
    monkeypatch.setattr(study_router, "_get_study_scheduler", lambda: sched)

    monkeypatch.setenv("QUANTNODES_RESEARCH_GOAL_DB_PATH", str(_app_env / "goals.db"))
    monkeypatch.setenv("QUANTNODES_RESEARCH_HYPOTHESES_PATH", str(_app_env / "hyp.json"))

    async with _api_client() as client:
        r = await client.post("/api/study/ghost-study/resume")
        assert r.status_code == 404
        sched.resume.assert_not_called()
        sched.resume_interrupted.assert_not_called()


@pytest.mark.asyncio
async def test_start_rejects_strategy_name_with_path_traversal(_app_env, monkeypatch):
    """A1: strategy_name 包含路径分隔符或 ".." 必须被拒绝（400）。"""
    monkeypatch.setenv("QUANTNODES_RESEARCH_GOAL_DB_PATH", str(_app_env / "goals.db"))
    monkeypatch.setenv("QUANTNODES_RESEARCH_HYPOTHESES_PATH", str(_app_env / "hyp.json"))

    from unittest.mock import AsyncMock, MagicMock

    from strategy_research.interfaces.api.routers import study as study_router

    sched = MagicMock()
    sched.submit = AsyncMock()
    monkeypatch.setattr(study_router, "_get_study_scheduler", lambda: sched)

    for bad_name in ["../pwned", "foo/bar", "..", ".hidden", "with\\backslash"]:
        body = {
            "session_id": "sess-1", "objective": "x",
            "workspace_path": str(_app_env),
            "strategy_name": bad_name,
            "executor_type": "autoresearch", "max_rounds": 1,
        }
        async with _api_client() as client:
            r = await client.post("/api/study/start", json=body)
            assert r.status_code == 400, f"{bad_name!r} should be 400, got {r.status_code}"
            assert "strategy_name" in r.json()["detail"].lower() or "segment" in r.json()["detail"].lower() or "outside" in r.json()["detail"].lower()
        sched.submit.assert_not_called()
        sched.submit.reset_mock()


@pytest.mark.asyncio
async def test_start_accepts_plain_strategy_name(_app_env, monkeypatch):
    """A1: 合法的 strategy_name 仍然通过。"""
    from unittest.mock import AsyncMock, MagicMock

    from strategy_research.interfaces.api.routers import study as study_router

    sched = MagicMock()
    sched.submit = AsyncMock()
    monkeypatch.setattr(study_router, "_get_study_scheduler", lambda: sched)
    monkeypatch.setenv("QUANTNODES_RESEARCH_GOAL_DB_PATH", str(_app_env / "goals.db"))
    monkeypatch.setenv("QUANTNODES_RESEARCH_HYPOTHESES_PATH", str(_app_env / "hyp.json"))

    body = {
        "session_id": "sess-1", "objective": "x",
        "workspace_path": str(_app_env),
        "strategy_name": "demo_strategy",
        "executor_type": "autoresearch", "max_rounds": 1,
    }
    async with _api_client() as client:
        r = await client.post("/api/study/start", json=body)
        assert r.status_code == 200


# ── v2 guidance endpoint (§13.4) ────────────────────────────────────


@pytest.mark.asyncio
async def test_guidance_endpoint_task_file(_app_env, tmp_path, monkeypatch):
    monkeypatch.setenv("QUANTNODES_RESEARCH_GOAL_DB_PATH", str(tmp_path / "goals.db"))
    monkeypatch.setenv("QUANTNODES_RESEARCH_HYPOTHESES_PATH", str(tmp_path / "hyp.json"))
    study_id = _seed_study(_app_env, tmp_path, monkeypatch)
    gdir = _app_env / "study" / study_id
    gdir.mkdir(parents=True)
    (gdir / "guidance.md").write_text(
        "---\n"
        "gates:\n"
        "  - {id: risk-max-dd, metric: max_dd, op: '>=', value: -0.15}\n"
        "---\n"
        "# 研究指引\n\n禁止 MaxDD 低于 -0.15\n",
        encoding="utf-8",
    )
    async with _api_client() as client:
        r = await client.get(f"/api/study/{study_id}/guidance")
        assert r.status_code == 200
        data = r.json()
        assert data["task_scope"] is True
        assert data["gates"][0]["id"] == "risk-max-dd"
        assert data["gates"][0]["value"] == -0.15
        assert "禁止 MaxDD" in data["body"]
        assert "禁止 MaxDD" in data["text"]


@pytest.mark.asyncio
async def test_guidance_endpoint_global_fallback_and_404(_app_env, tmp_path, monkeypatch):
    monkeypatch.setenv("QUANTNODES_RESEARCH_GOAL_DB_PATH", str(tmp_path / "goals.db"))
    monkeypatch.setenv("QUANTNODES_RESEARCH_HYPOTHESES_PATH", str(tmp_path / "hyp.json"))
    study_id = _seed_study(_app_env, tmp_path, monkeypatch)

    # no guidance anywhere → 404
    async with _api_client() as client:
        r = await client.get(f"/api/study/{study_id}/guidance")
        assert r.status_code == 404

    # global template only → 200 with task_scope=False
    gdir = _app_env / "study"
    gdir.mkdir(parents=True)
    (gdir / "guidance.md").write_text("global template body\n", encoding="utf-8")
    async with _api_client() as client:
        r = await client.get(f"/api/study/{study_id}/guidance")
        assert r.status_code == 200
        data = r.json()
        assert data["task_scope"] is False
        assert "global template body" in data["body"]


@pytest.mark.asyncio
async def test_summary_includes_monitor_state(_app_env, tmp_path, monkeypatch):
    """B4: summary 应返回 monitor_state（drift_count/last_check_at/interval）。"""
    from strategy_research.apps.study import StudyStore

    db_path = tmp_path / "goals.db"
    with StudyStore(db_path=db_path) as store:
        study = store.create_study(
            owner_session_id="sess-1",
            goal_id=None,
            objective="monitor me",
            workspace_path=str(_app_env),
            strategy_name="demo_strategy",
            executor_type="autoresearch",
            monitor_interval_seconds=60,
        )
        store.update_monitor_check(study.study_id, last_check_at="2026-08-01T12:00:00", drift=True)
        store.update_monitor_check(study.study_id, last_check_at="2026-08-01T13:00:00", drift=False)
        study_id = study.study_id

    monkeypatch.setenv("QUANTNODES_RESEARCH_GOAL_DB_PATH", str(db_path))
    monkeypatch.setenv("QUANTNODES_RESEARCH_HYPOTHESES_PATH", str(tmp_path / "hyp.json"))

    app = _build_asgi_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers=_bearer(),
    ) as client:
        r = await client.get(f"/api/study/{study_id}/summary")
        assert r.status_code == 200
        ms = r.json()["monitor_state"]
        assert ms is not None
        assert ms["drift_count"] == 1  # drift=True 一次
        assert ms["interval_seconds"] == 60
        assert ms["last_check_at"] is not None


@pytest.mark.asyncio
async def test_start_rejects_workflow_executor_type(_app_env):
    """E3: /study/start 只接受 autoresearch；workflow 引导到专用端点。"""
    app = _build_asgi_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers=_bearer(),
    ) as client:
        body = {
            "session_id": "sess-1", "objective": "x",
            "workspace_path": str(_app_env), "strategy_name": "demo_strategy",
            "executor_type": "workflow",
        }
        r = await client.post("/api/study/start", json=body)
        assert r.status_code == 400
        assert "workflow" in r.json()["detail"]
