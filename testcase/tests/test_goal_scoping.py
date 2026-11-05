"""Goal + hypothesis user-scoping tests (isolation D1).

Verifies that goal and hypothesis endpoints are scoped to the owning
user via their sessions, and that the goals.user_id backfill migration
assigns ownership correctly for legacy rows.
"""

from __future__ import annotations

import sqlite3

import pytest


def _login(client, username: str, password: str) -> dict:
    resp = client.post("/api/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _auth(body: dict) -> dict:
    return {"Authorization": f"Bearer {body['access_token']}"}


def _seed_session(db_path, session_id: str, user_id: str) -> None:
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "CREATE TABLE IF NOT EXISTS sessions ("
        " id TEXT PRIMARY KEY, user_id TEXT NOT NULL DEFAULT 'anonymous',"
        " title TEXT, created_at REAL, updated_at REAL, starred INTEGER DEFAULT 0,"
        " tags_json TEXT DEFAULT '[]', message_count INTEGER DEFAULT 0,"
        " archived INTEGER DEFAULT 0)"
    )
    now = 1.0
    conn.execute(
        "INSERT OR IGNORE INTO sessions "
        "(id, user_id, title, created_at, updated_at, starred, tags_json, message_count, archived) "
        "VALUES (?, ?, '', ?, ?, 0, '[]', 0, 0)",
        (session_id, user_id, now, now),
    )
    conn.commit()
    conn.close()


def _make_user(client, admin: dict, username: str, password: str = "pw") -> dict:
    resp = client.post("/api/admin/users", headers=_auth(admin),
                       json={"username": username, "password": password})
    assert resp.status_code == 201, resp.text
    return resp.json()


@pytest.fixture
def setup(tmp_path, monkeypatch):
    """Isolated app with tmp session DB + user DB + goal/hypothesis paths."""
    import strategy_research.interfaces.api.user_db as user_db
    from strategy_research.interfaces.api.routers import auth as auth_router
    from strategy_research.interfaces.api.app import create_app

    monkeypatch.setattr(auth_router, "_DEFAULT_ITERATIONS", 1000)
    monkeypatch.setattr(user_db, "hash_password",
                        lambda pw: auth_router._hash_password(pw))
    real_get = user_db.get_user_db
    monkeypatch.setattr(user_db, "get_user_db", lambda *a, **k: real_get(tmp_path))
    monkeypatch.setattr(auth_router, "_user_db", None)

    session_db = tmp_path / "session.db"
    goal_db = tmp_path / "goals.db"
    hyp_path = tmp_path / "hypotheses.db"
    monkeypatch.setenv("SR_SESSIONS_DB", str(session_db))
    monkeypatch.setenv("SR_GOAL_DB_PATH", str(goal_db))
    monkeypatch.setenv("SR_HYPOTHESES_PATH", str(hyp_path))
    monkeypatch.setenv("QUANTNODES_RESEARCH_HYPOTHESES_DB_PATH", str(hyp_path))

    from fastapi.testclient import TestClient
    client = TestClient(create_app())

    admin = _login(client, "admin", "admin")
    alice = _make_user(client, admin, "alice")
    bob = _make_user(client, admin, "bob")

    _seed_session(session_db, "sess-alice", alice["id"])
    _seed_session(session_db, "sess-bob", bob["id"])

    return {
        "client": client,
        "admin": _auth(admin),
        "alice": _auth(_login(client, "alice", "pw")),
        "bob": _auth(_login(client, "bob", "pw")),
        "alice_id": alice["id"],
        "bob_id": bob["id"],
        "session_db": session_db,
        "goal_db": goal_db,
    }


# ── Goal isolation ──────────────────────────────────────────────


def test_goal_start_other_users_session_403(setup) -> None:
    client = setup["client"]
    resp = client.post("/api/goal/start", headers=setup["alice"],
                       json={"session_id": "sess-bob", "objective": "prying"})
    assert resp.status_code == 403


def test_goal_status_other_users_session_403(setup) -> None:
    client = setup["client"]
    resp = client.get("/api/goal/status?session_id=sess-bob", headers=setup["alice"])
    assert resp.status_code == 403


def test_goal_own_session_ok(setup) -> None:
    client = setup["client"]
    resp = client.post("/api/goal/start", headers=setup["alice"],
                       json={"session_id": "sess-alice", "objective": "mine"})
    assert resp.status_code == 200
    resp = client.get("/api/goal/status?session_id=sess-alice", headers=setup["alice"])
    assert resp.status_code == 200
    assert resp.json()["objective"] == "mine"


