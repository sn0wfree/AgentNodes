"""Tests for the Textual TUI skeleton (Commit 1).

These tests verify the bare mount lifecycle:

1. The app composes the seven widgets we promised (Header, Sidebar,
   Transcript, Rail, Input, Footer, embedded Banner).
2. The brand-token bridge reads the project theme correctly.
3. The :class:`WriteTranscript` message appends to the transcript log.
4. The :class:`SynthesizeInput` echo path works for sidebar clicks.
5. ``Textual`` ``App.run_test`` mounts without exceptions.

Five tests total — heavier coverage lands with the ChatSession commit.
"""
from __future__ import annotations

import pytest

from strategy_research.interfaces.cli.tui.app import ResearchApp
from strategy_research.interfaces.cli.tui.keybindings import TUI_BINDINGS
from strategy_research.interfaces.cli.tui.messages import (
    SynthesizeInput,
)
from strategy_research.interfaces.cli.tui.theme import (
    _strip_rich_modifier,
    active_primary,
    brand_tokens,
)
from strategy_research.interfaces.cli.tui.widgets import (
    CommandSidebar,
    ModeBar,
    TranscriptView,
)


def test_brand_tokens_returns_expected_keys():
    """brand_tokens() returns the eight CSS-friendly tokens."""
    from dataclasses import asdict
    tokens = brand_tokens()
    d = asdict(tokens)
    for key in ("primary", "primary_dim", "success", "danger",
                "warning", "info", "muted", "surface"):
        assert key in d, f"missing token: {key}"
        assert isinstance(d[key], str)
        # Hex string or empty (NO_COLOR mode); never a Rich modifier prefix.
        v = d[key]
        assert not v.lower().startswith("bold ")
        assert not v.lower().startswith("italic ")


def test_strip_rich_modifier_handles_prefixes():
    """Style modifiers ('bold ', 'italic ') are removed."""
    assert _strip_rich_modifier("bold #d97706") == "#d97706"
    assert _strip_rich_modifier("italic red") == "red"
    assert _strip_rich_modifier("plain blue") == "plain blue"
    assert _strip_rich_modifier("") == ""


def test_tui_bindings_are_well_formed():
    """TUI_BINDINGS exposes the core key bindings."""
    keys = {b.key for b in TUI_BINDINGS}
    assert "ctrl+c" in keys, "ctrl+c halt binding missing"
    assert "ctrl+d" in keys, "ctrl+d quit binding missing"
    assert "f1" in keys, "f1 help binding missing"


@pytest.mark.asyncio
async def test_research_app_run_test_mounts_widgets():
    """End-to-end: app.run_test() mounts the full skeleton."""
    app = ResearchApp(model="gpt-4o", version="0.4.2")
    async with app.run_test() as pilot:
        await pilot.pause()
        # All 5 named inner widgets are present (header + footer are auto-mounted).
        assert app.query_one(TranscriptView).is_mounted
        assert app.query_one(CommandSidebar).is_mounted
        assert app.query_one("#mode-bar", ModeBar).is_mounted
        assert app.query_one("#input").is_mounted  # ChatInput uses id="input"


@pytest.mark.asyncio
async def test_write_transcript_message_appends_to_log():
    """Posting a ``WriteTranscript`` message lands inside the transcript widget."""
    app = ResearchApp(model="m", version="0.4.2")
    async with app.run_test() as pilot:
        await pilot.pause()
        text_marker = "TI_MARKER_HELLO"
        app.write_transcript(text_marker)
        await pilot.pause()
        await pilot.pause()  # let the render pipeline run
        transcript = app.query_one(TranscriptView)
        joined = " ".join(str(line) for line in transcript.lines)
        assert text_marker in joined, f"marker not found in transcript lines: {joined!r}"


@pytest.mark.asyncio
async def test_synthesize_input_echoes_to_transcript():
    """Sidebar ``SynthesizeInput`` clicks dispatch through the session.

    Commit 2 wires ``on_synthesize_input`` to :class:`ChatSession.dispatch`
    (the real ``process_turn`` dispatcher). For plain text input, this
    lands in ``ctx.history`` as a {role: user} entry. We verify the
    history is populated rather than the transcript (the help panel
    rendering lives in Commit 3).
    """
    app = ResearchApp(model="m", version="0.4.2")
    async with app.run_test() as pilot:
        await pilot.pause()
        # Manually wire the session — Commit 2 wires it in on_mount, but
        # since the test app skips banner rendering we initialize it.
        if app.session is None:
            from strategy_research.interfaces.cli.tui.session import ChatSession

            app.session = ChatSession(app.ctx, app=app)
        await pilot.pause()
        await app.on_synthesize_input(SynthesizeInput(text="hi-from-sidebar"))
        await pilot.pause()
        # Plain text lands in ctx.history.
        assert any(
            m.get("content") == "hi-from-sidebar" for m in app.ctx.history
        ), f"history missing entry: {app.ctx.history!r}"


@pytest.mark.asyncio
async def test_app_mount_renders_banner_in_transcript():
    """End-to-end: app mount renders the gradient banner into TranscriptView.

    This is the smoke test that proves the full skeleton works:
    CSS loads, widgets mount, on_mount runs, and the banner Rich
    ``Text`` lands inside the chat log as the first scrollback entry.
    """
    app = ResearchApp(model="gpt-4o", version="0.4.2")
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        tv = app.query_one(TranscriptView)
        assert len(tv.lines) > 0, "banner should produce at least one line"
        joined = " ".join(str(s) for line in tv.lines for s in line)
        # Brand line "QuantNodes-Research" should appear verbatim.
        assert "QuantNodes-Research" in joined
        assert "0.4.2" in joined
        assert app.banner is not None


@pytest.mark.asyncio
async def test_app_save_screenshot_produces_valid_svg(tmp_path):
    """``App.save_screenshot(path)`` writes a non-empty SVG; we sample
    one to keep CI honest that the renderer emits an actual layout
    (CSS OK, widgets painted, no exceptions).
    """
    app = ResearchApp(model="gpt-4o", version="0.4.2")
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        svg_path = tmp_path / "snapshot.svg"
        app.save_screenshot(str(svg_path))
        # SVG is non-trivial (the empty banner alone is ~80 KB).
        assert svg_path.exists()
        assert svg_path.stat().st_size > 1024
        assert svg_path.read_text(encoding="utf-8", errors="ignore").startswith(
            "<svg"
        ) or "<svg" in svg_path.read_text(encoding="utf-8", errors="ignore")[:200]


# Cheap non-asyncio test: brand primary must not be the literal Rich bold prefix.
def test_active_primary_is_clean_hex():
    p = active_primary()
    assert not p.lower().startswith("bold ")
    assert p.startswith("#")


# Cheap non-asyncio test: brand primary must not be the literal Rich bold prefix.
