"""Tests for ``cli.commands.slash_halt`` — /halt /resume slash commands."""

from __future__ import annotations

import pytest

from strategy_research.interfaces.cli.commands.slash_halt import (
    cmd_halt,
    cmd_resume,
    is_halt_command,
    is_resume_command,
    run_halt,
    run_resume,
)
from strategy_research.interfaces.cli.halt import is_halted

# ─── is_halt_command / is_resume_command ──────────────────────────────


class TestBareWordDetectors:
    @pytest.mark.parametrize(
        "text",
        ["停", "停手", "stop", "kill", "halt", "halt!"],
    )
    def test_halt_triggers(self, text):
        assert is_halt_command(text) is True

    @pytest.mark.parametrize(
        "text",
        ["resume", "continue", "go"],
    )
    def test_resume_triggers(self, text):
        assert is_resume_command(text) is True

    @pytest.mark.parametrize(
        "text",
        ["", "STOP kill all", "halt please", "stop doing it"],
    )
    def test_multi_word_not_halt(self, text):
        assert is_halt_command(text) is False

    @pytest.mark.parametrize(
        "text",
        ["", "let's resume", "continue now", "go for it"],
    )
    def test_multi_word_not_resume(self, text):
        assert is_resume_command(text) is False

    def test_case_insensitive(self):
        assert is_halt_command("HALT")
        assert is_halt_command("Stop")
        assert is_resume_command("Resume")


# ─── cmd_halt / cmd_resume ────────────────────────────────────────────


class TestCmdHaltResume:
    def test_cmd_halt_trips(self):
        rc = cmd_halt("test reason")
        assert rc == 0
        assert is_halted() is True

    def test_cmd_resume_clears(self):
        from strategy_research.interfaces.cli.halt import trip_halt
        trip_halt()
        rc = cmd_resume()
        assert rc == 0
        assert is_halted() is False

    def test_cmd_halt_empty_reason(self):
        rc = cmd_halt()
        assert rc == 0
        assert is_halted()


# ─── Slash router entrypoints ─────────────────────────────────────────


class TestRouterEntrypoints:
    def test_run_halt(self):
        rc = run_halt()
        assert rc == 0
        assert is_halted() is True

    def test_run_resume(self):
        from strategy_research.interfaces.cli.halt import trip_halt
        trip_halt()
        rc = run_resume()
        assert rc == 0
        assert is_halted() is False
