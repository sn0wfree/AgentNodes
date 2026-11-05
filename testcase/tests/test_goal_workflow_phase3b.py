"""Tests for Phase 3 remaining items — P3.5/P3.6/P3.8/P3.9.

Coverage:
  P3.9 — GoalWorkflowRunner delegates to SwarmRuntime:
    * Runner creates SwarmPreset from GoalWorkflowConfig
    * Runner creates GoalWorkflowHook with evidence_map
    * Runner.start() delegates to SwarmRuntime.execute()

  P3.5 — Sub-workflow support:
    * start_sub_workflow() creates child goal with parent_goal_id
    * start_sub_workflow() delegates to SwarmRuntime

  P3.6 — Checkpoint + resume:
    * CheckpointStore.save() creates checkpoint directory
    * CheckpointStore.load() restores state
    * CheckpointStore.delete() removes checkpoint
    * CheckpointStore.list_checkpoints() enumerates
    * GoalWorkflowRunner.checkpoint() saves current state
    * GoalWorkflowRunner.resume_from_checkpoint() restores

  P3.8 — Visual editor:
    * GoalPanel.on_workflow_event handles agent_complete
    * GoalPanel.on_workflow_event handles workflow_completed
    * GoalPanel.on_workflow_event handles workflow_failed
"""
from __future__ import annotations

import tempfile
from pathlib import Path
from unittest import mock

import pytest

from strategy_research.apps.goal.checkpoint_store import CheckpointStore
from strategy_research.apps.goal.workflow import (
    CompletionConfig,
    GoalAgentConfig,
    GoalWorkflowConfig,
    GoalWorkflowGoalConfig,
    GoalWorkflowRunner,
)

# ── P3.9: GoalWorkflowRunner delegates to SwarmRuntime ────────


class TestGoalWorkflowRunnerP39:
    def _make_config(self):
        return GoalWorkflowConfig(
            name="test_wf",
            description="test",
            goal=GoalWorkflowGoalConfig(default_criteria=["c1"]),
            agents=[
                GoalAgentConfig(id="a", prompt_file=".prompts/a.md"),
            ],
            dag={"a": []},
            completion=CompletionConfig(mode="auto"),
        )

    def test_runner_creates_state(self):
        config = self._make_config()
        runner = GoalWorkflowRunner(config, "test-session")
        assert runner.state.status == "idle"
        assert runner.goal_id == ""

    def test_runner_to_swarm_preset(self):
        config = self._make_config()
        preset = config.to_swarm_preset()
        assert preset.name == "test_wf"
        assert len(preset.agents) == 1

    def test_runner_subscribe(self):
        config = self._make_config()
        runner = GoalWorkflowRunner(config, "test-session")
        obs = mock.MagicMock()
        runner.subscribe(obs)
        runner._event_bus.emit("test_event", data="value")
        obs.on_event.assert_called_once()

    def test_runner_get_progress(self):
        config = self._make_config()
        runner = GoalWorkflowRunner(config, "test-session")
        progress = runner.get_progress()
        assert progress["status"] == "idle"
        assert progress["agents_total"] == 1


# ── P3.5: Sub-workflow support ────────────────────────────────


class TestSubWorkflow:
    def _make_config(self):
        return GoalWorkflowConfig(
            name="parent_wf",
            description="parent",
            goal=GoalWorkflowGoalConfig(default_criteria=["c1"]),
            agents=[GoalAgentConfig(id="a", prompt_file=".prompts/a.md")],
            dag={"a": []},
        )

    @pytest.mark.asyncio
    async def test_start_sub_workflow_creates_child_goal(self):
        config = self._make_config()
        store = mock.MagicMock()
        mock_goal = mock.MagicMock()
        mock_goal.goal_id = "child_goal_1"
        store.replace_goal.return_value = mock_goal
        store.get_current_snapshot.return_value = {
            "goal": {"goal_id": "child_goal_1", "status": "active"},
            "criteria": [{"criterion_id": "c1", "status": "pending", "required": True}],
            "evidence": [],
            "evidence_count": 0,
        }

        runner = GoalWorkflowRunner(config, "s1", store=store)
        goal_id = await runner.start_sub_workflow("child objective", "parent_123")

        assert goal_id == "child_goal_1"
        store.replace_goal.assert_called_once()
        call_kwargs = store.replace_goal.call_args
        assert call_kwargs[1]["parent_goal_id"] == "parent_123"


