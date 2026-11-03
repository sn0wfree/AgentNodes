"""Tests for ``cli.components.working_indicator.ThinkingSpinner``."""

from __future__ import annotations

import pytest
from rich.console import Console

from strategy_research.interfaces.cli.components.working_indicator import ThinkingSpinner


@pytest.fixture
def capture_console():
    """A Rich Console with record=True — captures output for assertions."""
    return Console(record=True, force_terminal=False, width=120)


class TestSpinnerInit:
    def test_default_seeded_verb_is_string(self):
        s = ThinkingSpinner()
        assert isinstance(s._verb, str)
        assert s._verb.endswith("…")

    def test_explicit_verb(self):
        s = ThinkingSpinner(verb="Loading…")
        assert s._verb == "Loading…"

    def test_seed_is_deterministic(self):
        a = ThinkingSpinner(seed=42)
        b = ThinkingSpinner(seed=42)
        assert a._verb == b._verb


class TestSpinnerRenderable:
    def test_renderable_is_rich_text(self):
        s = ThinkingSpinner(verb="Loading…")
        from rich.text import Text
        assert isinstance(s._renderable, Text)

    def test_renderable_contains_verb(self, capture_console):
        s = ThinkingSpinner(verb="Loading…")
        capture_console.print(s._renderable)
        assert "Loading…" in capture_console.export_text()

    def test_update_verb_swaps(self, capture_console):
        s = ThinkingSpinner(verb="Loading…")
        s.update_verb("Reading…")
        assert s._verb == "Reading…"
        capture_console.print(s._renderable)
        assert "Reading…" in capture_console.export_text()


class TestSpinnerContextManager:
    def test_context_manager_no_crash(self):
        # The Live render would normally spin on a TTY; force_terminal=False
        # makes it a no-op for capture.
        from io import StringIO
        Console(file=StringIO(), force_terminal=False, width=80, record=False)
        s = ThinkingSpinner(verb="Loading…")
        with s as spinner:
            assert spinner is s
            spinner.update_verb("Reading…")
            assert spinner._verb == "Reading…"


class TestSpinnerExtra:
    def test_set_extra(self, capture_console):
        s = ThinkingSpinner(verb="Loading…")
        s.set_extra("(120 tokens)")
        assert s._extra == "(120 tokens)"
        rendered = capture_console.export_text()
        capture_console.print(s._renderable)
        rendered = capture_console.export_text()
        assert "Loading…" in rendered
        assert "tokens" in rendered

    def test_clear_extra(self):
        s = ThinkingSpinner(verb="Loading…")
        s.set_extra("(120 tokens)")
        s.set_extra(None)
        assert s._extra is None

    def test_extra_renders_alongside_verb(self, capture_console):
        s = ThinkingSpinner(verb="Loading…")
        s.set_extra("1.2k tokens · $0.003")
        capture_console.print(s._renderable, end="")
        rendered = capture_console.export_text()
        assert "Loading…" in rendered
        assert "tokens" in rendered
