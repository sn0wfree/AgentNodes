"""Tests for ``cli.ui.banner``."""

from __future__ import annotations

import pytest
from rich.console import Console

from agentnodes.interfaces.cli.ui.banner import (
    _build_logo,
    _gradient_color,
    print_banner,
)


@pytest.fixture
def console():
    return Console(record=True, force_terminal=False, width=120)


# ─── Gradient helper ────────────────────────────────────────────────────


class TestGradient:
    def test_color_returns_hex(self):
        c = _gradient_color(0.0)
        assert c.startswith("#")
        assert len(c) == 7

    def test_color_at_ends_differs(self):
        start = _gradient_color(0.0)
        end = _gradient_color(1.0)
        assert start != end

    def test_color_at_middle(self):
        # Step=0.5 ⇒ midpoint between #258BFF and #A5CFFF
        c = _gradient_color(0.5)
        assert c.startswith("#")


# ─── Logo ───────────────────────────────────────────────────────────────


class TestLogo:
    def test_logo_is_rich_text(self, console):
        text = _build_logo(width=120)
        assert str(text)  # non-empty

    def test_logo_truncates_narrow(self):
        text = _build_logo(width=10)
        # All lines should fit ≤10 chars (or less if line itself is short)
        for line in str(text).splitlines():
            assert len(line) <= 10

    def test_logo_has_multiple_lines(self):
        text = _build_logo(width=120)
        line_count = len(str(text).splitlines())
        assert line_count >= 3


# ─── print_banner ───────────────────────────────────────────────────────


class TestPrintBanner:
    def test_prints_no_crash(self, console):
        print_banner(console, model="minimax", version="0.4.2")
        out = console.export_text()
        assert "minimax" in out
        assert "0.4.2" in out
        assert "QuantNodes-Research" in out

    def test_default_model(self, console):
        print_banner(console)
        out = console.export_text()
        # Default model is "unknown" — should appear
        assert "unknown" in out

    def test_custom_width(self, console):
        print_banner(console, width=40)
        # Should not crash
        out = console.export_text()
        assert "QuantNodes-Research" in out

    def test_mode_label(self, console):
        print_banner(console, mode="chat")
        out = console.export_text()
        assert "chat" in out
