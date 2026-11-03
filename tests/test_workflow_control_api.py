"""Tests for /api/goal/workflow/* endpoints — /status, /pause, /resume,
/directive, /list. The previously untested control-surface endpoints.

All tests use httpx ASGI in-process; the workflow control endpoints
read the process-local ``_active_runners`` registry, so we patch the
runner in there directly (no real workflow execution).
"""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest


def _build_asgi_app():
    from fastapi import FastAPI

    from strategy_research.interfaces.api.middleware import AuthMiddleware
    from strategy_research.interfaces.api.routers import workflow

    app = FastAPI()
    app.add_middleware(AuthMiddleware)
    app.include_router(workflow.router, prefix="/api/goal/workflow")
    return app


def _client(app):
    from strategy_research.interfaces.api.auth_tokens import create_token

    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test",
        headers={"Authorization": f"Bearer {create_token('tester')}"},
    )


def _fake_runner(progress: dict | None = None) -> SimpleNamespace:
    """A runner stub that mimics the workflow runner's surface."""
    progress = progress if progress is not None else {
        "status": "running", "completed": 1, "total": 5, "progress": 20,
    }

    r = SimpleNamespace()
    r.get_progress = lambda: progress
    r.pause = lambda *, immediate=False: setattr(r, "_paused_immediate", immediate)
    r.resume = lambda: setattr(r, "_resumed", True)
    r.add_directive = lambda content: setattr(r, "_directive", content)
    return r


@pytest.fixture(autouse=True)
def _clean_runners():
    """Reset module-level _active_runners dict around every test."""
    from strategy_research.interfaces.api.routers import workflow as wf_router

    wf_router._active_runners.clear()
    yield
    wf_router._active_runners.clear()


# ────────────────────────── /list ──────────────────────────


@pytest.mark.asyncio
async def test_list_returns_builtin_workflows():
    app = _build_asgi_app()
    async with _client(app) as c:
        r = await c.get("/api/goal/workflow/list")
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "ok"
        assert isinstance(data["workflows"], list)
        # Built-in presets are seeded by list_goal_workflows().
        assert any("name" in w for w in data["workflows"])


# ────────────────────────── /status ──────────────────────────


@pytest.mark.asyncio
async def test_status_unknown_goal_returns_not_found():
    app = _build_asgi_app()
    async with _client(app) as c:
        r = await c.get("/api/goal/workflow/status?goal_id=ghost-goal")
        assert r.status_code == 200
        assert r.json() == {
            "status": "not_found", "goal_id": "ghost-goal",
        }


@pytest.mark.asyncio
async def test_status_returns_runner_progress():
    import time
    app = _build_asgi_app()
    from strategy_research.interfaces.api.routers import workflow as wf_router

    runner = _fake_runner({"status": "running", "progress": 75})
    wf_router._active_runners["g-1"] = {
        "runner": runner,
        "session_id": "sess-1",
        "workflow_name": "factor_research",
        "started_at": time.time(),
    }

    async with _client(app) as c:
        r = await c.get("/api/goal/workflow/status?goal_id=g-1")
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "ok"
        assert data["goal_id"] == "g-1"
        assert data["workflow_name"] == "factor_research"
        assert data["progress"]["progress"] == 75


@pytest.mark.asyncio
async def test_status_prunes_terminal_entries():
    """Completed runners are pruned on access; subsequent reads return not_found."""
    app = _build_asgi_app()
    from strategy_research.interfaces.api.routers import workflow as wf_router

    runner = _fake_runner({"status": "completed", "hook_completed": True})
    wf_router._active_runners["g-done"] = {
        "runner": runner, "session_id": "s",
        "workflow_name": "wf", "started_at": __import__("time").time(),
    }

    async with _client(app) as c:
        r = await c.get("/api/goal/workflow/status?goal_id=g-done")
        assert r.json()["status"] == "not_found"
        assert "g-done" not in wf_router._active_runners


# ────────────────────────── /pause /resume /directive ──────────────────────────


@pytest.mark.asyncio
async def test_pause_unknown_goal_returns_404():
    app = _build_asgi_app()
    async with _client(app) as c:
        r = await c.post("/api/goal/workflow/pause?goal_id=ghost")
        assert r.status_code == 404


@pytest.mark.asyncio
async def test_pause_invokes_runner_pause():
    app = _build_asgi_app()
    from strategy_research.interfaces.api.routers import workflow as wf_router

    runner = _fake_runner()
    wf_router._active_runners["g-1"] = {
        "runner": runner, "session_id": "s", "workflow_name": "wf",
        "started_at": __import__("time").time(),
    }
    async with _client(app) as c:
        r = await c.post("/api/goal/workflow/pause?goal_id=g-1&immediate=true")
        assert r.status_code == 200
        assert r.json() == {"status": "ok", "paused": True}
        assert runner._paused_immediate is True


@pytest.mark.asyncio
async def test_resume_unknown_goal_returns_404():
    app = _build_asgi_app()
    async with _client(app) as c:
        r = await c.post("/api/goal/workflow/resume?goal_id=ghost")
        assert r.status_code == 404


@pytest.mark.asyncio
async def test_resume_invokes_runner_resume():
    app = _build_asgi_app()
    from strategy_research.interfaces.api.routers import workflow as wf_router

    runner = _fake_runner()
    wf_router._active_runners["g-1"] = {
        "runner": runner, "session_id": "s", "workflow_name": "wf",
        "started_at": __import__("time").time(),
    }
    async with _client(app) as c:
        r = await c.post("/api/goal/workflow/resume?goal_id=g-1")
        assert r.status_code == 200
        assert r.json() == {"status": "ok", "resumed": True}
        assert runner._resumed is True


@pytest.mark.asyncio
async def test_directive_unknown_goal_returns_404():
    app = _build_asgi_app()
    async with _client(app) as c:
        r = await c.post(
            "/api/goal/workflow/directive?goal_id=ghost",
            json={"content": "停一停"},
        )
        assert r.status_code == 404


@pytest.mark.asyncio
async def test_directive_passes_content_to_runner():
    app = _build_asgi_app()
    from strategy_research.interfaces.api.routers import workflow as wf_router

    runner = _fake_runner()
    wf_router._active_runners["g-1"] = {
        "runner": runner, "session_id": "s", "workflow_name": "wf",
        "started_at": __import__("time").time(),
    }
    async with _client(app) as c:
        r = await c.post(
            "/api/goal/workflow/directive?goal_id=g-1",
            json={"content": "改成反转策略"},
        )
        assert r.status_code == 200
        assert r.json() == {"status": "ok", "directive_added": True}
        assert runner._directive == "改成反转策略"
