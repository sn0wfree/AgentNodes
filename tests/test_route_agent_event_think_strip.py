"""Tests for route_agent_event think-tag stripping.

Verifies that ``text_delta`` and ``assistant_message`` events routed
through ``ResearchApp.route_agent_event`` have thinking/reasoning
tags stripped before reaching the TranscriptView.
"""
from __future__ import annotations

from unittest import mock

from agentnodes.interfaces.cli.tui.app import ResearchApp
from agentnodes.interfaces.cli.tui.widgets.transcript import TranscriptView


class TestTextDeltaStrip:
    """text_delta events get thinking tags stripped."""

    def _app_with_fake_tv(self) -> tuple[ResearchApp, mock.MagicMock, mock.MagicMock]:
        app = ResearchApp.__new__(ResearchApp)
        app._tool_total = 0
        app._tool_ok = 0
        app._finalized_text_ids = set()
        app._active_text_id = None
        mock_tv = mock.MagicMock(spec=TranscriptView)
        app.query_one = mock.MagicMock(return_value=mock_tv)
        # Mock update_streaming_delta explicitly
        app.update_streaming_delta = mock.MagicMock()
        return app, mock_tv, app.update_streaming_delta

    def test_strips_think_tag(self):
        app, mock_tv, mock_usd = self._app_with_fake_tv()
        app.route_agent_event("text_delta", {
            "text": "<think>reasoning</think>visible answer",
        })
        # update_streaming_delta called with stripped text
        mock_usd.assert_called_once_with("visible answer")

    def test_strips_unclosed_think(self):
        app, mock_tv, mock_usd = self._app_with_fake_tv()
        app.route_agent_event("text_delta", {
            "text": "before<think>still reasoning",
        })
        mock_usd.assert_called_once_with("before")

    def test_passes_plain_text_through(self):
        app, mock_tv, mock_usd = self._app_with_fake_tv()
        app.route_agent_event("text_delta", {"text": "plain answer"})
        mock_usd.assert_called_once_with("plain answer")

    def test_empty_text_passes_through(self):
        app, mock_tv, mock_usd = self._app_with_fake_tv()
        app.route_agent_event("text_delta", {"text": ""})
        mock_usd.assert_called_once_with("")


class TestAssistantMessageExtract:
    """assistant_message events extract think tags and write to BOTH
    append_thinking (for the foldable) and write_assistant_message
    (for the Markdown body).

    Note: as of the think-folding change, ``assistant_message`` no
    longer strips think tags — it extracts them. Streaming preview
    (text_delta) still strips, so the user never sees internal
    reasoning during typing.
    """

    def _app_with_fake_tv(self):
        app = ResearchApp.__new__(ResearchApp)
        app._tool_total = 0
        app._tool_ok = 0
        app._finalized_text_ids = set()
        app._active_text_id = None
        mock_tv = mock.MagicMock()
        app.query_one = mock.MagicMock(return_value=mock_tv)
        return app, mock_tv

    def test_extracts_think_and_writes_both(self):
        app, mock_tv = self._app_with_fake_tv()
        app.route_agent_event("assistant_message", {
            "content": "<think>internal</think># Visible\n\nMarkdown content",
        })
        # append_thinking called with extracted think content
        mock_tv.append_thinking.assert_called_once_with("internal")
        # write_assistant_message called with stripped body
        mock_tv.write_assistant_message.assert_called_once_with(
            "# Visible\n\nMarkdown content"
        )

    def test_calls_write_assistant_message_when_no_think(self):
        app, mock_tv = self._app_with_fake_tv()
        app.route_agent_event("assistant_message", {"content": "answer"})
        mock_tv.append_thinking.assert_not_called()
        mock_tv.write_assistant_message.assert_called_once_with("answer")

    def test_does_not_call_finalize_method(self):
        """The old _finalize_assistant_message helper is gone."""
        app, mock_tv = self._app_with_fake_tv()
        app.route_agent_event("assistant_message", {"content": "x"})
        mock_tv.end_streaming.assert_not_called()

    def test_chinese_content_through_route(self):
        app, mock_tv = self._app_with_fake_tv()
        app.route_agent_event("assistant_message", {
            "content": "<think>推理</think># A股动量策略\n\n关键指标: 12.3%",
        })
        mock_tv.append_thinking.assert_called_once_with("推理")
        mock_tv.write_assistant_message.assert_called_once_with(
            "# A股动量策略\n\n关键指标: 12.3%"
        )


class TestStripSourceIsTextFilters:
    """Sanity: route_agent_event imports from text_filters, not inline regex."""

    def test_uses_text_filters_module(self):
        import inspect

        from agentnodes.interfaces.cli.tui import app as app_module
        src = inspect.getsource(app_module.ResearchApp.route_agent_event)
        route_src = inspect.getsource(app_module.ResearchApp._route_assistant_message)
        # text_delta path still uses strip_thinking_tags
        assert "strip_thinking_tags" in src
        # assistant_message path now uses extract_thinking_tags (in the
        # extracted _route_assistant_message helper)
        assert "extract_thinking_tags" in route_src
        assert "from agentnodes.interfaces.cli.tui.text_filters import" in src
