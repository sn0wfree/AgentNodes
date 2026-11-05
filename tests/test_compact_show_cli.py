"""Tests for the `compact show` CLI command."""
from __future__ import annotations

import io
from contextlib import redirect_stdout

from agentnodes.interfaces.cli.commands.compact_show import cmd_compact_show


class TestCompactShow:
    def test_returns_zero_exit_code(self):
        ret = cmd_compact_show()
        assert ret == 0

    def test_output_includes_all_fields(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            cmd_compact_show()
        output = buf.getvalue()
        # Should mention every field
        for field in [
            "enabled",
            "threshold_tokens",
            "compaction_buffer_tokens",
            "microcompact_ratio",
            "llm_summarize_ratio",
            "hard_truncate_ratio",
            "overflow_ratio",
            "microcompact_tool_result_chars",
            "tool_truncate_chars",
            "collapse_keep_recent",
            "preserve_recent_tokens",
            "tail_turns",
            "summary_output_tokens",
            "enable_incremental_summary",
        ]:
            assert field in output, f"Missing field: {field}"

    def test_output_shows_default_ratios(self):
        """Default ratios should match user-specified values."""
        buf = io.StringIO()
        with redirect_stdout(buf):
            cmd_compact_show()
        output = buf.getvalue()
        assert "0.9" in output  # microcompact_ratio
        assert "0.8" in output  # llm_summarize_ratio (opencode-aligned default)
        assert "0.99" in output  # hard_truncate_ratio / overflow_ratio

    def test_output_shows_opencode_defaults(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            cmd_compact_show()
        output = buf.getvalue()
        # opencode-aligned: buffer=20K, summary cap=4096, chars=2K
        assert "20,000" in output
        assert "4,096" in output
        assert "2,000" in output

    def test_threshold_tokens_none_shows_derived(self):
        """When threshold_tokens is None, the derived value is shown."""
        buf = io.StringIO()
        with redirect_stdout(buf):
            cmd_compact_show()
        output = buf.getvalue()
        # Should mention the derived threshold
        assert "derived_threshold" in output or "→ derived" in output
        assert "opencode" in output.lower()
