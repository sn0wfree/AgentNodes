"""GoalContinuationInjector final-JSON guard tests.

Root cause (docs/rootcause-goal-injection-maxiter.md): with an active
goal + pending criteria, every non-tool-call response was force-continued
until max_iterations — even when the model had just produced a complete
structured answer. Study rounds then degraded to the
"Reached max_iterations=…" placeholder and propagated {} downstream.

Three behaviours locked down:
1. complete JSON object  → guard returns False (stop proceeds)
2. non-JSON text         → legacy injection still fires (continuation)
3. no goal / disabled    → zero regression, returns False as before

The loop-level e2e effect is covered by AgentLoop integration in the
reproduction script inside the design doc; here we unit-test the injector.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from strategy_research.kernel.agent.context_injector import (
    GoalContinuationInjector,
)
from strategy_research.foundation.llm import LLMResponse

GOOD_JSON = json.dumps({
    "action": "optimize_param",
    "hypothesis": "increase top_n",
    "reason": "calmar headroom",
})


def _loop(session_id="study_x", enabled=True):
    loop = MagicMock()
    loop.enable_goal_injection = enabled
    loop.session_id = session_id
    return loop


def _snapshot_active_with_pending():
    return {
        "goal": {
            "goal_id": "g1",
            "status": "active",
            # format_goal_continuation_prompt renders these; missing keys
            # raise inside the injector's silent try/except -> returns False
            "objective": "tune params",
        },
        # shape mirrors GoalStore.get_current_snapshot rows: criterion_id
        # + status + text (format_goal_continuation_prompt needs `text`;
        # criterion_is_covered checks status vs snapshot["evidence"])
        "criteria": [
            {
                "criterion_id": f"c{i}",
                "status": "pending",
                "required": True,
                "text": f"criterion {i}",
            }
            for i in range(3)
        ],
        "evidence": [],
    }


@pytest.fixture
def active_goal():
    with patch(
        "strategy_research.kernel.agent.context_injector."
        "_get_goal_snapshot",
        return_value=_snapshot_active_with_pending(),
    ):
        yield


class TestFinalJsonGuard:
    def test_complete_json_object_is_final(self, active_goal):
        """A complete JSON dict response must NOT be force-continued."""
        resp = LLMResponse(content=GOOD_JSON, finish_reason="stop")
        r = GoalContinuationInjector().inject_post_response(
            _loop(), resp, [], MagicMock(), iteration=5,
        )
        assert r is False

    def test_non_json_text_still_continues(self, active_goal):
        """Legacy semantics preserved: prose answers get the nudge."""
        resp = LLMResponse(content="我再查一下数据…", finish_reason="stop")
        messages: list = []
        r = GoalContinuationInjector().inject_post_response(
            _loop(), resp, messages, MagicMock(), iteration=2,
        )
        assert r is True
        assert messages, "continuation prompt must be appended"

    def test_malformed_partial_json_still_continues(self, active_goal):
        """Half-emitted JSON keeps the continuation path."""
        resp = LLMResponse(content='{"action": "hold", "hyp', finish_reason="stop")
        r = GoalContinuationInjector().inject_post_response(
            _loop(), resp, [], MagicMock(), iteration=2,
        )
        assert r is True


class TestZeroRegression:
    def test_disabled_injection_untouched(self):
        loop = _loop(enabled=False)
        resp = LLMResponse(content="plain text", finish_reason="stop")
        assert GoalContinuationInjector().inject_post_response(
            loop, resp, [], MagicMock(), iteration=1,
        ) is False

    def test_no_session_id_untouched(self):
        loop = _loop(session_id=None)
        resp = LLMResponse(content="plain text", finish_reason="stop")
        assert GoalContinuationInjector().inject_post_response(
            loop, resp, [], MagicMock(), iteration=1,
        ) is False

    def test_no_goal_snapshot_untouched(self):
        loop = _loop()
        resp = LLMResponse(content="plain text", finish_reason="stop")
        with patch(
            "strategy_research.kernel.agent.context_injector."
            "_get_goal_snapshot",
            return_value=None,
        ):
            assert GoalContinuationInjector().inject_post_response(
                loop, resp, [], MagicMock(), iteration=1,
            ) is False
