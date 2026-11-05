"""Tests for ``cli.utils.ascii_compat`` — Unicode ↔ ASCII fallback.

Verifies the env-var probe, register_ascii_mode override, helper
functions (status_marker / ellipsis / middot / arrow), and the
``ascii_fallback`` substitution. Also verifies the consumer-facing
components (tool_event, hint_bar) reflect the active mode.
"""
from __future__ import annotations

import pytest

from agentnodes.interfaces.cli.utils.ascii_compat import (
    ARROW_ASCII,
    ARROW_UNICODE,
    ELLIPSIS_ASCII,
    ELLIPSIS_UNICODE,
    MIDDOT_ASCII,
    MIDDOT_UNICODE,
    STATUS_MARKERS_ASCII,
    STATUS_MARKERS_UNICODE,
    arrow,
    ascii_fallback,
    ellipsis,
    is_ascii_mode,
    middot,
    register_ascii_mode,
    status_marker,
)


@pytest.fixture(autouse=True)
def _clear_override():
    """Always start with the env-var probe (no thread override)."""
    register_ascii_mode(None)
    yield
    register_ascii_mode(None)


# ─── env-var probe ─────────────────────────────────────────────────


class TestIsAsciiMode:
    def test_explicit_unicode_via_env(self, monkeypatch):
        monkeypatch.setenv("STRATEGY_ASCII_MODE", "0")
        assert is_ascii_mode() is False

    def test_explicit_ascii_via_env(self, monkeypatch):
        monkeypatch.setenv("STRATEGY_ASCII_MODE", "1")
        assert is_ascii_mode() is True

    def test_yes_env_truthy(self, monkeypatch):
        monkeypatch.setenv("STRATEGY_ASCII_MODE", "yes")
        assert is_ascii_mode() is True

    def test_no_env_falsy(self, monkeypatch):
        monkeypatch.setenv("STRATEGY_ASCII_MODE", "no")
        assert is_ascii_mode() is False

    def test_lang_C_triggers_ascii(self, monkeypatch):
        # No STRATEGY_ASCII_MODE; LANG=C implies plain ASCII.
        monkeypatch.delenv("STRATEGY_ASCII_MODE", raising=False)
        monkeypatch.setenv("LANG", "C")
        monkeypatch.delenv("LC_ALL", raising=False)
        monkeypatch.delenv("LANGUAGE", raising=False)
        assert is_ascii_mode() is True

    def test_lang_en_utf8_keeps_unicode(self, monkeypatch):
        monkeypatch.delenv("STRATEGY_ASCII_MODE", raising=False)
        monkeypatch.setenv("LANG", "en_US.UTF-8")
        monkeypatch.delenv("LC_ALL", raising=False)
        assert is_ascii_mode() is False

    def test_lang_C_utf8_keeps_unicode(self, monkeypatch):
        # C.UTF-8 is a UTF-8 locale; should NOT trigger ASCII.
        monkeypatch.delenv("STRATEGY_ASCII_MODE", raising=False)
        monkeypatch.setenv("LANG", "C.UTF-8")
        monkeypatch.delenv("LC_ALL", raising=False)
        assert is_ascii_mode() is False

    def test_register_ascii_mode_force_true_overrides_env(self, monkeypatch):
        monkeypatch.delenv("STRATEGY_ASCII_MODE", raising=False)
        monkeypatch.setenv("LANG", "en_US.UTF-8")
        register_ascii_mode(True)
        assert is_ascii_mode() is True

    def test_register_ascii_mode_force_false_overrides_env(self, monkeypatch):
        monkeypatch.setenv("STRATEGY_ASCII_MODE", "1")
        register_ascii_mode(False)
        assert is_ascii_mode() is False

    def test_register_ascii_mode_none_restores_env(self, monkeypatch):
        monkeypatch.setenv("STRATEGY_ASCII_MODE", "1")
        register_ascii_mode(None)
        assert is_ascii_mode() is True


# ─── helpers ────────────────────────────────────────────────────────


