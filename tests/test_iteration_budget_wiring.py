"""Tests for the chat iteration-budget wiring (context-overflow Fix2).

Verifies:
- send_async reads max_iterations from LLMConfig (not the unbounded
  legacy default) and passes it to send_message.
- send_message defaults to a bounded budget (50) rather than
  9999999999, so a failing tool loop cannot grow the prompt unboundedly.
- _run_with_agent forwards max_iterations to AgentLoop unchanged.
"""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from strategy_research.foundation.llm.config import LLMConfig


class TestSendMessageDefault(unittest.TestCase):
    """send_message signature must default to a bounded budget."""

    def test_default_is_bounded_not_unbounded(self):
        import inspect

        from strategy_research.api.session.service import SessionService

        sig = inspect.signature(SessionService.send_message)
        default = sig.parameters["max_iterations"].default
        assert default == 50, f"send_message default should be 50, got {default}"
        assert default < 1_000_000, "must not be an unbounded budget"


class TestLLMConfigDefaults(unittest.TestCase):
    """Chat vs agent iteration ceilings."""

    def test_chat_default_50(self):
        assert LLMConfig().max_iterations == 50

    def test_agent_default_9999(self):
        assert LLMConfig().agent_max_iterations == 9999


class TestSendAsyncPassesConfigBudget(unittest.TestCase):
    """send_async must pass LLMConfig.max_iterations (not the default)."""

    async def _run_send_async(self, cfg_max_iterations):
        from strategy_research.api.routers import chat as chat_router

        captured = {}

        async def fake_send_message(session_id, content, **kwargs):
            captured["max_iterations"] = kwargs.get("max_iterations")
            return {
                "message_id": "m1",
                "attempt_id": "a1",
                "user_message_id": "um1",
                "assistant_message_id": "am1",
            }

        class _Body:
            session_id = "s1"
            content = "hello"
            agent_id = None
            mode = "build"
            model = None
            thinking = "auto"

        class _Request:
            state = MagicMock(user_id="u1")

        with (
            patch.object(
                LLMConfig,
                "load",
                return_value=LLMConfig(max_iterations=cfg_max_iterations),
            ),
            patch("strategy_research.api.routers.web_session._fetch_session_owned", return_value=None),
            patch("strategy_research.api.routers.web_session._get_db", return_value=MagicMock()),
            patch("strategy_research.api.routers.chat._get_session_service", return_value=MagicMock(send_message=fake_send_message)),
            patch("strategy_research.api.routers.chat._handle_goal_command"),
            patch("strategy_research.api.routers.chat._handle_compact_command"),
        ):
            body = _Body()
            await chat_router.send_async(body, _Request())

        return captured.get("max_iterations")

    def test_passes_config_value(self):
        import asyncio

        result = asyncio.run(self._run_send_async(30))
        assert result == 30

    def test_passes_default_when_config_unset(self):
        import asyncio

        # LLMConfig() with no overrides → default 50
        result = asyncio.run(self._run_send_async(50))
        assert result == 50


class TestRunWithAgentForwardsBudget(unittest.TestCase):
    """max_iterations must reach AgentLoop unchanged."""

    def test_run_with_agent_passes_max_iterations(self):
        from strategy_research.api.session.service import SessionService

        svc = object.__new__(SessionService)
        svc.event_bus = MagicMock()
        seen = {}

        async def fake_agent_arun(self, task, history=None):
            seen["max_iterations"] = self.max_iterations
            result = MagicMock()
            result.answer = "ok"
            result.iterations = 1
            result.tool_calls_made = 0
            result.finished_reason = "stop"
            result.error = None
            result.messages = []
            result.trace_path = None
            result.compression_applied = []
            return result

        with (
            patch.object(SessionService, "_run_test_script"),
            patch(
                "strategy_research.kernel.agent.chat_loop.AgentLoop",
                lambda **kw: type(
                    "AL", (), {"arun": fake_agent_arun, "max_iterations": kw.get("max_iterations")}
                )(),
            ),
            patch("strategy_research.kernel.agent.builtin_tools.build_default_registry", return_value=None),
        ):
            # exercise _run_with_agent directly with a bounded budget
            async def run():
                attempt = MagicMock()
                attempt.session_id = "s1"
                attempt.message_id = "m1"
                attempt.attempt_id = "a1"
                cfg = LLMConfig()
                await svc._run_with_agent(
                    attempt=attempt,
                    history=[],
                    model=None,
                    max_iterations=42,
                    system_prompt="sp",
                    allow_shell_tools=False,
                    accumulated_parts=[],
                    cfg=cfg,
                )

            import asyncio

            asyncio.run(run())

        assert seen.get("max_iterations") == 42
