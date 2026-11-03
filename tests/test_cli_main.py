"""Tests for ``cli.interactive.main`` — REPL driver dispatch table."""

from __future__ import annotations

import pytest

from strategy_research.interfaces.cli.interactive.main import (
    InteractiveContext,
    dispatch_slash,
    main,
    process_turn,
)

# ─── InteractiveContext ────────────────────────────────────────────────


class TestInteractiveContext:
    def test_defaults(self):
        ctx = InteractiveContext()
        assert ctx.session_id == "cli"
        assert ctx.history == []
        assert ctx.debug is False
        assert ctx.pending_prompt == ""
        assert ctx.last_recap_history_len == 0

    def test_mutation(self):
        ctx = InteractiveContext()
        ctx.history.append({"role": "user", "content": "x"})
        ctx.debug = True
        ctx.pending_prompt = "queued"
        assert len(ctx.history) == 1
        assert ctx.debug is True
        assert ctx.pending_prompt == "queued"


# ─── dispatch_slash ────────────────────────────────────────────────────


class TestDispatchSlash:
    def test_known_command(self):
        handler, args = dispatch_slash("/help", InteractiveContext())
        assert handler is not None
        assert args == ()

    def test_help_with_args(self):
        handler, args = dispatch_slash("/show run_0001", InteractiveContext())
        assert handler is not None
        assert args == ("run_0001",)

    def test_unknown_command_raises(self):
        with pytest.raises(ValueError):
            dispatch_slash("/nonexistent_command", InteractiveContext())


# ─── process_turn ─────────────────────────────────────────────────────


class TestProcessTurn:
    def test_help_returns_zero(self):
        rc = process_turn("/help")
        assert rc == 0

    def test_quit_returns_two(self):
        rc = process_turn("/quit")
        assert rc == 2

    def test_quit_with_alias(self):
        assert process_turn("/q") == 2
        assert process_turn("/exit") == 2
        assert process_turn("/:q") == 2

    def test_clear_returns_zero(self):
        rc = process_turn("/clear")
        assert rc == 0

    def test_model_returns_zero(self):
        rc = process_turn("/model")
        assert rc == 0

    def test_export_returns_zero(self):
        rc = process_turn("/export")
        assert rc == 0

    def test_unknown_slash_falls_back_to_help(self):
        rc = process_turn("/nonexistent")
        assert rc == 0  # help returns 0

    def test_plain_text_appends_history(self):
        ctx = InteractiveContext()
        rc = process_turn("What is the best strategy?", ctx)
        assert rc == 0
        assert len(ctx.history) == 1
        assert ctx.history[0]["role"] == "user"
        assert "best strategy" in ctx.history[0]["content"]

    def test_empty_input_returns_zero(self):
        assert process_turn("") == 0
        assert process_turn("   ") == 0

    def test_search_no_args(self):
        rc = process_turn("/search")
        # No query → falls back to export placeholder, rc=0
        assert rc == 0

    def test_search_with_args(self):
        rc = process_turn("/search hello")
        assert rc in (0, 1)

    def test_bare_word_halt_trips(self):
        from strategy_research.interfaces.cli.halt import clear_halt, is_halted
        clear_halt()
        rc = process_turn("halt")
        assert rc == 0
        assert is_halted() is True
        clear_halt()

    def test_bare_word_resume_clears(self):
        from strategy_research.interfaces.cli.halt import clear_halt, is_halted, trip_halt
        trip_halt()
        rc = process_turn("resume")
        assert rc == 0
        assert is_halted() is False
        clear_halt()


# ─── main() entrypoint ─────────────────────────────────────────────────


class TestMainEntrypoint:
    def test_banner(self, capsys):
        rc = main(["--banner"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "QuantNodes-Research" in out

    def test_help_no_args(self, capsys):
        rc = main([])
        assert rc == 0
        # Should print usage
        out = capsys.readouterr().out
        assert "interactive" in out.lower() or "usage" in out.lower() or args_help_in_output(out)

    def test_run_quit(self):
        rc = main(["/quit"])
        assert rc == 2

    def test_run_help(self):
        rc = main(["/help"])
        assert rc == 0


def args_help_in_output(out: str) -> bool:
    """Loose helper to detect usage output."""
    return "--banner" in out or "--model" in out
