"""P2-b+P2-e End-to-End tests.

Tests for:
- AgentLoop hook integration
- Cross-session memory recall via ContextBuilder
- CLI session show/search/delete commands
"""

from __future__ import annotations

import argparse
from unittest.mock import MagicMock, patch

import pytest

from strategy_research.kernel.agent.loop import AgentLoop
from strategy_research.kernel.agent.tools import ToolRegistry
from strategy_research.kernel.hooks.composite import AgentHook, CompositeHook
from strategy_research.kernel.hooks.context import AgentHookContext
from strategy_research.foundation.llm.config import LLMConfig

# ── Test Hooks ─────────────────────────────────────────────


class RecordingHook(AgentHook):
    """Hook that records all calls for verification."""

    name = "recording"

    def __init__(self):
        self.calls: list[tuple[str, list]] = []

    def before_run(self, ctx: AgentHookContext) -> None:
        self.calls.append(("before_run", [ctx.iteration]))

    def after_run(self, ctx: AgentHookContext, result) -> None:
        self.calls.append(("after_run", [ctx.iteration, result]))

    def before_iteration(self, ctx: AgentHookContext) -> None:
        self.calls.append(("before_iteration", [ctx.iteration]))

    def after_iteration(self, ctx: AgentHookContext) -> None:
        self.calls.append(("after_iteration", [ctx.iteration]))

    def before_execute_tools(self, ctx: AgentHookContext) -> None:
        self.calls.append(("before_execute_tools", [ctx.iteration]))

    def after_tool_executed(self, ctx, tool_call, result) -> None:
        self.calls.append(("after_tool_executed", [tool_call, result]))

    def on_tool_error(self, ctx, tool_call, error) -> None:
        self.calls.append(("on_tool_error", [tool_call, error]))

    def on_error(self, ctx, error) -> None:
        self.calls.append(("on_error", [error]))


# ── AgentLoop Hook Integration Tests ──────────────────────


class TestAgentLoopHookIntegration:
    """Test that AgentLoop correctly fires hook events."""

    def test_hooks_called_on_simple_run(self):
        hook = RecordingHook()
        composite = CompositeHook([hook])

        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.content = "done"
        mock_response.has_tool_calls.return_value = False
        mock_response.finish_reason = "stop"
        mock_client.chat.return_value = mock_response
        # stream_mode is default: stream() must yield nothing and
        # parse_response must return the canned response
        mock_client.stream.return_value = iter([])
        mock_client.parse_response.return_value = mock_response

        registry = ToolRegistry()
        config = LLMConfig(model="test", api_key="test")

        loop = AgentLoop(
            config=config,
            registry=registry,
            hooks=composite,
            max_iterations=3,
        )
        loop.client = mock_client

        loop.run('test task')

        call_names = [c[0] for c in hook.calls]
        assert "before_run" in call_names
        assert "after_run" in call_names
        assert "before_iteration" in call_names
        assert "after_iteration" in call_names

    def test_hooks_fired_per_iteration(self):
        hook = RecordingHook()
        composite = CompositeHook([hook])

        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.content = "done"
        mock_response.has_tool_calls.return_value = False
        mock_response.finish_reason = "stop"
        mock_client.chat.return_value = mock_response
        # stream_mode is default: stream() must yield nothing and
        # parse_response must return the canned response
        mock_client.stream.return_value = iter([])
        mock_client.parse_response.return_value = mock_response

        registry = ToolRegistry()
        config = LLMConfig(model="test", api_key="test")

        loop = AgentLoop(
            config=config,
            registry=registry,
            hooks=composite,
            max_iterations=5,
        )
        loop.client = mock_client

        loop.run('test task')

        iter_calls = [c for c in hook.calls if c[0] == "before_iteration"]
        assert len(iter_calls) == 1  # only 1 iteration before stop

    def test_no_hooks_no_crash(self):
        """AgentLoop works fine without hooks."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.content = "done"
        mock_response.has_tool_calls.return_value = False
        mock_response.finish_reason = "stop"
        mock_client.chat.return_value = mock_response
        # stream_mode is default: stream() must yield nothing and
        # parse_response must return the canned response
        mock_client.stream.return_value = iter([])
        mock_client.parse_response.return_value = mock_response

        registry = ToolRegistry()
        config = LLMConfig(model="test", api_key="test")

        loop = AgentLoop(
            config=config,
            registry=registry,
            hooks=None,
            max_iterations=3,
        )
        loop.client = mock_client

        result = loop.run("test task")
        assert result.success

    def test_hook_error_does_not_break_loop(self):
        """A failing hook should not crash the agent loop."""

        class BrokenHook(AgentHook):
            name = "broken"

            def before_iteration(self, ctx):
                raise RuntimeError("hook broken")

        composite = CompositeHook([BrokenHook()])

        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.content = "done"
        mock_response.has_tool_calls.return_value = False
        mock_response.finish_reason = "stop"
        mock_client.chat.return_value = mock_response
        # stream_mode is default: stream() must yield nothing and
        # parse_response must return the canned response
        mock_client.stream.return_value = iter([])
        mock_client.parse_response.return_value = mock_response

        registry = ToolRegistry()
        config = LLMConfig(model="test", api_key="test")

        loop = AgentLoop(
            config=config,
            registry=registry,
            hooks=composite,
            max_iterations=3,
        )
        loop.client = mock_client

        # Should not raise
        result = loop.run("test task")
        assert result.success


# ── ContextBuilder Cross-Session Memory Tests (removed) ────

# B4 Step 4-5 (2026-10-30): ``TestContextBuilderCrossSession`` went with the
# feature it tested. ContextBuilder used to accept a ``session_manager`` for
# cross-session recall, but no caller ever supplied one (measured: zero
# assembly points) and the manager itself was the retired legacy stack.
# PersistentMemory recall is still covered by test_context_builder.py.


# ── CLI Session Commands Tests ─────────────────────────────


class TestCLISessionCommands:
    """Test CLI session show/search/delete commands."""

    def test_session_show_help(self):
        from strategy_research.cli import main
        with patch("sys.argv", ["quantnodes-research", "session", "show", "--help"]):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0

    def test_session_search_help(self):
        from strategy_research.cli import main
        with patch("sys.argv", ["quantnodes-research", "session", "search", "--help"]):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0

    def test_session_delete_help(self):
        from strategy_research.cli import main
        with patch("sys.argv", ["quantnodes-research", "session", "delete", "--help"]):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0

    def test_session_show_nonexistent(self):
        from strategy_research.cli import cmd_session_show
        args = argparse.Namespace(session_id="nonexistent_123")
        result = cmd_session_show(args)
        assert result == 1

    def test_session_delete_nonexistent(self):
        from strategy_research.cli import cmd_session_delete
        args = argparse.Namespace(session_id="nonexistent_123")
        result = cmd_session_delete(args)
        assert result == 1

    def test_session_search_empty(self):
        from strategy_research.cli import cmd_session_search
        args = argparse.Namespace(query="xyz_nonexistent_query_12345", limit=10)
        result = cmd_session_search(args)
        assert result == 0
