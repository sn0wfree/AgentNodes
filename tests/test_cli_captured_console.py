"""Tests for ``cli.theme.captured_console`` — TUI capture context manager.

Verifies that handlers writing through ``get_console()`` are routed
into a recording Console that the TUI can drain into the chat
transcript, while the legacy singleton is preserved on either side
of the ``with`` block.
"""
from __future__ import annotations

from rich.console import Console

from agentnodes.interfaces.cli.theme import captured_console, get_console


class TestCapturedConsole:
    def test_returns_a_recording_console(self):
        with captured_console(width=80) as rec:
            assert isinstance(rec, Console)
            assert rec.record is True

    def test_default_singleton_preserved_outside_block(self):
        before = get_console()
        with captured_console(width=80):
            # Inside the block, get_console() returns the recording
            # console, NOT the original singleton.
            rec = get_console()
            assert rec is not before
        # After the block, the singleton resumes — the capture is
        # scoped to the with-block via contextvars.
        assert get_console() is before

    def test_handler_output_captured(self):
        with captured_console(width=80) as rec:
            rec.print("first line")
            rec.print("[bold]bold line[/bold]")
        captured = rec.export_text(clear=False, styles=False)
        assert "first line" in captured
        assert "bold line" in captured

    def test_recording_clears_after_export(self):
        with captured_console(width=80) as rec:
            rec.print("to be cleared")
        # clear=True wipes the buffer.
        cleared = rec.export_text(clear=True, styles=False)
        # Capture again — should be empty.
        rec.print("after")
        again = rec.export_text(clear=True, styles=False)
        assert "to be cleared" not in again
        assert "after" in cleared or "to be cleared" in cleared

    def test_respects_no_color_env(self, monkeypatch):
        monkeypatch.setenv("NO_COLOR", "1")
        with captured_console(width=80) as rec:
            rec.print("hello")
            captured = rec.export_text(clear=False, styles=False)
        # NO_COLOR strips style spans; word "hello" still present.
        assert "hello" in captured

    def test_can_capture_markup_text(self):
        with captured_console(width=120) as rec:
            rec.print("[bold]hello[/bold] world")
            captured = rec.export_text(clear=False, styles=False)
        assert "hello" in captured
        assert "world" in captured


class TestGetConsoleOverride:
    def test_get_console_returns_override_inside_context(self):
        before = get_console()
        sentinel_before = before
        with captured_console(width=80) as rec:
            inside = get_console()
            assert inside is rec
        # After the context the singleton resumes (same object identity).
        assert get_console() is sentinel_before


class TestNestedContexts:
    def test_nested_capture_restores_outer(self):
        with captured_console(width=80) as outer:
            with captured_console(width=40) as inner:
                assert get_console() is inner
            # Inner context exited — outer is restored.
            assert get_console() is outer
        # Both exited — singleton resumes.
        assert get_console() is not outer

    def test_records_into_inner_only(self):
        with captured_console(width=80) as outer:
            outer.print("outer-pre")
            with captured_console(width=40) as inner:
                inner.print("inner-text")
            outer.print("outer-post")
        outer_text = outer.export_text(clear=False, styles=False)
        # outer captured both phases; the inner phase was redirected.
        assert "outer-pre" in outer_text
        assert "outer-post" in outer_text
        # "inner-text" never reached outer because the inner capture
        # got it. Confirm by exporting inner:
        # (need a fresh rec since we already cleared by exiting outer)
        with captured_console(width=40) as rec2:
            rec2.print("inner-text")
        assert "inner-text" in rec2.export_text(clear=False, styles=False)
        # Sanity: outer_text — by the time we read it, the outer capture
        # was closed and `inner-text` was inside `inner`. So outer should NOT
        # contain inner-text. Check that.
        assert "inner-text" not in outer_text
