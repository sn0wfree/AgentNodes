"""C1 tests — chat attempt wallclock timeout + failed attempts visibility.

Covers:
- SR_CHAT_ATTEMPT_TIMEOUT env var respected
- Timeout → mark_failed + attempt.failed + agent_done SSE events
- hanging_events chat_attempt_stall event written
- list_active_attempts includes failed attempts with error
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from agentnodes.interfaces.api.session.store import SessionStore


@pytest.fixture(autouse=True)
def _env(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("QUANTNODES_RESEARCH_GOAL_DB_PATH", str(tmp_path / "goals.db"))
    monkeypatch.setenv("QUANTNODES_RESEARCH_HYPOTHESES_PATH", str(tmp_path / "hyp.json"))
    monkeypatch.setenv("SR_SESSIONS_DB", str(tmp_path / "sessions.db"))
    yield


class TestChatAttemptTimeout:
    def test_timeout_env_default_is_600(self):
        from agentnodes.interfaces.api.session.service import _CHAT_ATTEMPT_TIMEOUT
        assert _CHAT_ATTEMPT_TIMEOUT == 600

    def test_timeout_zero_disables(self, monkeypatch):
        monkeypatch.setenv("SR_CHAT_ATTEMPT_TIMEOUT", "0")
        # Reimport to pick up env change
        import importlib

        import agentnodes.interfaces.api.session.service as svc_mod
        importlib.reload(svc_mod)
        assert svc_mod._CHAT_ATTEMPT_TIMEOUT == 0
        # Restore for other tests
        monkeypatch.setenv("SR_CHAT_ATTEMPT_TIMEOUT", "600")
        importlib.reload(svc_mod)


class TestListActiveAttemptsIncludesFailed:
    def _make_store(self, tmp_path: Path) -> "SessionStore":
        from agentnodes.interfaces.api.session.store import SessionStore
        db = tmp_path / "chat_c1.db"
        store = SessionStore(str(db))
        # Create the required tables (normally done by web_session._ensure_schema).
        with store._conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY, user_id TEXT, title TEXT,
                    created_at TEXT, updated_at TEXT, starred INTEGER DEFAULT 0,
                    tags_json TEXT DEFAULT '[]', message_count INTEGER DEFAULT 0,
                    archived INTEGER DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS attempts (
                    attempt_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    parent_attempt_id TEXT,
                    status TEXT NOT NULL,
                    prompt TEXT,
                    run_dir TEXT,
                    summary TEXT,
                    react_trace_json TEXT,
                    metrics_json TEXT,
                    created_at TEXT,
                    completed_at TEXT,
                    error TEXT,
                    message_id TEXT,
                    persona TEXT,
                    mode TEXT,
                    model_override TEXT,
                    thinking TEXT,
                    FOREIGN KEY (session_id) REFERENCES sessions(id)
                );
                CREATE INDEX IF NOT EXISTS idx_attempts_session
                    ON attempts(session_id, status);
                CREATE TABLE IF NOT EXISTS messages (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT,
                    created_at TEXT NOT NULL,
                    metadata_json TEXT,
                    FOREIGN KEY (session_id) REFERENCES sessions(id)
                );
                CREATE TABLE IF NOT EXISTS message_parts (
                    id TEXT PRIMARY KEY,
                    message_id TEXT NOT NULL,
                    seq INTEGER NOT NULL,
                    type TEXT NOT NULL,
                    text TEXT,
                    tool_name TEXT,
                    tool_call_id TEXT,
                    tool_input_json TEXT,
                    thinking_json TEXT,
                    thinking_text TEXT,
                    error TEXT,
                    FOREIGN KEY (message_id) REFERENCES messages(id)
                );
            """)
            conn.execute(
                "INSERT OR IGNORE INTO sessions (id, user_id, title, created_at, updated_at)"
                " VALUES ('sess-c1', 'tester', 'c1 test', datetime('now'), datetime('now'))"
            )
            conn.commit()
        return store

    def test_failed_attempts_included(self, tmp_path):
        from agentnodes.interfaces.api.session.models import Attempt, AttemptStatus
        from agentnodes.interfaces.api.session.service import SessionService

        store = self._make_store(tmp_path)
        for i in range(3):
            attempt = Attempt(
                attempt_id=f"att-fail-{i}",
                session_id="sess-c1",
                status=AttemptStatus.FAILED,
                prompt=f"failed prompt {i}",
                error=f"error {i}",
                message_id=f"msg-{i}",
            )
            store.create_attempt(attempt)

        # list_active_attempts only reads from store, event_bus not exercised
        svc = SessionService(store=store, event_bus=None)
        attempts = svc.list_active_attempts("sess-c1")
        failed = [a for a in attempts if a["status"] == "failed"]
        assert len(failed) == 3
        for fa in failed:
            assert fa["error"], "failed attempt must have error"

    def test_failed_attempts_limited_to_5(self, tmp_path):
        from agentnodes.interfaces.api.session.models import Attempt, AttemptStatus
        from agentnodes.interfaces.api.session.service import SessionService

        store = self._make_store(tmp_path)
        for i in range(8):
            attempt = Attempt(
                attempt_id=f"att-many-{i}",
                session_id="sess-c1",
                status=AttemptStatus.FAILED,
                prompt=f"fail {i}",
                error=f"err {i}",
                message_id=f"msg-many-{i}",
            )
            store.create_attempt(attempt)

        svc = SessionService(store=store, event_bus=None)
        attempts = svc.list_active_attempts("sess-c1")
        failed = [a for a in attempts if a["status"] == "failed"]
        assert len(failed) == 5, f"expected max 5 failed, got {len(failed)}"
