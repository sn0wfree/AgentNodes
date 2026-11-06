"""P5-5③ security regression: workflow object-level ownership.

Covers both runner families and drafts:
- goal-runner registry (start/status/pause): owner-scoped, foreign →
  not_found (no existence oracle)
- definition runs (start-definition): session ownership required at
  start; run access resolves through the run record's session
- canvas drafts: owner-stamped on write, foreign reads → {dag: null},
  foreign delete → 404, legacy NULL-owner rows stay shared-legacy
"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from agentnodes.interfaces.api.auth_tokens import create_token


def _build_app():
    from fastapi import FastAPI

    from agentnodes.interfaces.api.middleware import AuthMiddleware
    from agentnodes.interfaces.api.routers import workflow

    app = FastAPI()
    app.add_middleware(AuthMiddleware)
    app.include_router(workflow.router, prefix="/api/goal/workflow")
    return app


def _client(app, user: str = "tester"):
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test",
        headers={"Authorization": f"Bearer {create_token(user)}"},
    )


@pytest.fixture()
def env(tmp_path: Path, monkeypatch):
    """App + seeded sessions: s1→tester, s2→alice; runners reset."""
    import agentnodes.interfaces.api.routers.workflow as wf
    import agentnodes.interfaces.api.session.web_session_db as wsdb

    monkeypatch.setenv("AN_WORKSPACE_PATH", str(tmp_path))
    monkeypatch.setenv("AN_ADMIN_TOKEN", "test-admin-token")
    monkeypatch.setattr(wsdb, "_get_db_path", lambda: str(tmp_path / "sessions.db"))
    wf._run_store = None
    wf._run_registry = None
    wf._active_runners.clear()

    async def _seed() -> None:
        from agentnodes.interfaces.api.routers.web_session import (
            WebSessionCreate,
            create_session,
        )
        request = SimpleNamespace(state=SimpleNamespace(user_id="tester"))
        await create_session(WebSessionCreate(id="s1", title="t"), request)
        request_a = SimpleNamespace(state=SimpleNamespace(user_id="alice"))
        await create_session(WebSessionCreate(id="s2", title="t"), request_a)

    asyncio.run(_seed())
    yield wf
    wf._active_runners.clear()
    wf._run_store = None
    wf._run_registry = None


def _runner_entry(owner: str = "tester") -> dict:
    return {
        "runner": SimpleNamespace(
            get_progress=lambda: {"status": "running"},
            pause=lambda **k: None,
            resume=lambda: None,
            add_directive=lambda content: None,
        ),
        "session_id": "s1",
        "workflow_name": "demo",
        "started_at": time.time(),
        "owner": owner,
    }


# ── goal-runner registry family ─────────────────────────────


def test_runner_start_records_owner(env):
    env._active_runners["g1"] = _runner_entry()
    app = _build_app()

    async def _run():
        async with _client(app, "tester") as c:
            return (await c.get("/api/goal/workflow/status", params={"goal_id": "g1"})).json()

    body = asyncio.run(_run())
    assert body["status"] == "ok"


def test_runner_status_foreign_is_not_found(env):
    """Foreign user gets the same not_found shape as an unknown id —
    no existence oracle."""
    env._active_runners["g1"] = _runner_entry()
    app = _build_app()

    async def _run():
        async with _client(app, "alice") as c:
            r = await c.get("/api/goal/workflow/status", params={"goal_id": "g1"})
            r2 = await c.get("/api/goal/workflow/status", params={"goal_id": "ghost"})
            return r.json(), r2.json()

    foreign, unknown = asyncio.run(_run())
    # 不可区分性：状态同形（goal_id 各自回显查询值）
    assert foreign["status"] == unknown["status"] == "not_found"


def test_runner_pause_foreign_is_404(env):
    env._active_runners["g1"] = _runner_entry()
    app = _build_app()

    async def _run():
        async with _client(app, "alice") as c:
            return await c.post("/api/goal/workflow/pause", params={"goal_id": "g1"})

    assert asyncio.run(_run()).status_code == 404


# ── definition runs family ──────────────────────────────────


def test_start_definition_requires_session_ownership(env, tmp_path, monkeypatch):
    """start-definition with a session the caller doesn't own → 403
    (existing-but-foreign; _fetch_session_owned semantics)."""
    app = _build_app()

    async def _run():
        async with _client(app, "alice") as c:
            return await c.post("/api/goal/workflow/start-definition", json={
                "session_id": "s1",  # owned by tester; alice is foreign
                "definition_name": "plan_execute_auto",
                "objective": "研究动量策略",
            })

    resp = asyncio.run(_run())
    assert resp.status_code == 403


def test_run_access_via_record_session(env, tmp_path):
    """Persisted run (no live runner): ownership resolves through the
    record's session_id — owner passes, foreign 404s."""
    store = env._get_run_store()
    store.create_run(run_id="r1", definition_name="d", session_id="s1",
                     objective="o", params_snapshot=None)
    app = _build_app()

    async def _run():
        async with _client(app, "tester") as c:
            ok = await c.get("/api/goal/workflow/run/r1/status")
        async with _client(app, "alice") as c:
            foreign = await c.get("/api/goal/workflow/run/r1/status")
        return ok.status_code, foreign.status_code

    ok_code, foreign_code = asyncio.run(_run())
    assert ok_code == 200
    assert foreign_code == 403


