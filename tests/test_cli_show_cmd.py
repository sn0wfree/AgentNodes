"""Tests for ``cli.commands.show``."""

from __future__ import annotations

import json

import pytest
from rich.console import Console

from agentnodes.interfaces.cli.commands.show import (
    _locate_run,
    cmd_pine,
    cmd_show,
    cmd_skill,
    run_pine,
    run_show,
    run_skill,
)


@pytest.fixture
def console():
    return Console(record=True, force_terminal=False, width=120)


@pytest.fixture
def fake_workspace(tmp_path, monkeypatch):
    """Build a fake workspace + summary.json for /show and /pine tests."""
    ws = tmp_path / "ws"
    strat = ws / "strategies" / "demo" / "runs" / "run_0001"
    strat.mkdir(parents=True)
    (strat / "summary.json").write_text(
        json.dumps({"round": 1, "acceptance_decision": "discard"}),
        encoding="utf-8",
    )
    monkeypatch.setenv("STRATEGY_RESEARCH_WORKSPACE", str(ws))
    monkeypatch.chdir(tmp_path)
    return ws


# ─── _locate_run ────────────────────────────────────────────────────────


class TestLocateRun:
    def test_with_run_prefix(self, fake_workspace):
        path = _locate_run("run_0001")
        assert path is not None
        assert path.exists()

    def test_with_bare_id(self, fake_workspace):
        path = _locate_run("0001")
        assert path is not None
        assert path.name == "run_0001"

    def test_missing_returns_none(self, fake_workspace):
        assert _locate_run("9999") is None

    def test_no_workspace(self, tmp_path, monkeypatch):
        monkeypatch.setenv("STRATEGY_RESEARCH_WORKSPACE", str(tmp_path / "empty"))
        monkeypatch.chdir(tmp_path)
        assert _locate_run("0001") is None


# ─── cmd_show ───────────────────────────────────────────────────────────


class TestCmdShow:
    def test_existing_run(self, fake_workspace, console):
        rc = cmd_show("run_0001", console=console)
        assert rc == 0
        out = console.export_text()
        assert "summary" in out.lower() or "round" in out.lower()

    def test_missing_run_returns_1(self, fake_workspace, console):
        rc = cmd_show("9999", console=console)
        assert rc == 1
        assert "not found" in console.export_text().lower()

    def test_run_dir_with_summary_missing(self, fake_workspace, console, tmp_path):
        # Directory without summary.json
        (fake_workspace / "strategies" / "demo" / "runs" / "run_0002").mkdir()
        rc = cmd_show("run_0002", console=console)
        assert rc == 1


# ─── cmd_pine ───────────────────────────────────────────────────────────


class TestCmdPine:
    def test_emits_pine_v5(self, fake_workspace, console):
        rc = cmd_pine("run_0001", console=console)
        assert rc == 0
        out = console.export_text()
        assert "@version=5" in out
        assert "strategy(" in out

    def test_missing_run(self, fake_workspace, console):
        rc = cmd_pine("9999", console=console)
        assert rc == 1


# ─── cmd_skill ──────────────────────────────────────────────────────────


class TestCmdSkill:
    def test_no_skills_or_loader(self, console, monkeypatch):
        # No loader available — should still print a friendly message
        rc = cmd_skill(console=console)
        assert rc in (0, 1)


# ─── Slash router entrypoints ───────────────────────────────────────────


class TestRouterEntrypoints:
    def test_run_show_no_args_calls_skill(self):
        rc = run_show()
        assert rc in (0, 1)

    def test_run_pine_no_args(self):
        rc = run_pine()
        assert rc in (0, 1)

    def test_run_skill(self):
        rc = run_skill()
        assert rc in (0, 1)
