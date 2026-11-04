"""Tests for core/study/graph.py and core/study/graph_templates.py.

Covers:
  - GraphNode / GraphEdge round-trip (to_dict / from_dict)
  - StudyGraph.topological_layers() correctness (multi-entry / multi-exit)
  - StudyGraph.validate() for cycles, duplicate nodes, unknown edges
  - StudyGraph.entry_ids() / exit_ids() correctness
  - JSON round-trip (to_json / from_json / save / load)
  - graph_templates: all templates valid, have entry + exit
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from strategy_research.apps.study.graph import GraphEdge, GraphNode, StudyGraph
from strategy_research.apps.study.graph_templates import (
    DEFAULT_STANDARD_GRAPH,
    EXPLORE_GRAPH,
    MINIMAL_GRAPH,
    TEMPLATES,
    get_template,
)


# ── Node / Edge serialization ──────────────────────────────────────


class TestGraphNode:
    def test_round_trip(self):
        n = GraphNode(id="alpha", type="llm_agent", label="Alpha Agent",
                       config={"max_tokens": 5000}, enabled=False)
        d = n.to_dict()
        assert d == {
            "id": "alpha", "type": "llm_agent", "label": "Alpha Agent",
            "config": {"max_tokens": 5000}, "enabled": False,
        }
        n2 = GraphNode.from_dict(d)
        assert n2 == n

    def test_defaults(self):
        n = GraphNode(id="x", type="evaluator")
        assert n.label == ""
        assert n.config == {}
        assert n.enabled is True

    def test_from_dict_missing_optional_fields(self):
        d = {"id": "a", "type": "tool"}
        n = GraphNode.from_dict(d)
        assert n.label == ""
        assert n.enabled is True


class TestGraphEdge:
    def test_round_trip(self):
        e = GraphEdge(source="a", target="b", condition="skip_if_failed")
        d = e.to_dict()
        assert d == {"source": "a", "target": "b", "condition": "skip_if_failed"}
        e2 = GraphEdge.from_dict(d)
        assert e2 == e

    def test_condition_none_not_serialized(self):
        e = GraphEdge(source="a", target="b")
        d = e.to_dict()
        assert "condition" not in d
        e2 = GraphEdge.from_dict(d)
        assert e2.condition is None


# ── StudyGraph.topological_layers ──────────────────────────────────


class TestTopologicalLayers:
    def test_linear_chain(self):
        g = StudyGraph(
            nodes=(
                GraphNode("a", "llm_agent"),
                GraphNode("b", "evaluator"),
                GraphNode("c", "planner"),
            ),
            edges=(
                GraphEdge("a", "b"),
                GraphEdge("b", "c"),
            ),
        )
        layers = g.topological_layers()
        assert layers == [["a"], ["b"], ["c"]]

    def test_multi_entry_diamond(self):
        """A → B, A → C, B → D, C → D  (diamond shape)."""
        g = StudyGraph(
            nodes=(
                GraphNode("a", "llm_agent"),
                GraphNode("b", "evaluator"),
                GraphNode("c", "evaluator"),
                GraphNode("d", "planner"),
            ),
            edges=(
                GraphEdge("a", "b"),
                GraphEdge("a", "c"),
                GraphEdge("b", "d"),
                GraphEdge("c", "d"),
            ),
        )
        layers = g.topological_layers()
        assert layers == [["a"], ["b", "c"], ["d"]]

    def test_multi_entry_multi_exit(self):
        """Standard study graph: researcher fans out, risk fans out."""
        g = DEFAULT_STANDARD_GRAPH
        layers = g.topological_layers()
        # First layer is researcher
        assert layers[0] == ["researcher"]
        # Second layer is data_quality + factor_analyst (multi-entry)
        assert set(layers[1]) == {"data_quality", "factor_analyst"}
        # Last layer is attribution + anti_overfit (multi-exit)
        last = layers[-1]
        assert "attribution_analyst" in last
        assert "anti_overfit_analyst" in last

    def test_disconnected_graph(self):
        """Nodes with no edges should all be in layer 0."""
        g = StudyGraph(
            nodes=(
                GraphNode("x", "llm_agent"),
                GraphNode("y", "evaluator"),
            ),
            edges=(),
        )
        layers = g.topological_layers()
        assert sorted(layers[0]) == ["x", "y"]

    def test_empty_graph(self):
        g = StudyGraph(nodes=(), edges=())
        layers = g.topological_layers()
        assert layers == []


class TestEntryExitIds:
    def test_linear_chain(self):
        g = StudyGraph(
            nodes=(
                GraphNode("a", "llm_agent"),
                GraphNode("b", "evaluator"),
                GraphNode("c", "planner"),
            ),
            edges=(GraphEdge("a", "b"), GraphEdge("b", "c")),
        )
        assert g.entry_ids() == ["a"]
        assert g.exit_ids() == ["c"]

    def test_multi_entry_multi_exit(self):
        g = DEFAULT_STANDARD_GRAPH
        assert g.entry_ids() == ["researcher"]
        exits = g.exit_ids()
        assert "attribution_analyst" in exits
        assert "anti_overfit_analyst" in exits

    def test_count(self):
        g = DEFAULT_STANDARD_GRAPH
        assert g.entry_count() == 1
        assert g.exit_count() == 2


# ── StudyGraph.validate ────────────────────────────────────────────


class TestValidate:
    def test_valid_graph(self):
        assert DEFAULT_STANDARD_GRAPH.validate() == []

    def test_duplicate_node_ids(self):
        g = StudyGraph(
            nodes=(
                GraphNode("x", "llm_agent"),
                GraphNode("x", "evaluator"),  # duplicate!
            ),
            edges=(),
        )
        errors = g.validate()
        assert len(errors) == 1
        assert "duplicate" in errors[0]

    def test_unknown_source_in_edge(self):
        g = StudyGraph(
            nodes=(GraphNode("a", "llm_agent"),),
            edges=(GraphEdge("z", "a"),),
        )
        errors = g.validate()
        assert any("source not in nodes" in e for e in errors)

    def test_unknown_target_in_edge(self):
        g = StudyGraph(
            nodes=(GraphNode("a", "llm_agent"),),
            edges=(GraphEdge("a", "z"),),
        )
        errors = g.validate()
        assert any("target not in nodes" in e for e in errors)

    def test_cycle_detected(self):
        g = StudyGraph(
            nodes=(
                GraphNode("a", "llm_agent"),
                GraphNode("b", "evaluator"),
                GraphNode("c", "evaluator"),
            ),
            edges=(
                GraphEdge("a", "b"),
                GraphEdge("b", "c"),
                GraphEdge("c", "a"),  # cycle!
            ),
        )
        errors = g.validate()
        assert any("cycle" in e for e in errors)


# ── JSON round-trip + persistence ──────────────────────────────────


class TestSerialization:
    def test_json_round_trip(self):
        g = DEFAULT_STANDARD_GRAPH
        raw = g.to_json()
        g2 = StudyGraph.from_json(raw)
        assert g2.node_ids == g.node_ids
        assert len(g2.edges) == len(g.edges)

    def test_to_dict_matches_from_dict(self):
        g = DEFAULT_STANDARD_GRAPH
        d = g.to_dict()
        g2 = StudyGraph.from_dict(d)
        assert g2 == g

    def test_save_load(self, tmp_path):
        ws = tmp_path / "ws"
        ws.mkdir()
        study_id = "test-save-load"
        g = DEFAULT_STANDARD_GRAPH
        g.save(ws, study_id)

        # Verify file exists
        p = ws / "study" / study_id / "graph.json"
        assert p.is_file()

        # Load back
        g2 = StudyGraph.load(ws, study_id)
        assert g2 is not None
        assert g2.node_ids == g.node_ids
        assert len(g2.edges) == len(g.edges)

    def test_load_returns_none_when_missing(self, tmp_path):
        g = StudyGraph.load(tmp_path, "nonexistent")
        assert g is None

    def test_load_returns_none_on_corrupt_file(self, tmp_path):
        ws = tmp_path / "ws"
        study_id = "corrupt"
        (ws / "study" / study_id).mkdir(parents=True)
        (ws / "study" / study_id / "graph.json").write_text("NOT JSON!!!")
        g = StudyGraph.load(ws, study_id)
        assert g is None


# ── graph_templates ────────────────────────────────────────────────


class TestGraphTemplates:
    def test_standard_has_8_nodes(self):
        assert len(DEFAULT_STANDARD_GRAPH.nodes) == 8

    def test_standard_has_8_edges(self):
        assert len(DEFAULT_STANDARD_GRAPH.edges) == 8

    def test_standard_is_valid(self):
        assert DEFAULT_STANDARD_GRAPH.validate() == []

    def test_standard_has_multi_entry(self):
        """researcher fans out to data_quality + factor_analyst."""
        layers = DEFAULT_STANDARD_GRAPH.topological_layers()
        assert layers[0] == ["researcher"]
        assert set(layers[1]) == {"data_quality", "factor_analyst"}

    def test_standard_has_multi_exit(self):
        exits = DEFAULT_STANDARD_GRAPH.exit_ids()
        assert "attribution_analyst" in exits
        assert "anti_overfit_analyst" in exits

    def test_minimal_linear(self):
        layers = MINIMAL_GRAPH.topological_layers()
        assert layers == [["researcher"], ["strategist"], ["backtest"]]
        assert MINIMAL_GRAPH.entry_ids() == ["researcher"]
        assert MINIMAL_GRAPH.exit_ids() == ["backtest"]

    def test_explore_has_explore_node(self):
        node_ids = [n.id for n in EXPLORE_GRAPH.nodes]
        assert "explore" in node_ids

    def test_explore_valid(self):
        assert EXPLORE_GRAPH.validate() == []

    def test_all_templates_are_valid(self):
        for name, g in TEMPLATES.items():
            errors = g.validate()
            assert errors == [], f"template {name!r} has errors: {errors}"

    def test_get_template_returns_copy(self):
        g1 = get_template("standard")
        g2 = get_template("standard")
        assert g1 is not g2  # distinct instances
        assert g1.node_ids == g2.node_ids

    def test_get_template_unknown_falls_back_to_standard(self):
        g = get_template("nonexistent_template_xyz")
        assert g.node_ids == DEFAULT_STANDARD_GRAPH.node_ids


# ── StudyGraph.frozen (immutable) ─────────────────────────────────


class TestImmutability:
    def test_nodes_cannot_be_mutated(self):
        g = DEFAULT_STANDARD_GRAPH
        with pytest.raises(AttributeError):
            g.nodes = ()  # type: ignore[misc]

    def test_node_to_dict_returns_fresh_dict(self):
        """to_dict() returns independent dicts (no shared references)."""
        g = DEFAULT_STANDARD_GRAPH
        d1 = g.nodes[0].to_dict()
        d2 = g.nodes[0].to_dict()
        assert d1 == d2
        d1["id"] = "mutated"
        assert d2["id"] != "mutated"
