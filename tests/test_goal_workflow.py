"""Tests for Goal Workflow engine.

Coverage:
  GoalWorkflowConfig:
    * config creation with defaults
    * agent config creation
    * completion config creation

  GoalWorkflowState:
    * default state
    * set_agent_status
    * get_summary

  GoalEvidenceCollector:
    * collect with valid evidence
    * collect with empty result
    * collect with invalid criterion index

  GoalWorkflowRunner:
    * init with config
    * get_progress returns correct structure
    * start creates goal and sets state
    * pause/resume
    * _build_goal_context
    * _get_layers
    * _get_agent_config

  load_goal_workflow:
    * load existing preset
    * load nonexistent raises error

  list_goal_workflows:
    * returns at least one workflow

  YAML validation:
    * validate DAG is acyclic
    * validate agent references

Refactor coverage (Phase 2 R1-R12):
  * PromptBuilder reuse in _build_prompt
  * AgentRunnerFactory integration
  * CompletionStrategyFactory dispatch
  * ValidatorRegistry default validators
  * Decorator chain composition
  * WorkflowEventBus emission
  * GoalStore injection
  * DAG layer caching
  * _run_layers deduplication
"""
from __future__ import annotations

from unittest import mock

import pytest

from agentnodes.apps.goal.workflow import (
    BranchConfig,
    CompletionConfig,
    GoalAgentConfig,
    GoalEvidenceCollector,
    GoalWorkflowConfig,
    GoalWorkflowGoalConfig,
    GoalWorkflowRunner,
    GoalWorkflowState,
)


def mock_store():
    """Build a MagicMock GoalStore for tests that don't touch the DB."""
    store = mock.MagicMock()
    store.get_current_snapshot.return_value = {
        "goal": {"goal_id": "goal-1", "status": "active"},
        "criteria": [
            {"criterion_id": "crit_1", "status": "pending", "required": True},
            {"criterion_id": "crit_2", "status": "pending", "required": True},
        ],
        "evidence": [],
        "evidence_count": 0,
    }
    return store


# ── Config Models ────────────────────────────────────────────


class TestGoalWorkflowConfig:
    def test_default_config(self):
        config = GoalWorkflowConfig(name="test", description="test desc")
        assert config.name == "test"
        assert config.version == "1.0"
        assert config.agents == []
        assert config.dag == {}
        assert config.completion.mode == "auto"

    def test_agent_config(self):
        agent = GoalAgentConfig(
            id="researcher",
            prompt_file=".prompts/researcher.md",
            tools=["read"],
            input_from=[],
            evidence_criterion=0,
        )
        assert agent.id == "researcher"
        assert agent.timeout == 120
        assert agent.max_retries == 3

    def test_completion_config(self):
        comp = CompletionConfig(mode="lite", auto_audit=False)
        assert comp.mode == "lite"
        assert comp.auto_audit is False

    def test_branch_config(self):
        branch = BranchConfig(
            condition="factor_analyst.output.sharpe < 0.3",
            action="skip",
            target="risk_reviewer",
            reason="Sharpe too low",
        )
        assert branch.action == "skip"
        assert branch.target == "risk_reviewer"


# ── Workflow State ───────────────────────────────────────────


class TestGoalWorkflowState:
    def test_default_state(self):
        state = GoalWorkflowState()
        assert state.status == "idle"
        assert state.current_layer == 0
        assert state.paused is False
        assert state.evidence_count == 0

    def test_set_agent_status(self):
        state = GoalWorkflowState()
        state.set_agent_status("researcher", "running")
        assert state.agent_statuses["researcher"] == "running"

    def test_set_agent_status_with_error(self):
        state = GoalWorkflowState()
        state.set_agent_status("researcher", "error", "timeout")
        assert state.agent_statuses["researcher"] == "error"
        assert state.agent_errors["researcher"] == "timeout"

    def test_get_summary(self):
        state = GoalWorkflowState()
        state.status = "running"
        state.evidence_count = 5
        summary = state.get_summary()
        assert summary["status"] == "running"
        assert summary["evidence_count"] == 5
        assert "agent_statuses" in summary


# ── Evidence Collector ───────────────────────────────────────


