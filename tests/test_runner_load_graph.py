"""Tests for AutoresearchRunner._load_graph and graph fallback.

The runner's graph loading is critical for both phase and langgraph engines:
- Falls back to DEFAULT_STANDARD_GRAPH when graph.json is missing
- Falls back to DEFAULT_STANDARD_GRAPH when graph.json is malformed
- Uses DEFAULT_STANDARD_GRAPH when graph has validation errors
- Successfully loads valid graph.json
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from strategy_research.apps.study.runner import AutoresearchRunner
from strategy_research.apps.study.graph_templates import DEFAULT_STANDARD_GRAPH


def _make_runner():
    runner = AutoresearchRunner.__new__(AutoresearchRunner)
    study = MagicMock()
    study.study_id = "test-sid"
    study.workspace_path = "/tmp"
    study.strategy_name = "demo"
    study.session_id = "test-sid"
    runner.study = study
    runner._get_study = lambda: study
    runner.study_store = MagicMock()
    runner._emit = MagicMock()
    runner._load_graph = AutoresearchRunner._load_graph.__get__(runner)
    return runner


def test_fallback_when_no_graph_file(tmp_path):
    """Missing graph.json → fallback to DEFAULT_STANDARD_GRAPH."""
    study_dir = tmp_path / "study" / "test-sid"
    study_dir.mkdir(parents=True)
    runner = _make_runner()
    runner.study.workspace_path = str(tmp_path)
    graph = runner._load_graph(tmp_path, "test-sid")
    assert graph is not None
    assert graph == DEFAULT_STANDARD_GRAPH


def test_fallback_when_graph_is_malformed(tmp_path):
    """Malformed graph.json → fallback to DEFAULT_STANDARD_GRAPH."""
    study_dir = tmp_path / "study" / "test-sid"
    study_dir.mkdir(parents=True)
    (study_dir / "graph.json").write_text("not valid json {{{", encoding="utf-8")
    runner = _make_runner()
    runner.study.workspace_path = str(tmp_path)
    graph = runner._load_graph(tmp_path, "test-sid")
    assert graph == DEFAULT_STANDARD_GRAPH


def test_fallback_when_graph_has_validation_errors(tmp_path):
    """Graph with invalid edges → fallback to DEFAULT_STANDARD_GRAPH."""
    study_dir = tmp_path / "study" / "test-sid"
    study_dir.mkdir(parents=True)
    (study_dir / "graph.json").write_text(json.dumps({
        "nodes": [{"id": "nonexistent", "type": "llm_agent"}],
        "edges": [{"source": "nonexistent", "target": "nonexistent2"}],
    }), encoding="utf-8")
    runner = _make_runner()
    runner.study.workspace_path = str(tmp_path)
    graph = runner._load_graph(tmp_path, "test-sid")
    assert graph == DEFAULT_STANDARD_GRAPH


def test_loads_valid_graph(tmp_path):
    """Valid graph.json is loaded correctly."""
    study_dir = tmp_path / "study" / "test-sid"
    study_dir.mkdir(parents=True)
    (study_dir / "graph.json").write_text(json.dumps({
        "nodes": [
            {"id": "researcher", "type": "llm_agent", "label": "Researcher", "enabled": True},
            {"id": "strategist", "type": "planner", "label": "Strategist", "enabled": True},
        ],
        "edges": [
            {"source": "researcher", "target": "strategist"},
        ],
    }), encoding="utf-8")
    runner = _make_runner()
    runner.study.workspace_path = str(tmp_path)
    graph = runner._load_graph(tmp_path, "test-sid")
    assert graph is not None
    assert graph != DEFAULT_STANDARD_GRAPH
    node_ids = {n.id for n in graph.nodes}
    assert "researcher" in node_ids
    assert "strategist" in node_ids


def test_loads_graph_from_round_dir_fallback(tmp_path):
    """If study-level graph.json missing, check round dir."""
    study_dir = tmp_path / "study" / "test-sid"
    study_dir.mkdir(parents=True)
    runner = _make_runner()
    runner.study.workspace_path = str(tmp_path)
    graph = runner._load_graph(tmp_path, "test-sid")
    assert graph == DEFAULT_STANDARD_GRAPH