def test_goal_list_scoped_to_owner(setup) -> None:
    client = setup["client"]
    client.post("/api/goal/start", headers=setup["alice"],
                json={"session_id": "sess-alice", "objective": "alice goal"})
    client.post("/api/goal/start", headers=setup["bob"],
                json={"session_id": "sess-bob", "objective": "bob goal"})

    resp = client.get("/api/goal/list", headers=setup["alice"])
    assert resp.status_code == 200
    goals = resp.json()["goals"]
    assert all(g["session_id"] == "sess-alice" for g in goals)
    assert any(g["objective"] == "alice goal" for g in goals)


# ── Goal user_id backfill migration ─────────────────────────────


def test_goal_user_id_backfill(setup) -> None:
    """Legacy goals (schema without user_id) get ownership on migration init."""
    import sqlite3

    # Build a legacy goals table WITHOUT the user_id column, with a row.
    conn = sqlite3.connect(str(setup["goal_db"]))
    conn.execute(
        "CREATE TABLE goals ("
        " goal_id TEXT PRIMARY KEY, session_id TEXT NOT NULL, status TEXT NOT NULL,"
        " objective TEXT NOT NULL, ui_summary TEXT NOT NULL, source TEXT NOT NULL,"
        " protocol TEXT NOT NULL, risk_tier TEXT NOT NULL, token_budget INTEGER,"
        " tokens_used INTEGER NOT NULL DEFAULT 0, turn_budget INTEGER,"
        " turns_used INTEGER NOT NULL DEFAULT 0, time_budget_seconds INTEGER,"
        " time_used_seconds INTEGER NOT NULL DEFAULT 0, budget_wrapup_sent INTEGER NOT NULL DEFAULT 0,"
        " created_at TEXT NOT NULL, updated_at TEXT NOT NULL, completed_at TEXT, recap TEXT,"
        " progress_percent REAL NOT NULL DEFAULT 0.0, parent_goal_id TEXT, workflow_id TEXT)"
    )
    conn.execute(
        "INSERT INTO goals (goal_id, session_id, status, objective, ui_summary, source, protocol,"
        " risk_tier, created_at, updated_at, progress_percent) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("goal-legacy", "sess-alice", "active", "legacy objective", "", "api", "thesis_review",
         "research_general", "2024-01-01T00:00:00Z", "2024-01-01T00:00:00Z", 0.0),
    )
    conn.commit()
    conn.close()

    # Opening GoalStore runs _migrate_user_id: adds column + backfills ownership.
    from strategy_research.apps.goal import GoalStore

    with GoalStore(db_path=str(setup["goal_db"])) as store:
        goal = store.get_goal("goal-legacy")
        assert goal is not None
        assert goal.user_id == setup["alice_id"]


# ── Hypothesis isolation ────────────────────────────────────────


def test_hypothesis_list_scoped_to_owner(setup) -> None:
    """Global /list only shows hypotheses whose goal the user owns."""
    client = setup["client"]
    # alice creates a goal + a linked hypothesis
    start = client.post("/api/goal/start", headers=setup["alice"],
                        json={"session_id": "sess-alice", "objective": "alice study"})
    goal_id = start.json()["goal_id"]
    from strategy_research.apps.hypothesis import HypothesisRegistry
    from strategy_research.apps.hypothesis.store import default_db_path

    reg = HypothesisRegistry(path=default_db_path())
    alice_hyp = reg.create(title="alice hyp", thesis="t")
    reg.link_goal(alice_hyp.hypothesis_id, goal_id)

    # bob creates an unrelated hypothesis (no goal -> always visible)
    bob_hyp = reg.create(title="bob hyp", thesis="t")

    resp = client.get("/api/hypothesis/list", headers=setup["alice"])
    assert resp.status_code == 200
    titles = {h["title"] for h in resp.json()["hypotheses"]}
    assert "alice hyp" in titles
    assert "bob hyp" in titles  # orphan hypotheses stay visible

    resp = client.get("/api/hypothesis/list", headers=setup["bob"])
    assert resp.status_code == 200
    # bob cannot see alice's goal-linked hypothesis
    titles = {h["title"] for h in resp.json()["hypotheses"]}
    assert "alice hyp" not in titles
    assert "bob hyp" in titles