class TestMarkerHelpers:
    def test_status_marker_unicode_by_default(self):
        register_ascii_mode(False)
        assert status_marker("running") == STATUS_MARKERS_UNICODE["running"]
        assert status_marker("ok") == STATUS_MARKERS_UNICODE["ok"]
        assert status_marker("error") == STATUS_MARKERS_UNICODE["error"]

    def test_status_marker_ascii_when_ascii_mode(self):
        register_ascii_mode(True)
        assert status_marker("running") == STATUS_MARKERS_ASCII["running"]
        assert status_marker("ok") == STATUS_MARKERS_ASCII["ok"]
        assert status_marker("error") == STATUS_MARKERS_ASCII["error"]

    def test_status_marker_unknown_falls_back(self):
        register_ascii_mode(False)
        assert status_marker("unknown") == "?"
        register_ascii_mode(True)
        assert status_marker("unknown") == "?"

    def test_ellipsis_picks_correct_glyph(self):
        register_ascii_mode(False)
        assert ellipsis() == ELLIPSIS_UNICODE
        register_ascii_mode(True)
        assert ellipsis() == ELLIPSIS_ASCII

    def test_middot_picks_correct_glyph(self):
        register_ascii_mode(False)
        assert middot() == MIDDOT_UNICODE
        register_ascii_mode(True)
        assert middot() == MIDDOT_ASCII

    def test_arrow_picks_correct_glyph(self):
        register_ascii_mode(False)
        assert arrow() == ARROW_UNICODE
        register_ascii_mode(True)
        assert arrow() == ARROW_ASCII


# ─── ascii_fallback ────────────────────────────────────────────────


class TestAsciiFallback:
    def test_passthrough_for_pure_ascii(self):
        register_ascii_mode(True)
        assert ascii_fallback("hello world") == "hello world"

    def test_passthrough_in_unicode_mode(self):
        register_ascii_mode(False)
        # Even with unicode characters in the string, ascii_fallback
        # is a no-op when not in ascii mode.
        assert ascii_fallback("hello ●") == "hello ●"

    def test_replaces_ellipsis_with_three_dots(self):
        register_ascii_mode(True)
        assert ascii_fallback("truncated…") == "truncated..."

    def test_replaces_middot_with_dash(self):
        register_ascii_mode(True)
        assert ascii_fallback("a·b·c") == "a-b-c"

    def test_replaces_arrow_with_arrow(self):
        register_ascii_mode(True)
        assert ascii_fallback("user → model") == "user -> model"

    def test_unknown_glyph_becomes_question_mark(self):
        register_ascii_mode(True)
        # 中文 (CJK ideograph) has no ASCII mapping → "?"
        assert ascii_fallback("中文") == "??"

    def test_status_glyph_substitution(self):
        register_ascii_mode(True)
        # Raw ``●`` and ``×`` in user-supplied strings should be mapped
        # to ASCII lookalikes, not the placeholder ``?``.
        assert ascii_fallback("hello ●") == "hello *"
        assert ascii_fallback("error ×") == "error x"
        assert ascii_fallback("ok ○") == "ok o"


# ─── consumer integration ──────────────────────────────────────────


class TestToolEventModeReflection:
    def test_render_tool_event_uses_unicode_marker_by_default(self):
        from agentnodes.interfaces.cli.components.tool_event import render_tool_event
        register_ascii_mode(False)
        text = render_tool_event("Get Market Data", {"symbol": "AAPL"}, status="ok")
        rendered = text.plain
        assert "●" in rendered

    def test_render_tool_event_uses_ascii_marker_in_ascii_mode(self):
        from agentnodes.interfaces.cli.components.tool_event import render_tool_event
        register_ascii_mode(True)
        text = render_tool_event("Get Market Data", {"symbol": "AAPL"}, status="ok")
        rendered = text.plain
        # The Unicode ● MUST NOT appear; we should see the ASCII *.
        assert "●" not in rendered
        assert "*" in rendered

    def test_render_tool_event_uses_ascii_ellipsis_in_ascii_mode(self):
        from agentnodes.interfaces.cli.components.tool_event import (
            summarize_args,
        )
        register_ascii_mode(True)
        # ``summarize_args`` is the function that emits ellipsis on
        # truncation; that's where the unicode vs ascii pick shows up.
        long = {"path": "/" + "/".join(["segment"] * 30)}
        summary = summarize_args(long, max_len=10)
        # Unicode "…" MUST NOT appear.
        assert "…" not in summary
        assert "..." in summary


class TestHintBarModeReflection:
    def test_hint_bar_uses_unicode_ellipsis_by_default(self):
        from agentnodes.interfaces.cli.components.hint_bar import render_hint_bar
        register_ascii_mode(False)
        # Left side long enough to overflow 30 cols.
        text = render_hint_bar("x" * 100, right="ctrl+c", width=30)
        rendered = text.plain
        assert "…" in rendered

    def test_hint_bar_uses_ascii_ellipsis_in_ascii_mode(self):
        from agentnodes.interfaces.cli.components.hint_bar import render_hint_bar
        register_ascii_mode(True)
        text = render_hint_bar("x" * 100, right="ctrl+c", width=30)
        rendered = text.plain
        assert "…" not in rendered
        assert "..." in rendered
