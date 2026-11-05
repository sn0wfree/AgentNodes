"""Tests for Stage 2 — TUI event routing via AgentLoop.

Verifies that ``app.route_agent_event`` correctly dispatches AgentLoop
events to the right widgets, and that ``ChatSession._run_agent_loop``
wires the callback properly.
"""
from __future__ import annotations

from typing import Any
from unittest import mock

import pytest

from agentnodes.interfaces.cli.tui.app import ResearchApp


@pytest.fixture(autouse=True)
def _isolate_session_db(tmp_path, monkeypatch):
    """Isolate the unified session DB (TUI flow persists to cwd by default)."""
    monkeypatch.setenv("SR_WORKSPACE_PATH", str(tmp_path))


class _CaptureSink:
    """Collect every event passed to on_event."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    def __call__(self, event_type: str, data: dict[str, Any]) -> None:
        self.events.append((event_type, dict(data)))


class TestRouteAgentEvent:
    def _app(self) -> ResearchApp:
        return ResearchApp(skip_resume=True)

    def test_text_delta_calls_update_streaming_delta(self):
        app = self._app()
        with mock.patch.object(app, "update_streaming_delta") as m:
            app.route_agent_event("text_delta", {"text": "hi"})
        m.assert_called_once_with("hi")

    def test_tool_call_dispatches_to_transcript(self):
        """Stage C: tool_call goes inline to TranscriptView (not rail)."""
        app = self._app()
        mock_tv = mock.MagicMock()
        with mock.patch.object(app, "query_one", return_value=mock_tv):
            app.route_agent_event(
                "tool_call",
                {"tool": "read", "args": {"path": "/x"}, "call_id": "c1"},
            )
        # TranscriptView.append_tool_call called
        mock_tv.append_tool_call.assert_called_once()
        call_id, tool, args = mock_tv.append_tool_call.call_args.args
        assert call_id == "c1"
        assert tool == "read"
        assert args == {"path": "/x"}

    def test_tool_result_dispatches_to_transcript(self):
        app = self._app()
        mock_tv = mock.MagicMock()
        with mock.patch.object(app, "query_one", return_value=mock_tv):
            app.route_agent_event(
                "tool_result",
                {
                    "tool": "read",
                    "call_id": "c1",
                    "status": "ok",
                    "ok": True,
                    "elapsed_ms": 100,
                    "preview": "data",
                },
            )
        mock_tv.update_tool_result.assert_called_once_with("c1", True, 100)

    def test_tool_progress_is_silent(self):
        """Stage C: tool_progress is a no-op (reserved for future inline use)."""
        app = self._app()
        mock_tv = mock.MagicMock()
        with mock.patch.object(app, "write_rail") as wr, \
             mock.patch.object(app, "query_one", return_value=mock_tv):
            app.route_agent_event(
                "tool_progress",
                {"tool": "dl", "call_id": "c1", "message": "downloading"},
            )
        wr.assert_not_called()
        # No TranscriptView method called for progress
        mock_tv.append_tool_call.assert_not_called()
        mock_tv.update_tool_result.assert_not_called()

    def test_tool_heartbeat_is_silent(self):
        app = self._app()
        mock_tv = mock.MagicMock()
        with mock.patch.object(app, "write_rail") as wr, \
             mock.patch.object(app, "query_one", return_value=mock_tv):
            app.route_agent_event(
                "tool_heartbeat",
                {"tool": "t", "call_id": "c1", "elapsed_s": 5.2},
            )
        wr.assert_not_called()
        mock_tv.append_tool_call.assert_not_called()
        mock_tv.update_tool_result.assert_not_called()

    def test_compact_dispatches_to_write_rail(self):
        app = self._app()
        with mock.patch.object(app, "write_rail") as m:
            app.route_agent_event(
                "compact",
                {"layer": "microcompact", "iteration": 1, "summary": "ctx"},
            )
        m.assert_called_once()

    def test_iter_start_calls_start_thinking(self):
        app = self._app()
        with mock.patch.object(app, "start_thinking") as m:
            app.route_agent_event("iter_start", {"iteration": 1})
        m.assert_called_once()

    def test_iter_end_calls_stop_thinking(self):
        app = self._app()
        with mock.patch.object(app, "stop_thinking") as m:
            app.route_agent_event("iter_end", {"iteration": 1})
        m.assert_called_once()

    def test_iter_end_calls_append_done(self):
        app = self._app()
        mock_tv = mock.MagicMock()
        with mock.patch.object(app, "stop_thinking"), \
             mock.patch.object(app, "query_one", return_value=mock_tv):
            app.route_agent_event("iter_end", {"iteration": 1})
        mock_tv.append_done.assert_called_once()

    def test_thinking_start_calls_start_thinking(self):
        app = self._app()
        with mock.patch.object(app, "start_thinking") as m:
            app.route_agent_event("thinking_start", {})
        m.assert_called_once()

    def test_thinking_end_calls_stop_thinking(self):
        app = self._app()
        with mock.patch.object(app, "stop_thinking") as m:
            app.route_agent_event("thinking_end", {})
        m.assert_called_once()

    def test_llm_usage_updates_header_token_count(self):
        app = self._app()
        with mock.patch.object(app, "update_header") as m:
            app.route_agent_event(
                "llm_usage", {"output_tokens": 500}
            )
        m.assert_called_once_with(token_used=500)

    def test_error_posts_message_to_transcript(self):
        app = self._app()
        with mock.patch.object(app, "query_one") as q:
            tv = mock.MagicMock()
            q.return_value = tv
            app.route_agent_event(
                "error", {"message": "rate limit", "fatal": True}
            )
        # Verify a WriteTranscript message was posted with the error
        tv.post_message.assert_called_once()
        msg = tv.post_message.call_args.args[0]
        assert "rate limit" in str(msg.content)

    def test_thinking_done_is_silent(self):
        """thinking_done is a transition marker — no widget dispatch."""
        app = self._app()
        with mock.patch.object(app, "start_thinking") as s, \
             mock.patch.object(app, "stop_thinking") as p, \
             mock.patch.object(app, "write_rail") as w, \
             mock.patch.object(app, "update_streaming_delta") as u:
            app.route_agent_event("thinking_done", {})
        s.assert_not_called()
        p.assert_not_called()
        w.assert_not_called()
        u.assert_not_called()


class TestUpdateStreamingDelta:
    def test_starts_streaming_if_idle(self):
        """First delta after no active session begins a new streaming line."""
        from agentnodes.interfaces.cli.tui.widgets.transcript import TranscriptView

        app = ResearchApp(skip_resume=True)
        tv = mock.MagicMock(spec=TranscriptView)
        tv._streamer = None
        tv._stream_baseline = 0
        # Simulate: begin_streaming creates a new streamer
        new_streamer = mock.MagicMock()
        tv.begin_streaming.side_effect = lambda: setattr(tv, "_streamer", new_streamer)
        with mock.patch.object(app, "query_one", return_value=tv):
            app.update_streaming_delta("hello")
        # begin_streaming should be invoked since no active streamer
        tv.begin_streaming.assert_called_once()
        new_streamer.append_delta.assert_called_once_with("hello")
        # Write rendered text
        tv.write.assert_called()

    def test_appends_to_existing_streamer(self):
        """Subsequent deltas append to the active streamer."""
        from agentnodes.interfaces.cli.tui.widgets.streaming_text import StreamingText

        app = ResearchApp(skip_resume=True)
        tv = mock.MagicMock()
        st = mock.MagicMock(spec=StreamingText)
        tv._streamer = st
        tv._stream_baseline = 0
        with mock.patch.object(app, "query_one", return_value=tv):
            app.update_streaming_delta("more")
        st.append_delta.assert_called_once_with("more")
        # No new streaming session
        tv.begin_streaming.assert_not_called()


class TestEventTypeMatrix:
    """All expected event types should be handled (no AttributeError)."""

    EVENT_TYPES = [
        "iter_start",
        "iter_end",
        "thinking_start",
        "thinking_done",
        "thinking_end",
        "text_delta",
        "llm_usage",
        "tool_call",
        "tool_result",
        "tool_progress",
        "tool_heartbeat",
        "compact",
        "error",
    ]

    def test_all_event_types_handled_gracefully(self):
        app = ResearchApp(skip_resume=True)
        for et in self.EVENT_TYPES:
            try:
                app.route_agent_event(et, {})
            except Exception as exc:
                pytest.fail(
                    f"route_agent_event crashed on {et!r}: {type(exc).__name__}: {exc}"
                )
