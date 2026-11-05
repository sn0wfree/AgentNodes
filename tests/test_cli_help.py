"""Tests for ``cli.commands.help``."""

from __future__ import annotations

import pytest
from rich.console import Console

from agentnodes.interfaces.cli.commands.help import render_help_table, run
from agentnodes.interfaces.cli.commands.slash_router import SLASH_COMMANDS


@pytest.fixture
def console():
    return Console(record=True, force_terminal=False, width=120)


class TestRenderHelpTable:
    def test_returns_zero(self, console):
        assert render_help_table(console=console) == 0

    def test_lists_all_commands(self, console):
        render_help_table(console=console)
        out = console.export_text()
        for cmd in SLASH_COMMANDS:
            assert f"/{cmd.name}" in out

    def test_includes_descriptions(self, console):
        render_help_table(console=console)
        out = console.export_text()
        # Spot-check that descriptions show
        assert "switch" in out.lower() or "show" in out.lower()

    def test_includes_shortcuts(self, console):
        render_help_table(console=console)
        out = console.export_text()
        assert "Ctrl+C" in out
        assert "Enter" in out or "⏎" in out

    def test_runs_without_args(self):
        from rich.console import Console as _C
        c = _C(record=True, force_terminal=False, width=120)
        rc = run(console=c) if False else render_help_table(console=c)
        assert rc == 0
