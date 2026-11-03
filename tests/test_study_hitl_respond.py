"""PR-A regression tests: HITL approval chain repair.

Covers the four breaks found in the audit:
- B1: routers/study.py used json.dumps without importing json → 500
- B2: decision contract mismatch (API approve/reject vs stored
  approved/rejected) → runner poll never saw a decision
- ownership: respond endpoint did not verify the interrupt belonged to
  the study — global id was a cross-study write primitive
- B3/B4 (resume-side propagation) live in test_hitl_resume_rebuild.py
  and test_hitl_gate_graph.py.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import httpx
import pytest


def _build_asgi_app():
    from fastapi import FastAPI

    from strategy_research.interfaces.api.middleware import AuthMiddleware
    from strategy_research.interfaces.api.routers import study

    app = FastAPI()
    app.add_middleware(AuthMiddleware)
    app.include_router(study.router, prefix="/api/study")
    return app


@pytest.fixture
def app_env(tmp_path: Path, monkeypatch):
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
    for sid in ("sess-owner",):
        conn.execute(
            "INSERT INTO sessions (id, user_id, title, created_at, updated_at) "
            "VALUES (?, 'tester', 't', ?, ?)",
            (sid, now, now),
        )
    conn.commit()
    conn.close()

    from strategy_research.apps.study import StudyStore

    with StudyStore() as store:
        study_rec = store.create_study(
            owner_session_id="sess-owner",
            goal_id=None,
            objective="test hitl",
            workspace_path=str(tmp_path / "ws"),
            strategy_name="demo",
        )
        intr = store.create_interrupt(
            study_id=study_rec.study_id,
            round_num=2,
            interrupt_type="novelty_gate",
        )
    return {"interrupt": intr}


def _bearer(user_id: str = "tester") -> dict:
    from strategy_research.interfaces.api.auth_tokens import create_token

    return {"Authorization": f"Bearer {create_token(user_id)}"}


@pytest.mark.asyncio
async def test_respond_approve_normalizes_to_approved(app_env):
    """'approve' (API verb form) is stored as 'approved' so the runner's
    _wait_for_approval poll can observe it."""
    intr = app_env["interrupt"]
    app = _build_asgi_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test",
        headers=_bearer(),
    ) as client:
        r = await client.post(
            f"/api/study/{intr.study_id}/interrupts/{intr.interrupt_id}/respond",
            json={"decision": "approve"},
        )
    assert r.status_code == 200
    assert r.json()["decision"] == "approved"

    from strategy_research.apps.study import StudyStore
    with StudyStore() as store:
        row = store.get_interrupt(intr.interrupt_id)
    assert row.status == "approved"


@pytest.mark.asyncio
async def test_respond_reject_normalizes_to_rejected(app_env):
    intr = app_env["interrupt"]
    app = _build_asgi_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test",
        headers=_bearer(),
    ) as client:
        r = await client.post(
            f"/api/study/{intr.study_id}/interrupts/{intr.interrupt_id}/respond",
            json={"decision": "reject"},
        )
    assert r.status_code == 200
    from strategy_research.apps.study import StudyStore
    with StudyStore() as store:
        row = store.get_interrupt(intr.interrupt_id)
    assert row.status == "rejected"


@pytest.mark.asyncio
async def test_respond_with_payload_does_not_crash(app_env):
    """B1 regression: payload handling must not raise NameError(json)."""
    intr = app_env["interrupt"]
    app = _build_asgi_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test",
        headers=_bearer(),
    ) as client:
        r = await client.post(
            f"/api/study/{intr.study_id}/interrupts/{intr.interrupt_id}/respond",
            json={"decision": "approve", "payload": {"note": "ok"}},
        )
    assert r.status_code == 200, r.text
    from strategy_research.apps.study import StudyStore
    with StudyStore() as store:
        row = store.get_interrupt(intr.interrupt_id)
    assert row.response is not None and "ok" in row.response


@pytest.mark.asyncio
async def test_respond_past_participle_still_accepted(app_env):
    """Legacy clients sending 'approved'/'rejected' keep working."""
    intr = app_env["interrupt"]
    app = _build_asgi_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test",
        headers=_bearer(),
    ) as client:
        r = await client.post(
            f"/api/study/{intr.study_id}/interrupts/{intr.interrupt_id}/respond",
            json={"decision": "approved"},
        )
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_respond_rejects_foreign_interrupt(tmp_path, app_env, monkeypatch):
    """An interrupt belonging to another study must not be writable via
    this study's URL path (cross-study write primitive)."""
    from strategy_research.apps.study import StudyStore

    with StudyStore() as store:
        other_rec = store.create_study(
            owner_session_id="sess-owner",
            goal_id=None,
            objective="other study",
            workspace_path="/tmp/ws-other",
            strategy_name="demo",
        )
        foreign = store.create_interrupt(
            study_id=other_rec.study_id, round_num=1,
            interrupt_type="novelty_gate",
        )

    intr = app_env["interrupt"]
    app = _build_asgi_app()

    # foreign interrupt belongs to `other_study` but the first study owns
    # both; to exercise the mismatch we address the first study's path
    # with the foreign interrupt id.
    first_study = intr.study_id
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test",
        headers=_bearer(),
    ) as client:
        r = await client.post(
            f"/api/study/{first_study}/interrupts/{foreign.interrupt_id}/respond",
            json={"decision": "approve"},
        )
    assert r.status_code == 404
    # Foreign interrupt untouched
    with StudyStore() as store:
        row = store.get_interrupt(foreign.interrupt_id)
    assert row.status == "pending"


def test_store_get_interrupt_by_id(app_env):
    from strategy_research.apps.study import StudyStore

    intr = app_env["interrupt"]
    with StudyStore() as store:
        got = store.get_interrupt(intr.interrupt_id)
        assert got is not None
        assert got.interrupt_id == intr.interrupt_id
        assert got.round_num == 2
        assert store.get_interrupt("no-such-id") is None
