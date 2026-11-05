"""Tests for Stage C: TranscriptView.append_tool_call / update_tool_result.

Verifies the inline tool-call rendering protocol:

* append_tool_call writes a '⏳ tool · {args preview}' line and records
  its line index for in-place replacement.
* update_tool_result replaces that line with '✔/✘ tool · 0.3s'.
* Args are JSON-serialised and truncated at 80 chars.
* Elapsed time formats as '320ms' (<1s) or '1.2s' (>=1s).
* Unknown call_id is silently ignored.
"""
from __future__ import annotations

import pytest

from agentnodes.interfaces.cli.tui.widgets.transcript import TranscriptView

# ---------------------------------------------------------------- helpers


@pytest.fixture
def tv() -> TranscriptView:
    """A minimal TranscriptView with the RichLog surface bypassed.

    We only exercise the new methods' state-mutation effects, so we
    skip Textual mount and create the widget directly. We patch ``write``
    to capture lines and ``_truncate_to`` to a no-op.
    """
    widget = TranscriptView.__new__(TranscriptView)
    widget._stream_baseline = None
    widget._streamer = None
    widget._folders = []
    widget._fold_baselines = []
    widget._fold_line_counts = []
    widget._active_folder_idx = None
    widget._tool_lines = {}
    widget._tool_names = {}
    # Both ``lines`` and ``_lines`` are referenced by RichLog internals
    # and TranscriptView._truncate_to. Keep them in sync.
    widget._lines = []
    widget.lines = widget._lines
    widget._line_cache = {}

    class _VirtualSize:
        def __class__(self, *_): return self
    widget._widest_line_width = 0

    written: list[str] = []

    def fake_write(content):
        widget._lines.append(content)
        widget.lines = widget._lines
        written.append(content)

    widget.write = fake_write
    widget._truncate_to = lambda baseline: None
    widget.refresh = lambda: None
    widget._captured = written
    return widget


# ---------------------------------------------------------------- append_tool_call


class TestAppendToolCall:
    def test_writes_running_line(self, tv: TranscriptView):
        tv.append_tool_call("c1", "read", {"path": "config.yaml"})
        assert len(tv._captured) == 1
        line = tv._captured[0]
        assert "⏳" in line
        assert "read" in line
        assert "config.yaml" in line

    def test_records_line_index_and_tool_name(self, tv: TranscriptView):
        tv.append_tool_call("c1", "read", {"path": "x"})
        assert "c1" in tv._tool_lines
        assert tv._tool_lines["c1"] == 0
        assert tv._tool_names["c1"] == "read"

    def test_long_args_are_truncated(self, tv: TranscriptView):
        big = {"x": "a" * 200}
        tv.append_tool_call("c1", "store_data", big)
        line = tv._captured[0]
        assert "..." in line
        # 80-char preview budget; JSON will include truncation
        assert len(line) < 200

    def test_unicode_args_survive(self, tv: TranscriptView):
        tv.append_tool_call("c1", "search", {"q": "A股动量"})
        assert "A股动量" in tv._captured[0]

    def test_multiple_calls_record_distinct_indices(self, tv: TranscriptView):
        tv.append_tool_call("c1", "read", {"path": "a"})
        tv.append_tool_call("c2", "read", {"path": "b"})
        assert tv._tool_lines["c1"] == 0
        assert tv._tool_lines["c2"] == 1


# ---------------------------------------------------------------- update_tool_result


class TestUpdateToolResult:
    def test_ok_replaces_with_green_check(self, tv: TranscriptView):
        tv.append_tool_call("c1", "read", {"path": "x"})
        tv.update_tool_result("c1", ok=True, elapsed_ms=320)
        assert len(tv._captured) == 2
        result_line = tv._captured[1]
        assert "✔" in result_line
        assert "320ms" in result_line
        assert "read" in result_line

    def test_error_replaces_with_red_cross(self, tv: TranscriptView):
        tv.append_tool_call("c1", "run_backtest", {"strategy": "mom"})
        tv.update_tool_result("c1", ok=False, elapsed_ms=1234)
        result_line = tv._captured[1]
        assert "✘" in result_line
        assert "1.2s" in result_line

    def test_removes_from_state_after_update(self, tv: TranscriptView):
        tv.append_tool_call("c1", "x", {})
        tv.update_tool_result("c1", ok=True, elapsed_ms=10)
        assert "c1" not in tv._tool_lines
        assert "c1" not in tv._tool_names

    def test_unknown_call_id_is_ignored(self, tv: TranscriptView):
        """No crash on stray event."""
        tv.update_tool_result("ghost", ok=True, elapsed_ms=100)
        assert tv._captured == []

    def test_after_clear_log_unknown_ids(self, tv: TranscriptView):
        """After clear_log, in-flight call_ids become unknown."""
        tv.append_tool_call("c1", "x", {})
        tv._tool_lines.clear()
        tv._tool_names.clear()
        tv.update_tool_result("c1", ok=True, elapsed_ms=100)
        # No new line written
        assert len(tv._captured) == 1  # only the original call line


# ---------------------------------------------------------------- elapsed formatting


class TestElapsedFormat:
    @pytest.mark.parametrize("ms,expected", [
        (0, "0ms"),
        (320, "320ms"),
        (999, "999ms"),
        (1000, "1.0s"),
        (1234, "1.2s"),
        (12345, "12.3s"),
        (60000, "60.0s"),
    ])
    def test_format_elapsed(self, ms, expected):
        assert TranscriptView._format_elapsed(ms) == expected


# ---------------------------------------------------------------- args preview


class TestArgsPreview:
    def test_short_args(self):
        out = TranscriptView._format_args_preview({"path": "x"})
        assert out == '{"path": "x"}'

    def test_long_args_truncated_with_ellipsis(self):
        big = {"x": "a" * 200}
        out = TranscriptView._format_args_preview(big)
        assert out.endswith("...")
        assert len(out) <= 80

    def test_unsortable_keys_handled(self):
        # JSON-serialisable with sort_keys=True → consistent ordering
        out = TranscriptView._format_args_preview({"b": 2, "a": 1})
        assert out == '{"a": 1, "b": 2}'

    def test_non_json_falls_back_to_repr(self):
        # A set isn't JSON-serialisable; fallback should still produce a string
        out = TranscriptView._format_args_preview({"set_value": {1, 2, 3}})
        assert isinstance(out, str)
        assert len(out) > 0
