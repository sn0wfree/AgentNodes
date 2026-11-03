"""Tests for HangingEventsStore + the hanging-protection event hooks.

Covers:
- event write/read (record → count_since → report)
- report aggregation shape (by_type / by_study / recent)
- schema on a fresh DB
- the ``hanging_signals_in_window`` field now populated in the dump
  endpoint once events are recorded
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from strategy_research.interfaces.api.app import create_app
from strategy_research.foundation.observability.hanging_events import (
    EVENT_CHAT_STALL,
    EVENT_CIRCUIT_OPEN,
    EVENT_LOG_STALL,
    EVENT_NO_PROGRESS,
    EVENT_WALLCLOCK,
    EVENT_WATCHDOG,
    HangingEventsStore,
    record_event,
)


@pytest.fixture
def store(tmp_path: Path) -> HangingEventsStore:
    return HangingEventsStore(tmp_path / "goals.db")


# ── store CRUD ──────────────────────────────────────────────────────


class TestHangingEventsStore:
    def test_record_and_count(self, store):
        store.record(EVENT_WALLCLOCK, session_id="s1", detail="d1")
        store.record(EVENT_WALLCLOCK, session_id="s1", detail="d2")
        store.record(EVENT_WATCHDOG, session_id="s1", detail="d3")
        counts = store.count_since(session_id="s1", hours=24)
        assert counts[EVENT_WALLCLOCK] == 2
        assert counts[EVENT_WATCHDOG] == 1
        assert counts[EVENT_NO_PROGRESS] == 0
        assert counts[EVENT_CHAT_STALL] == 0

    def test_unknown_type_ignored(self, store):
        store.record("bogus_type", session_id="s1")
        assert store.count_since() == {t: 0 for t in (
            EVENT_WALLCLOCK, EVENT_LOG_STALL, EVENT_NO_PROGRESS,
            EVENT_CIRCUIT_OPEN, EVENT_WATCHDOG, EVENT_CHAT_STALL,
        )}

    def test_count_since_respects_window(self, store):
        store.record(EVENT_WALLCLOCK, session_id="s1")
        # Force an old event by patching created_at
        store._conn.execute(
            "UPDATE hanging_events SET created_at = ? WHERE event_type = ?",
            (time.time() - 48 * 3600, EVENT_WALLCLOCK),
        )
        store._conn.commit()
        assert store.count_since(session_id="s1", hours=24)[EVENT_WALLCLOCK] == 0
        assert store.count_since(session_id="s1", hours=72)[EVENT_WALLCLOCK] == 1

    def test_report_shape(self, store):
        store.record(EVENT_NO_PROGRESS, study_id="st-1", session_id="s1")
        store.record(EVENT_NO_PROGRESS, study_id="st-1", session_id="s1")
        store.record(EVENT_CIRCUIT_OPEN, study_id="st-2", session_id="s1",
                     detail="tool 'x' failed 3 consecutive times")
        rep = store.report(hours=24)
        assert rep["total_events"] == 3
        assert rep["by_type"][EVENT_NO_PROGRESS] == 2
        assert rep["by_type"][EVENT_CIRCUIT_OPEN] == 1
        assert rep["by_study"] == [
            {"study_id": "st-1", "count": 2},
            {"study_id": "st-2", "count": 1},
        ]
        assert len(rep["recent"]) == 3
        r0 = rep["recent"][0]
        assert r0["event_type"] in (EVENT_NO_PROGRESS, EVENT_CIRCUIT_OPEN)
        assert "created_at_iso" in r0

    def test_clear_all(self, store):
        store.record(EVENT_WATCHDOG)
        assert store.clear() >= 1
        assert store.count_since() == {t: 0 for t in (
            EVENT_WALLCLOCK, EVENT_LOG_STALL, EVENT_NO_PROGRESS,
            EVENT_CIRCUIT_OPEN, EVENT_WATCHDOG, EVENT_CHAT_STALL,
        )}

    def test_clear_older_than(self, store):
        store.record(EVENT_WATCHDOG)
        store._conn.execute(
            "UPDATE hanging_events SET created_at = ?",
            (time.time() - 48 * 3600,),
        )
        store._conn.commit()
        store.record(EVENT_WALLCLOCK)
        assert store.clear(hours=24) == 1
        assert store.count_since(hours=24)[EVENT_WALLCLOCK] == 1


# ── module-level hook ───────────────────────────────────────────────


class TestRecordEventHook:
    def test_record_event_no_throw_on_bad_db(self, tmp_path, monkeypatch):
        monkeypatch.setenv("QUANTNODES_RESEARCH_GOAL_DB_PATH", "/nonexistent-dir/x/goals.db")
        # Should not raise even though the path parent doesn't exist
        record_event(EVENT_WALLCLOCK, session_id="s1")
        # (parent dir is auto-created by the store; nothing to assert besides no raise)

    def test_record_event_writes(self, tmp_path):
        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setenv("QUANTNODES_RESEARCH_GOAL_DB_PATH", str(tmp_path / "goals.db"))
        record_event(EVENT_LOG_STALL, study_id="st-9", session_id="s9",
                     detail="bg task stalled")
        with HangingEventsStore() as s:
            assert s.count_since(session_id="s9")[EVENT_LOG_STALL] == 1
        monkeypatch.undo()


# ── CLI ─────────────────────────────────────────────────────────────


class TestHangsCli:
    def test_dispatch_prints_report(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("QUANTNODES_RESEARCH_GOAL_DB_PATH", str(tmp_path / "goals.db"))
        record_event(EVENT_WALLCLOCK, session_id="s1", detail="model=x")
        record_event(EVENT_NO_PROGRESS, session_id="s1")

        import argparse
        import importlib

        cli_mod = importlib.import_module("strategy_research.cli")
        args = argparse.Namespace(hours=24, limit=50)
        rc = cli_mod._dispatch_hangs(args, {})
        out = capsys.readouterr().out
        assert rc == 0
        assert "卡死防护事件报告" in out
        assert "wallclock_timeout" in out
        assert "no_progress" in out

    def test_dispatch_empty(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("QUANTNODES_RESEARCH_GOAL_DB_PATH", str(tmp_path / "goals.db"))
        import argparse
        import importlib

        cli_mod = importlib.import_module("strategy_research.cli")
        args = argparse.Namespace(hours=24, limit=50)
        rc = cli_mod._dispatch_hangs(args, {})
        out = capsys.readouterr().out
        assert rc == 0
        assert "共 0 条" in out


# ── dump endpoint integration ───────────────────────────────────────


class TestDumpHangingSignals:
    def test_dump_shows_recorded_events(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SR_ADMIN_TOKEN", "dump-admin-token")
        monkeypatch.setenv("QUANTNODES_RESEARCH_GOAL_DB_PATH", str(tmp_path / "goals.db"))
        monkeypatch.setenv("QUANTNODES_RESEARCH_HYPOTHESES_PATH", str(tmp_path / "hyp.json"))
        monkeypatch.setenv("SR_SESSIONS_DB", str(tmp_path / "sessions.db"))

        record_event(EVENT_WALLCLOCK, session_id="sess-1", detail="model=x")
        record_event(EVENT_NO_PROGRESS, session_id="sess-1")

        client = TestClient(
            create_app(),
            headers={"X-Admin-Token": "dump-admin-token"},
        )
        r = client.get("/api/study/_internal/dump", params={"session_id": "sess-1"})
        assert r.status_code == 200
        body = r.json()
        h = body["hanging_signals_in_window"]
        assert h[EVENT_WALLCLOCK] == 1
        assert h[EVENT_NO_PROGRESS] == 1
        assert h[EVENT_WATCHDOG] == 0
        assert h[EVENT_CHAT_STALL] == 0


class TestListRecent:
    def test_list_recent_filters_by_study(self, store):
        store.record(EVENT_WALLCLOCK, study_id="st-a", session_id="s1", detail="a1")
        store.record(EVENT_WATCHDOG, study_id="st-b", session_id="s2", detail="b1")
        store.record(EVENT_LOG_STALL, study_id="st-a", session_id="s1", detail="a2")
        rows = store.list_recent(study_id="st-a", hours=24)
        assert len(rows) == 2
        assert all(r["study_id"] == "st-a" for r in rows)
        # newest first (a2 recorded last)
        assert rows[0]["event_type"] == EVENT_LOG_STALL
        assert rows[0]["created_at_iso"]

    def test_list_recent_all_without_filter(self, store):
        store.record(EVENT_WALLCLOCK, study_id="st-a")
        store.record(EVENT_WATCHDOG, study_id="st-b")
        rows = store.list_recent(hours=24)
        assert len(rows) == 2


class TestStudyHangingEventsApi:
    def test_endpoint_returns_by_type_and_recent(self, tmp_path, monkeypatch):
        monkeypatch.setenv("QUANTNODES_RESEARCH_GOAL_DB_PATH", str(tmp_path / "goals.db"))
        monkeypatch.setenv("QUANTNODES_RESEARCH_HYPOTHESES_PATH", str(tmp_path / "hyp.json"))
        sessions_db = tmp_path / "sessions.db"
        monkeypatch.setenv("SR_SESSIONS_DB", str(sessions_db))
        import sqlite3
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
            "VALUES ('sess-h', 'tester', '2026-08-01T10:00:00', '2026-08-01T10:00:00')"
        )
        conn.commit()
        conn.close()
        from strategy_research.interfaces.api.auth_tokens import create_token
        from strategy_research.apps.study import StudyStore

        store = StudyStore()
        rec = store.create_study(
            owner_session_id="sess-h",
            goal_id=None,
            objective="x",
            workspace_path=str(tmp_path),
            strategy_name="demo",
        )
        record_event(EVENT_WALLCLOCK, study_id=rec.study_id, session_id=rec.session_id)
        record_event(EVENT_WATCHDOG, study_id=rec.study_id, session_id=rec.session_id)
        record_event(EVENT_WATCHDOG, study_id="other-study", session_id="s-other")

        client = TestClient(
            create_app(),
            headers={"Authorization": f"Bearer {create_token('tester')}"},
        )
        r = client.get(f"/api/study/{rec.study_id}/hanging_events")
        assert r.status_code == 200
        body = r.json()
        assert body["by_type"][EVENT_WALLCLOCK] == 1
        assert body["by_type"][EVENT_WATCHDOG] == 1
        assert body["by_type"][EVENT_NO_PROGRESS] == 0
        assert len(body["recent"]) == 2
        assert all(e["study_id"] == rec.study_id for e in body["recent"])
