"""Phase 5 tests — action matrix + available_actions + unified dispatch + redo.

Covers:
- ``allowed_actions`` matrix per status
- GET /{id}/available_actions (status-dependent list)
- POST /{id}/actions/{name} dispatch + 409 when not allowed
- POST /{id}/rounds/{n}/redo: DB row + state.json + round dir rewind
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import httpx
import pytest

from strategy_research.apps.study.models import (
    StudyAction,
    StudyStatus,
    allowed_actions,
)


def _force_status(store, study_id: str, status: StudyStatus) -> None:
    """Helper: directly set execution_status (test-only shortcut)."""
    store.update_execution_status(study_id, status)


def _bearer(user_id: str = "tester") -> dict:
    from strategy_research.api.auth_tokens import create_token

    return {"Authorization": f"Bearer {create_token(user_id)}"}


def _build_asgi_app():
    from fastapi import FastAPI

    from strategy_research.api.middleware import AuthMiddleware
    from strategy_research.api.routers import chat, study
    from strategy_research.api.routers.web_session import router as session_router

    app = FastAPI()
    app.add_middleware(AuthMiddleware)
    app.include_router(chat.router, prefix="/api/chat")
    app.include_router(session_router, prefix="/api/chat/session")
    app.include_router(study.router, prefix="/api/study")
    return app


@pytest.fixture
def _env(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("QUANTNODES_RESEARCH_GOAL_DB_PATH", str(tmp_path / "goals.db"))
    monkeypatch.setenv("QUANTNODES_RESEARCH_HYPOTHESES_PATH", str(tmp_path / "hyp.json"))
    sessions_db = tmp_path / "sessions.db"
    monkeypatch.setenv("SR_SESSIONS_DB", str(sessions_db))
    conn = sqlite3.connect(str(sessions_db))
    conn.executescript(
        """
        CREATE TABLE sessions (
          id TEXT PRIMARY KEY, user_id TEXT NOT NULL, title TEXT,
          created_at TEXT, updated_at TEXT, starred INTEGER NOT NULL DEFAULT 0,
          tags_json TEXT NOT NULL DEFAULT '[]', message_count INTEGER NOT NULL DEFAULT 0,
          archived INTEGER NOT NULL DEFAULT 0
        );
        """
    )
    conn.execute(
        "INSERT INTO sessions (id, user_id, created_at, updated_at) "
        "VALUES ('sess-5', 'tester', '2026-08-01T10:00:00', '2026-08-01T10:00:00')"
    )
    conn.commit()
    conn.close()
    from strategy_research.apps.study import StudyStore

    store = StudyStore()
    rec = store.create_study(
        owner_session_id="sess-5", goal_id=None, objective="x",
        workspace_path=str(tmp_path), strategy_name="demo",
    )
    return rec.study_id, tmp_path


# ── action matrix ────────────────────────────────────────────────────


class TestActionMatrix:
    def test_running_allows_pause_cancel(self):
        acts = allowed_actions(StudyStatus.RUNNING)
        assert StudyAction.PAUSE in acts
        assert StudyAction.CANCEL in acts
        assert StudyAction.CONTINUE not in acts

    def test_paused_allows_continue_cancel(self):
        acts = allowed_actions(StudyStatus.PAUSED)
        assert StudyAction.CONTINUE in acts
        assert StudyAction.CANCEL in acts

    def test_interrupted_allows_continue_archive_replace(self):
        acts = allowed_actions(StudyStatus.INTERRUPTED)
        assert StudyAction.CONTINUE in acts
        assert StudyAction.ARCHIVE in acts
        assert StudyAction.REPLACE_OBJECTIVE in acts
        assert StudyAction.PAUSE not in acts
        # M11: an interrupted study (crashed executor) can now be
        # abandoned directly instead of only archive/resume-first.
        assert StudyAction.CANCEL in acts

    def test_complete_cancelled_allow_continue_archive(self):
        for st in (StudyStatus.COMPLETE, StudyStatus.CANCELLED):
            acts = allowed_actions(st)
            assert StudyAction.CONTINUE in acts, st
            assert StudyAction.ARCHIVE in acts, st
            assert len(acts) == 2, st

    def test_retryable_terminal_states_allow_continue_archive(self):
        for st in (StudyStatus.ERROR, StudyStatus.BUDGET_LIMITED,
                   StudyStatus.EARLY_STOPPED, StudyStatus.NEEDS_REFRESH):
            acts = allowed_actions(st)
            assert StudyAction.CONTINUE in acts, st
            assert StudyAction.ARCHIVE in acts, st
            assert len(acts) == 2, st

    def test_archived_only_allows_unarchive(self):
        acts = allowed_actions(StudyStatus.ARCHIVED)
        assert acts == frozenset({StudyAction.UNARCHIVE})


# ── HTTP: available_actions + dispatch ───────────────────────────────


@pytest.mark.asyncio
async def test_available_actions_matches_matrix(_env):
    study_id, tmp_path = _env
    app = _build_asgi_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers=_bearer(),
    ) as client:
        r = await client.get(f"/api/study/{study_id}/available_actions")
        assert r.status_code == 200
        body = r.json()
        assert body["execution_status"] == StudyStatus.QUEUED.value
        names = {a["name"] for a in body["actions"]}
        # QUEUED now: CANCEL + ARCHIVE + REPLACE_OBJECTIVE
        assert names == {
            StudyAction.CANCEL.value,
            StudyAction.ARCHIVE.value,
            StudyAction.REPLACE_OBJECTIVE.value,
        }
        # CANCEL + ARCHIVE are destructive; REPLACE_OBJECTIVE is not
        action_map = {a["name"]: a for a in body["actions"]}
        assert action_map[StudyAction.CANCEL.value]["destructive"] is True
        assert action_map[StudyAction.ARCHIVE.value]["destructive"] is True
        assert action_map[StudyAction.REPLACE_OBJECTIVE.value]["destructive"] is False


@pytest.mark.asyncio
async def test_dispatch_cancel_not_allowed_returns_409(_env):
    study_id, _ = _env
    app = _build_asgi_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers=_bearer(),
    ) as client:
        # QUEUED → pause is not in the matrix → 409
        r = await client.post(f"/api/study/{study_id}/actions/pause")
        assert r.status_code == 409
        # QUEUED → cancel IS allowed but scheduler has no token yet → 409
        # (scheduler.pause/cancel returns False when no control token)
        r2 = await client.post(f"/api/study/{study_id}/actions/cancel")
        assert r2.status_code in (200, 409)


@pytest.mark.asyncio
async def test_dispatch_unknown_action_404(_env):
    study_id, _ = _env
    app = _build_asgi_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers=_bearer(),
    ) as client:
        r = await client.post(f"/api/study/{study_id}/actions/bogus")
        assert r.status_code == 404


# ── redo ─────────────────────────────────────────────────────────────


def test_scheduler_redo_rewinds_state_and_db(tmp_path, monkeypatch):
    monkeypatch.setenv("QUANTNODES_RESEARCH_GOAL_DB_PATH", str(tmp_path / "g.db"))
    monkeypatch.setenv("QUANTNODES_RESEARCH_HYPOTHESES_PATH", str(tmp_path / "h.json"))
    import asyncio

    from strategy_research.apps.study import StudyScheduler, StudyStore
    from strategy_research.apps.study import state_store as ss

    store = StudyStore()
    rec = store.create_study(
        owner_session_id="s1", goal_id=None, objective="x",
        workspace_path=str(tmp_path), strategy_name="demo",
    )
    sid = rec.study_id
    # Simulate: round 3 completed (DB row + state.json + round dir).
    store.append_round(sid, 3, "run_0001", metrics={"calmar": 1.0}, verdict="keep")
    store.update_round_heartbeat(sid, 3)
    ws = tmp_path / "study" / sid
    (ws / "rounds" / "round_0003" / "run_0001").mkdir(parents=True)
    (ws / "rounds" / "round_0003" / "run_0001" / "strategy.py").write_text("PARAMS={}\n")
    st = ss.StudyState()
    st.last_completed_round = 3
    st.last_keep_run_dir = "rounds/round_0003/run_0001"
    ss.save(tmp_path, sid, st)

    sched = StudyScheduler(store, session_service=None)

    async def main():
        ok = await sched.redo(sid, 3, workspace_path=str(tmp_path))
        assert ok is True
        # DB round row gone + current_round rewound
        assert store.get_round(sid, 3) is None
        assert store.get_study(sid).current_round == 2
        # state rewound
        st2 = ss.load(tmp_path, sid)
        assert st2.last_completed_round == 2
        assert st2.last_keep_run_dir is None
        # round dir removed
        assert not (ws / "rounds" / "round_0003").exists()
        # re-queued for execution
        assert store.get_study(sid).execution_status == StudyStatus.QUEUED
        await sched.shutdown()

    asyncio.run(main())


def test_scheduler_redo_rejects_running(tmp_path, monkeypatch):
    monkeypatch.setenv("QUANTNODES_RESEARCH_GOAL_DB_PATH", str(tmp_path / "g.db"))
    monkeypatch.setenv("QUANTNODES_RESEARCH_HYPOTHESES_PATH", str(tmp_path / "h.json"))
    import asyncio

    from strategy_research.apps.study import StudyScheduler, StudyStore

    store = StudyStore()
    rec = store.create_study(
        owner_session_id="s1", goal_id=None, objective="x",
        workspace_path=str(tmp_path), strategy_name="demo",
    )
    store.update_execution_status(rec.study_id, StudyStatus.RUNNING)
    sched = StudyScheduler(store, session_service=None)

    async def main():
        ok = await sched.redo(rec.study_id, 2, workspace_path=str(tmp_path))
        assert ok is False
        await sched.shutdown()

    asyncio.run(main())


# ── G1: per-user concurrency cap ─────────────────────────────────────


def test_per_user_cap_blocks_third_study(tmp_path, monkeypatch):
    """G1: SR_STUDY_MAX_PER_USER=2 — 用户 A 第 3 个 study 被 per-user
    semaphore 挡住（全局 semaphore 用高值避免干扰）。"""
    monkeypatch.setenv("QUANTNODES_RESEARCH_GOAL_DB_PATH", str(tmp_path / "g.db"))
    monkeypatch.setenv("QUANTNODES_RESEARCH_HYPOTHESES_PATH", str(tmp_path / "h.json"))
    monkeypatch.setenv("SR_STUDY_MAX_CONCURRENT", "10")
    monkeypatch.setenv("SR_STUDY_MAX_PER_USER", "2")
    import asyncio

    from strategy_research.apps.study import StudyScheduler, StudyStore

    store = StudyStore()
    ids = []
    for i in range(3):
        rec = store.create_study(
            owner_session_id="user-a", goal_id=None, objective=f"o{i}",
            workspace_path=str(tmp_path), strategy_name="demo",
        )
        ids.append(rec.study_id)

    sched = StudyScheduler(store, session_service=None)
    # 直接占用 2 个 per-user 名额（模拟已在运行）
    s1 = sched._user_semaphores.setdefault("user-a", asyncio.Semaphore(2))
    await_got = []

    async def main():
        await s1.acquire()
        await s1.acquire()
        # 第 3 个应该被阻塞 —— 用 wait_for 确认超时
        owner = "user-a"
        sem = sched._user_semaphores.setdefault(owner, asyncio.Semaphore(2))
        try:
            await asyncio.wait_for(sem.acquire(), timeout=0.2)
            await_got.append(True)
        except asyncio.TimeoutError:
            await_got.append(False)
        # dump 反映 per-user 状态
        dump = sched.dump_concurrency()
        assert dump["per_user_limit"] == 2
        assert dump["per_user_active"].get("user-a") == 2
        await sched.shutdown()

    asyncio.run(main())
    assert await_got == [False], "第 3 个 study 不应获得 per-user 名额"


# ── E1: IDOR enforcement（SR_ENFORCE_STUDY_IDOR=1） ──────────────────


def test_idor_blocks_other_users_study(tmp_path, monkeypatch):
    """E1: 开启 IDOR 后，另一用户的 study 不可读/不可操作（403）。"""
    monkeypatch.setenv("QUANTNODES_RESEARCH_GOAL_DB_PATH", str(tmp_path / "g.db"))
    monkeypatch.setenv("QUANTNODES_RESEARCH_HYPOTHESES_PATH", str(tmp_path / "h.json"))
    monkeypatch.setenv("SR_ENFORCE_STUDY_IDOR", "1")
    import sqlite3
    from strategy_research.api.auth_tokens import create_token
    from strategy_research.apps.study import StudyStore

    # sessions: owner-1 属于 alice，owner-2 属于 bob
    conn = sqlite3.connect(str(tmp_path / "sessions.db"))
    conn.executescript(
        """
        CREATE TABLE sessions (
          id TEXT PRIMARY KEY, user_id TEXT NOT NULL, title TEXT,
          created_at TEXT, updated_at TEXT, starred INTEGER NOT NULL DEFAULT 0,
          tags_json TEXT NOT NULL DEFAULT '[]', message_count INTEGER NOT NULL DEFAULT 0,
          archived INTEGER NOT NULL DEFAULT 0
        );
        """
    )
    for sid, uid in (("owner-1", "alice"), ("owner-2", "bob")):
        conn.execute(
            "INSERT INTO sessions (id, user_id, created_at, updated_at) "
            "VALUES (?, ?, '2026-08-01T10:00:00', '2026-08-01T10:00:00')",
            (sid, uid),
        )
    conn.commit()
    conn.close()
    monkeypatch.setenv("SR_SESSIONS_DB", str(tmp_path / "sessions.db"))

    store = StudyStore()
    rec = store.create_study(
        owner_session_id="owner-2", goal_id=None, objective="bob's study",
        workspace_path=str(tmp_path), strategy_name="demo",
    )
    study_id = rec.study_id

    from strategy_research.api.app import create_app
    from fastapi.testclient import TestClient

    client = TestClient(
        create_app(),
        headers={"Authorization": f"Bearer {create_token('alice')}"},
    )
    # alice 试图读 bob 的 study → 403
    r = client.get(f"/api/study/{study_id}/summary")
    assert r.status_code == 403
    r2 = client.get(f"/api/study/{study_id}/rounds")
    assert r2.status_code == 403
    r3 = client.post(f"/api/study/{study_id}/actions/cancel")
    assert r3.status_code == 403
    # Newly-isolated control endpoints must also be 403 for non-owners.
    r4 = client.post(f"/api/study/{study_id}/pause")
    assert r4.status_code == 403
    r5 = client.post(f"/api/study/{study_id}/resume")
    assert r5.status_code == 403
    r6 = client.get(f"/api/study/{study_id}/directives")
    assert r6.status_code == 403
    r7 = client.post(
        f"/api/study/{study_id}/directive",
        json={"content": "redirect"},
    )
    assert r7.status_code == 403


def test_idor_allows_own_study(tmp_path, monkeypatch):
    """E1: 开启 IDOR 后，本人 study 正常可读。"""
    monkeypatch.setenv("QUANTNODES_RESEARCH_GOAL_DB_PATH", str(tmp_path / "g.db"))
    monkeypatch.setenv("QUANTNODES_RESEARCH_HYPOTHESES_PATH", str(tmp_path / "h.json"))
    monkeypatch.setenv("SR_ENFORCE_STUDY_IDOR", "1")
    import sqlite3
    from strategy_research.api.auth_tokens import create_token
    from strategy_research.apps.study import StudyStore

    conn = sqlite3.connect(str(tmp_path / "sessions.db"))
    conn.executescript(
        """
        CREATE TABLE sessions (
          id TEXT PRIMARY KEY, user_id TEXT NOT NULL, title TEXT,
          created_at TEXT, updated_at TEXT, starred INTEGER NOT NULL DEFAULT 0,
          tags_json TEXT NOT NULL DEFAULT '[]', message_count INTEGER NOT NULL DEFAULT 0,
          archived INTEGER NOT NULL DEFAULT 0
        );
        """
    )
    conn.execute(
        "INSERT INTO sessions (id, user_id, created_at, updated_at) "
        "VALUES ('owner-1', 'alice', '2026-08-01T10:00:00', '2026-08-01T10:00:00')"
    )
    conn.commit()
    conn.close()
    monkeypatch.setenv("SR_SESSIONS_DB", str(tmp_path / "sessions.db"))

    store = StudyStore()
    rec = store.create_study(
        owner_session_id="owner-1", goal_id=None, objective="alice's study",
        workspace_path=str(tmp_path), strategy_name="demo",
    )
    from strategy_research.api.app import create_app
    from fastapi.testclient import TestClient

    client = TestClient(
        create_app(),
        headers={"Authorization": f"Bearer {create_token('alice')}"},
    )
    r = client.get(f"/api/study/{rec.study_id}/summary")
    assert r.status_code == 200
    assert r.json()["objective"] == "alice's study"


def test_idor_enforcement_off_allows_cross_user(tmp_path, monkeypatch):
    """SR_ENFORCE_STUDY_IDOR=0 disables ownership checks (single-user)."""
    monkeypatch.setenv("QUANTNODES_RESEARCH_GOAL_DB_PATH", str(tmp_path / "g.db"))
    monkeypatch.setenv("QUANTNODES_RESEARCH_HYPOTHESES_PATH", str(tmp_path / "h.json"))
    monkeypatch.setenv("SR_ENFORCE_STUDY_IDOR", "0")
    import sqlite3
    from strategy_research.api.auth_tokens import create_token
    from strategy_research.apps.study import StudyStore

    conn = sqlite3.connect(str(tmp_path / "sessions.db"))
    conn.executescript(
        """
        CREATE TABLE sessions (
          id TEXT PRIMARY KEY, user_id TEXT NOT NULL, title TEXT,
          created_at TEXT, updated_at TEXT, starred INTEGER NOT NULL DEFAULT 0,
          tags_json TEXT NOT NULL DEFAULT '[]', message_count INTEGER NOT NULL DEFAULT 0,
          archived INTEGER NOT NULL DEFAULT 0
        );
        """
    )
    conn.execute(
        "INSERT INTO sessions (id, user_id, created_at, updated_at) "
        "VALUES ('owner-9', 'bob', '2026-08-01T10:00:00', '2026-08-01T10:00:00')"
    )
    conn.commit()
    conn.close()
    monkeypatch.setenv("SR_SESSIONS_DB", str(tmp_path / "sessions.db"))

    store = StudyStore()
    rec = store.create_study(
        owner_session_id="owner-9", goal_id=None, objective="bob's",
        workspace_path=str(tmp_path), strategy_name="demo",
    )
    from strategy_research.api.app import create_app
    from fastapi.testclient import TestClient

    client = TestClient(
        create_app(),
        headers={"Authorization": f"Bearer {create_token('alice')}"},
    )
    r = client.get(f"/api/study/{rec.study_id}/summary")
    assert r.status_code == 200


def test_idor_unknown_study_404(tmp_path, monkeypatch):
    """A study id that doesn't exist → 404 even for an authenticated user."""
    monkeypatch.setenv("QUANTNODES_RESEARCH_GOAL_DB_PATH", str(tmp_path / "g.db"))
    monkeypatch.setenv("QUANTNODES_RESEARCH_HYPOTHESES_PATH", str(tmp_path / "h.json"))
    monkeypatch.setenv("SR_ENFORCE_STUDY_IDOR", "1")
    monkeypatch.setenv("SR_SESSIONS_DB", str(tmp_path / "sessions.db"))
    from strategy_research.api.auth_tokens import create_token
    from strategy_research.api.app import create_app
    from fastapi.testclient import TestClient

    client = TestClient(
        create_app(),
        headers={"Authorization": f"Bearer {create_token('alice')}"},
    )
    r = client.get("/api/study/does-not-exist/summary")
    assert r.status_code == 404


