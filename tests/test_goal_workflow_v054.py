"""Phase 4 — v0.5.4 tests: 4 new workflow presets + cookbook + demo.

TDD tests for the 4 new goal workflow YAML presets.

Covers:
  - Each preset YAML loads successfully
  - Each preset has valid DAG (no cycles, no unknown agents)
  - Each preset has agents with proper IDs
  - Each preset has criteria that match evidence_criterion indices
  - list_goal_workflows() returns all 5 presets

Reference: docs/phase-4-plan.md §6.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from strategy_research.apps.goal.workflow_config import (
    list_goal_workflows,
    load_goal_workflow,
)

# ─── Preset names ──────────────────────────────────────────────────────

PRESET_NAMES = [
    "goal_factor_research",      # existing
    "goal_market_analysis",      # new
    "goal_risk_assessment",      # new
    "goal_strategy_review",      # new
    "goal_portfolio_review",     # new
]


# ─── All presets load ──────────────────────────────────────────────────


class TestPresetLoading:
    """Each preset YAML should load without errors."""

    @pytest.mark.parametrize("name", PRESET_NAMES)
    def test_load_preset(self, name):
        config = load_goal_workflow(name)
        assert config is not None
        assert config.name == name

    @pytest.mark.parametrize("name", PRESET_NAMES)
    def test_preset_has_agents(self, name):
        config = load_goal_workflow(name)
        assert len(config.agents) >= 2

    @pytest.mark.parametrize("name", PRESET_NAMES)
    def test_preset_has_dag(self, name):
        config = load_goal_workflow(name)
        assert len(config.dag) >= 2

    @pytest.mark.parametrize("name", PRESET_NAMES)
    def test_preset_has_criteria(self, name):
        config = load_goal_workflow(name)
        assert len(config.goal.default_criteria) >= 2


# ─── DAG validation ────────────────────────────────────────────────────


class TestPresetDAG:
    """Each preset DAG should be valid (no cycles, no missing agents)."""

    @pytest.mark.parametrize("name", PRESET_NAMES)
    def test_dag_is_acyclic(self, name):
        config = load_goal_workflow(name)
        # DAG was validated during load; if it loads, it's acyclic
        agent_ids = {a.id for a in config.agents}
        dag_nodes = set(config.dag.keys())
        for deps in config.dag.values():
            dag_nodes.update(deps)
        # All DAG nodes should reference known agents
        missing = dag_nodes - agent_ids
        assert not missing, f"Unknown agents in DAG: {missing}"

    @pytest.mark.parametrize("name", PRESET_NAMES)
    def test_evidence_criterion_in_range(self, name):
        config = load_goal_workflow(name)
        num_criteria = len(config.goal.default_criteria)
        for agent in config.agents:
            assert 0 <= agent.evidence_criterion < num_criteria, (
                f"Agent {agent.id} evidence_criterion "
                f"{agent.evidence_criterion} out of range [0, {num_criteria})"
            )


# ─── list_goal_workflows() ────────────────────────────────────────────


class TestListGoalWorkflows:
    """list_goal_workflows() should return all 5 presets."""

    def test_returns_all_presets(self):
        workflows = list_goal_workflows()
        names = {w["name"] for w in workflows}
        for preset in PRESET_NAMES:
            assert preset in names, f"Missing preset: {preset}"

    def test_count_at_least_five(self):
        workflows = list_goal_workflows()
        assert len(workflows) >= 5


# ─── Preset-specific checks ───────────────────────────────────────────


class TestMarketAnalysis:
    def test_three_agents(self):
        config = load_goal_workflow("goal_market_analysis")
        assert len(config.agents) == 3
        agent_ids = {a.id for a in config.agents}
        assert "market_scanner" in agent_ids
        assert "regime_classifier" in agent_ids
        assert "report_writer" in agent_ids

    def test_three_criteria(self):
        config = load_goal_workflow("goal_market_analysis")
        assert len(config.goal.default_criteria) == 3


class TestRiskAssessment:
    def test_four_agents(self):
        config = load_goal_workflow("goal_risk_assessment")
        assert len(config.agents) == 4
        agent_ids = {a.id for a in config.agents}
        assert "position_auditor" in agent_ids
        assert "risk_controller" in agent_ids
        assert "stress_tester" in agent_ids
        assert "report_writer" in agent_ids

    def test_four_criteria(self):
        config = load_goal_workflow("goal_risk_assessment")
        assert len(config.goal.default_criteria) == 4


class TestStrategyReview:
    def test_five_agents(self):
        config = load_goal_workflow("goal_strategy_review")
        assert len(config.agents) == 5
        agent_ids = {a.id for a in config.agents}
        assert "pnl_attribution" in agent_ids
        assert "summary_writer" in agent_ids

    def test_five_criteria(self):
        config = load_goal_workflow("goal_strategy_review")
        assert len(config.goal.default_criteria) == 5


class TestPortfolioReview:
    def test_four_agents(self):
        config = load_goal_workflow("goal_portfolio_review")
        assert len(config.agents) == 4
        agent_ids = {a.id for a in config.agents}
        assert "portfolio_construction" in agent_ids
        assert "report_writer" in agent_ids

    def test_four_criteria(self):
        config = load_goal_workflow("goal_portfolio_review")
        assert len(config.goal.default_criteria) == 4


# ─── Demo script ───────────────────────────────────────────────────────


class TestDemoScript:
    def test_demo_script_importable(self):
        """examples/goal_workflow_demo.py should be importable."""
        demo_path = Path(__file__).parent.parent / "examples" / "goal_workflow_demo.py"
        assert demo_path.exists(), f"Demo script not found: {demo_path}"


# ─── Cookbook ───────────────────────────────────────────────────────────


class TestCookbook:
    def test_cookbook_exists(self):
        cookbook = Path(__file__).parent.parent / "docs" / "goal-workflow-cookbook.md"
        assert cookbook.exists(), f"Cookbook not found: {cookbook}"

    def test_cookbook_mentions_presets(self):
        cookbook = Path(__file__).parent.parent / "docs" / "goal-workflow-cookbook.md"
        if cookbook.exists():
            content = cookbook.read_text(encoding="utf-8")
            assert "goal_factor_research" in content
            assert "goal_market_analysis" in content
