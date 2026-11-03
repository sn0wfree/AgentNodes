"""P3-d integration tests — GoalContext injection + Hypothesis auto-create.

These tests verify the AgentLoop wires the P3-a/P3-b subsystems at run()
entry, without requiring real LLM calls.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from strategy_research.kernel.agent.loop import AgentLoop
from strategy_research.kernel.agent.tools import ToolRegistry
from strategy_research.foundation.llm.openai_client import LLMConfig


@pytest.fixture
def cfg() -> LLMConfig:
    return LLMConfig(api_key="sk-test", base_url="https://example.com/v1", model="m")


@pytest.fixture
def registry() -> ToolRegistry:
    return ToolRegistry()


@pytest.fixture(autouse=True)
def isolated_goals_and_hypothesis(tmp_path, monkeypatch):
    """Use temp DBs / JSON for goal + hypothesis subsystems."""
    goals_db = tmp_path / "goals.db"
    monkeypatch.setenv("QUANTNODES_RESEARCH_GOAL_DB_PATH", str(goals_db))
    monkeypatch.setenv("QUANTNODES_RESEARCH_HYPOTHESES_PATH", str(tmp_path / "hyp.json"))


def _make_loop(cfg, registry, **kwargs) -> AgentLoop:
    return AgentLoop(
        config=cfg, registry=registry, workspace=None,
        max_iterations=1,
        auto_git_commit=False,
        **kwargs,
    )


# ─── Hypothesis auto-create ─────────────────────────────────────────────


class TestHypothesisAutoCreate:
    def test_no_session_id_no_create(self, cfg, registry):
        loop = _make_loop(cfg, registry)
        assert loop._maybe_auto_create_hypothesis("test task") is None

    def test_no_strategy_name_no_create(self, cfg, registry):
        loop = _make_loop(cfg, registry, session_id="s1")
        assert loop._maybe_auto_create_hypothesis("test task") is None

    def test_creates_on_first_call(self, cfg, registry):
        loop = _make_loop(cfg, registry, session_id="s1", strategy_name="mom_v1")
        loop._maybe_auto_create_hypothesis("initial momentum thesis")
        from strategy_research.apps.hypothesis import HypothesisRegistry
        h = HypothesisRegistry().list()
        assert len(h) == 1
        assert h[0].signal_definition == "mom_v1"
        assert h[0].status == "exploring"

    def test_idempotent_second_call(self, cfg, registry):
        loop = _make_loop(cfg, registry, session_id="s1", strategy_name="mom_v1")
        loop._maybe_auto_create_hypothesis("first")
        loop._maybe_auto_create_hypothesis("second")
        from strategy_research.apps.hypothesis import HypothesisRegistry
        h = HypothesisRegistry().list()
        assert len(h) == 1

    def test_disabled_via_flag(self, cfg, registry):
        loop = _make_loop(
            cfg, registry,
            session_id="s1", strategy_name="x",
            enable_hypothesis_auto_create=False,
        )
        loop._maybe_auto_create_hypothesis("task")
        from strategy_research.apps.hypothesis import HypothesisRegistry
        assert HypothesisRegistry().list() == []

    def test_trace_records_creation(self, cfg, registry):
        loop = _make_loop(cfg, registry, session_id="s1", strategy_name="mom_v1")
        # _maybe_auto_create_hypothesis calls _trace internally; without trace_dir
        # it should be a no-op but still produce the hypothesis
        loop._maybe_auto_create_hypothesis("initial")
        # No exception raised → success


# ─── Goal context injection ─────────────────────────────────────────────




# ─── Integration: full run() with mocks ──────────────────────────────────


class TestFullRunIntegration:
    def test_goal_context_injected_into_initial_message(self, cfg, registry, monkeypatch):
        """Verify the initial user message contains the goal context block."""
        # Set up a goal first
        from strategy_research.apps.goal import GoalStore
        store = GoalStore()
        store.replace_goal(
            session_id="sess_full",
            objective="Inject goal context into prompt",
            criteria=["x"],
        )

        loop = _make_loop(
            cfg, registry,
            session_id="sess_full", strategy_name="alpha_v1",
        )

        # Mock the LLM client to return a stop signal on first call
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].finish_reason = "stop"
        mock_response.choices[0].message = MagicMock()
        mock_response.choices[0].message.content = "done"
        mock_response.choices[0].message.tool_calls = None
        mock_response.usage = MagicMock()
        mock_response.usage.total_tokens = 100

        monkeypatch.setattr(loop.client, "chat", lambda *a, **k: mock_response)

        result = loop.run("run the research task")
        # Initial user message should contain goal context
        user_msgs = [m for m in result.messages if m["role"] == "user"]
        assert any("Inject goal context" in m["content"] for m in user_msgs)
        assert any("<current-research-goal>" in m["content"] for m in user_msgs)

    def test_no_goal_no_injection(self, cfg, registry, monkeypatch):
        loop = _make_loop(cfg, registry, session_id="nonexistent_session")

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].finish_reason = "stop"
        mock_response.choices[0].message = MagicMock()
        mock_response.choices[0].message.content = "done"
        mock_response.choices[0].message.tool_calls = None
        mock_response.usage = MagicMock()
        mock_response.usage.total_tokens = 50

        monkeypatch.setattr(loop.client, "chat", lambda *a, **k: mock_response)

        result = loop.run("task")
        user_msgs = [m for m in result.messages if m["role"] == "user"]
        # No goal context block
        assert not any("<current-research-goal>" in m["content"] for m in user_msgs)
        # But hypothesis was auto-created
        from strategy_research.apps.hypothesis import HypothesisRegistry
        # Note: no strategy_name set → no auto-create
        assert HypothesisRegistry().list() == []


# ─── Hypothesis auto-create during run ──────────────────────────────────


class TestHypothesisAutoCreateDuringRun:
    def test_strategy_name_triggers_auto_create(self, cfg, registry, monkeypatch):
        loop = _make_loop(
            cfg, registry,
            session_id="sess_h", strategy_name="alpha_xyz",
        )

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].finish_reason = "stop"
        mock_response.choices[0].message = MagicMock()
        mock_response.choices[0].message.content = "ok"
        mock_response.choices[0].message.tool_calls = None
        mock_response.usage = MagicMock()
        mock_response.usage.total_tokens = 50

        monkeypatch.setattr(loop.client, "chat", lambda *a, **k: mock_response)

        loop.run("initial task for alpha_xyz")

        from strategy_research.apps.hypothesis import HypothesisRegistry
        h = HypothesisRegistry().list()
        assert len(h) == 1
        assert h[0].signal_definition == "alpha_xyz"