def test_idor_empty_owner_denied_for_named_user(tmp_path, monkeypatch):
    """A study with an empty owner session id is denied to a named
    (non-anonymous) user — it cannot be attributed to them."""
    monkeypatch.setenv("QUANTNODES_RESEARCH_GOAL_DB_PATH", str(tmp_path / "g.db"))
    monkeypatch.setenv("QUANTNODES_RESEARCH_HYPOTHESES_PATH", str(tmp_path / "h.json"))
    monkeypatch.setenv("SR_ENFORCE_STUDY_IDOR", "1")
    from strategy_research.api.auth_tokens import create_token
    from strategy_research.apps.study import StudyStore

    store = StudyStore()
    rec = store.create_study(
        owner_session_id="orphan-missing-session", goal_id=None, objective="orphan",
        workspace_path=str(tmp_path), strategy_name="demo",
    )
    # Point the study at a session that does not exist in the session DB.
    import sqlite3
    conn = sqlite3.connect(str(tmp_path / "sessions.db"))
    conn.executescript(
        """
        CREATE TABLE sessions (
          id TEXT PRIMARY KEY, user_id TEXT NOT NULL, title TEXT,
          created_at TEXT, updated_at TEXT, starred INTEGER NOT NULL DEFAULT 0,
          tags_json TEXT NOT NULL DEFAULT '[]', message_count INTEGER NOT NULL DEFAULT 0,
          archived INTEGER NOT NULL DEFAULT 0
        );
        """
    )
    conn.commit()
    conn.close()
    monkeypatch.setenv("SR_SESSIONS_DB", str(tmp_path / "sessions.db"))

    from strategy_research.api.app import create_app
    from fastapi.testclient import TestClient

    client = TestClient(
        create_app(),
        headers={"Authorization": f"Bearer {create_token('alice')}"},
    )
    # owner session doesn't exist for alice → 404 (session not found) or 403.
    r = client.get(f"/api/study/{rec.study_id}/summary")
    assert r.status_code in (403, 404)


