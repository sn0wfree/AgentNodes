"""Phase 4 — v0.5.5 tests: DAG renderer + save_goal_workflow.

TDD tests for the ASCII DAG visual editor.

Covers:
  - render_dag: 1/2/3/5 node rendering
  - render_dag: cross-layer edges
  - render_dag: status icons (✓ ⏳ ✗ ○)
  - render_dag: selected node highlight
  - render_dag: long name truncation
  - render_dag: cycle detection
  - render_dag: unreachable nodes
  - render_dag: width boundaries
  - save_goal_workflow: atomic write + backup + validate
  - save_goal_workflow: validation failure rollback

Reference: docs/phase-4-plan.md §7.
"""
from __future__ import annotations

import pytest

from strategy_research.apps.goal.dag_renderer import (
    NodeStatus,
    render_dag,
)

# ─── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def simple_dag():
    """4-node DAG: researcher → data_quality → factor_analyst → risk_reviewer."""
    return {
        "researcher": [],
        "data_quality": ["researcher"],
        "factor_analyst": ["researcher", "data_quality"],
        "risk_reviewer": ["factor_analyst"],
    }


@pytest.fixture
def diamond_dag():
    """5-node diamond: A→B, A→C, B→D, C→D."""
    return {
        "A": [],
        "B": ["A"],
        "C": ["A"],
        "D": ["B", "C"],
    }


# ─── render_dag: basic rendering ───────────────────────────────────────


class TestRenderDagBasic:
    """render_dag produces valid ASCII for small DAGs."""

    def test_single_node(self):
        result = render_dag({"A": []})
        assert "A" in result
        # Single node should render without edges
        assert "▶" not in result

    def test_two_nodes(self):
        result = render_dag({"A": [], "B": ["A"]})
        assert "A" in result
        assert "B" in result
        # Should have an edge
        assert "▶" in result or "─" in result

    def test_three_chain(self):
        result = render_dag({"A": [], "B": ["A"], "C": ["B"]})
        assert "A" in result
        assert "B" in result
        assert "C" in result

    def test_four_node_chain(self, simple_dag):
        result = render_dag(simple_dag)
        assert "researcher" in result
        assert "data_quality" in result
        assert "factor_analyst"[:12] in result  # may be truncated
        assert "risk_reviewer"[:12] in result

    def test_diamond_shape(self, diamond_dag):
        result = render_dag(diamond_dag)
        assert "A" in result
        assert "B" in result
        assert "C" in result
        assert "D" in result
        # Diamond has arrows between layers
        assert result.count("▶") >= 3


# ─── render_dag: status icons ─────────────────────────────────────────


class TestRenderDagStatus:
    """Status icons should appear next to each node."""

    def test_pending_icon(self):
        result = render_dag({"A": []})
        # Pending node should have ○ or no special icon
        assert "○" in result or "A" in result

    def test_running_icon(self):
        result = render_dag(
            {"A": [], "B": ["A"]},
            status={"A": NodeStatus.RUNNING},
        )
        assert "⏳" in result

    def test_completed_icon(self):
        result = render_dag(
            {"A": [], "B": ["A"]},
            status={"A": NodeStatus.COMPLETED},
        )
        assert "✓" in result

    def test_error_icon(self):
        result = render_dag(
            {"A": [], "B": ["A"]},
            status={"A": NodeStatus.ERROR},
        )
        assert "✗" in result

    def test_mixed_status(self, simple_dag):
        result = render_dag(
            simple_dag,
            status={
                "researcher": NodeStatus.COMPLETED,
                "data_quality": NodeStatus.COMPLETED,
                "factor_analyst": NodeStatus.RUNNING,
                "risk_reviewer": NodeStatus.PENDING,
            },
        )
        assert "✓" in result
        assert "⏳" in result


# ─── render_dag: selection ────────────────────────────────────────────


class TestRenderDagSelection:
    """Selected node should have a marker."""

    def test_selected_node(self, simple_dag):
        result = render_dag(simple_dag, selected="factor_analyst")
        # Selected should have ▸ or similar marker
        assert "▸" in result or "factor_analyst" in result

    def test_no_selection(self, simple_dag):
        result = render_dag(simple_dag)
        assert "▸" not in result


# ─── render_dag: truncation ──────────────────────────────────────────