# ── canvas drafts ───────────────────────────────────────────


def test_draft_owner_scoped(env):
    app = _build_app()
    payload = {
        "dag_id": "d1",
        "nodes": [{"id": "n1"}],
        "edges": [],
    }

    async def _run():
        async with _client(app, "tester") as c:
            put = await c.put("/api/goal/workflow/orchestrate/draft", json=payload)
            own_get = await c.get("/api/goal/workflow/orchestrate/draft/d1")
        async with _client(app, "alice") as c:
            foreign_get = await c.get("/api/goal/workflow/orchestrate/draft/d1")
            foreign_del = await c.delete("/api/goal/workflow/orchestrate/draft/d1")
        async with _client(app, "tester") as c:
            own_del = await c.delete("/api/goal/workflow/orchestrate/draft/d1")
        return put, own_get, foreign_get, foreign_del, own_del

    put, own_get, foreign_get, foreign_del, own_del = asyncio.run(_run())
    assert put.status_code == 200
    assert own_get.json()["dag"] == {"nodes": [{"id": "n1"}], "edges": []}
    # foreign read: same shape as absent (no oracle); delete: 404
    assert foreign_get.json() == {"dag": None}
    assert foreign_del.status_code == 404
    assert own_del.status_code == 200


def test_draft_legacy_null_owner_stays_shared(env, tmp_path):
    """Pre-fix DB rows (owner NULL) remain readable/deletable — the
    documented shared-legacy semantics."""
    conn = env._draft_db()
    conn.execute(
        "INSERT INTO drafts (dag_id, nodes_json, edges_json, updated_at, owner) "
        "VALUES ('legacy', '[]', '[]', 0, NULL)",
    )
    conn.commit()
    conn.close()

    app = _build_app()

    async def _run():
        async with _client(app, "alice") as c:
            got = await c.get("/api/goal/workflow/orchestrate/draft/legacy")
            deleted = await c.delete("/api/goal/workflow/orchestrate/draft/legacy")
        return got.json(), deleted.status_code

    body, code = asyncio.run(_run())
    assert body == {"dag": {"nodes": [], "edges": []}}
    assert code == 200


# ── definitions delete admin gate ───────────────────────────


def test_definitions_delete_requires_admin(env, tmp_path, monkeypatch):
    """User-token DELETE of a user definition → 401/403; admin token → 200."""
    from agentnodes.kernel.workflow.builtin import (
        load_definition,
        save_user_definition,
    )
    from agentnodes.kernel.workflow.definition import WorkflowDefinition

    definition = WorkflowDefinition.from_dict(
        {"name": "mine",
         "nodes": [{"id": "n1", "type": "llm_agent",
                    "config": {"role": "planner"},
                    "depends_on": []}],
         "edges": []},
        source="user",
    )
    save_user_definition(definition, tmp_path)
    assert load_definition("mine", tmp_path) is not None

    app = _build_app()

    async def _run():
        async with _client(app, "tester") as c:
            denied = await c.delete("/api/goal/workflow/definitions/mine")
        async with _client(app, "tester") as c:
            allowed = await c.delete(
                "/api/goal/workflow/definitions/mine",
                headers={"X-Admin-Token": "test-admin-token"},
            )
        return denied.status_code, allowed.status_code

    denied, allowed = asyncio.run(_run())
    assert denied in (401, 403)
    assert allowed == 200
