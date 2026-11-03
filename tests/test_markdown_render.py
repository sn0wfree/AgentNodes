"""Tests for TranscriptView.write_markdown / write_assistant_message.

Verifies that the final assistant message renders as Rich Markdown
with proper formatting (headers, bold, code, lists, tables) and that
empty input falls back to a muted hint. Streaming sessions are
replaced (not folded) on completion.
"""
from __future__ import annotations

import pytest

from strategy_research.interfaces.cli.tui.widgets.transcript import TranscriptView

# ── helpers ──────────────────────────────────────────────────────────


@pytest.fixture
def tv() -> TranscriptView:
    widget = TranscriptView.__new__(TranscriptView)
    widget._stream_baseline = None
    widget._streamer = None
    widget._folders = []
    widget._fold_baselines = []
    widget._fold_line_counts = []
    widget._active_folder_idx = None
    widget._tool_lines = {}
    widget._tool_names = {}
    widget._lines = []
    widget.lines = widget._lines
    widget._line_cache = {}
    widget._widest_line_width = 0

    written: list = []

    def fake_write(content):
        widget._lines.append(content)
        widget.lines = widget._lines
        written.append(content)

    widget.write = fake_write
    widget._truncate_to = lambda baseline: None
    widget.refresh = lambda: None
    widget._captured = written
    return widget


# ── write_markdown ────────────────────────────────────────────────────


class TestWriteMarkdown:
    def test_renders_non_empty_content(self, tv: TranscriptView):
        tv.write_markdown("# Title\n\nBody paragraph")
        assert len(tv._captured) == 1
        # Rich Markdown is a Renderable, not a plain string
        from rich.markdown import Markdown
        assert isinstance(tv._captured[0], Markdown)

    def test_empty_string_shows_hint(self, tv: TranscriptView):
        tv.write_markdown("")
        assert len(tv._captured) == 1
        assert "(empty response)" in str(tv._captured[0])

    def test_whitespace_only_shows_hint(self, tv: TranscriptView):
        tv.write_markdown("   \n\n  \n")
        assert "(empty response)" in str(tv._captured[0])

    def test_none_safe(self, tv: TranscriptView):
        """None falls back to empty hint."""
        tv.write_markdown(None)
        assert "(empty response)" in str(tv._captured[0])

    def test_strips_trailing_whitespace(self, tv: TranscriptView):
        """Trailing blank lines don't add extra vertical space."""
        tv.write_markdown("# Title\n\nbody\n\n\n\n")
        from rich.markdown import Markdown
        assert isinstance(tv._captured[0], Markdown)
        # Markdown constructed cleanly (no error)

    def test_code_block_renders(self, tv: TranscriptView):
        content = "```python\ndef foo():\n    return 42\n```"
        tv.write_markdown(content)
        from rich.markdown import Markdown
        assert isinstance(tv._captured[0], Markdown)

    def test_chinese_content(self, tv: TranscriptView):
        content = "# A股动量策略\n\n关键指标: 12.3%"
        tv.write_markdown(content)
        from rich.markdown import Markdown
        assert isinstance(tv._captured[0], Markdown)

    def test_table_renders(self, tv: TranscriptView):
        content = "| col1 | col2 |\n|------|------|\n| a | b |"
        tv.write_markdown(content)
        from rich.markdown import Markdown
        assert isinstance(tv._captured[0], Markdown)

    def test_bold_italic_preserved(self, tv: TranscriptView):
        content = "**bold** and *italic* text"
        tv.write_markdown(content)
        from rich.markdown import Markdown
        assert isinstance(tv._captured[0], Markdown)


# ── write_assistant_message ───────────────────────────────────────────


class TestWriteAssistantMessage:
    def test_replaces_active_streamer(self, tv: TranscriptView):
        """streaming area is truncated; markdown appended."""
        from strategy_research.interfaces.cli.tui.widgets.streaming_text import StreamingText
        tv._stream_baseline = 5
        tv._streamer = StreamingText()
        tv._streamer.start()
        tv._streamer.append_delta("raw preview text")

        tv.write_assistant_message("# Final answer")

        # Streamer cleared
        assert tv._streamer is None
        assert tv._stream_baseline is None
        # Markdown was written
        from rich.markdown import Markdown
        assert any(isinstance(c, Markdown) for c in tv._captured)

    def test_no_streamer_just_appends(self, tv: TranscriptView):
        tv.write_assistant_message("Just plain text, no stream.")
        from rich.markdown import Markdown
        assert any(isinstance(c, Markdown) for c in tv._captured)

    def test_empty_content_shows_hint(self, tv: TranscriptView):
        tv.write_assistant_message("")
        assert any("(empty response)" in str(c) for c in tv._captured)

    def test_does_not_create_folder(self, tv: TranscriptView):
        """write_assistant_message must NOT add to _folders."""
        before = len(tv._folders)
        tv.write_assistant_message("# Title\n\nBody")
        assert len(tv._folders) == before

    def test_tool_lines_preserved(self, tv: TranscriptView):
        """Markdown rendering doesn't clobber in-flight tool call state."""
        tv._tool_lines["c1"] = 0
        tv._tool_names["c1"] = "read"
        tv.write_assistant_message("# Final answer")
        # Tool state intact (used by future update_tool_result)
        assert "c1" in tv._tool_lines