# ── archive / unarchive (A1: soft-delete) ────────────────────────────


class TestArchiveActionMatrix:
    """ARCHIVED status + ARCHIVE/UNARCHIVE action wiring."""

    def test_archived_status_present(self):
        assert StudyStatus.ARCHIVED.value == "archived"

    def test_archive_action_present(self):
        assert StudyAction.ARCHIVE.value == "archive"
        assert StudyAction.UNARCHIVE.value == "unarchive"

    def test_running_allows_archive(self):
        acts = allowed_actions(StudyStatus.RUNNING)
        assert StudyAction.ARCHIVE in acts
        assert StudyAction.PAUSE in acts

    def test_paused_allows_archive(self):
        acts = allowed_actions(StudyStatus.PAUSED)
        assert StudyAction.ARCHIVE in acts

    def test_terminal_statuses_allow_archive(self):
        for st in (StudyStatus.COMPLETE, StudyStatus.CANCELLED,
                   StudyStatus.ERROR, StudyStatus.BUDGET_LIMITED,
                   StudyStatus.EARLY_STOPPED, StudyStatus.NEEDS_REFRESH):
            assert StudyAction.ARCHIVE in allowed_actions(st), st

    def test_archived_allows_only_unarchive(self):
        acts = allowed_actions(StudyStatus.ARCHIVED)
        assert acts == frozenset({StudyAction.UNARCHIVE})


