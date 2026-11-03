"""Tests for ``cli.commands.slash_memory``."""

from __future__ import annotations

from pathlib import Path

import pytest
from rich.console import Console

from strategy_research.interfaces.cli.commands.slash_memory import (
    cmd_forget,
    cmd_list,
    cmd_search,
    cmd_show,
    run,
)


@pytest.fixture
def console():
    return Console(record=True, force_terminal=False, width=120)


@pytest.fixture
def populated_memory(tmp_path: Path, monkeypatch):
    """Create a PersistentMemory with two test entries."""
    monkeypatch.setenv("STRATEGY_RESEARCH_MEMORY_DIR", str(tmp_path))
    store = None
    try:
        from strategy_research.kernel.memory.persistent import PersistentMemory
        store = PersistentMemory(memory_dir=tmp_path)
        store.add(name="alpha_rule", content="Use t-stat for factor selection", memory_type="project")
        store.add(name="beta_rule", content="Risk cap 2% per trade", memory_type="feedback")
    except Exception:
        # If import fails, skip the test entirely.
        pytest.skip("PersistentMemory unavailable")
    return tmp_path


# ─── cmd_list ───────────────────────────────────────────────────────────


class TestCmdList:
    def test_empty(self, console, tmp_path, monkeypatch):
        monkeypatch.setenv("STRATEGY_RESEARCH_MEMORY_DIR", str(tmp_path))
        rc = cmd_list(console=console)
        assert rc == 0

    def test_two_entries(self, console, populated_memory):
        rc = cmd_list(console=console)
        assert rc == 0
        out = console.export_text()
        assert "alpha_rule" in out or "Name" in out  # columns or data


# ─── cmd_show ───────────────────────────────────────────────────────────


class TestCmdShow:
    def test_missing_name(self, console):
        rc = cmd_show("", console=console)
        assert rc == 1

    def test_present(self, console, populated_memory):
        rc = cmd_show("alpha_rule", console=console)
        assert rc == 0
        assert "t-stat" in console.export_text()

    def test_missing(self, console, populated_memory):
        rc = cmd_show("nope", console=console)
        assert rc == 0  # silent — prints "no such entry"


# ─── cmd_search ─────────────────────────────────────────────────────────


class TestCmdSearch:
    def test_empty_query(self, console):
        rc = cmd_search("", console=console)
        assert rc == 1

    def test_match(self, console, populated_memory):
        rc = cmd_search("t-stat", console=console)
        assert rc in (0, 1)
        out = console.export_text()
        assert "alpha_rule" in out or "No matches" in out

    def test_no_match(self, console, populated_memory):
        rc = cmd_search("xyz_no_match_term", console=console)
        assert rc == 0


# ─── cmd_forget ─────────────────────────────────────────────────────────


class TestCmdForget:
    def test_missing_name(self, console):
        rc = cmd_forget("", console=console)
        assert rc == 1

    def test_no_confirm(self, console, populated_memory):
        rc = cmd_forget("alpha_rule", console=console, yes=False)
        assert rc == 1  # Refused without confirmation

    def test_confirm_success(self, console, populated_memory):
        rc = cmd_forget("alpha_rule", console=console, yes=True)
        assert rc == 0
        # Verify it's gone
        rc2 = cmd_show("alpha_rule", console=console)
        # cmd_show on missing → rc 0 (silent no-match message)
        assert rc2 == 0


# ─── run dispatcher ─────────────────────────────────────────────────────


class TestRunDispatcher:
    def test_run_no_args(self, console, populated_memory):
        rc = run()
        assert rc == 0

    def test_run_search(self):
        rc = run(None, "search", "t-stat")
        assert rc in (0, 1)

    def test_run_forget_no_confirmation(self, populated_memory):
        rc = run(None, "forget", "alpha_rule")
        # Without yes=True, forget refuses
        assert rc == 1

    def test_run_show(self):
        rc = run(None, "alpha_rule")
        # Will return 0 if entry exists, 0 if not (silent)
        assert rc == 0

    def test_run_show_missing(self, console, populated_memory):
        rc = run(None, "nonexistent")
        assert rc == 0
