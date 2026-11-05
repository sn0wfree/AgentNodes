"""Tests for the ``/api/schedule/*`` routes — CRUD + ownership (IDOR).

The dispatch bridge itself (job → study) is covered by
``test_scheduled_executor.py`` + ``test_schedule_to_study.py``; here we
verify the HTTP layer: parsing, cron validation, ownership checks.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import httpx
import pytest

from strategy_research.kernel.scheduled_research.models import JobStatus
from strategy_research.kernel.scheduled_research.store import ScheduledResearchStore


def _build_asgi_app():
    from fastapi import FastAPI

    from strategy_research.interfaces.api.middleware import AuthMiddleware
    from strategy_research.interfaces.api.routers import chat, schedule, study
    from strategy_research.interfaces.api.routers.web_session import router as session_router

    app = FastAPI()
    app.add_middleware(AuthMiddleware)
    app.include_router(chat.router, prefix="/api/chat")
    app.include_router(session_router, prefix="/api/chat/session")
    app.include_router(study.router, prefix="/api/study")
    app.include_router(schedule.router, prefix="/api/schedule")
    return app


@pytest.fixture
def _app_env(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("QUANTNODES_RESEARCH_GOAL_DB_PATH", str(tmp_path / "goals.db"))
    monkeypatch.setenv("QUANTNODES_RESEARCH_HYPOTHESES_PATH", str(tmp_path / "hyp.json"))
    sessions_db = tmp_path / "sessions.db"
    monkeypatch.setenv("SR_SESSIONS_DB", str(sessions_db))
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
    for sid in ("sess-1", "sess-A", "sess-other"):
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


def _store(tmp_path: Path) -> ScheduledResearchStore:
    return ScheduledResearchStore(path=tmp_path / "goals.db")


async def _create_job(client, *, session_id="sess-1", objective="研究动量因子",
                      strategy="demo_strategy", interval=3600, **extra) -> dict:
    body = {
        "session_id": session_id,
        "objective": objective,
        "workspace_path": str(client.base_url) if False else "/ws",  # replaced below
        "strategy_name": strategy,
        "interval_seconds": interval,
        "max_rounds": 3,
        "metric_targets": [{"name": "calmar", "op": ">=", "value": 0.5}],
        **extra,
    }
    r = await client.post("/api/schedule/create", json=body)
    assert r.status_code == 200, r.text
    return r.json()


@pytest.mark.asyncio
async def test_create_job(_app_env, monkeypatch):
    monkeypatch.setenv("QUANTNODES_RESEARCH_GOAL_DB_PATH", str(_app_env.parent / "goals.db"))
    app = _build_asgi_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers=_bearer(),
    ) as client:
        body = {
            "session_id": "sess-1",
            "objective": "研究动量因子",
            "workspace_path": str(_app_env),
            "strategy_name": "demo_strategy",
            "interval_seconds": 3600,
            "max_rounds": 3,
            "metric_targets": [{"name": "calmar", "op": ">=", "value": 0.5}],
        }
        r = await client.post("/api/schedule/create", json=body)
    assert r.status_code == 200, r.text
    data = r.json()
    store = _store(_app_env.parent)
    job = store.get(data["job_id"])
    store.close()
    assert job is not None
    assert job.target == "study"
    assert job.prompt == "研究动量因子"
    assert job.owner_session_id == "sess-1"
    assert job.config["metric_targets"][0]["name"] == "calmar"
    assert job.next_run_at > 0


@pytest.mark.asyncio
async def test_create_with_cron(_app_env):
    app = _build_asgi_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers=_bearer(),
    ) as client:
        r = await client.post("/api/schedule/create", json={
            "session_id": "sess-1",
            "objective": "x",
            "workspace_path": str(_app_env),
            "strategy_name": "demo_strategy",
            "cron": "0 2 * * *",
        })
    assert r.status_code == 200, r.text
    store = _store(_app_env.parent)
    job = store.get(r.json()["job_id"])
    store.close()
    assert job.cron == "0 2 * * *"


@pytest.mark.asyncio
async def test_create_requires_cron_or_interval(_app_env):
    app = _build_asgi_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers=_bearer(),
    ) as client:
        r = await client.post("/api/schedule/create", json={
            "session_id": "sess-1",
            "objective": "x",
            "workspace_path": str(_app_env),
            "strategy_name": "demo_strategy",
        })
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_create_invalid_cron(_app_env):
    app = _build_asgi_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers=_bearer(),
    ) as client:
        r = await client.post("/api/schedule/create", json={
            "session_id": "sess-1",
            "objective": "x",
            "workspace_path": str(_app_env),
            "strategy_name": "demo_strategy",
            "cron": "bad cron expr",
        })
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_create_unknown_session_rejected(_app_env):
    app = _build_asgi_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers=_bearer(),
    ) as client:
        r = await client.post("/api/schedule/create", json={
            "session_id": "no-such",
            "objective": "x",
            "workspace_path": str(_app_env),
            "strategy_name": "demo_strategy",
            "interval_seconds": 60,
        })
    assert r.status_code == 404
    assert "session not found" in r.json()["detail"].lower()


@pytest.mark.asyncio
async def test_list_own_jobs_only(_app_env):
    app = _build_asgi_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers=_bearer(),
    ) as client:
        for sid in ("sess-1", "sess-1", "sess-A"):
            await client.post("/api/schedule/create", json={
                "session_id": sid,
                "objective": "x",
                "workspace_path": str(_app_env),
                "strategy_name": "demo_strategy",
                "interval_seconds": 3600,
            })
        r = await client.get("/api/schedule/list", params={"session_id": "sess-1"})
    assert r.status_code == 200, r.text
    jobs = r.json()["jobs"]
    assert len(jobs) == 2
    assert all(j["owner_session_id"] == "sess-1" for j in jobs)


@pytest.mark.asyncio
async def test_show_own_job(_app_env):
    app = _build_asgi_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers=_bearer(),
    ) as client:
        data = await client.post("/api/schedule/create", json={
            "session_id": "sess-1",
            "objective": "x",
            "workspace_path": str(_app_env),
            "strategy_name": "demo_strategy",
            "interval_seconds": 3600,
        })
        job_id = data.json()["job_id"]
        r = await client.get(
            f"/api/schedule/show/{job_id}",
            params={"session_id": "sess-1"},
        )
    assert r.status_code == 200, r.text
    assert r.json()["job"]["job_id"] == job_id


@pytest.mark.asyncio
async def test_show_foreign_job_forbidden(_app_env):
    app = _build_asgi_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers=_bearer(),
    ) as client:
        data = await client.post("/api/schedule/create", json={
            "session_id": "sess-1",
            "objective": "x",
            "workspace_path": str(_app_env),
            "strategy_name": "demo_strategy",
            "interval_seconds": 3600,
        })
        job_id = data.json()["job_id"]
        r = await client.get(
            f"/api/schedule/show/{job_id}",
            params={"session_id": "sess-A"},
        )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_show_missing_job_404(_app_env):
    app = _build_asgi_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers=_bearer(),
    ) as client:
        r = await client.get(
            "/api/schedule/show/job_ghost",
            params={"session_id": "sess-1"},
        )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_cancel_job(_app_env):
    app = _build_asgi_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers=_bearer(),
    ) as client:
        data = await client.post("/api/schedule/create", json={
            "session_id": "sess-1",
            "objective": "x",
            "workspace_path": str(_app_env),
            "strategy_name": "demo_strategy",
            "interval_seconds": 3600,
        })
        job_id = data.json()["job_id"]
        r = await client.post(
            f"/api/schedule/cancel/{job_id}",
            params={"session_id": "sess-1"},
        )
    assert r.status_code == 200, r.text
    store = _store(_app_env.parent)
    job = store.get(job_id)
    store.close()
    assert job.status == JobStatus.CANCELLED


@pytest.mark.asyncio
async def test_cancel_foreign_job_forbidden(_app_env):
    app = _build_asgi_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers=_bearer(),
    ) as client:
        data = await client.post("/api/schedule/create", json={
            "session_id": "sess-1",
            "objective": "x",
            "workspace_path": str(_app_env),
            "strategy_name": "demo_strategy",
            "interval_seconds": 3600,
        })
        job_id = data.json()["job_id"]
        r = await client.post(
            f"/api/schedule/cancel/{job_id}",
            params={"session_id": "sess-A"},
        )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_delete_job(_app_env):
    app = _build_asgi_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers=_bearer(),
    ) as client:
        data = await client.post("/api/schedule/create", json={
            "session_id": "sess-1",
            "objective": "x",
            "workspace_path": str(_app_env),
            "strategy_name": "demo_strategy",
            "interval_seconds": 3600,
        })
        job_id = data.json()["job_id"]
        r = await client.post(
            f"/api/schedule/delete/{job_id}",
            params={"session_id": "sess-1"},
        )
    assert r.status_code == 200, r.text
    store = _store(_app_env.parent)
    assert store.get(job_id) is None
    store.close()
