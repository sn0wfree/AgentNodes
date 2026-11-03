"""Tests for DAGPlanner + AI compose API (unified engine, Phase 6)."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from strategy_research.kernel.agent.plugin import AgentPlugin
from strategy_research.kernel.agent.registry import AgentPluginRegistry
from strategy_research.apps.study.dag_planner import (
    DAGPlanner,
    PlannerConstraints,
)


@pytest.fixture
def registry() -> AgentPluginRegistry:
    """Minimal registry with a handful of plugins for planner tests."""
    reg = AgentPluginRegistry()
    for pid, cat, kw in [
        ("researcher", "research", ("研究", "假设")),
        ("strategist", "execution", ("策略", "信号")),
        ("factor_analyst", "execution", ("因子", "IC")),
        ("risk_controller", "evaluation", ("风险",)),
        ("data_quality", "evaluation", ("数据", "质量")),
        ("backtest", "tool", ("回测",)),
    ]:
        reg.register(AgentPlugin(
            id=pid, name=pid, category=cat, description=pid,
            keywords=kw, optional=pid not in ("researcher", "strategist"),
        ))
    return reg


# ── Keyword fallback ────────────────────────────────────────────────


class TestKeywordFallback:
    def test_keyword_match(self, registry):
        planner = DAGPlanner(registry)
        plan = planner.plan("研究 A 股动量因子，目标 Calmar >= 0.5")
        assert "researcher" in plan.selected_agents
        assert "factor_analyst" in plan.selected_agents
        # Core required always present
        assert "strategist" in plan.selected_agents

    def test_no_keywords_returns_core(self, registry):
        planner = DAGPlanner(registry)
        plan = planner.plan("whatever")
        # Core required always present
        for pid in ("researcher", "strategist"):
            assert pid in plan.selected_agents

    def test_exclude_agents(self, registry):
        planner = DAGPlanner(registry)
        plan = planner.plan(
            "因子研究", PlannerConstraints(exclude_agents=["factor_analyst"]),
        )
        assert "factor_analyst" not in plan.selected_agents

    def test_force_agents(self, registry):
        planner = DAGPlanner(registry)
        plan = planner.plan(
            "x", PlannerConstraints(force_agents=["data_quality"]),
        )
        assert "data_quality" in plan.selected_agents

    def test_max_agents_truncates(self, registry):
        planner = DAGPlanner(registry)
        plan = planner.plan(
            "因子研究 风险 数据 回测",
            PlannerConstraints(max_agents=3),
        )
        assert len(plan.selected_agents) <= 3

    def test_completes_dependencies(self, registry):
        planner = DAGPlanner(registry)
        plan = planner.plan(
            "x", PlannerConstraints(force_agents=["factor_analyst"]),
        )
        # factor_analyst.requires=(researcher,) — closure adds it.
        assert "researcher" in plan.selected_agents


# ── Plan structure ──────────────────────────────────────────────────


class TestPlanResult:
    def test_graph_serializable(self, registry):
        planner = DAGPlanner(registry)
        plan = planner.plan("因子研究")
        d = plan.config.to_study_graph().to_dict()
        assert "nodes" in d and "edges" in d
        for node in d["nodes"]:
            assert {"id", "type", "label", "config", "enabled"} <= node.keys()

    def test_dag_is_acyclic(self, registry):
        planner = DAGPlanner(registry)
        plan = planner.plan("因子研究")
        from strategy_research.kernel.workflow.dag import validate_dag
        adj: dict[str, list[str]] = {}
        for node in plan.config.nodes:
            adj[node.id] = plan.config.dag.get(node.id, [])
        try:
            validate_dag(adj)
        except ValueError as exc:
            pytest.fail(f"DAG has cycle: {exc}")

    def test_llm_unavailable_falls_back(self, registry, monkeypatch):
        """When LLM is not available, the keyword path is used."""
        # Force should_use_real_llm() to return False
        from strategy_research.kernel.agent import role_factory

        monkeypatch.setattr(role_factory, "should_use_real_llm", lambda: False)
        planner = DAGPlanner(registry)
        plan = planner.plan("因子研究")
        assert plan.selected_agents  # non-empty


# ── LLM path (mocked) ───────────────────────────────────────────────


class TestLLMPath:
    def test_llm_parses_json_response(self, registry, monkeypatch):
        from strategy_research.kernel.agent import role_factory

        monkeypatch.setattr(role_factory, "should_use_real_llm", lambda: True)
        monkeypatch.setattr(
            role_factory, "run_agent_via_llm",
            lambda **kw: json.dumps(
                {"selected": ["researcher", "factor_analyst", "strategist"],
                 "reasoning": "因子研究"},
                ensure_ascii=False,
            ),
        )
        planner = DAGPlanner(registry)
        plan = planner.plan("因子研究")
        assert "factor_analyst" in plan.selected_agents

    def test_llm_code_fence_json_parsed(self, registry, monkeypatch):
        from strategy_research.kernel.agent import role_factory

        monkeypatch.setattr(role_factory, "should_use_real_llm", lambda: True)
        monkeypatch.setattr(
            role_factory, "run_agent_via_llm",
            lambda **kw: "```json\n"
                '{"selected": ["researcher", "strategist"]}\n```',
        )
        planner = DAGPlanner(registry)
        plan = planner.plan("anything")
        assert "researcher" in plan.selected_agents

    def test_llm_garbage_falls_back_to_keywords(self, registry, monkeypatch):
        from strategy_research.kernel.agent import role_factory

        monkeypatch.setattr(role_factory, "should_use_real_llm", lambda: True)
        monkeypatch.setattr(
            role_factory, "run_agent_via_llm",
            lambda **kw: "not parseable as json",
        )
        planner = DAGPlanner(registry)
        plan = planner.plan("因子研究")
        # Should still get keyword-based fallback
        assert plan.selected_agents


# ── Bootstrap auto_compose ──────────────────────────────────────────


class TestBootstrapAutoCompose:
    def test_auto_compose_writes_graph(self, tmp_path: Path, monkeypatch):
        from strategy_research.apps.study.bootstrap import init_study_dir

        # Setup workspace with strategy
        ws = tmp_path / "ws"
        (ws / "strategies" / "foo").mkdir(parents=True)
        (ws / "strategies" / "foo" / "strategy.py").write_text(
            "PARAMS = {}\nFACTOR_EXPRS = []\n",
            encoding="utf-8",
        )

        from strategy_research.kernel.agent import role_factory

        monkeypatch.setattr(role_factory, "should_use_real_llm", lambda: False)

        init_study_dir(
            ws, "st-auto", "foo", "研究 A 股动量因子",
            auto_compose=True,
        )
        graph_path = ws / "study" / "st-auto" / "graph.json"
        assert graph_path.exists()
        data = json.loads(graph_path.read_text(encoding="utf-8"))
        assert "nodes" in data and len(data["nodes"]) > 0

    def test_no_auto_compose_uses_default(self, tmp_path: Path):
        from strategy_research.apps.study.bootstrap import init_study_dir

        ws = tmp_path / "ws"
        (ws / "strategies" / "foo").mkdir(parents=True)
        (ws / "strategies" / "foo" / "strategy.py").write_text(
            "PARAMS = {}\n", encoding="utf-8",
        )

        init_study_dir(ws, "st-default", "foo", "anything")
        graph_path = ws / "study" / "st-default" / "graph.json"
        data = json.loads(graph_path.read_text(encoding="utf-8"))
        # DEFAULT_STANDARD_GRAPH has 8 agents
        assert len(data["nodes"]) == 8