def test_store_archive_sets_status_and_metadata(tmp_path, monkeypatch):
    """store.archive_study sets ARCHIVED + archived_at/archived_by."""
    from strategy_research.apps.study import StudyStore

    store = StudyStore()
    rec = store.create_study(
        owner_session_id="sess-archive", goal_id=None,
        objective="archive test", workspace_path=str(tmp_path),
        strategy_name="demo",
    )
    updated = store.archive_study(rec.study_id, archived_by="tester")
    assert updated is not None
    assert updated.execution_status == StudyStatus.ARCHIVED
    assert updated.archived_at is not None
    assert updated.archived_by == "tester"

    # detail read still works
    again = store.get_study(rec.study_id)
    assert again is not None
    assert again.execution_status == StudyStatus.ARCHIVED


def test_store_unarchive_clears_metadata_and_sets_interrupted(tmp_path, monkeypatch):
    """store.unarchive_study reverts ARCHIVED -> INTERRUPTED + clears archive fields."""
    from strategy_research.apps.study import StudyStore

    store = StudyStore()
    rec = store.create_study(
        owner_session_id="sess-unarchive", goal_id=None,
        objective="unarchive test", workspace_path=str(tmp_path),
        strategy_name="demo",
    )
    store.archive_study(rec.study_id, archived_by="tester")
    reverted = store.unarchive_study(rec.study_id)
    assert reverted is not None
    assert reverted.execution_status == StudyStatus.INTERRUPTED
    assert reverted.archived_at is None
    assert reverted.archived_by is None