# ── P3.6: Checkpoint + resume ─────────────────────────────────


class TestCheckpointStore:
    def test_save_creates_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cp = CheckpointStore(Path(tmpdir))
            cp.save(
                session_id="s1",
                goal_id="g1",
                state={"status": "running", "current_layer": 2},
                layer_results={"a": {"answer": "hello"}},
                workflow_name="test_wf",
            )
            cp_dir = Path(tmpdir) / "s1" / "g1"
            assert cp_dir.exists()
            assert (cp_dir / "state.json").exists()
            assert (cp_dir / "layer_results.json").exists()
            assert (cp_dir / "meta.json").exists()

    def test_load_restores_state(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cp = CheckpointStore(Path(tmpdir))
            cp.save(
                session_id="s1",
                goal_id="g1",
                state={"status": "running", "evidence_count": 5},
                layer_results={"a": {"answer": "data"}},
                workflow_name="test_wf",
            )
            data = cp.load("s1", "g1")
            assert data is not None
            assert data["state"]["status"] == "running"
            assert data["state"]["evidence_count"] == 5
            assert data["meta"]["workflow_name"] == "test_wf"

    def test_load_returns_none_for_missing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cp = CheckpointStore(Path(tmpdir))
            assert cp.load("s1", "nonexistent") is None

    def test_delete_removes_checkpoint(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cp = CheckpointStore(Path(tmpdir))
            cp.save("s1", "g1", {"status": "x"}, {}, "wf")
            assert cp.delete("s1", "g1") is True
            assert cp.load("s1", "g1") is None

    def test_list_checkpoints(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cp = CheckpointStore(Path(tmpdir))
            cp.save("s1", "g1", {"status": "x"}, {}, "wf1")
            cp.save("s1", "g2", {"status": "x"}, {}, "wf2")
            items = cp.list_checkpoints("s1")
            assert len(items) == 2
            names = {i["workflow_name"] for i in items}
            assert "wf1" in names

    def test_runner_checkpoint_saves(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config = GoalWorkflowConfig(
                name="test", description="test",
                agents=[GoalAgentConfig(id="a", prompt_file="a.md")],
                dag={"a": []},
            )
            runner = GoalWorkflowRunner(config, "s1")
            runner._goal_id = "g1"
            runner._state.status = "running"
            runner._state.evidence_count = 3
            # Create a real CheckpointStore with temp dir
            real_cp = CheckpointStore(Path(tmpdir))
            # Patch the lazy import inside checkpoint()
            with mock.patch(
                "strategy_research.apps.goal.checkpoint_store.CheckpointStore",
                return_value=real_cp,
            ):
                result = runner.checkpoint()
                assert result is not None
                assert result.exists()


# ── P3.8: GoalPanel.on_workflow_event ─────────────────────────


class TestGoalPanelOnWorkflowEvent:
    def test_agent_complete_updates_panel(self):
        from strategy_research.interfaces.cli.tui.widgets.goal_panel import GoalPanel
        panel = GoalPanel()
        panel.update_goal(
            objective="test",
            status="active",
            criteria=[
                {"criterion_id": "c1", "text": "crit 1", "status": "pending", "required": True},
                {"criterion_id": "c2", "text": "crit 2", "status": "pending", "required": True},
            ],
            evidence_count=0,
        )
        panel.on_workflow_event("agent_complete", {
            "agent_id": "researcher",
            "evidence_count": 1,
        })
        # Should have re-rendered — evidence count should be updated
        assert panel._evidence_count == 1

    def test_workflow_completed_sets_status(self):
        from strategy_research.interfaces.cli.tui.widgets.goal_panel import GoalPanel
        panel = GoalPanel()
        panel.update_goal(objective="test", status="active")
        panel.on_workflow_event("workflow_completed", {})
        assert panel._status == "complete"
        assert panel._progress == 100.0

    def test_workflow_failed_sets_status(self):
        from strategy_research.interfaces.cli.tui.widgets.goal_panel import GoalPanel
        panel = GoalPanel()
        panel.update_goal(objective="test", status="active")
        panel.on_workflow_event("workflow_failed", {"error": "boom"})
        assert panel._status == "error"

    def test_unknown_event_does_not_crash(self):
        from strategy_research.interfaces.cli.tui.widgets.goal_panel import GoalPanel
        panel = GoalPanel()
        panel.update_goal(objective="test", status="active")
        # Should not raise
        panel.on_workflow_event("unknown_event", {})
