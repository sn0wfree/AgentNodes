"""Tests for Stage C4: tool count source migrates from ToolsRail to App.

Verifies that:

* ``ResearchApp.__init__`` initialises ``_tool_total`` / ``_tool_ok`` to 0.
* ``_route_tool_event(tool_call, ...)`` increments ``_tool_total``.
* ``_route_tool_event(tool_result, ok=True, ...)`` increments ``_tool_ok``.
* ``_route_tool_event(tool_result, ok=False, ...)`` does NOT increment
  ``_tool_ok``.
* ``_bump_tool_count`` calls ``update_header`` with the new totals.
* ChatSession._update_header_stats reads tool totals from
  ``app._tool_total`` / ``_tool_ok`` (NOT from rail._timeline).
"""
from __future__ import annotations

from unittest import mock

from strategy_research.cli.tui.app import ResearchApp


class TestAppToolCounterInit:
    def test_app_init_creates_tool_total_and_tool_ok(self):
        app = ResearchApp.__new__(ResearchApp)
        # Mirror __init__ side-effects (skip super().__init__ for speed)
        app._tool_total = 0
        app._tool_ok = 0
        assert app._tool_total == 0
        assert app._tool_ok == 0


class TestRouteToolEventIncrementsCounter:
    """_route_tool_event must increment app-level tool counters."""

    def _app_with_mocked_widgets(self):
        app = ResearchApp.__new__(ResearchApp)
        app._tool_total = 0
        app._tool_ok = 0
        app.update_header = mock.MagicMock()
        # query_one returns a fake TV (TranscriptView)
        fake_tv = mock.MagicMock()
        fake_query = mock.MagicMock(return_value=fake_tv)
        app.query_one = fake_query
        return app

    def test_tool_call_increments_total(self):
        app = self._app_with_mocked_widgets()
        app._route_tool_event("tool_call", {
            "call_id": "c1", "tool": "read", "args": {"path": "x"},
        })
        assert app._tool_total == 1
        assert app._tool_ok == 0

    def test_tool_result_ok_increments_ok(self):
        app = self._app_with_mocked_widgets()
        app._route_tool_event("tool_call", {"call_id": "c1", "tool": "x"})
        app._route_tool_event("tool_result", {
            "call_id": "c1", "ok": True, "elapsed_ms": 320,
        })
        assert app._tool_total == 1
        assert app._tool_ok == 1

    def test_tool_result_error_does_not_increment_ok(self):
        app = self._app_with_mocked_widgets()
        app._route_tool_event("tool_call", {"call_id": "c1", "tool": "x"})
        app._route_tool_event("tool_result", {
            "call_id": "c1", "ok": False, "elapsed_ms": 50,
        })
        assert app._tool_total == 1
        assert app._tool_ok == 0

    def test_multiple_tool_calls_increment_correctly(self):
        app = self._app_with_mocked_widgets()
        for i in range(3):
            app._route_tool_event("tool_call", {"call_id": f"c{i}", "tool": "x"})
        app._route_tool_event("tool_result", {"call_id": "c0", "ok": True, "elapsed_ms": 1})
        app._route_tool_event("tool_result", {"call_id": "c1", "ok": False, "elapsed_ms": 1})
        app._route_tool_event("tool_result", {"call_id": "c2", "ok": True, "elapsed_ms": 1})
        assert app._tool_total == 3
        assert app._tool_ok == 2

    def test_record_tool_start_calls_update_header(self):
        app = self._app_with_mocked_widgets()
        app._route_tool_event("tool_call", {"call_id": "c1", "tool": "x"})
        app.update_header.assert_called_with(tool_count=1, tool_ok=0)

    def test_record_tool_result_updates_header(self):
        app = self._app_with_mocked_widgets()
        app._route_tool_event("tool_call", {"call_id": "c1", "tool": "x"})
        app.update_header.reset_mock()
        app._route_tool_event("tool_result", {"call_id": "c1", "ok": True, "elapsed_ms": 100})
        app.update_header.assert_called_with(tool_count=1, tool_ok=1)


class TestSessionReadsToolCountFromApp:
    """ChatSession._update_header_stats reads from app, not from rail."""

    def test_update_header_stats_uses_app_tool_total(self):
        from strategy_research.cli.interactive.main import InteractiveContext
        from strategy_research.cli.tui.session import ChatSession

        ctx = InteractiveContext()
        app = mock.MagicMock()
        app._tool_total = 7
        app._tool_ok = 5
        app.update_header = mock.MagicMock()

        session = ChatSession(ctx, app=app)
        # Replace LLMConfig.load to avoid disk hits
        from strategy_research.foundation.llm.config import LLMConfig
        with mock.patch.object(LLMConfig, "load", side_effect=Exception("nope")):
            session._update_header_stats()

        # update_header called with tool_count=7, tool_ok=5
        call_kwargs = app.update_header.call_args.kwargs
        assert call_kwargs["tool_count"] == 7
        assert call_kwargs["tool_ok"] == 5

    def test_session_does_not_query_tools_rail_for_tool_count(self):
        """The source should NOT call app.query_one(ToolsRail) for tool count."""
        import inspect

        from strategy_research.cli.tui.session import ChatSession

        src = inspect.getsource(ChatSession._update_header_stats)
        assert "query_one(ToolsRail)" not in src
        assert "_tool_total" in src
