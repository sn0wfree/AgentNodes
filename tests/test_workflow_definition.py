"""Tests for WorkflowDefinition: model, validation, graph cutting, preset conversion.

Design: docs/design/workflow-module-design.md
"""

from __future__ import annotations

import json
import pathlib

import pytest

from agentnodes.kernel.workflow.definition import (
    DEFAULT_PARAMS,
    WorkflowDefinition,
    WorkflowDefinitionError,
)


def make_def(nodes, edges, **kwargs):
    data = {
        "name": kwargs.pop("name", "test_wf"),
        "nodes": nodes,
        "edges": edges,
    }
    data.update(kwargs)
    return WorkflowDefinition.from_dict(data)


def llm(nid, role="researcher", **cfg):
    base = {"role": role}
    base.update(cfg)
    return {"id": nid, "type": "llm_agent", "label": nid, "config": base}


def approval(nid):
    return {"id": nid, "type": "approval", "label": nid, "config": {}}


def planner(nid, **cfg):
    return {"id": nid, "type": "planner", "label": nid, "config": cfg}


def evaluator(nid, **cfg):
    return {"id": nid, "type": "evaluator", "label": nid, "config": cfg}


def python_node(nid, fn="my_func"):
    return {"id": nid, "type": "python", "label": nid, "config": {"function": fn}}


def tool_node(nid, tool="run_backtest"):
    return {"id": nid, "type": "tool", "label": nid, "config": {"tool": tool}}


# ── Validation ─────────────────────────────────────────────────


class TestValidation:
    def test_valid_chain(self):
        d = make_def(
            [llm("a"), approval("p"), evaluator("e")],
            [{"source": "a", "target": "p"}, {"source": "p", "target": "e"}],
        )
        assert d.validate() == []

    def test_empty_nodes(self):
        d = make_def([], [])
        assert any("nodes must not be empty" in e for e in d.validate())

    def test_unknown_type(self):
        d = make_def([{"id": "x", "type": "magic"}], [])
        assert any("unknown type 'magic'" in e for e in d.validate())

    def test_duplicate_id(self):
        d = make_def([llm("a"), llm("a")], [])
        assert any("duplicate node id 'a'" in e for e in d.validate())

    def test_missing_required_config(self):
        d = make_def([{"id": "x", "type": "llm_agent", "config": {}}], [])
        assert any("missing required config 'role'" in e for e in d.validate())
        d2 = make_def([{"id": "x", "type": "tool", "config": {}}], [])
        assert any("missing required config 'tool'" in e for e in d2.validate())
        d3 = make_def([{"id": "x", "type": "python", "config": {}}], [])
        assert any("missing required config 'function'" in e for e in d3.validate())

    def test_singleton_types(self):
        d = make_def([planner("p1"), planner("p2")], [])
        assert any("'planner' may appear at most once" in e for e in d.validate())
        d2 = make_def([evaluator("e1"), evaluator("e2")], [])
        assert any("'evaluator' may appear at most once" in e for e in d2.validate())
        d3 = make_def([approval("a1"), approval("a2")], [])
        assert any("'approval' may appear at most once" in e for e in d3.validate())

    def test_cycle_detected(self):
        d = make_def(
            [llm("a"), llm("b")],
            [{"source": "a", "target": "b"}, {"source": "b", "target": "a"}],
        )
        assert any("cycle" in e for e in d.validate())

    def test_orphan_node(self):
        d = make_def(
            [llm("a"), llm("b")],
            [{"source": "a", "target": "b"}],
        )
        # b is connected; make a real orphan
        d2 = make_def([llm("a"), llm("c")], [])
        assert any("orphaned" in e for e in d2.validate())
        assert d.validate() == []

    def test_edge_missing_endpoints(self):
        d = make_def([llm("a")], [{"source": "a", "target": "ghost"}])
        assert any("target 'ghost' not found" in e for e in d.validate())

    def test_self_loop(self):
        d = make_def([llm("a")], [{"source": "a", "target": "a"}])
        assert any("self-loop" in e for e in d.validate())

    def test_params_value_domain(self):
        d = make_def([llm("a")], [], params={"planner": {"max_steps": 12}})
        assert any("max_steps" in e for e in d.validate())
        d2 = make_def([llm("a")], [], params={"llm": {"temperature": 5}})
        assert any("temperature" in e for e in d2.validate())

    def test_default_params_merge(self):
        d = make_def([llm("a")], [], params={"planner": {"max_steps": 8}})
        assert d.params["planner"]["max_steps"] == 8
        assert d.params["exec"]["max_segments"] == DEFAULT_PARAMS["exec"]["max_segments"]
        assert d.params["summary"]["max_chars"] == 300


# ── Graph cutting ──────────────────────────────────────────────


