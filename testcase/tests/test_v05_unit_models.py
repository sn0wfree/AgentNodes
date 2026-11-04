"""Phase 4 - v0.5 unit tests: workflow models + state + evidence + executor.

Fills coverage gaps for:
  - GoalWorkflowState: set_agent_status / get_summary / defaults
  - GoalEvidenceCollector: collect() with various inputs
  - _AgentConfigExecutor: name / run() stub
  - GoalWorkflowConfig.to_swarm_preset(): config conversion
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from strategy_research.apps.goal.workflow import (
    BranchConfig,
    GoalAgentConfig,
    GoalEvidenceCollector,
    GoalWorkflowConfig,
    GoalWorkflowState,
)
from strategy_research.apps.goal.workflow_config import load_goal_workflow

# _AgentConfigExecutor removed in P8 cleanup
_AgentConfigExecutor = None  # type: ignore[assignment,misc]


@pytest.fixture
def fresh_db(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("STRATEGY_RESEARCH_GOAL_DB", str(tmp_path / "goals.db"))


@pytest.fixture
def workflow_config():
    return load_goal_workflow("goal_factor_research")


# ═══════════════════════════════════════════════════════════════════════
# GoalWorkflowState
# ═══════════════════════════════════════════════════════════════════════


class TestGoalWorkflowState:
    def test_set_agent_status_no_error(self):
        state = GoalWorkflowState()
        state.set_agent_status("agent_1", "running")
        assert state.agent_statuses["agent_1"] == "running"
        assert "agent_1" not in state.agent_errors

    def test_set_agent_status_with_error(self):
        state = GoalWorkflowState()
        state.set_agent_status("agent_1", "error", "timeout exceeded")
        assert state.agent_statuses["agent_1"] == "error"
        assert state.agent_errors["agent_1"] == "timeout exceeded"

    def test_set_agent_status_overwrites(self):
        state = GoalWorkflowState()
        state.set_agent_status("a", "running")
        state.set_agent_status("a", "completed")
        assert state.agent_statuses["a"] == "completed"

    def test_get_summary_fields(self):
        state = GoalWorkflowState()
        state.status = "running"
        state.current_layer = 2
        state.evidence_count = 3
        state.set_agent_status("a", "completed")
        state.set_agent_status("b", "running")
        summary = state.get_summary()
        assert summary["status"] == "running"
        assert summary["current_layer"] == 2
        assert summary["paused"] is False
        assert summary["evidence_count"] == 3
        assert summary["agent_statuses"]["a"] == "completed"

    def test_get_summary_error_message(self):
        state = GoalWorkflowState()
        state.error_message = "something broke"
        assert state.get_summary()["error_message"] == "something broke"

    def test_default_values(self):
        state = GoalWorkflowState()
        assert state.status == "idle"
        assert state.current_layer == 0
        assert state.paused is False
        assert state.cancelled is False
        assert state.pause_layer == -1
        assert state.evidence_count == 0
        assert state.start_time == 0.0
        assert state.error_message == ""


# ═══════════════════════════════════════════════════════════════════════
# GoalEvidenceCollector
# ═══════════════════════════════════════════════════════════════════════


class TestGoalEvidenceCollector:
    def test_collect_empty_answer(self):
        collector = GoalEvidenceCollector(MagicMock(), "sess", "g1")
        assert collector.collect("a1", {"answer": ""}, 0) == 0

    def test_collect_short_answer(self):
        collector = GoalEvidenceCollector(MagicMock(), "sess", "g1")
        assert collector.collect("a1", {"answer": "short"}, 0) == 0

    def test_collect_no_snapshot(self):
        store = MagicMock()
        store.get_current_snapshot.return_value = None
        collector = GoalEvidenceCollector(store, "sess", "g1")
        assert collector.collect("a1", {"answer": "x" * 20}, 0) == 0

    def test_collect_criterion_out_of_range(self):
        store = MagicMock()
        store.get_current_snapshot.return_value = {"criteria": [{"criterion_id": "c0"}]}
        collector = GoalEvidenceCollector(store, "sess", "g1")
        assert collector.collect("a1", {"answer": "x" * 20}, 5) == 0

    def test_collect_no_criterion_id(self):
        store = MagicMock()
        store.get_current_snapshot.return_value = {"criteria": [{}]}
        collector = GoalEvidenceCollector(store, "sess", "g1")
        assert collector.collect("a1", {"answer": "x" * 20}, 0) == 0

    def test_collect_success(self):
        store = MagicMock()
        store.get_current_snapshot.return_value = {"criteria": [{"criterion_id": "c0"}]}
        collector = GoalEvidenceCollector(store, "sess", "g1")
        assert collector.collect("a1", {"answer": "x" * 20}, 0) == 1
        store.append_evidence.assert_called_once()

    def test_collect_store_exception(self):
        store = MagicMock()
        store.get_current_snapshot.return_value = {"criteria": [{"criterion_id": "c0"}]}
        store.append_evidence.side_effect = RuntimeError("DB locked")
        collector = GoalEvidenceCollector(store, "sess", "g1")
        assert collector.collect("a1", {"answer": "x" * 20}, 0) == 0

    def test_collect_truncates_to_2000(self):
        store = MagicMock()
        store.get_current_snapshot.return_value = {"criteria": [{"criterion_id": "c0"}]}
        collector = GoalEvidenceCollector(store, "sess", "g1")
        collector.collect("a1", {"answer": "x" * 5000}, 0)
        evidence = store.append_evidence.call_args.kwargs["evidence"]
        assert len(evidence.text) == 2000


# ═══════════════════════════════════════════════════════════════════════
# _AgentConfigExecutor
# ═══════════════════════════════════════════════════════════════════════



# ═══════════════════════════════════════════════════════════════════════
# GoalWorkflowConfig.to_swarm_preset()
# ═══════════════════════════════════════════════════════════════════════


class TestToSwarmPreset:
    def test_name_and_description(self, workflow_config):
        preset = workflow_config.to_swarm_preset()
        assert preset.name == workflow_config.name
        assert preset.description == workflow_config.description

    def test_agents_match(self, workflow_config):
        preset = workflow_config.to_swarm_preset()
        assert len(preset.agents) == len(workflow_config.agents)
        ids = [a.agent_name for a in preset.agents]
        assert ids == [a.id for a in workflow_config.agents]

    def test_dag_preserved(self, workflow_config):
        preset = workflow_config.to_swarm_preset()
        assert preset.dag == workflow_config.dag

    def test_agent_context_has_keys(self, workflow_config):
        preset = workflow_config.to_swarm_preset()
        for call in preset.agents:
            assert "tools" in call.context
            assert "input_from" in call.context
            assert "evidence_criterion" in call.context
            assert "timeout" in call.context
            assert "max_retries" in call.context

    def test_goal_and_completion(self, workflow_config):
        preset = workflow_config.to_swarm_preset()
        assert "default_criteria" in preset.goal
        assert "risk_tier" in preset.goal
        assert "mode" in preset.completion
        assert "auto_audit" in preset.completion

    def test_branches_serialized(self):
        config = GoalWorkflowConfig(
            name="t", description="",
            agents=[GoalAgentConfig(id="a", prompt_file=".prompts/a.md")],
            dag={"a": []},
            branches=[BranchConfig(condition="x > 1", action="skip", target="a")],
        )
        preset = config.to_swarm_preset()
        assert len(preset.branches) == 1
        assert preset.branches[0]["condition"] == "x > 1"

    def test_version_preserved(self, workflow_config):
        preset = workflow_config.to_swarm_preset()
        assert preset.version == workflow_config.version
