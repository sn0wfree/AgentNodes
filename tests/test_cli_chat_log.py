"""Tests for ``cli.components.chat_log``."""

from __future__ import annotations

from rich.console import Console
from rich.text import Text

from agentnodes.interfaces.cli.components.chat_log import render_history, render_turn


def _plain(text: Text) -> str:
    console = Console(record=True, force_terminal=False, width=80)
    console.print(text, end="")
    return console.export_text()


class TestRenderTurn:
    def test_user_role(self):
        out = _plain(render_turn({"role": "user", "content": "hi"}))
        assert "you" in out
        assert "hi" in out

    def test_assistant_role(self):
        out = _plain(render_turn({"role": "assistant", "content": "hello"}))
        assert "Vibe" in out
        assert "hello" in out

    def test_system_role(self):
        out = _plain(render_turn({"role": "system", "content": "rules"}))
        assert "system" in out
        assert "rules" in out

    def test_tool_role(self):
        out = _plain(render_turn({"role": "tool", "content": "ok"}))
        assert "tool" in out
        assert "ok" in out

    def test_unknown_role_defaults_to_user(self):
        out = _plain(render_turn({"role": "alien", "content": "x"}))
        assert "you" in out

    def test_role_case_insensitive(self):
        out = _plain(render_turn({"role": "USER", "content": "hi"}))
        assert "you" in out

    def test_missing_role_defaults_user(self):
        out = _plain(render_turn({"content": "x"}))
        assert "you" in out

    def test_missing_content(self):
        out = _plain(render_turn({"role": "user"}))
        # No body, just the header
        assert "you" in out

    def test_empty_content(self):
        out = _plain(render_turn({"role": "user", "content": ""}))
        assert "you" in out


class TestRenderHistory:
    def test_empty(self):
        result = render_history([])
        assert isinstance(result, Text)
        # No exceptions; exported text is empty
        rendered = _plain(result)
        assert rendered == ""

    def test_user_assistant_pair(self):
        history = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ]
        out = _plain(render_history(history))
        assert "you" in out
        assert "Vibe" in out
        assert "hi" in out
        assert "hello" in out

    def test_skips_non_mapping(self):
        history = [
            {"role": "user", "content": "hi"},
            "garbage",
            {"role": "assistant", "content": "ok"},
        ]
        out = _plain(render_history(history))
        assert "hi" in out
        assert "ok" in out

    def test_multiple_turns(self):
        history = [
            {"role": "user", "content": "a"},
            {"role": "assistant", "content": "b"},
            {"role": "user", "content": "c"},
            {"role": "assistant", "content": "d"},
        ]
        out = _plain(render_history(history))
        for c in ("a", "b", "c", "d"):
            assert c in out
