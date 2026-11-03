"""Tests for the ops status dump endpoint.

``/api/study/_internal/dump`` requires ``X-Admin-Token`` and returns a
JSON snapshot of the scheduler + DB state for one session. It is the
in-process twin of grepping logs and the SQLite tables, intended for
the ops runbook and on-call triage.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from strategy_research.api.app import create_app
from strategy_research.apps.study.models import StudyStatus
from strategy_research.apps.study.store import StudyStore

# ── fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def admin_env(tmp_path: Path, monkeypatch):
    """Wire a fresh goals DB + admin token so the dump endpoint can run."""
    monkeypatch.setenv("SR_ADMIN_TOKEN", "test-admin-secret-dump")
    monkeypatch.setenv("QUANTNODES_RESEARCH_GOAL_DB_PATH", str(tmp_path / "goals.db"))
    monkeypatch.setenv("QUANTNODES_RESEARCH_HYPOTHESES_PATH", str(tmp_path / "hyp.json"))
    # admin/metrics path needs SR_SESSIONS_DB
    monkeypatch.setenv("SR_SESSIONS_DB", str(tmp_path / "sessions.db"))
    return tmp_path


def _seed_study(store: StudyStore, *, session_id: str, study_id: str,
                heartbeat: str | None = None, status: StudyStatus = StudyStatus.RUNNING) -> None:
    store.create_study(
        owner_session_id=session_id,
        goal_id=None,
        objective="beat SP500 by 5%",
        workspace_path="/tmp/ws",
        strategy_name="demo",
        executor_type="autoresearch",
        metric_targets=[],
    )
    # create_study auto-generates an id; force the desired one for
    # deterministic test queries.
    with store._conn:
        if heartbeat is not None:
            store._conn.execute(
                "UPDATE studies SET heartbeat = ?, execution_status = ? WHERE study_id = ?",
                (heartbeat, status.value, study_id),
            )
        else:
            store._conn.execute(
                "UPDATE studies SET execution_status = ? WHERE study_id = ?",
                (status.value, study_id),
            )


# ── dump endpoint ────────────────────────────────────────────────────


class TestStudyDumpEndpoint:
    def test_requires_admin_token(self, admin_env):
        app = create_app()
        # Temporarily disable admin to prove the gate
        os.environ.pop("SR_ADMIN_TOKEN", None)
        client = TestClient(app)
        r = client.get(
            "/api/study/_internal/dump",
            params={"session_id": "sess-1"},
        )
        assert r.status_code == 503  # admin disabled

    def test_rejects_missing_token(self, admin_env):
        client = TestClient(create_app())
        r = client.get(
            "/api/study/_internal/dump",
            params={"session_id": "sess-1"},
        )
        assert r.status_code == 401

    def test_dump_empty_session(self, admin_env):
        client = TestClient(create_app(), headers={"X-Admin-Token": "test-admin-secret-dump"})
        r = client.get(
            "/api/study/_internal/dump",
            params={"session_id": "sess-1"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert body["session_id"] == "sess-1"
        assert body["studies"] == []
        # All event types present with zero counts once the store is wired.
        from strategy_research.foundation.observability.hanging_events import _ALL_EVENT_TYPES
        assert set(body["hanging_signals_in_window"]) == _ALL_EVENT_TYPES
        assert all(v == 0 for v in body["hanging_signals_in_window"].values())
        # Concurrency block is always present
        assert "semaphore_limit" in body["concurrency"]
        assert "active_executor_ids" in body["concurrency"]
        # Watchdog block
        assert "alive" in body["watchdog"]
        assert body["watchdog"]["heartbeat_timeout_s"] >= 60

    def test_dump_with_studies(self, admin_env):
        with StudyStore() as store:
            # Seed two studies for sess-A, one for sess-other
            for sid in ("sess-A", "sess-A", "sess-other"):
                store.create_study(
                    owner_session_id=sid,
                    goal_id=None,
                    objective=f"obj-{sid}",
                    workspace_path="/tmp/ws",
                    strategy_name="demo",
                    executor_type="autoresearch",
                    metric_targets=[],
                )
                with store._conn:
                    store._conn.execute(
                        "UPDATE studies SET execution_status = ? WHERE owner_session_id = ? "
                        "AND study_id = (SELECT study_id FROM studies WHERE "
                        "owner_session_id = ? ORDER BY created_at DESC, rowid DESC LIMIT 1)",
                        (StudyStatus.RUNNING.value, sid, sid),
                    )

        client = TestClient(create_app(), headers={"X-Admin-Token": "test-admin-secret-dump"})
        r = client.get(
            "/api/study/_internal/dump",
            params={"session_id": "sess-A"},
        )
        assert r.status_code == 200
        body = r.json()
        assert len(body["studies"]) == 2
        for s in body["studies"]:
            assert s["execution_status"] == StudyStatus.RUNNING.value
            assert "hanging_protection" in s
            assert "is_active_in_scheduler" in s["hanging_protection"]
            assert "heartbeat_stale" in s["hanging_protection"]
            assert "watchdog_will_interrupt" in s["hanging_protection"]

    def test_dump_focuses_study_id(self, admin_env):
        with StudyStore() as store:
            sids = []
            for _ in range(3):
                store.create_study(
                    owner_session_id="sess-A",
                    goal_id=None,
                    objective="x",
                    workspace_path="/tmp/ws",
                    strategy_name="demo",
                    executor_type="autoresearch",
                    metric_targets=[],
                )
                with store._conn:
                    row = store._conn.execute(
                        "SELECT study_id FROM studies WHERE owner_session_id='sess-A' "
                        "ORDER BY created_at DESC, rowid DESC LIMIT 1"
                    ).fetchone()
                    sids.append(row[0])
        target = sids[1]

        client = TestClient(create_app(), headers={"X-Admin-Token": "test-admin-secret-dump"})
        r = client.get(
            "/api/study/_internal/dump",
            params={"session_id": "sess-A", "study_id": target},
        )
        assert r.status_code == 200
        body = r.json()
        assert len(body["studies"]) == 1
        assert body["studies"][0]["study_id"] == target

    def test_dump_heartbeat_age_recent(self, admin_env):
        with StudyStore() as store:
            store.create_study(
                owner_session_id="sess-A",
                goal_id=None,
                objective="x",
                workspace_path="/tmp/ws",
                strategy_name="demo",
                executor_type="autoresearch",
                metric_targets=[],
            )
            recent = datetime.now(timezone.utc).isoformat()
            with store._conn:
                store._conn.execute(
                    "UPDATE studies SET heartbeat = ? WHERE owner_session_id='sess-A'",
                    (recent,),
                )

        client = TestClient(create_app(), headers={"X-Admin-Token": "test-admin-secret-dump"})
        r = client.get(
            "/api/study/_internal/dump",
            params={"session_id": "sess-A"},
        )
        s = r.json()["studies"][0]
        assert s["heartbeat"] == recent
        assert s["heartbeat_age_s"] is not None
        # 30s tolerance for test execution
        assert 0 <= s["heartbeat_age_s"] < 30
        assert s["hanging_protection"]["heartbeat_stale"] is False


# ── scheduler helpers (unit) ─────────────────────────────────────────


class TestSchedulerDumpHelpers:
    def test_dump_concurrency_no_active(self, admin_env):
        from strategy_research.apps.study.scheduler import StudyScheduler, StudyStore
        with StudyStore() as store:  # close the connection — leaks cause lock flakes
            sched = StudyScheduler(store)
        snap = sched.dump_concurrency()
        assert snap["queued_count"] == 0
        assert snap["active_count"] == 0
        assert snap["active_executor_ids"] == []
        assert snap["queued_study_ids"] == []
        assert snap["semaphore_limit"] >= 1

    def test_dump_watchdog_alive_is_false_before_ensure(self, admin_env):
        from strategy_research.apps.study.scheduler import StudyScheduler, StudyStore
        with StudyStore() as store:  # close the connection — leaks cause lock flakes
            sched = StudyScheduler(store)
        snap = sched.dump_watchdog()
        # No submit() happened, so the watchdog task was never created.
        assert snap["alive"] is False
        assert snap["interval_s"] >= 10
        assert snap["heartbeat_timeout_s"] >= 60

    def test_dump_session_queues_empty(self, admin_env):
        from strategy_research.apps.study.scheduler import StudyScheduler, StudyStore
        with StudyStore() as store:  # close the connection — leaks cause lock flakes
            sched = StudyScheduler(store)
        assert sched.dump_session_queues() == {}
