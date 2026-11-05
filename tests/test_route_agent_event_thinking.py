"""Tests for ``route_agent_event["assistant_message"]`` routing.

Verifies that the assistant_message event:
  1. Extracts think content via ``extract_thinking_tags``
  2. Calls ``TranscriptView.append_thinking`` when think content exists
  3. Calls ``TranscriptView.write_assistant_message`` with body only
  4. Order: thinking folder appears BEFORE the body Markdown
  5. Empty think content (no tags emitted) → no-op for append_thinking
  6. Multiple think sections are joined and rendered as one folder
  7. Streaming preview path (text_delta) still uses strip, NOT extract
"""
from __future__ import annotations

from unittest import mock

from agentnodes.interfaces.cli.tui.app import ResearchApp


def _make_app() -> ResearchApp:
    """Create a ResearchApp with mocked TranscriptView."""
    app = ResearchApp.__new__(ResearchApp)
    app._tool_total = 0
    app._tool_ok = 0
    app._finalized_text_ids = set()
    app._active_text_id = None
    return app


def _setup_tv_mock():
    """Return a mock TranscriptView with the methods we need."""
    tv = mock.MagicMock()
    tv._streamer = None
    tv._stream_baseline = None
    return tv


class TestAssistantMessageRouting:

    def test_routes_think_and_body(self):
        app = _make_app()
        tv = _setup_tv_mock()
        app.query_one = mock.MagicMock(return_value=tv)

        content = "<think>reasoning</think>actual answer"
        app.route_agent_event("assistant_message", {"content": content})

        tv.append_thinking.assert_called_once_with("reasoning")
        tv.write_assistant_message.assert_called_once_with("actual answer")

    def test_no_think_skips_append(self):
        app = _make_app()
        tv = _setup_tv_mock()
        app.query_one = mock.MagicMock(return_value=tv)

        app.route_agent_event("assistant_message", {"content": "no tags here"})

        tv.append_thinking.assert_not_called()
        tv.write_assistant_message.assert_called_once_with("no tags here")

    def test_empty_content(self):
        app = _make_app()
        tv = _setup_tv_mock()
        app.query_one = mock.MagicMock(return_value=tv)

        app.route_agent_event("assistant_message", {"content": ""})

        tv.append_thinking.assert_not_called()
        # Empty body still gets rendered (write_assistant_message handles empty)
        tv.write_assistant_message.assert_called_once_with("")

    def test_multiple_think_sections_joined(self):
        app = _make_app()
        tv = _setup_tv_mock()
        app.query_one = mock.MagicMock(return_value=tv)

        content = "<think>first reason</think>middle<think>second reason</think>final"
        app.route_agent_event("assistant_message", {"content": content})

        # Two think sections joined with "\n\n"
        tv.append_thinking.assert_called_once_with("first reason\n\nsecond reason")
        tv.write_assistant_message.assert_called_once_with("middlefinal")

    def test_unclosed_think_tag_extracted(self):
        app = _make_app()
        tv = _setup_tv_mock()
        app.query_one = mock.MagicMock(return_value=tv)

        content = "preamble<think>reasoning goes here"
        app.route_agent_event("assistant_message", {"content": content})

        tv.append_thinking.assert_called_once_with("reasoning goes here")
        tv.write_assistant_message.assert_called_once_with("preamble")

    def test_chinese_think_content(self):
        app = _make_app()
        tv = _setup_tv_mock()
        app.query_one = mock.MagicMock(return_value=tv)

        content = "<think>散户主导的市场有 T+1 限制</think>正文内容"
        app.route_agent_event("assistant_message", {"content": content})

        tv.append_thinking.assert_called_once_with("散户主导的市场有 T+1 限制")
        tv.write_assistant_message.assert_called_once_with("正文内容")

    def test_order_thinking_then_body(self):
        """append_thinking should be called BEFORE write_assistant_message.

        Order matters: the think folder should appear above the body
        in the rendered transcript.
        """
        app = _make_app()
        tv = _setup_tv_mock()
        app.query_one = mock.MagicMock(return_value=tv)

        call_order = []
        tv.append_thinking.side_effect = lambda x: call_order.append("thinking")
        tv.write_assistant_message.side_effect = lambda x: call_order.append("body")

        app.route_agent_event(
            "assistant_message",
            {"content": "<think>r</think>b"},
        )
        assert call_order == ["thinking", "body"]

    def test_query_one_failure_silent(self):
        """If TranscriptView can't be queried (e.g. during teardown),
        the route must not crash the event loop."""
        app = _make_app()
        app.query_one = mock.MagicMock(side_effect=Exception("not mounted"))

        # Should NOT raise
        app.route_agent_event("assistant_message", {"content": "<think>r</think>b"})


class TestTextDeltaPathUnchanged:
    """Verify text_delta still uses strip_thinking_tags (not extract)."""

    def test_text_delta_strips_but_does_not_capture(self):
        app = _make_app()
        tv = _setup_tv_mock()
        # Set up begin_streaming to populate _streamer on first call
        streamer = mock.MagicMock()
        tv._streamer = None  # start with None
        tv.begin_streaming.side_effect = lambda: setattr(tv, "_streamer", streamer)
        tv._stream_baseline = None
        tv._truncate_to = mock.MagicMock()
        app.query_one = mock.MagicMock(return_value=tv)

        # text_delta should not call append_thinking
        app.route_agent_event("text_delta", {"text": "<think>reasoning</think>body"})

        tv.append_thinking.assert_not_called()
        # begin_streaming was called (since _streamer was None)
        tv.begin_streaming.assert_called()
        # And the stripped body is "body" (not the full raw text)
        streamer.append_delta.assert_called_with("body")


class TestThinkingSpentOnUnrelatedEvents:
    """Other events must not trigger think-folder rendering."""

    def test_tool_call_does_not_render_thinking(self):
        app = _make_app()
        tv = _setup_tv_mock()
        app.query_one = mock.MagicMock(return_value=tv)

        app.route_agent_event("tool_call", {"call_id": "x", "tool": "read", "args": {}})

        tv.append_thinking.assert_not_called()

    def test_error_event_does_not_render_thinking(self):
        app = _make_app()
        tv = _setup_tv_mock()
        app.query_one = mock.MagicMock(return_value=tv)

        app.route_agent_event("error", {"message": "something broke"})

        tv.append_thinking.assert_not_called()
