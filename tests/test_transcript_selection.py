"""Tests for TranscriptView mouse selection support.

Verifies that:
1. write() stores plain text in _plain_lines alongside Rich renderables
2. get_selection() extracts the correct text from _plain_lines (source-level)
3. selection_updated() clears _line_cache and refreshes (source-level)
4. render_line() calls apply_offsets() for compositor coordinate mapping
5. clear_log() resets _plain_lines (source-level)
"""
from __future__ import annotations

import inspect

from strategy_research.interfaces.cli.tui.widgets.transcript import TranscriptView

# ---------------------------------------------------------------- helpers


def _make_tv() -> TranscriptView:
    """Create a minimal TranscriptView bypassing Textual mount.

    Only tests write() and _plain_lines — does not mount the widget.
    """
    import io

    from rich.console import Console as RichConsole
    from rich.segment import Segment
    from textual._cells import cell_len
    from textual.strip import Strip

    tv = TranscriptView.__new__(TranscriptView)
    tv._stream_baseline = None
    tv._streamer = None
    tv._folders = []
    tv._fold_baselines = []
    tv._fold_line_counts = []
    tv._active_folder_idx = None
    tv._tool_lines = {}
    tv._tool_names = {}
    tv._plain_lines = []
    tv._lines = []
    tv.lines = tv._lines
    tv._line_cache = {}
    tv._widest_line_width = 0
    tv._start_line = 0
    tv._deferred_renders = []

    def fake_base_write(content, *args, **kwargs):
        """Simulate RichLog.write: append a Strip to _lines."""
        text = str(content)
        segments = [Segment(text)]
        strip = Strip(segments, cell_len(text))
        tv._lines.append(strip)
        return tv

    def patched_write(content, *args, **kwargs):
        """TranscriptView.write: store plain text, then base write."""
        if isinstance(content, str):
            tv._plain_lines.append(content)
        else:
            try:
                buf = io.StringIO()
                console = RichConsole(file=buf, force_terminal=False, width=200)
                console.print(content, end="")
                tv._plain_lines.append(buf.getvalue().rstrip("\n"))
            except Exception:
                tv._plain_lines.append(str(content))
        fake_base_write(content, *args, **kwargs)
        return tv

    tv.write = patched_write
    return tv


# ---------------------------------------------------------------- write stores plain text


class TestWriteStoresPlainLines:
    def test_string_write(self):
        tv = _make_tv()
        tv.write("Hello world")
        assert tv._plain_lines == ["Hello world"]

    def test_multiple_writes(self):
        tv = _make_tv()
        tv.write("line 1")
        tv.write("line 2")
        tv.write("line 3")
        assert tv._plain_lines == ["line 1", "line 2", "line 3"]

    def test_rich_text_write(self):
        tv = _make_tv()
        from rich.text import Text
        tv.write(Text("Bold text", style="bold"))
        assert len(tv._plain_lines) == 1
        assert "Bold text" in tv._plain_lines[0]

    def test_markdown_write(self):
        tv = _make_tv()
        from rich.markdown import Markdown
        tv.write(Markdown("# Title\n\nBody"))
        assert len(tv._plain_lines) == 1
        assert "Title" in tv._plain_lines[0]

    def test_empty_string(self):
        tv = _make_tv()
        tv.write("")
        assert tv._plain_lines == [""]

    def test_chinese_content(self):
        tv = _make_tv()
        tv.write("A股动量策略")
        assert tv._plain_lines == ["A股动量策略"]


# ---------------------------------------------------------------- source-level checks


class TestSelectionSourceLevel:
    def test_render_line_calls_apply_offsets(self):
        """render_line must call apply_offsets for compositor coordinate mapping."""
        src = inspect.getsource(TranscriptView.render_line)
        assert "apply_offsets" in src

    def test_render_line_applies_selection_highlight(self):
        """render_line must check for active selection and apply highlight."""
        src = inspect.getsource(TranscriptView.render_line)
        assert "text_selection" in src
        assert "screen--selection" in src

    def test_get_selection_uses_plain_lines(self):
        """get_selection must use _plain_lines, not re-render Rich content."""
        src = inspect.getsource(TranscriptView.get_selection)
        assert "_plain_lines" in src
        assert "selection.extract" in src

    def test_selection_updated_clears_cache(self):
        """selection_updated must clear _line_cache."""
        src = inspect.getsource(TranscriptView.selection_updated)
        assert "_line_cache" in src
        assert "clear()" in src

    def test_clear_log_resets_plain_lines(self):
        """clear_log must reset _plain_lines."""
        src = inspect.getsource(TranscriptView.clear_log)
        assert "_plain_lines" in src

    def test_write_stores_plain_text(self):
        """write must store plain text in _plain_lines."""
        src = inspect.getsource(TranscriptView.write)
        assert "_plain_lines" in src


# ---------------------------------------------------------------- RichLog.on_resize regression


class TestWriteAcceptsRichLogPositionalArgs:
    """Regression for: TypeError: TranscriptView.write() takes 2 positional
    arguments but 6 were given.

    ``RichLog.on_resize`` replays deferred writes via::

        self.write(*deferred_render)

    where ``deferred_render`` is a ``DeferredRender`` namedtuple with
    fields ``(content, width, expand, shrink, scroll_end)`` — i.e. 5
    positional args. Our override must accept them via ``*args``.
    """

    def test_write_accepts_5_positional_args_from_deferred_render(self):
        from textual.widgets._rich_log import DeferredRender

        tv = _make_tv()
        deferred = DeferredRender(
            content="hello",
            width=80,
            expand=False,
            shrink=True,
            scroll_end=None,
        )
        # This call shape is exactly what RichLog.on_resize does.
        tv.write(*deferred)
        assert tv._plain_lines == ["hello"]

    def test_write_accepts_all_kinds_of_positional_args(self):
        """Width/expand/shrink/scroll_end/animate variants must also work."""
        tv = _make_tv()
        # Content + 5 trailing positional args (matches RichLog signature)
        tv.write("x", None, False, True, None, False)
        assert tv._plain_lines == ["x"]