class TestSegmentCut:
    def test_no_approval_single_segment(self):
        d = make_def(
            [llm("a"), evaluator("e")],
            [{"source": "a", "target": "e"}],
        )
        segs = d.segment_cut()
        assert len(segs) == 1
        assert segs[0].node_ids == ["a", "e"]
        assert segs[0].approval_after is None
        assert segs[0].inputs == []

    def test_single_approval_two_segments(self):
        d = make_def(
            [llm("a"), approval("p"), llm("b")],
            [{"source": "a", "target": "p"}, {"source": "p", "target": "b"}],
        )
        segs = d.segment_cut()
        assert len(segs) == 2
        assert segs[0].node_ids == ["a"]
        assert segs[0].approval_after is None
        assert segs[1].node_ids == ["b"]
        assert segs[1].approval_after == "p"
        assert segs[1].inputs == ["a"]  # walks back through the approval

    def test_approval_with_no_upstream_is_noop(self):
        d = make_def(
            [approval("p"), llm("b")],
            [{"source": "p", "target": "b"}],
        )
        segs = d.segment_cut()
        assert len(segs) == 1
        assert segs[0].approval_after is None

    def test_approval_in_middle_of_chain(self):
        d = make_def(
            [llm("a"), llm("b"), approval("p"), llm("c"), llm("d")],
            [
                {"source": "a", "target": "b"},
                {"source": "b", "target": "p"},
                {"source": "p", "target": "c"},
                {"source": "c", "target": "d"},
            ],
        )
        segs = d.segment_cut()
        assert [s.node_ids for s in segs] == [["a", "b"], ["c", "d"]]
        assert segs[1].inputs == ["b"]

    def test_parallel_branches_merge_after_approval(self):
        d = make_def(
            [llm("a"), llm("b"), approval("p"), llm("c")],
            [
                {"source": "a", "target": "p"},
                {"source": "b", "target": "p"},
                {"source": "p", "target": "c"},
            ],
        )
        segs = d.segment_cut()
        assert [s.node_ids for s in segs] == [["a", "b"], ["c"]]
        assert sorted(segs[1].inputs) == ["a", "b"]


# ── SwarmPreset conversion ─────────────────────────────────────


class TestToSwarmPreset:
    def test_llm_nodes_default_executor(self):
        d = make_def(
            [llm("a", role="researcher", tools=["read"]), tool_node("t", "run_backtest")],
            [{"source": "a", "target": "t"}],
        )
        preset = d.to_swarm_preset(d.segment_cut()[0], "objective")
        assert preset.name == "test_wf#seg0"
        assert [a.agent_name for a in preset.agents] == ["a", "t"]
        assert preset.dag["t"] == ["a"]
        assert preset.dag["a"] == []
        llm_call = preset.agents[0]
        assert llm_call.context["node_type"] == "llm_agent"
        assert llm_call.context["role"] == "researcher"
        assert "executor_type" not in llm_call.context
        tool_call = preset.agents[1]
        assert tool_call.context["executor_type"] == "python_executor"
        assert tool_call.context["python_function"] == "run_backtest"

    def test_python_node(self):
        d = make_def([python_node("f", "compute_metrics")], [])
        preset = d.to_swarm_preset(d.segment_cut()[0], "objective")
        assert preset.agents[0].context["python_function"] == "compute_metrics"

    def test_budget_passthrough(self):
        d = make_def([llm("a")], [], budget={"token": 1000, "turn": 5, "time_seconds": 60})
        preset = d.to_swarm_preset(d.segment_cut()[0], "objective")
        assert preset.budget_token == 1000
        assert preset.budget_turn == 5
        assert preset.budget_time_seconds == 60


# ── File I/O ───────────────────────────────────────────────────


class TestFileIO:
    def test_roundtrip(self, tmp_path: pathlib.Path):
        path = tmp_path / "demo.json"
        d = make_def(
            [llm("a"), approval("p"), evaluator("e")],
            [{"source": "a", "target": "p"}, {"source": "p", "target": "e"}],
        )
        d.save(path)
        loaded = WorkflowDefinition.load(path, source="user")
        assert loaded.name == "test_wf"
        assert [n.id for n in loaded.nodes] == ["a", "p", "e"]
        assert loaded.source == "user"
        assert loaded.validate() == []

    def test_load_invalid_json(self, tmp_path: pathlib.Path):
        path = tmp_path / "bad.json"
        path.write_text("{not json", encoding="utf-8")
        with pytest.raises(WorkflowDefinitionError, match="invalid JSON"):
            WorkflowDefinition.load(path)

    def test_load_invalid_definition(self, tmp_path: pathlib.Path):
        path = tmp_path / "bad.json"
        path.write_text(json.dumps({"name": "x", "nodes": [{"id": "a", "type": "nope"}]}), encoding="utf-8")
        with pytest.raises(WorkflowDefinitionError, match="unknown type"):
            WorkflowDefinition.load(path)

    def test_save_rejects_invalid(self, tmp_path: pathlib.Path):
        d = make_def([{"id": "a", "type": "llm_agent", "config": {}}], [])
        with pytest.raises(WorkflowDefinitionError, match="invalid"):
            d.save(tmp_path / "x.json")


# ── AgentResult unified envelope (runtime extension) ───────────


class TestAgentResultEnvelope:
    def test_optional_fields_default(self):
        from agentnodes.kernel.swarm.runtime import AgentResult, AgentStatus
        r = AgentResult(agent_id="a")
        assert r.summary == ""
        assert r.artifacts == {}
        assert r.metrics == {}
        assert r.status == AgentStatus.PENDING

    def test_awaiting_status_value(self):
        from agentnodes.kernel.workflow.types import AgentStatus
        assert AgentStatus.AWAITING.value == "awaiting"
        assert AgentStatus("awaiting") == AgentStatus.AWAITING
