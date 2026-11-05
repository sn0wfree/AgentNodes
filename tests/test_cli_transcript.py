"""Tests for ``cli.ui.transcript``."""

from __future__ import annotations

from rich.console import Console
from rich.text import Text

from agentnodes.interfaces.cli.ui.transcript import (
    render_answer,
    render_elapsed_status,
    render_prompt_footer,
    render_recap,
)


def _collect(content) -> str:
    console = Console(record=True, force_terminal=False, width=120)
    for chunk in render_answer(content):
        console.print(chunk, end="")
    return console.export_text()


# ─── render_answer ──────────────────────────────────────────────────────


class TestRenderAnswer:
    def test_empty(self):
        out = _collect("")
        assert out == ""

    def test_plain_line(self):
        out = _collect("hello world")
        assert "hello world" in out

    def test_strips_bold(self):
        out = _collect("**bold** text")
        assert "bold" in out
        # No markup should leak into plain output
        assert "**" not in out

    def test_strips_italic(self):
        out = _collect("*italic* text")
        assert "italic" in out
        assert "*" not in out

    def test_strips_inline_code(self):
        out = _collect("`code` text")
        assert "code" in out
        assert "`" not in out

    def test_strips_hrule(self):
        out = _collect("before\n\n---\n\nafter")
        assert "before" in out
        assert "after" in out
        # The hrule should not be in the rendered output
        assert "---" not in out

    def test_pipe_table_upgraded(self):
        content = (
            "| col1 | col2 |\n"
            "|------|------|\n"
            "| a    | b    |\n"
        )
        out = _collect(content)
        # Table cells should appear
        assert "col1" in out
        assert "col2" in out
        assert "a" in out
        assert "b" in out

    def test_pipe_table_with_formatting(self):
        content = (
            "| **A** | B |\n"
            "|--------|---|\n"
            "| 1 | 2 |\n"
        )
        out = _collect(content)
        # ** stripped; "A" remains
        assert "A" in out
        assert "B" in out


# ─── render_recap ───────────────────────────────────────────────────────


class TestRenderRecap:
    def test_empty_history(self):
        result = render_recap([])
        assert isinstance(result, Text)
        assert "Last request:" in str(result)

    def test_single_turn(self):
        history = [{"role": "user", "content": "hello"}]
        result = render_recap(history)
        text = str(result)
        assert "hello" in text

    def test_two_turns(self):
        history = [
            {"role": "user", "content": "user question"},
            {"role": "assistant", "content": "assistant answer"},
        ]
        result = render_recap(history)
        text = str(result)
        assert "user question" in text
        assert "assistant answer" in text

    def test_truncates_long(self):
        long = "x" * 200
        result = render_recap([{"role": "user", "content": long}])
        text = str(result)
        # max last_request = 92; truncated with ellipsis
        assert "…" in text

    def test_dataclass_works(self):
        from dataclasses import dataclass

        @dataclass
        class Turn:
            content: str

        history = [Turn(content="q"), Turn(content="a")]
        result = render_recap(history)
        assert isinstance(result, Text)


# ─── render_elapsed_status ───────────────────────────────────────────────


class TestRenderElapsed:
    def test_seconds(self):
        result = render_elapsed_status(2.5)
        text = str(result)
        assert "Analyzed" in text
        assert "s" in text or "ms" in text

    def test_zero(self):
        result = render_elapsed_status(0)
        text = str(result)
        assert "Analyzed" in text


# ─── render_prompt_footer ───────────────────────────────────────────────


class TestRenderPromptFooter:
    def test_default_width(self):
        result = render_prompt_footer()
        assert isinstance(result, Text)
        text = str(result)
        # Should have at least 40 dashes
        assert text.strip().count("─") >= 40

    def test_custom_width(self):
        result = render_prompt_footer(width=20)
        text = str(result)
        assert text.strip().count("─") >= 20