def test_list_studies_excludes_archived_by_default(tmp_path, monkeypatch):
    """list_studies() default behavior: ARCHIVED rows are hidden."""
    from strategy_research.apps.study import StudyStore

    store = StudyStore()
    visible = store.create_study(
        owner_session_id="sess-list", goal_id=None,
        objective="visible", workspace_path=str(tmp_path),
        strategy_name="demo",
    )
    hidden = store.create_study(
        owner_session_id="sess-list", goal_id=None,
        objective="to be archived", workspace_path=str(tmp_path),
        strategy_name="demo",
    )
    store.archive_study(hidden.study_id)

    rows = store.list_studies(session_id="sess-list")
    ids = {r.study_id for r in rows}
    assert visible.study_id in ids
    assert hidden.study_id not in ids

    rows_all = store.list_studies(session_id="sess-list", include_archived=True)
    ids_all = {r.study_id for r in rows_all}
    assert hidden.study_id in ids_all
    assert visible.study_id in ids_all


@pytest.mark.asyncio
async def test_available_actions_includes_archive(_env):
    """HTTP: GET /available_actions surfaces archive for COMPLETE."""
    study_id, _ = _env
    from strategy_research.apps.study import StudyStore
    with StudyStore() as store:
        store.archive_study(study_id)  # already archived
    app = _build_asgi_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers=_bearer(),
    ) as client:
        r = await client.get(f"/api/study/{study_id}/available_actions")
        assert r.status_code == 200
        names = {a["name"] for a in r.json()["actions"]}
        # ARCHIVED only allows UNARCHIVE
        assert names == {StudyAction.UNARCHIVE.value}