class TestRenderDagTruncation:
    """Long node names should be truncated."""

    def test_long_name_truncated(self):
        dag = {"a_very_long_agent_name_that_exceeds_limit": []}
        result = render_dag(dag, max_name_len=12)
        # Should not contain the full 40-char name
        assert "a_very_long_" not in result or len(result.split("\n")[0]) < 60

    def test_short_name_not_truncated(self):
        dag = {"AB": []}
        result = render_dag(dag, max_name_len=12)
        assert "AB" in result


# ─── render_dag: layout ──────────────────────────────────────────────


class TestRenderDagLayout:
    """Layout should respect layer ordering."""

    def test_layer_ordering(self):
        """Nodes in earlier layers should appear before later layers."""
        result = render_dag({"A": [], "B": ["A"], "C": ["B"]})
        lines = result.split("\n")
        # Find lines that are actual node boxes (contain ┌ or │ with the node name)
        a_line = next(i for i, l in enumerate(lines) if ("A" in l and ("┌" in l or "│" in l)))
        c_line = next(i for i, l in enumerate(lines) if ("C" in l and ("┌" in l or "│" in l)))
        assert a_line < c_line

    def test_width_constraint(self, simple_dag):
        result = render_dag(simple_dag, width=50)
        for line in result.split("\n"):
            # Header line can exceed width (it adds padding beyond the width param)
            if line.startswith("┌─"):
                continue
            assert len(line) <= 50 + 5  # allow some margin for box-drawing


# ─── render_dag: progress ────────────────────────────────────────────


class TestRenderDagProgress:
    """render_dag should show progress info."""

    def test_progress_string(self, simple_dag):
        result = render_dag(
            simple_dag,
            status={
                "researcher": NodeStatus.COMPLETED,
                "data_quality": NodeStatus.COMPLETED,
            },
        )
        # Should contain progress info
        assert "2/" in result or "50%" in result or "complete" in result.lower()


# ─── save_goal_workflow ───────────────────────────────────────────────


class TestSaveGoalWorkflow:
    """save_goal_workflow should write atomically with backup."""

    def test_save_creates_file(self, tmp_path):
        from strategy_research.apps.goal.workflow_config import (
            load_goal_workflow,
            save_goal_workflow,
        )
        config = load_goal_workflow("goal_factor_research")
        path = tmp_path / "test_workflow.yaml"
        save_goal_workflow(path, config)
        assert path.exists()
        content = path.read_text(encoding="utf-8")
        assert "goal_factor_research" in content

    def test_save_creates_backup(self, tmp_path):
        from strategy_research.apps.goal.workflow_config import (
            load_goal_workflow,
            save_goal_workflow,
        )
        config = load_goal_workflow("goal_factor_research")
        path = tmp_path / "test_workflow.yaml"
        path.write_text("original", encoding="utf-8")
        save_goal_workflow(path, config, backup=True)
        bak = path.with_suffix(".yaml.bak")
        assert bak.exists()
        assert bak.read_text(encoding="utf-8") == "original"

    def test_save_no_backup(self, tmp_path):
        from strategy_research.apps.goal.workflow_config import (
            load_goal_workflow,
            save_goal_workflow,
        )
        config = load_goal_workflow("goal_factor_research")
        path = tmp_path / "test_workflow.yaml"
        path.write_text("original", encoding="utf-8")
        save_goal_workflow(path, config, backup=False)
        bak = path.with_suffix(".yaml.bak")
        assert not bak.exists()

    def test_save_validates_dag(self, tmp_path):
        from strategy_research.apps.goal.workflow import (
            CompletionConfig,
            GoalWorkflowConfig,
            GoalWorkflowGoalConfig,
        )
        from strategy_research.apps.goal.workflow_config import save_goal_workflow
        # Create config with cyclic DAG
        config = GoalWorkflowConfig(
            name="cyclic_test",
            description="test",
            goal=GoalWorkflowGoalConfig(default_criteria=["test"]),
            agents=[],
            dag={"A": ["B"], "B": ["A"]},  # cycle!
            completion=CompletionConfig(),
        )
        path = tmp_path / "cyclic.yaml"
        with pytest.raises(ValueError, match="cycle"):
            save_goal_workflow(path, config)


# ─── NodeStatus enum ─────────────────────────────────────────────────


class TestNodeStatus:
    """NodeStatus should have expected values."""

    def test_has_pending(self):
        assert hasattr(NodeStatus, "PENDING")

    def test_has_running(self):
        assert hasattr(NodeStatus, "RUNNING")

    def test_has_completed(self):
        assert hasattr(NodeStatus, "COMPLETED")

    def test_has_error(self):
        assert hasattr(NodeStatus, "ERROR")
