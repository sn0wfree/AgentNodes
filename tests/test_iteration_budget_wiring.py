"""Tests for chat iteration-budget wiring.

Verifies:
- SessionService retains a bounded iteration budget for legacy callers.
- Gateway receives its build budget from singleton configuration; plan
  requests carry a one-iteration per-turn override.
- send_message defaults to a bounded budget (50) rather than
  9999999999, so a failing tool loop cannot grow the prompt unboundedly.
- _run_with_agent forwards max_iterations to AgentLoop unchanged.
"""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from agentnodes.foundation.llm.config import LLMConfig


class TestSendMessageDefault(unittest.TestCase):
    """send_message signature must default to a bounded budget."""

    def test_default_is_bounded_not_unbounded(self):
        import inspect

        from agentnodes.interfaces.api.session.service import SessionService

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


class TestGatewayTurnBudget(unittest.TestCase):
    """Gateway requests carry only mode-specific iteration overrides."""

    @staticmethod
    def _run_gateway_submit(mode):
        from types import SimpleNamespace
        from agentnodes.interfaces.api.routers import chat as chat_router
        from agentnodes.kernel.orchestration import Receipt

        entry = SimpleNamespace(submit=MagicMock(return_value=Receipt.ok("s1")))
        gateway = SimpleNamespace(
            entry=entry,
            consumer_running=MagicMock(return_value=False),
            add_sink=MagicMock(return_value=lambda: None),
            ensure_sink=MagicMock(return_value=False),
        )
        service = SimpleNamespace(
            load_history=MagicMock(return_value=[]),
            update_attempt_status=MagicMock(return_value=None),
        )

        async def submit():
            with (
                patch.object(chat_router, "_get_gateway", return_value=gateway),
                patch.object(chat_router, "_get_session_service", return_value=service),
                patch.object(chat_router, "_build_llm_config", return_value=None),
            ):
                await chat_router._send_via_gateway(
                    "s1", "hello", mode=mode, receipt_only=True,
                )
            request = entry.submit.call_args.args[0]
            return request.payload.get("max_iterations")

        import asyncio

        return asyncio.run(submit())

    def test_build_uses_gateway_default_budget(self):
        # Build mode uses the Gateway's singleton-configured budget.
        assert self._run_gateway_submit("build") is None

    def test_plan_overrides_budget_with_one_iteration(self):
        assert self._run_gateway_submit("plan") == 1


class TestRunWithAgentForwardsBudget(unittest.TestCase):
    """max_iterations must reach AgentLoop unchanged."""

    def test_run_with_agent_passes_max_iterations(self):
        from agentnodes.interfaces.api.session.service import SessionService

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
                "agentnodes.kernel.agent.factory.chat_loop.AgentLoop",
                lambda **kw: type(
                    "AL", (), {"arun": fake_agent_arun, "max_iterations": kw.get("max_iterations")}
                )(),
            ),
            patch("agentnodes.kernel.agent.builtin_tools.build_default_registry", return_value=None),
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