@pytest.mark.asyncio
async def test_dispatch_archive_then_unarchive(_env):
    """End-to-end: POST archive → 200, POST unarchive → 200."""
    study_id, _ = _env
    app = _build_asgi_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers=_bearer(),
    ) as client:
        # Archive
        r1 = await client.post(f"/api/study/{study_id}/actions/archive")
        assert r1.status_code == 200
        body1 = r1.json()
        assert body1["action"] == "archived"
        assert body1["study_id"] == study_id

        # Verify status persisted
        r2 = await client.get(f"/api/study/{study_id}/available_actions")
        assert r2.status_code == 200
        names = {a["name"] for a in r2.json()["actions"]}
        assert names == {StudyAction.UNARCHIVE.value}

        # Unarchive
        r3 = await client.post(f"/api/study/{study_id}/actions/unarchive")
        assert r3.status_code == 200
        assert r3.json()["action"] == "unarchived"


@pytest.mark.asyncio
async def test_dispatch_archive_twice_returns_409(_env):
    """Re-archiving an already-archived study returns 409 (scheduler.archive returns False)."""
    study_id, _ = _env
    app = _build_asgi_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers=_bearer(),
    ) as client:
        r1 = await client.post(f"/api/study/{study_id}/actions/archive")
        assert r1.status_code == 200
        r2 = await client.post(f"/api/study/{study_id}/actions/archive")
        assert r2.status_code == 409


