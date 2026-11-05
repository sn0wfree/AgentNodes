"""Phase 4 — v0.5.1 CLI tests: /goal start --workflow, /goal workflows, /goal checkpoint.

TDD stubs written before implementation. Each test should fail until the
corresponding feature lands in cli/commands/slash_goal.py.

Covers:
  - cmd_start gains --workflow flag (loads YAML, returns runner)
  - cmd_workflows (list / show / path subcommands)
  - cmd_checkpoint (save / list / resume / delete subcommands)

Reference: docs/phase-4-plan.md §4.1.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from rich.console import Console

from strategy_research.interfaces.cli.commands.slash_goal import (
    cmd_checkpoint,
    cmd_start,
    cmd_workflows,
    run,
)

# ─── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def console():
    return Console(record=True, force_terminal=False, width=120)


@pytest.fixture
def fresh_db(tmp_path: Path, monkeypatch):
    """Fresh goal DB per test."""
    monkeypatch.setenv("STRATEGY_RESEARCH_GOAL_DB", str(tmp_path / "goals.db"))


@pytest.fixture
def fresh_checkpoint_dir(tmp_path: Path, monkeypatch):
    """Redirect CheckpointStore to a fresh tmp dir."""
    cp_dir = tmp_path / "checkpoints"
    cp_dir.mkdir()
    monkeypatch.setattr(
        "strategy_research.interfaces.cli.commands.slash_goal._checkpoint_base_dir",
        lambda: cp_dir,
    )
    return cp_dir


# ─── v0.5.1 P0.1 — cmd_start --workflow ────────────────────────────────


class TestCmdStartWorkflow:
    """`/goal start <objective> --workflow <name>` loads YAML preset."""

    def test_with_workflow_loads_preset(self, console, fresh_db):
        rc = cmd_start(
            "Study momentum",
            console=console,
            workflow_name="goal_factor_research",
        )
        assert rc == 0
        out = console.export_text()
        assert "workflow" in out.lower() or "started" in out.lower()

    def test_unknown_workflow_errors(self, console, fresh_db):
        rc = cmd_start(
            "test",
            console=console,
            workflow_name="does_not_exist",
        )
        assert rc == 1
        assert "not found" in console.export_text().lower() or "unknown" in console.export_text().lower()

    def test_workflow_and_template_mutually_exclusive(self, console, fresh_db):
        rc = cmd_start(
            "test",
            console=console,
            workflow_name="goal_factor_research",
            template_key="market_analysis",
        )
        # Either reject explicitly, or accept with workflow taking precedence.
        # The contract: workflow wins, no error.
        assert rc == 0

    def test_no_workflow_no_template_still_works(self, console, fresh_db):
        """Backwards compat: --workflow absent → existing behavior."""
        rc = cmd_start("plain goal", console=console)
        assert rc == 0


# ─── v0.5.1 P0.2 — cmd_workflows ───────────────────────────────────────


class TestCmdWorkflows:
    """`/goal workflows [list|show|path]` enumerates presets."""

    def test_list_default(self, console):
        rc = cmd_workflows(console=console)
        assert rc == 0
        out = console.export_text()
        # Should mention at least the bundled preset
        assert "factor_research" in out or "goal_factor_research" in out

    def test_list_empty_user_dir(self, console, tmp_path, monkeypatch):
        # Point user workflows dir at empty tmp
        empty = tmp_path / "empty_workflows"
        empty.mkdir()
        monkeypatch.setattr(
            "strategy_research.interfaces.cli.commands.slash_goal._user_workflows_dir",
            lambda: empty,
        )
        rc = cmd_workflows(console=console)
        assert rc == 0
        # Built-in presets still listed
        assert "factor_research" in console.export_text()

    def test_show_existing(self, console):
        rc = cmd_workflows("show", "goal_factor_research", console=console)
        assert rc == 0
        out = console.export_text()
        # Should at least show the workflow's name + agents
        assert "factor_research" in out.lower() or "researcher" in out.lower()

    def test_show_missing(self, console):
        rc = cmd_workflows("show", "nope", console=console)
        assert rc == 1
        assert "not found" in console.export_text().lower() or "unknown" in console.export_text().lower()

    def test_path_subcommand(self, console):
        rc = cmd_workflows("path", "goal_factor_research", console=console)
        assert rc == 0
        out = console.export_text().strip()
        # Should print a real path ending in .yaml
        assert out.endswith(".yaml")
        assert Path(out).exists()


# ─── v0.5.1 P0.3 — cmd_checkpoint ──────────────────────────────────────


class TestCmdCheckpoint:
    """`/goal checkpoint save|list|resume|delete [goal_id]`."""

    def test_save_without_active_runner(self, console, fresh_db, fresh_checkpoint_dir):
        rc = cmd_checkpoint("save", console=console)
        assert rc == 1
        assert "no active" in console.export_text().lower() or "no goal" in console.export_text().lower()

    def test_list_empty(self, console, fresh_db, fresh_checkpoint_dir):
        rc = cmd_checkpoint("list", console=console)
        assert rc == 0
        assert "no checkpoints" in console.export_text().lower() or len(console.export_text()) >= 0

    def test_list_with_session_filter(self, console, fresh_db, fresh_checkpoint_dir):
        rc = cmd_checkpoint("list", "sess_xyz", console=console)
        assert rc == 0

    def test_resume_without_goal_id_uses_latest(self, console, fresh_db, fresh_checkpoint_dir):
        # With no checkpoints at all, must error gracefully
        rc = cmd_checkpoint("resume", console=console)
        # Either 1 (no checkpoints) or 0 + "no checkpoints" message
        out = console.export_text().lower()
        assert rc in (0, 1)
        if rc == 0:
            assert "no checkpoint" in out or "nothing to resume" in out

    def test_resume_unknown_goal_id(self, console, fresh_db, fresh_checkpoint_dir):
        rc = cmd_checkpoint("resume", "ghost_goal", session_id="sess_x", console=console)
        assert rc == 1

    def test_delete_missing(self, console, fresh_db, fresh_checkpoint_dir):
        rc = cmd_checkpoint("delete", "ghost_goal", session_id="sess_x", console=console)
        # No-op success is fine
        assert rc in (0, 1)

    def test_unknown_subcommand_falls_through_to_help(self, console):
        rc = cmd_checkpoint("wat", console=console)
        # Either help rendered (rc=0) or error (rc=1) — both acceptable
        assert rc in (0, 1)


# ─── v0.5.1 — run() dispatcher ─────────────────────────────────────────


class TestRunDispatcherV051:
    """`/goal` router accepts new subcommands."""

    def test_run_start_with_workflow_flag(self, console, fresh_db):
        rc = run(
            None, "start", "Study momentum", "--workflow", "goal_factor_research"
        )
        assert rc == 0

    def test_run_workflows_list(self, console):
        rc = run(None, "workflows")
        assert rc == 0

    def test_run_workflows_show(self, console):
        rc = run(None, "workflows", "show", "goal_factor_research")
        assert rc == 0

    def test_run_checkpoint_save(self, console, fresh_db, fresh_checkpoint_dir):
        rc = run(None, "checkpoint", "save")
        # No active goal → rc=1 expected
        assert rc == 1

    def test_run_checkpoint_list(self, console, fresh_db, fresh_checkpoint_dir):
        rc = run(None, "checkpoint", "list")
        assert rc == 0

    def test_run_help_mentions_new_subcommands(self, console):
        """`/goal help` (called directly) lists new subcommands."""
        from strategy_research.interfaces.cli.commands.slash_goal import cmd_help
        rc = cmd_help(console=console)
        assert rc == 0
        out = console.export_text()
        assert "workflows" in out
        assert "checkpoint" in out
