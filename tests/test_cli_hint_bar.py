"""Tests for ``cli.components.hint_bar.render_hint_bar``."""

from __future__ import annotations

import pytest
from rich.console import Console
from rich.text import Text

from strategy_research.interfaces.cli.components.hint_bar import render_hint_bar
from strategy_research.interfaces.cli.utils.ascii_compat import (
    ELLIPSIS_UNICODE,
    register_ascii_mode,
)


@pytest.fixture(autouse=True)
def _force_unicode_mode_for_legacy_tests():
    """The Unicode-vs-ASCII choice is per-thread; pin ``Unicode`` so the
    pre-ASCII-fallback legacy tests in this module stay deterministic.
    Newer tests should use :func:`register_ascii_mode` explicitly.
    """
    register_ascii_mode(False)
    yield
    register_ascii_mode(None)


def _plain(text: Text) -> str:
    console = Console(record=True, force_terminal=False, width=80)
    console.print(text, end="")
    return console.export_text()


class TestHintBar:
    def test_left_only(self):
        out = _plain(render_hint_bar("hello", width=40))
        assert "hello" in out

    def test_left_and_right(self):
        out = _plain(render_hint_bar("tab to complete", "ctrl-c to exit", width=40))
        assert "tab to complete" in out
        assert "ctrl-c to exit" in out

    def test_returns_rich_text(self):
        result = render_hint_bar("a", "b", width=40)
        assert isinstance(result, Text)

    def test_truncates_left_when_too_long(self):
        left = "x" * 100
        right = "END"
        out = _plain(render_hint_bar(left, right, width=20))
        assert "END" in out
        # 'x' * 100 truncated — should still contain many xs but with ellipsis
        assert ELLIPSIS_UNICODE in out

    def test_preserves_right_when_overflow(self):
        left = "a" * 80
        right = "right-stays"
        out = _plain(render_hint_bar(left, right, width=30))
        assert "right-stays" in out

    def test_empty_left(self):
        out = _plain(render_hint_bar("", "right", width=20))
        assert "right" in out

    def test_empty_right(self):
        out = _plain(render_hint_bar("left", "", width=40))
        assert "left" in out

    def test_exact_width_fits(self):
        left = "abc"
        right = "xy"
        out = _plain(render_hint_bar(left, right, width=20))
        assert "abc" in out
        assert "xy" in out

    def test_width_one_returns_only_right(self):
        out = _plain(render_hint_bar("abcdef", "Z", width=3))
        assert "Z" in out

    def test_zero_width_falls_back(self, monkeypatch):
        # _resolve_width handles width=0 by falling back to terminal width
        result = render_hint_bar("hi", "bye", width=0)
        assert isinstance(result, Text)

    def test_negative_width_falls_back(self):
        result = render_hint_bar("hi", "bye", width=-1)
        assert isinstance(result, Text)
