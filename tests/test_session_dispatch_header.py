"""Tests for ChatSession.dispatch header-stats refresh timing.

After Stage A+D changes:
1. ``_update_header_stats`` is called once BEFORE arun() (user msg present)
2. ``_update_header_stats`` is called once AFTER arun() (assistant msg
   present too) so the header reflects the true state.

Without (2), the header would show stale counts (e.g. "0 msg") that
do not include the assistant's response.
"""
from __future__ import annotations

import asyncio
from unittest import mock

from agentnodes.interfaces.cli.interactive.main import InteractiveContext
from agentnodes.interfaces.cli.tui.session import ChatSession


class TestDispatchRefreshesHeaderAfterArun:
    def _session(self) -> ChatSession:
        ctx = InteractiveContext()
        app = mock.MagicMock()
        llm_client = mock.MagicMock()  # Required for chat path
        # Session needs update_header_stats; track its calls
        ctx.history = []
        ctx.session_id = "test-sid"
        return ChatSession(ctx, app=app, llm_client=llm_client)

    def test_header_stats_called_twice(self):
        """dispatch calls _update_header_stats before AND after arun."""
        session = self._session()
        call_log: list[str] = []

        def fake_update_header_stats():
            call_log.append("update_header_stats")

        session._update_header_stats = fake_update_header_stats

        async def fake_run_agent_loop(task):
            # Append the assistant message during arun (real flow)
            session.ctx.history.append({"role": "assistant", "content": "answer"})

        session._run_agent_loop = fake_run_agent_loop

        # Stub process_turn to return rc=0
        with mock.patch.object(
            session, "_dispatch_with_capture", return_value=(0, "")
        ):
            asyncio.run(session.dispatch("hi"))

        # _update_header_stats must have been called twice
        assert call_log.count("update_header_stats") == 2, (
            f"Expected 2 calls, got {call_log.count('update_header_stats')}"
        )

    def test_header_stats_called_after_arun(self):
        """The second _update_header_stats call happens AFTER the LLM turn.

        2026-11-05：CLI 已在 P5-1c ④ 改为唯一入口 Gateway，
        ``_run_agent_loop`` 更名为 ``_run_via_gateway``。
        """
        session = self._session()
        call_order: list[str] = []

        session._update_header_stats = lambda: call_order.append("header")

        async def fake_run_via_gateway(task):
            session.ctx.history.append({"role": "assistant", "content": "answer"})
            call_order.append("arun_end")

        session._run_via_gateway = fake_run_via_gateway

        with mock.patch.object(
            session, "_dispatch_with_capture", return_value=(0, "")
        ):
            asyncio.run(session.dispatch("hi"))

        # arun_end comes before second header call
        idx_arun = call_order.index("arun_end")
        # Find the second header occurrence
        header_indices = [i for i, c in enumerate(call_order) if c == "header"]
        assert len(header_indices) == 2
        assert header_indices[1] > idx_arun, (
            f"Second _update_header_stats call must happen AFTER arun. Order: {call_order}"
        )

    def test_source_includes_post_arun_call(self):
        """Source-level: dispatch() must call _update_header_stats after _run_agent_loop."""
        import inspect

        from agentnodes.interfaces.cli.tui.session import ChatSession

        src = inspect.getsource(ChatSession.dispatch)
        # Find the dispatch method, look for the second call to update_header_stats
        # Both calls must exist
        assert "self._update_header_stats()" in src
        # Count occurrences
        assert src.count("self._update_header_stats()") >= 2, (
            f"dispatch() must call _update_header_stats at least twice. "
            f"Found {src.count('self._update_header_stats()')} calls."
        )