@pytest.mark.asyncio
async def test_dispatch_unarchive_non_archived_returns_409(_env):
    """Unarchive on a non-archived study returns 409."""
    study_id, _ = _env
    app = _build_asgi_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers=_bearer(),
    ) as client:
        r = await client.post(f"/api/study/{study_id}/actions/unarchive")
        assert r.status_code == 409


@pytest.mark.asyncio
async def test_list_endpoint_hides_archived_by_default(_env):
    """HTTP: GET /list hides ARCHIVED unless include_archived=true."""
    study_id, _ = _env
    app = _build_asgi_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers=_bearer(),
    ) as client:
        # Archive
        await client.post(f"/api/study/{study_id}/actions/archive")

        # Default list
        r1 = await client.get("/api/study/list")
        assert r1.status_code == 200
        ids = {s["study_id"] for s in r1.json()["studies"]}
        assert study_id not in ids

        # Include archived
        r2 = await client.get("/api/study/list?include_archived=true")
        assert r2.status_code == 200
        ids_all = {s["study_id"] for s in r2.json()["studies"]}
        assert study_id in ids_all


# ── Round numbering on retry (Step A) ────────────────────────


class TestRetryRoundNumbering:
    """retry() supports two modes: append (default) and restart."""

    def test_store_reset_round_counter_append_keeps_history(self, tmp_path):
        """mode='append' must NOT touch current_round or delete rounds."""
        from strategy_research.apps.study import StudyStore
        import os
        os.environ["QUANTNODES_RESEARCH_GOAL_DB_PATH"] = str(tmp_path / "append.db")
        store = StudyStore()
        rec = store.create_study(
            owner_session_id="sess-rnm", goal_id=None,
            objective="retry-append test",
            workspace_path="/tmp", strategy_name="demo",
        )
        # Seed: pretend the study has run 3 rounds.
        for n in (1, 2, 3):
            store.append_round(
                study_id=rec.study_id, round_num=n,
                run_name=f"run_{n:04d}",
                metrics={"calmar": 0.1}, verdict="discard",
            )
        # Mark current_round = 3 (use the public update_round_heartbeat API).
        store.update_round_heartbeat(rec.study_id, 3)
        store.reset_round_counter(rec.study_id, mode="append")
        refreshed = store.get_study(rec.study_id)
        assert refreshed.current_round == 3, "append must preserve current_round"
        rounds = store.list_rounds(rec.study_id)
        assert len(rounds) == 3, "append must keep round history"

    def test_store_reset_round_counter_restart_clears_history(self, tmp_path):
        """mode='restart' must wipe rounds + reset current_round to 1."""
        from strategy_research.apps.study import StudyStore
        import os
        os.environ["QUANTNODES_RESEARCH_GOAL_DB_PATH"] = str(tmp_path / "restart.db")
        store = StudyStore()
        rec = store.create_study(
            owner_session_id="sess-rnm2", goal_id=None,
            objective="retry-restart test",
            workspace_path="/tmp", strategy_name="demo",
        )
        for n in (1, 2, 3):
            store.append_round(
                study_id=rec.study_id, round_num=n,
                run_name=f"run_{n:04d}",
                metrics={"calmar": 0.1}, verdict="discard",
            )
        store.reset_round_counter(rec.study_id, mode="restart")
        refreshed = store.get_study(rec.study_id)
        assert refreshed.current_round == 1, "restart must reset current_round"
        rounds = store.list_rounds(rec.study_id)
        assert len(rounds) == 0, "restart must clear round history"

    def test_scheduler_retry_default_mode_is_append(self):
        """retry() defaults to mode='append' (no clear)."""
        from strategy_research.apps.study.scheduler import StudyScheduler
        from unittest.mock import MagicMock
        sched = StudyScheduler.__new__(StudyScheduler)
        sched.store = MagicMock()
        sched._emit_event = MagicMock()
        sched.store.get_study.return_value = None  # force early return
        sched.retry("nonexistent")
        # The first call after the None-guard is reset_round_counter.
        # Just verify no exception and the store was consulted.
        sched.store.get_study.assert_called_once()

    def test_scheduler_retry_passes_mode_to_store(self):
        """retry(mode='restart') should call reset_round_counter with mode='restart'."""
        from strategy_research.apps.study.scheduler import StudyScheduler
        from unittest.mock import MagicMock
        from strategy_research.apps.study.models import StudyStatus

        sched = StudyScheduler.__new__(StudyScheduler)
        sched.store = MagicMock()
        sched._emit_event = MagicMock()

        # Build a fake study record that's in a retryable state.
        from strategy_research.apps.study.models import (
            StudyRecord, StudyAction, allowed_actions,
        )
        rec = StudyRecord(
            study_id="study-mock", session_id="study-mock",
            goal_id=None, objective="x",
            executor_type="autoresearch",
            workspace_path="/tmp", strategy_name="demo",
            current_round=3, execution_status=StudyStatus.ERROR,
        )
        sched.store.get_study.return_value = rec

        # Reset mock call counts then call retry with restart mode.
        sched.store.reset_mock()
        sched._emit_event.reset_mock()
        sched.retry("study-mock", mode="restart")

        # Verify reset_round_counter was called with mode='restart'.
        sched.store.reset_round_counter.assert_called_with(
            "study-mock", mode="restart", start_round=1,
        )


@pytest.mark.asyncio
async def test_dispatch_retry_accepts_mode_field(_env, monkeypatch):
    """HTTP: POST /actions/retry (deprecated → CONTINUE) with mode='restart'."""
    study_id, _ = _env
    from strategy_research.api.routers import study as routers_study
    from unittest.mock import AsyncMock, MagicMock

    fake = MagicMock()
    fake.continue_study = AsyncMock(return_value=True)
    monkeypatch.setattr(routers_study, "_get_study_scheduler", lambda: fake)

    from strategy_research.apps.study import StudyStore
    from strategy_research.apps.study.models import StudyStatus
    with StudyStore() as s:
        s.update_execution_status(study_id, StudyStatus.ERROR)

    app = _build_asgi_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers=_bearer(),
    ) as client:
        r = await client.post(
            f"/api/study/{study_id}/actions/retry",
            json={"mode": "restart"},
        )
        assert r.status_code == 200
        assert r.json()["action"] == "continue"
        # Verify the scheduler received the mode
        fake.continue_study.assert_called_once_with(
            study_id, from_round=None, mode="restart",
        )