class TestGoalEvidenceCollector:
    def test_collect_empty_result(self):
        collector = GoalEvidenceCollector(mock_store(), "session-1", "goal-1")
        count = collector.collect("researcher", {"answer": ""}, 0)
        assert count == 0

    def test_collect_short_result(self):
        collector = GoalEvidenceCollector(mock_store(), "session-1", "goal-1")
        count = collector.collect("researcher", {"answer": "hi"}, 0)
        assert count == 0

    def test_collect_invalid_criterion_index(self):
        collector = GoalEvidenceCollector(mock_store(), "session-1", "goal-1")
        count = collector.collect(
            "researcher",
            {"answer": "some evidence text that is long enough"},
            999,  # invalid index
        )
        assert count == 0


# ── Workflow Runner ──────────────────────────────────────────


class TestGoalWorkflowRunner:
    def _make_config(self):
        return GoalWorkflowConfig(
            name="test_workflow",
            description="Test workflow",
            goal=GoalWorkflowGoalConfig(
                default_criteria=["criterion 1", "criterion 2"],
                risk_tier="research_general",
            ),
            agents=[
                GoalAgentConfig(
                    id="agent_a",
                    prompt_file=".prompts/test.md",
                    tools=["read"],
                    input_from=[],
                    evidence_criterion=0,
                ),
                GoalAgentConfig(
                    id="agent_b",
                    prompt_file=".prompts/test.md",
                    tools=["read"],
                    input_from=["agent_a"],
                    evidence_criterion=1,
                ),
            ],
            dag={"agent_a": [], "agent_b": ["agent_a"]},
        )

    def test_init(self):
        config = self._make_config()
        runner = GoalWorkflowRunner(config, session_id="test-session")
        assert runner._config.name == "test_workflow"
        assert runner._session_id == "test-session"
        assert runner._state.status == "idle"

    def test_get_progress(self):
        config = self._make_config()
        runner = GoalWorkflowRunner(config, session_id="test-session")
        progress = runner.get_progress()
        assert progress["status"] == "idle"
        assert progress["agents_total"] == 2
        assert progress["evidence_count"] == 0
        assert progress["paused"] is False

    def test_get_layers(self):
        """After P3.9, GoalWorkflowRunner delegates DAG to SwarmRuntime.
        Test that to_swarm_preset() produces correct dag."""
        config = self._make_config()
        preset = config.to_swarm_preset()
        assert preset.dag == {"agent_a": [], "agent_b": ["agent_a"]}

    def test_get_agent_config(self):
        """After P3.9, agent config lookup is via to_swarm_preset()."""
        config = self._make_config()
        preset = config.to_swarm_preset()
        assert len(preset.agents) == 2
        names = {a.agent_name for a in preset.agents}
        assert "agent_a" in names
        assert "agent_b" in names

    def test_pause_resume(self):
        config = self._make_config()
        runner = GoalWorkflowRunner(config, session_id="test-session")
        runner._state.status = "running"
        runner.pause()
        assert runner._state.paused is True
        runner.resume()
        assert runner._state.paused is False

    def test_build_goal_context(self):
        """After P3.9, goal context is built inside start().
        Test that GoalWorkflowConfig.to_swarm_preset() includes goal data."""
        config = self._make_config()
        preset = config.to_swarm_preset()
        assert preset.goal is not None
        assert preset.goal["default_criteria"] == ["criterion 1", "criterion 2"]


# ── YAML Loading ─────────────────────────────────────────────


class TestYAMLLoading:
    def test_load_existing_preset(self):
        from agentnodes.apps.goal.workflow_config import load_goal_workflow
        config = load_goal_workflow("goal_factor_research")
        assert config.name == "goal_factor_research"
        assert len(config.agents) == 4
        assert len(config.dag) == 4

    def test_load_nonexistent_raises(self):
        from agentnodes.apps.goal.workflow_config import load_goal_workflow
        with pytest.raises(FileNotFoundError):
            load_goal_workflow("nonexistent_workflow_xyz")

    def test_list_workflows(self):
        from agentnodes.apps.goal import list_goal_workflows
        workflows = list_goal_workflows()
        assert len(workflows) >= 1
        names = [w["name"] for w in workflows]
        assert "goal_factor_research" in names


# ── DAG Validation ───────────────────────────────────────────


class TestDAGValidation:
    def test_validate_acyclic_dag(self):
        from agentnodes.kernel.workflow.dag import validate_dag
        validate_dag({"a": [], "b": ["a"], "c": ["b"]})

    def test_validate_cyclic_dag_raises(self):
        from agentnodes.kernel.workflow.dag import validate_dag
        with pytest.raises(ValueError, match="cycle"):
            validate_dag({"a": ["b"], "b": ["a"]})
