"""Phase 4 - v0.5 unit tests: workflow_config edge cases.

Fills coverage gaps for:
  - load_goal_workflow with explicit file path
  - _resolve_yaml_path: name, with/without goal_ prefix, user dir
  - _validate_config: empty name, no agents, no dag, unknown agents
  - save_goal_workflow round-trip (save then load)
  - save_goal_workflow with branches
  - save_goal_workflow with no validation
  - list_goal_workflows returns correct structure
"""
from __future__ import annotations

from pathlib import Path

import pytest

from strategy_research.apps.goal.workflow import (
    BranchConfig,
    GoalAgentConfig,
    GoalWorkflowConfig,
    GoalWorkflowGoalConfig,
)
from strategy_research.apps.goal.workflow_config import (
    _resolve_yaml_path,
    _validate_config,
    list_goal_workflows,
    load_goal_workflow,
    save_goal_workflow,
)

# ═══════════════════════════════════════════════════════════════════════
# load_goal_workflow with explicit path
# ═══════════════════════════════════════════════════════════════════════


class TestLoadExplicitPath:
    def test_load_by_explicit_path(self, tmp_path):
        yaml_content = """
name: test_wf
description: test
version: "2.0"
goal:
  default_criteria:
    - criterion_1
    - criterion_2
  risk_tier: research_general
agents:
  - id: agent_a
    prompt_file: .prompts/researcher.md
    evidence_criterion: 0
  - id: agent_b
    prompt_file: .prompts/data_quality.md
    evidence_criterion: 1
dag:
  agent_a: []
  agent_b: [agent_a]
"""
        yaml_file = tmp_path / "test_wf.yaml"
        yaml_file.write_text(yaml_content, encoding="utf-8")

        config = load_goal_workflow(str(yaml_file))
        assert config.name == "test_wf"
        assert config.version == "2.0"
        assert len(config.agents) == 2

    def test_load_not_found_raises(self):
        with pytest.raises(FileNotFoundError):
            load_goal_workflow("nonexistent_workflow_xyz")


# ═══════════════════════════════════════════════════════════════════════
# _resolve_yaml_path
# ═══════════════════════════════════════════════════════════════════════


class TestResolveYamlPath:
    def test_resolve_by_name(self):
        path = _resolve_yaml_path("goal_factor_research")
        assert path is not None
        assert path.exists()

    def test_resolve_without_prefix(self):
        # Should also work without "goal_" prefix
        path = _resolve_yaml_path("factor_research")
        assert path is not None
        assert path.exists()

    def test_resolve_explicit_path(self, tmp_path):
        yaml_file = tmp_path / "custom.yaml"
        yaml_file.write_text("name: custom\n", encoding="utf-8")
        path = _resolve_yaml_path(str(yaml_file))
        assert path == yaml_file

    def test_resolve_nonexistent(self):
        assert _resolve_yaml_path("totally_nonexistent_xyz") is None


# ═══════════════════════════════════════════════════════════════════════
# _validate_config
# ═══════════════════════════════════════════════════════════════════════


class TestValidateConfig:
    def _valid_config(self):
        return GoalWorkflowConfig(
            name="valid",
            description="test",
            agents=[GoalAgentConfig(id="a", prompt_file=".prompts/researcher.md")],
            dag={"a": []},
        )

    def test_valid_config_passes(self):
        _validate_config(self._valid_config())

    def test_empty_name_raises(self):
        config = self._valid_config()
        config.name = ""
        with pytest.raises(ValueError, match="name"):
            _validate_config(config)

    def test_no_agents_raises(self):
        config = self._valid_config()
        config.agents = []
        with pytest.raises(ValueError, match="agent"):
            _validate_config(config)

    def test_no_dag_raises(self):
        config = self._valid_config()
        config.dag = {}
        with pytest.raises(ValueError, match="DAG"):
            _validate_config(config)

    def test_dag_unknown_agent_raises(self):
        config = self._valid_config()
        config.dag = {"a": [], "ghost": ["a"]}
        with pytest.raises(ValueError, match="unknown agents"):
            _validate_config(config)

    def test_negative_evidence_criterion_raises(self):
        config = self._valid_config()
        config.agents[0].evidence_criterion = -1
        with pytest.raises(ValueError, match="evidence_criterion"):
            _validate_config(config)


# ═══════════════════════════════════════════════════════════════════════
# save_goal_workflow round-trip
# ═══════════════════════════════════════════════════════════════════════


class TestSaveRoundTrip:
    def test_save_load_round_trip(self, tmp_path):
        original = load_goal_workflow("goal_factor_research")
        path = tmp_path / "round_trip.yaml"
        save_goal_workflow(path, original)

        loaded = load_goal_workflow(str(path))
        assert loaded.name == original.name
        assert loaded.description == original.description
        assert len(loaded.agents) == len(original.agents)
        assert loaded.dag == original.dag

    def test_save_load_preserves_agents(self, tmp_path):
        original = load_goal_workflow("goal_market_analysis")
        path = tmp_path / "rt_market.yaml"
        save_goal_workflow(path, original)
        loaded = load_goal_workflow(str(path))
        original_ids = {a.id for a in original.agents}
        loaded_ids = {a.id for a in loaded.agents}
        assert original_ids == loaded_ids

    def test_save_load_preserves_criteria(self, tmp_path):
        original = load_goal_workflow("goal_risk_assessment")
        path = tmp_path / "rt_risk.yaml"
        save_goal_workflow(path, original)
        loaded = load_goal_workflow(str(path))
        assert loaded.goal.default_criteria == original.goal.default_criteria

    def test_save_with_branches(self, tmp_path):
        config = GoalWorkflowConfig(
            name="branch_test",
            description="test",
            goal=GoalWorkflowGoalConfig(default_criteria=["c1"]),
            agents=[GoalAgentConfig(id="a", prompt_file=".prompts/researcher.md", evidence_criterion=0)],
            dag={"a": []},
            branches=[BranchConfig(condition="a.x > 1", action="skip", target="a", reason="test")],
        )
        path = tmp_path / "branches.yaml"
        save_goal_workflow(path, config)
        loaded = load_goal_workflow(str(path))
        assert len(loaded.branches) == 1
        assert loaded.branches[0].condition == "a.x > 1"
        assert loaded.branches[0].action == "skip"

    def test_save_without_validation(self, tmp_path):
        config = load_goal_workflow("goal_factor_research")
        path = tmp_path / "no_validate.yaml"
        save_goal_workflow(path, config, validate=False)
        assert path.exists()

    def test_save_creates_parent_dir(self, tmp_path):
        config = load_goal_workflow("goal_factor_research")
        path = tmp_path / "subdir" / "nested" / "wf.yaml"
        save_goal_workflow(path, config)
        assert path.exists()


# ═══════════════════════════════════════════════════════════════════════
# list_goal_workflows
# ═══════════════════════════════════════════════════════════════════════


class TestListGoalWorkflows:
    def test_returns_list_of_dicts(self):
        workflows = list_goal_workflows()
        assert isinstance(workflows, list)
        for w in workflows:
            assert "name" in w
            assert "description" in w
            assert "path" in w

    def test_paths_are_valid(self):
        workflows = list_goal_workflows()
        for w in workflows:
            assert Path(w["path"]).exists()

    def test_contains_factor_research(self):
        workflows = list_goal_workflows()
        names = {w["name"] for w in workflows}
        assert "goal_factor_research" in names
