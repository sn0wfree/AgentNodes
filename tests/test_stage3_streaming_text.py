"""Tests for Stage 3 - StreamingText head/middle/tail rendering.

Verifies that the folded view shows head + middle indicator + tail,
and the expanded view shows the full text with a fold hint.
"""
from __future__ import annotations

from agentnodes.interfaces.cli.tui.widgets.streaming_text import StreamingText


class TestStreamingTextRender:
    def test_empty_returns_empty(self):
        s = StreamingText()
        s.start()
        assert s.render() == ""

    def test_short_text_returned_as_is(self):
        s = StreamingText()
        s.start()
        s.full_text = "hello world"
        assert s.render() == "hello world"

    def test_long_text_folded_shows_head_middle_tail(self):
        s = StreamingText()
        s.start()
        s.full_text = "A" * 80 + "B" * 50 + "C" * 120  # 250 chars total
        rendered = s.render()
        assert "[muted]" in rendered
        assert "(middle)" in rendered
        assert "(ctrl+e to expand)" in rendered
        # Head is first 80 chars
        assert "A" * 80 in rendered
        # Tail is last 120 chars
        assert "C" * 120 in rendered
        # Hidden count
        assert "+50 chars (middle)" in rendered

    def test_expanded_shows_full_text_and_fold_hint(self):
        s = StreamingText()
        s.start()
        s.full_text = "X" * 300
        s.expand()
        rendered = s.render()
        assert "(ctrl+e to fold)" in rendered
        assert "300 chars" in rendered
        assert "X" * 300 in rendered

    def test_toggle_expand_flips_state(self):
        s = StreamingText()
        s.start()
        s.full_text = "Y" * 300
        assert not s.expanded
        s.toggle_expand()
        assert s.expanded
        s.toggle_expand()
        assert not s.expanded

    def test_collapse_sets_expanded_false(self):
        s = StreamingText()
        s.start()
        s.full_text = "Z" * 300
        s.expand()
        assert s.expanded
        s.collapse()
        assert not s.expanded

    def test_folded_at_threshold_boundary(self):
        """Text exactly at threshold (200 chars) is returned as-is."""
        s = StreamingText()
        s.start()
        s.full_text = "T" * 200  # _HEAD_CHARS + _TAIL_CHARS = 200
        rendered = s.render()
        assert rendered == "T" * 200
        assert "[muted]" not in rendered

    def test_folded_just_above_threshold(self):
        """Text at 201 chars gets the middle indicator."""
        s = StreamingText()
        s.start()
        s.full_text = "T" * 201
        rendered = s.render()
        assert "[muted]" in rendered
        assert "+1 chars (middle)" in rendered


class TestAppendDone:
    def test_append_done_writes_marker(self):
        from agentnodes.interfaces.cli.tui.widgets.transcript import TranscriptView
        tv = TranscriptView()
        # RichLog needs a screen; test via mock
        with __import__("unittest.mock").mock.patch.object(tv, "write") as m:
            tv.append_done()
        m.assert_called_once_with("[dim]\u2022 Done.[/dim]")
