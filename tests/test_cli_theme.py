"""Tests for ``cli.theme`` — Rich stylesheet + dark-mode detection."""

from __future__ import annotations

import pytest

from agentnodes.interfaces.cli.theme import (
    Theme,
    _build_styles,
    _is_dark_terminal,
    _no_color_requested,
    clear_force_dark,
    force_dark,
    get_console,
    is_dark,
)


@pytest.fixture(autouse=True)
def _reset_theme():
    """Reset forced dark-mode override after every test."""
    yield
    clear_force_dark()


# ─── Dark-mode detection ────────────────────────────────────────────────


class TestDarkDetection:
    def test_no_env_returns_dark_default(self, monkeypatch):
        monkeypatch.delenv("AGENTNODES_THEME", raising=False)
        monkeypatch.delenv("COLORFGBG", raising=False)
        monkeypatch.delenv("TERM_PROGRAM", raising=False)
        assert _is_dark_terminal() is True

    def test_explicit_dark_env(self, monkeypatch):
        monkeypatch.setenv("AGENTNODES_THEME", "dark")
        assert _is_dark_terminal() is True

    def test_explicit_light_env(self, monkeypatch):
        monkeypatch.setenv("AGENTNODES_THEME", "light")
        assert _is_dark_terminal() is False

    def test_auto_env(self, monkeypatch):
        monkeypatch.setenv("AGENTNODES_THEME", "auto")
        # Falls through to COLORFGBG/TERM — none set → default dark.
        monkeypatch.delenv("COLORFGBG", raising=False)
        monkeypatch.delenv("TERM_PROGRAM", raising=False)
        assert _is_dark_terminal() is True

    def test_colorfgbg_low_brightness_dark(self, monkeypatch):
        monkeypatch.setenv("COLORFGBG", "15;0")  # bg=0 ⇒ dark
        monkeypatch.delenv("AGENTNODES_THEME", raising=False)
        assert _is_dark_terminal() is True

    def test_colorfgbg_high_brightness_light(self, monkeypatch):
        monkeypatch.setenv("COLORFGBG", "0;15")  # bg=15 ⇒ light
        monkeypatch.delenv("AGENTNODES_THEME", raising=False)
        assert _is_dark_terminal() is False

    def test_colorfgbg_invalid_falls_back(self, monkeypatch):
        monkeypatch.setenv("COLORFGBG", "garbage")
        monkeypatch.delenv("AGENTNODES_THEME", raising=False)
        # Should not raise; falls back to True
        assert _is_dark_terminal() is True

    def test_apple_terminal_is_dark(self, monkeypatch):
        monkeypatch.setenv("TERM_PROGRAM", "Apple_Terminal")
        monkeypatch.delenv("AGENTNODES_THEME", raising=False)
        assert _is_dark_terminal() is True

    def test_forced_dark_override(self, monkeypatch):
        monkeypatch.setenv("AGENTNODES_THEME", "light")  # overridden below
        force_dark(True)
        assert is_dark() is True

    def test_forced_light_override(self):
        force_dark(False)
        assert is_dark() is False

    def test_clear_force_dark_restores_detection(self, monkeypatch):
        monkeypatch.setenv("AGENTNODES_THEME", "light")
        force_dark(True)
        assert is_dark() is True
        clear_force_dark()
        assert is_dark() is False  # env var now wins again


# ─── NO_COLOR ────────────────────────────────────────────────────────────


class TestNoColor:
    def test_no_color_unset(self, monkeypatch):
        monkeypatch.delenv("NO_COLOR", raising=False)
        assert _no_color_requested() is False

    def test_no_color_set(self, monkeypatch):
        monkeypatch.setenv("NO_COLOR", "1")
        assert _no_color_requested() is True

    def test_no_color_empty_string(self, monkeypatch):
        # Per NO_COLOR spec, empty string is NOT a request for no color.
        monkeypatch.setenv("NO_COLOR", "")
        # bool("") is False in Python — but the function uses truthiness on .strip().
        # Implementation reads bool(...) on the raw value — empty ⇒ False.
        assert _no_color_requested() is False


# ─── Style bundle ────────────────────────────────────────────────────────


class TestBuildStyles:
    def test_dark_and_color(self):
        s = _build_styles(dark=True, no_color=False)
        assert s.primary.startswith("bold")
        assert s.success == "bold green"
        assert s.danger == "bold red"
        assert s.warning == "bold yellow"
        assert s.info == "bold cyan"
        assert s.bold == "bold"

    def test_light_and_color(self):
        s = _build_styles(dark=False, no_color=False)
        assert s.primary.startswith("bold")
        assert s.success == "bold green"

    def test_no_color_collapses_styles(self):
        s = _build_styles(dark=True, no_color=True)
        assert s.primary == ""
        assert s.success == ""
        assert s.danger == ""
        assert s.warning == ""
        assert s.info == ""
        assert s.muted == ""
        assert s.label == ""
        # bold and accent_bg remain functional
        assert s.bold == "bold"
        assert s.accent_bg == "reverse"

    def test_dark_vs_light_brand_colors_differ(self):
        dark = _build_styles(dark=True, no_color=False)
        light = _build_styles(dark=False, no_color=False)
        assert dark.primary_dim != light.primary_dim


# ─── Theme class + get_console ───────────────────────────────────────────


class TestThemeClass:
    def test_theme_has_styles(self):
        assert isinstance(Theme.primary, str)
        assert isinstance(Theme.success, str)

    def test_get_console_returns_singleton(self):
        c1 = get_console(force_terminal=True)
        c2 = get_console(force_terminal=True)
        assert c1 is c2
