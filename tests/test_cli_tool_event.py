"""Tests for ``cli.components.tool_event``."""

from __future__ import annotations

import pytest
from rich.console import Console
from rich.text import Text

from strategy_research.interfaces.cli.components.tool_event import (
    beautify_tool_name,
    render_tool_event,
    render_tool_events,
    summarize_args,
)


def _render_plain(text: Text) -> str:
    """Strip Rich markup and return plain text."""
    console = Console(record=True, force_terminal=False, width=160)
    console.print(text, end="")
    return console.export_text()


# ─── beautify_tool_name ─────────────────────────────────────────────────


class TestBeautifyToolName:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("get_financials", "Financials"),
            ("run_backtest", "Backtest"),
            ("fetch_data", "Data"),
            ("skill", "Skill"),
            ("build_features", "Features"),
            ("compute_factor", "Factor"),
            ("plain", "Plain"),
            ("get_URL", "URL"),
            ("compute_QPS_metrics", "QPS Metrics"),
        ]
    )
    def test_basic_stripping(self, raw, expected):
        assert beautify_tool_name(raw) == expected

    def test_empty_string(self):
        assert beautify_tool_name("") == ""

    def test_single_word_unchanged(self):
        assert beautify_tool_name("ping") == "Ping"

    def test_multiple_underscores(self):
        # run_get_data: prefixes stripped until none match (run_ strips first, then get_)
        assert beautify_tool_name("run_get_data") == "Data"


# ─── summarize_args ─────────────────────────────────────────────────────


class TestSummarizeArgs:
    def test_empty_args(self):
        assert summarize_args(None) == ""
        assert summarize_args({}) == ""

    def test_preferred_key_query(self):
        s = summarize_args({"query": "shanghai market"})
        assert "query" in s
        assert "shanghai market" in s

    def test_preferred_key_symbol(self):
        s = summarize_args({"symbol": "AAPL"})
        assert "AAPL" in s

    def test_multiple_preferred_keys(self):
        s = summarize_args({"prompt": "p1", "url": "u1"}, max_len=200)
        # Should include both preferred keys
        assert "p1" in s
        assert "u1" in s

    def test_fallback_to_first_kv(self):
        s = summarize_args({"foo": "bar"})
        assert "foo" in s
        assert "bar" in s

    def test_truncation(self):
        from strategy_research.interfaces.cli.utils.ascii_compat import (
            ELLIPSIS_UNICODE,
            register_ascii_mode,
        )
        # Force Unicode mode for the legacy assertions.
        register_ascii_mode(False)
        long = "x" * 200
        s = summarize_args({"query": long}, max_len=20)
        assert len(s) <= 21  # includes the trailing single-char "…"
        assert s.endswith(ELLIPSIS_UNICODE)

    def test_skip_none_or_empty(self):
        s = summarize_args({"query": "", "prompt": None, "foo": "bar"})
        assert "foo" in s
        assert "query" not in s
        assert "prompt" not in s


# ─── render_tool_event ──────────────────────────────────────────────────


class TestRenderToolEvent:
    def test_renders_text(self):
        text = render_tool_event("get_financials", {"symbol": "AAPL"})
        assert isinstance(text, Text)

    def test_renders_status_marker(self):
        from strategy_research.interfaces.cli.utils.ascii_compat import (
            register_ascii_mode,
            status_marker,
        )
        # Force unicode mode so the test asserts the unicode glyph.
        register_ascii_mode(False)
        text = render_tool_event("get_financials", status="running")
        out = _render_plain(text)
        assert status_marker("running") in out
        assert "Financials" in out

    def test_error_status_uses_x_marker(self):
        from strategy_research.interfaces.cli.utils.ascii_compat import (
            register_ascii_mode,
            status_marker,
        )
        register_ascii_mode(False)
        text = render_tool_event("get_financials", status="error")
        out = _render_plain(text)
        assert status_marker("error") in out

    def test_includes_args(self):
        text = render_tool_event("get_financials", {"symbol": "AAPL"})
        out = _render_plain(text)
        assert "AAPL" in out

    def test_includes_duration(self):
        text = render_tool_event("get_financials", status="ok", duration_ms=230)
        out = _render_plain(text)
        assert "ms" in out

    def test_includes_result_summary(self):
        text = render_tool_event("skill", status="ok", result_summary="loaded read_csv")
        out = _render_plain(text)
        assert "loaded read_csv" in out


# ─── render_tool_events ─────────────────────────────────────────────────


class TestRenderToolEvents:
    def test_batch_returns_list(self):
        events = [
            {"name": "get_financials", "args": {"symbol": "AAPL"}, "status": "ok"},
            {"name": "run_backtest", "args": {"query": "q"}, "status": "running"},
        ]
        results = render_tool_events(events)
        assert len(results) == 2
        assert all(isinstance(r, Text) for r in results)

    def test_empty_batch(self):
        assert render_tool_events([]) == []

    def test_batch_passes_kwargs(self):
        events = [
            {
                "name": "skill",
                "args": {"name": "read_csv"},
                "status": "ok",
                "duration_ms": 100,
                "result_summary": "loaded",
            }
        ]
        out = _render_plain(render_tool_events(events)[0])
        assert "Skill" in out
        assert "loaded" in out
        assert "ms" in out
