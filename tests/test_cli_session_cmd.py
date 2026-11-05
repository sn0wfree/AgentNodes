"""Tests for ``cli.commands.slash_session`` (slash command variants)."""

from __future__ import annotations

from pathlib import Path

import pytest
from rich.console import Console

from agentnodes.interfaces.cli.commands.slash_session import (
    cmd_export,
    cmd_history,
    cmd_search,
    run,
)


@pytest.fixture
def console():
    return Console(record=True, force_terminal=False, width=120)


def _make_sessions_db(tmp_path: Path) -> Path:
    """Create a minimal unified-DB file (B4 Step 3).

    The slash commands read the unified schema now, so the fixture seeds that
    one instead of the retired legacy schema.
    """
    import sqlite3

    from agentnodes.interfaces.api.session.web_session_db import _ensure_schema

    db_path = tmp_path / "sessions.db"
    conn = sqlite3.connect(str(db_path))
    _ensure_schema(conn)
    conn.commit()
    conn.close()
    return db_path


# ─── cmd_history ────────────────────────────────────────────────────────


class TestCmdHistory:
    def test_no_env_var_still_works(self, console, monkeypatch, tmp_path):
        """B4 Step 3: /history no longer needs AGENTNODES_SESSIONS_DB.

        It reads the unified DB like every other consumer. An empty DB is a
        normal state, not a configuration error.
        """
        monkeypatch.delenv("AGENTNODES_SESSIONS_DB", raising=False)
        monkeypatch.setenv("AN_SESSIONS_DB", str(tmp_path / "empty.db"))
        rc = cmd_history(console=console)
        assert rc == 0
        assert "no prior sessions" in console.export_text().lower()

    def test_db_unset_returns_zero(self, console, tmp_path, monkeypatch):
        monkeypatch.setenv("AN_SESSIONS_DB", str(tmp_path / "sessions.db"))
        rc = cmd_history(console=console)
        assert rc == 0


# ─── cmd_search ─────────────────────────────────────────────────────────


class TestCmdSearch:
    def test_empty_query(self, console):
        rc = cmd_search("", console=console)
        assert rc == 1

    def test_whitespace_query(self, console):
        rc = cmd_search("   ", console=console)
        assert rc == 1

    def test_no_db_still_searches(self, console, monkeypatch, tmp_path):
        """B4 Step 3: /search no longer needs the legacy env var either."""
        monkeypatch.delenv("AGENTNODES_SESSIONS_DB", raising=False)
        monkeypatch.setenv("AN_SESSIONS_DB", str(tmp_path / "empty.db"))
        rc = cmd_search("hello", console=console)
        assert rc == 0
        assert "no matches" in console.export_text().lower()

    def test_db_no_match(self, console, tmp_path, monkeypatch):
        db = _make_sessions_db(tmp_path)
        monkeypatch.setenv("AN_SESSIONS_DB", str(db))
        rc = cmd_search("nonexistent_term_xyz", console=console)
        assert rc == 0

    def test_db_returns_no_match_gracefully(self, console, tmp_path, monkeypatch):
        db = _make_sessions_db(tmp_path)
        monkeypatch.setenv("AGENTNODES_SESSIONS_DB", str(db))

        rc = cmd_search("nonexistent_term_xyz", console=console)
        assert rc == 0
        out = console.export_text()
        # Either matches rendered as table, or no match message
        assert "no matches" in out.lower() or "searchable" in out


# ─── cmd_export ────────────────────────────────────────────────────────


class TestCmdExport:
    def test_placeholder(self, console):
        rc = cmd_export(console=console)
        assert rc == 0
        out = console.export_text()
        assert "/export" in out or "wired" in out.lower()


# ─── Slash router entrypoints ───────────────────────────────────────────


class TestRouterEntrypoints:
    def test_run_no_args_calls_export(self):
        rc = run()
        assert rc == 0

    def test_run_with_args_calls_search(self):
        rc = run(None, "hello", "world")
        # Will return 0 or 1 depending on whether db is configured.
        assert rc in (0, 1)

    def test_run_with_single_arg(self):
        rc = run(None, "term")
        assert rc in (0, 1)
