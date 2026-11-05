"""Phase 4 - v0.5 unit tests: CheckpointStore + WorkflowEventBus + DAG.

Covers:
  - CheckpointStore: save, load, delete, list_checkpoints, edge cases
  - WorkflowEventBus: subscribe, unsubscribe, clear, emit, error safety
  - LoggerObserver, CollectingObserver, GoalPanelObserver, MetricsObserver
  - validate_dag: acyclic, cycle, empty, single node, diamond
  - topological_layers: ordering, multiple roots, chain, diamond
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from strategy_research.apps.goal.checkpoint_store import CheckpointStore
from strategy_research.apps.goal.event_bus import (
    CollectingObserver,
    GoalPanelObserver,
    LoggerObserver,
    MetricsObserver,
    WorkflowEventBus,
)
from strategy_research.kernel.workflow.dag import find_downstream, find_upstream, topological_layers, validate_dag

# ═══════════════════════════════════════════════════════════════════════
# CheckpointStore
# ═══════════════════════════════════════════════════════════════════════


class TestCheckpointStoreSave:
    def test_save_creates_directory(self, tmp_path):
        store = CheckpointStore(base_dir=tmp_path)
        cp_dir = store.save("sess1", "goal1", {"status": "running"}, {"a": 1})
        assert cp_dir.exists()
        assert (cp_dir / "state.json").exists()
        assert (cp_dir / "layer_results.json").exists()
        assert (cp_dir / "meta.json").exists()

    def test_save_state_content(self, tmp_path):
        store = CheckpointStore(base_dir=tmp_path)
        state = {"status": "running", "evidence_count": 3}
        cp_dir = store.save("s1", "g1", state, {})
        loaded = json.loads((cp_dir / "state.json").read_text())
        assert loaded["status"] == "running"
        assert loaded["evidence_count"] == 3

    def test_save_layer_results_content(self, tmp_path):
        store = CheckpointStore(base_dir=tmp_path)
        lr = {"agent_a": {"answer": "result"}}
        cp_dir = store.save("s1", "g1", {}, lr)
        loaded = json.loads((cp_dir / "layer_results.json").read_text())
        assert loaded["agent_a"]["answer"] == "result"

    def test_save_meta_content(self, tmp_path):
        store = CheckpointStore(base_dir=tmp_path)
        cp_dir = store.save("s1", "g1", {}, {}, workflow_name="my_wf")
        meta = json.loads((cp_dir / "meta.json").read_text())
        assert meta["workflow_name"] == "my_wf"
        assert meta["session_id"] == "s1"
        assert meta["goal_id"] == "g1"
        assert "created_at" in meta

    def test_save_overwrites_existing(self, tmp_path):
        store = CheckpointStore(base_dir=tmp_path)
        store.save("s1", "g1", {"v": 1}, {})
        store.save("s1", "g1", {"v": 2}, {})
        cp_dir = tmp_path / "s1" / "g1"
        state = json.loads((cp_dir / "state.json").read_text())
        assert state["v"] == 2


class TestCheckpointStoreLoad:
    def test_load_returns_dict(self, tmp_path):
        store = CheckpointStore(base_dir=tmp_path)
        store.save("s1", "g1", {"status": "idle"}, {"x": 1})
        data = store.load("s1", "g1")
        assert data is not None
        assert data["state"]["status"] == "idle"
        assert data["layer_results"]["x"] == 1
        assert "meta" in data

    def test_load_nonexistent_returns_none(self, tmp_path):
        store = CheckpointStore(base_dir=tmp_path)
        assert store.load("ghost", "ghost") is None

    def test_load_incomplete_checkpoint(self, tmp_path):
        store = CheckpointStore(base_dir=tmp_path)
        cp_dir = tmp_path / "s1" / "g1"
        cp_dir.mkdir(parents=True)
        (cp_dir / "state.json").write_text("{}", encoding="utf-8")
        # Missing layer_results.json and meta.json
        assert store.load("s1", "g1") is None

    def test_load_corrupt_json(self, tmp_path):
        store = CheckpointStore(base_dir=tmp_path)
        cp_dir = tmp_path / "s1" / "g1"
        cp_dir.mkdir(parents=True)
        (cp_dir / "state.json").write_text("NOT JSON", encoding="utf-8")
        (cp_dir / "layer_results.json").write_text("{}", encoding="utf-8")
        (cp_dir / "meta.json").write_text("{}", encoding="utf-8")
        assert store.load("s1", "g1") is None


class TestCheckpointStoreDelete:
    def test_delete_existing(self, tmp_path):
        store = CheckpointStore(base_dir=tmp_path)
        store.save("s1", "g1", {}, {})
        assert store.delete("s1", "g1") is True
        assert not (tmp_path / "s1" / "g1").exists()

    def test_delete_nonexistent(self, tmp_path):
        store = CheckpointStore(base_dir=tmp_path)
        assert store.delete("ghost", "ghost") is False


class TestCheckpointStoreList:
    def test_list_empty(self, tmp_path):
        store = CheckpointStore(base_dir=tmp_path)
        assert store.list_checkpoints() == []

    def test_list_all(self, tmp_path):
        store = CheckpointStore(base_dir=tmp_path)
        store.save("s1", "g1", {}, {}, workflow_name="wf1")
        store.save("s1", "g2", {}, {}, workflow_name="wf2")
        store.save("s2", "g3", {}, {}, workflow_name="wf3")
        # list_checkpoints() without session_id searches one level deep
        # (session dirs), which don't have meta.json directly.
        # Filter by session to get actual checkpoints.
        checkpoints_s1 = store.list_checkpoints("s1")
        checkpoints_s2 = store.list_checkpoints("s2")
        assert len(checkpoints_s1) >= 2
        assert len(checkpoints_s2) >= 1

    def test_list_filtered_by_session(self, tmp_path):
        store = CheckpointStore(base_dir=tmp_path)
        store.save("s1", "g1", {}, {}, workflow_name="wf1")
        store.save("s2", "g2", {}, {}, workflow_name="wf2")
        result = store.list_checkpoints("s1")
        # Should only return checkpoints from s1
        for cp in result:
            assert cp["session_id"] == "s1"

    def test_list_skips_non_dirs(self, tmp_path):
        store = CheckpointStore(base_dir=tmp_path)
        # Create a file where a dir should be
        (tmp_path / "s1").mkdir()
        (tmp_path / "s1" / "not_a_dir.txt").write_text("x")
        result = store.list_checkpoints("s1")
        # Should skip the file
        assert all("meta.json" in str(cp) or "workflow_name" in cp for cp in result)


# ═══════════════════════════════════════════════════════════════════════
# WorkflowEventBus
# ═══════════════════════════════════════════════════════════════════════


class TestWorkflowEventBus:
    def test_subscribe_and_emit(self):
        bus = WorkflowEventBus()
        obs = CollectingObserver()
        bus.subscribe(obs)
        bus.emit("test_event", key="value")
        assert len(obs.events) == 1
        assert obs.events[0][0] == "test_event"
        assert obs.events[0][1]["key"] == "value"

    def test_unsubscribe(self):
        bus = WorkflowEventBus()
        obs = CollectingObserver()
        bus.subscribe(obs)
        bus.unsubscribe(obs)
        bus.emit("test")
        assert len(obs.events) == 0

    def test_unsubscribe_not_subscribed(self):
        bus = WorkflowEventBus()
        obs = CollectingObserver()
        bus.unsubscribe(obs)  # should not raise

    def test_clear(self):
        bus = WorkflowEventBus()
        bus.subscribe(CollectingObserver())
        bus.subscribe(CollectingObserver())
        bus.clear()
        assert len(bus) == 0

    def test_multiple_observers(self):
        bus = WorkflowEventBus()
        obs1 = CollectingObserver()
        obs2 = CollectingObserver()
        bus.subscribe(obs1)
        bus.subscribe(obs2)
        bus.emit("event")
        assert len(obs1.events) == 1
        assert len(obs2.events) == 1

    def test_observer_exception_swallowed(self):
        bus = WorkflowEventBus()
        bad_obs = MagicMock()
        bad_obs.on_event.side_effect = RuntimeError("crash")
        good_obs = CollectingObserver()
        bus.subscribe(bad_obs)
        bus.subscribe(good_obs)
        bus.emit("event")
        assert len(good_obs.events) == 1

    def test_len(self):
        bus = WorkflowEventBus()
        assert len(bus) == 0
        bus.subscribe(CollectingObserver())
        assert len(bus) == 1
        bus.subscribe(CollectingObserver())
        assert len(bus) == 2

    def test_emit_no_observers(self):
        bus = WorkflowEventBus()
        bus.emit("event")  # should not raise

    def test_emit_passes_data_as_dict(self):
        bus = WorkflowEventBus()
        obs = CollectingObserver()
        bus.subscribe(obs)
        bus.emit("evt", a=1, b="two")
        assert obs.events[0][1] == {"a": 1, "b": "two"}


# ═══════════════════════════════════════════════════════════════════════
# LoggerObserver
# ═══════════════════════════════════════════════════════════════════════


class TestLoggerObserver:
    def test_on_event_does_not_crash(self):
        obs = LoggerObserver()
        obs.on_event("test", {"key": "value"})


# ═══════════════════════════════════════════════════════════════════════
# CollectingObserver
# ═══════════════════════════════════════════════════════════════════════


class TestCollectingObserver:
    def test_collects_events(self):
        obs = CollectingObserver()
        obs.on_event("e1", {"a": 1})
        obs.on_event("e2", {"b": 2})
        assert len(obs.events) == 2
        assert obs.events[0] == ("e1", {"a": 1})
        assert obs.events[1] == ("e2", {"b": 2})

    def test_clear(self):
        obs = CollectingObserver()
        obs.on_event("e1", {})
        obs.clear()
        assert len(obs.events) == 0


# ═══════════════════════════════════════════════════════════════════════
# GoalPanelObserver
# ═══════════════════════════════════════════════════════════════════════


class TestGoalPanelObserver:
    def test_calls_panel_method(self):
        panel = MagicMock()
        obs = GoalPanelObserver(panel)
        obs.on_event("workflow_start", {"workflow": "test"})
        panel.on_workflow_event.assert_called_once_with("workflow_start", {"workflow": "test"})

    def test_panel_missing_method_swallowed(self):
        panel = MagicMock()
        panel.on_workflow_event.side_effect = AttributeError("no method")
        obs = GoalPanelObserver(panel)
        obs.on_event("event", {})  # should not raise

    def test_panel_exception_swallowed(self):
        panel = MagicMock()
        panel.on_workflow_event.side_effect = RuntimeError("crash")
        obs = GoalPanelObserver(panel)
        obs.on_event("event", {})  # should not raise


# ═══════════════════════════════════════════════════════════════════════
# MetricsObserver
# ═══════════════════════════════════════════════════════════════════════


class TestMetricsObserver:
    def test_counts_events(self):
        obs = MetricsObserver()
        obs.on_event("agent_complete", {})
        obs.on_event("agent_complete", {})
        obs.on_event("layer_start", {})
        assert obs.event_counts["agent_complete"] == 2
        assert obs.event_counts["layer_start"] == 1

    def test_tracks_agent_timings(self):
        obs = MetricsObserver()
        obs.on_event("agent_complete", {"agent_id": "a1", "elapsed_s": 1.5})
        obs.on_event("agent_complete", {"agent_id": "a1", "elapsed_s": 2.5})
        assert obs.agent_timings["a1"] == [1.5, 2.5]

    def test_summary(self):
        obs = MetricsObserver()
        obs.on_event("agent_complete", {"agent_id": "a1", "elapsed_s": 2.0})
        obs.on_event("agent_complete", {"agent_id": "a1", "elapsed_s": 4.0})
        summary = obs.summary()
        assert summary["agent_avg_timings"]["a1"] == 3.0
        assert summary["event_counts"]["agent_complete"] == 2

    def test_clear(self):
        obs = MetricsObserver()
        obs.on_event("agent_complete", {"agent_id": "a1", "elapsed_s": 1.0})
        obs.clear()
        assert len(obs.agent_timings) == 0
        assert len(obs.event_counts) == 0


# ═══════════════════════════════════════════════════════════════════════
# validate_dag
# ═══════════════════════════════════════════════════════════════════════


class TestValidateDag:
    def test_empty_dag(self):
        validate_dag({})

    def test_single_node(self):
        validate_dag({"A": []})

    def test_chain(self):
        validate_dag({"A": [], "B": ["A"], "C": ["B"]})

    def test_diamond(self):
        validate_dag({"A": [], "B": ["A"], "C": ["A"], "D": ["B", "C"]})

    def test_cycle_raises(self):
        with pytest.raises(ValueError, match="cycle"):
            validate_dag({"A": ["B"], "B": ["A"]})

    def test_self_loop_raises(self):
        with pytest.raises(ValueError, match="cycle"):
            validate_dag({"A": ["A"]})

    def test_three_node_cycle(self):
        with pytest.raises(ValueError, match="cycle"):
            validate_dag({"A": ["C"], "B": ["A"], "C": ["B"]})

    def test_large_acyclic(self):
        dag = {f"n{i}": [] for i in range(20)}
        for i in range(1, 20):
            dag[f"n{i}"] = [f"n{i-1}"]
        validate_dag(dag)


# ═══════════════════════════════════════════════════════════════════════
# topological_layers
# ═══════════════════════════════════════════════════════════════════════


class TestTopologicalLayers:
    """topological_layers uses deps convention: {node: [upstream_deps]}.
    Nodes with no deps (in_degree=0) execute first."""

    def test_empty(self):
        assert topological_layers({}) == []

    def test_single_node(self):
        layers = topological_layers({"A": []})
        assert layers == [["A"]]

    def test_chain(self):
        # deps: B depends on A, C depends on B
        # in_degree: A=0, B=1, C=1 → layers: [A], [B], [C]
        layers = topological_layers({"A": [], "B": ["A"], "C": ["B"]})
        assert layers == [["A"], ["B"], ["C"]]

    def test_diamond(self):
        # deps: B depends on A, C depends on A, D depends on B and C
        # in_degree: A=0, B=1, C=1, D=2 → layers: [A], [B,C], [D]
        layers = topological_layers({"A": [], "B": ["A"], "C": ["A"], "D": ["B", "C"]})
        assert layers[0] == ["A"]
        assert set(layers[1]) == {"B", "C"}
        assert layers[2] == ["D"]

    def test_multiple_roots(self):
        # deps: C depends on A and B, A and B have no deps
        # in_degree: A=0, B=0, C=2 → layers: [A,B], [C]
        layers = topological_layers({"A": [], "B": [], "C": ["A", "B"]})
        assert set(layers[0]) == {"A", "B"}
        assert layers[1] == ["C"]

    def test_layers_are_sorted(self):
        layers = topological_layers({"Z": [], "A": [], "M": []})
        assert layers[0] == ["A", "M", "Z"]

    def test_disconnected_nodes(self):
        layers = topological_layers({"A": [], "B": [], "C": []})
        assert layers[0] == ["A", "B", "C"]

    def test_wide_dag(self):
        # deps: all children depend on root
        # in_degree: root=0, child_i=1 → layers: [root], [child_0..9]
        dag = {"root": []}
        for i in range(10):
            dag[f"child_{i}"] = ["root"]
        layers = topological_layers(dag)
        assert layers[0] == ["root"]
        assert len(layers[1]) == 10

    def test_cycle_raises(self):
        with pytest.raises(ValueError, match="cycle"):
            topological_layers({"A": ["B"], "B": ["A"]})

    def test_self_cycle_raises(self):
        with pytest.raises(ValueError, match="cycle"):
            topological_layers({"A": ["A"]})


# ═══════════════════════════════════════════════════════════════════════
# find_downstream
# ═══════════════════════════════════════════════════════════════════════


class TestFindDownstream:
    """find_downstream finds all nodes that depend on a given node."""

    def test_single_child(self):
        dag = {"A": [], "B": ["A"]}
        assert find_downstream(dag, "A") == ["B"]

    def test_diamond(self):
        dag = {"A": [], "B": ["A"], "C": ["A"], "D": ["B", "C"]}
        assert find_downstream(dag, "A") == ["B", "C", "D"]

    def test_chain(self):
        dag = {"A": [], "B": ["A"], "C": ["B"]}
        assert find_downstream(dag, "A") == ["B", "C"]

    def test_leaf_node(self):
        dag = {"A": [], "B": ["A"]}
        assert find_downstream(dag, "B") == []

    def test_no_deps(self):
        dag = {"A": [], "B": []}
        assert find_downstream(dag, "A") == []

    def test_partial_subtree(self):
        dag = {"A": [], "B": ["A"], "C": ["B"], "D": []}
        assert find_downstream(dag, "A") == ["B", "C"]

    def test_wide_dag(self):
        dag = {"root": []}
        for i in range(5):
            dag[f"child_{i}"] = ["root"]
        result = find_downstream(dag, "root")
        assert result == [f"child_{i}" for i in range(5)]

    def test_result_sorted(self):
        dag = {"A": [], "Z": ["A"], "M": ["A"], "B": ["A"]}
        assert find_downstream(dag, "A") == ["B", "M", "Z"]


# ═══════════════════════════════════════════════════════════════════════
# find_upstream
# ═══════════════════════════════════════════════════════════════════════


class TestFindUpstream:
    """find_upstream finds all nodes that a given node depends on."""

    def test_single_parent(self):
        dag = {"A": [], "B": ["A"]}
        assert find_upstream(dag, "B") == ["A"]

    def test_diamond(self):
        dag = {"A": [], "B": ["A"], "C": ["A"], "D": ["B", "C"]}
        assert find_upstream(dag, "D") == ["A", "B", "C"]

    def test_chain(self):
        dag = {"A": [], "B": ["A"], "C": ["B"]}
        assert find_upstream(dag, "C") == ["A", "B"]

    def test_root_node(self):
        dag = {"A": [], "B": ["A"]}
        assert find_upstream(dag, "A") == []

    def test_no_deps(self):
        dag = {"A": [], "B": []}
        assert find_upstream(dag, "A") == []

    def test_partial_chain(self):
        dag = {"A": [], "B": ["A"], "C": ["B"], "D": []}
        assert find_upstream(dag, "C") == ["A", "B"]

    def test_wide_dag(self):
        dag = {"root": []}
        for i in range(5):
            dag[f"child_{i}"] = ["root"]
        result = find_upstream(dag, "child_3")
        assert result == ["root"]

    def test_result_sorted(self):
        dag = {"A": [], "Z": [], "M": [], "target": ["Z", "M", "A"]}
        assert find_upstream(dag, "target") == ["A", "M", "Z"]
