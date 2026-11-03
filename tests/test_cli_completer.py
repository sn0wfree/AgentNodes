"""Tests for ``cli.interactive.completer.SlashCompleter``."""

from __future__ import annotations

from typing import Iterable

from prompt_toolkit.completion import Completion
from prompt_toolkit.document import Document

from strategy_research.interfaces.cli.interactive.completer import SlashCompleter


def _collect(completions: Iterable[Completion]) -> list[Completion]:
    return list(completions)


class TestSlashCompleter:
    def test_no_slash_returns_empty(self):
        c = SlashCompleter()
        completions = c.get_completions(Document("hello"), None)
        assert _collect(completions) == []

    def test_empty_slash_returns_empty(self):
        c = SlashCompleter()
        completions = c.get_completions(Document(""), None)
        assert _collect(completions) == []

    def test_slash_prefix_lists_commands(self):
        c = SlashCompleter(max_suggestions=20)
        completions = c.get_completions(Document("/"), None)
        names = [comp.text for comp in _collect(completions)]
        assert "help" in names
        assert "quit" in names
        # All built-in commands listed
        from strategy_research.interfaces.cli.commands.slash_router import SLASH_COMMANDS
        registry = {cmd.name for cmd in SLASH_COMMANDS}
        assert registry.issubset(set(names))

    def test_partial_match(self):
        c = SlashCompleter()
        completions = c.get_completions(Document("/h"), None)
        names = [comp.text for comp in _collect(completions)]
        assert "help" in names
        assert "history" in names

    def test_partial_match_for_show(self):
        c = SlashCompleter()
        completions = c.get_completions(Document("/s"), None)
        names = [comp.text for comp in _collect(completions)]
        # Multiple 's' prefixes
        assert "search" in names
        assert "show" in names

    def test_no_match_returns_empty(self):
        c = SlashCompleter()
        completions = c.get_completions(Document("/xyz_no_command"), None)
        assert _collect(completions) == []

    def test_after_space_does_not_complete(self):
        c = SlashCompleter()
        # Token already consumed, space present → stop completing
        completions = c.get_completions(Document("/help "), None)
        assert _collect(completions) == []

    def test_max_suggestions(self):
        c = SlashCompleter(max_suggestions=3)
        completions = c.get_completions(Document("/"), None)
        # Even with 16 commands, limit to 3
        assert len(_collect(completions)) <= 3

    def test_completion_has_display_meta(self):
        c = SlashCompleter()
        completions = c.get_completions(Document("/h"), None)
        for comp in _collect(completions):
            assert comp.display_meta  # Description text attached
