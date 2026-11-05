"""Tests for the unified AgentPlugin system (Phase 2).

Covers: plugin dataclass round-trip, registry lookup + dependency
closure + selection validation, builtin plugin sanity (prompt files
exist, standard pipeline valid), AgentDAGConfig serialization and
StudyGraph interop.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from agentnodes.kernel.agent.builtin_plugins import (
    BUILTIN_PLUGINS,
    standard_pipeline_adjacency,
    standard_pipeline_plugin_ids,
)
from agentnodes.kernel.agent.dag_config import (
    AgentDAGConfig,
    AgentNodeConfig,
)
from agentnodes.kernel.agent.plugin import AgentPlugin
from agentnodes.kernel.agent.registry import (
    AgentPluginRegistry,
    get_default_registry,
)

TEMPLATES_DIR = (
    Path(__file__).parent.parent / "src" / "agentnodes" / "templates"
)


@pytest.fixture
def registry() -> AgentPluginRegistry:
    return get_default_registry()


# ── AgentPlugin dataclass ────────────────────────────────────────────


class TestAgentPlugin:
    def test_round_trip(self):
        p = AgentPlugin(
            id="x", name="X", category="research", description="d",
            prompt_file=".prompts/x.md", tools=("read",),
            requires=("researcher",), provides="x_output",
            executor_type="python", python_function="fn",
            default_timeout=42, default_max_iterations=3,
            default_max_retries=1, optional=False, keywords=("k",),
        )
        q = AgentPlugin.from_dict(p.to_dict())
        assert q == p

    def test_defaults(self):
        p = AgentPlugin(id="y", name="Y", category="tool", description="")
        assert p.executor_type == "llm"
        assert p.tools == ()
        assert p.optional is True
        assert p.default_timeout == 180


# ── Registry ─────────────────────────────────────────────────────────


class TestRegistry:
    def test_default_registry_has_builtins(self, registry):
        assert len(registry) == len(BUILTIN_PLUGINS)
        for p in BUILTIN_PLUGINS:
            assert registry.has(p.id)

    def test_get_unknown_returns_none(self, registry):
        assert registry.get("nope") is None

    def test_by_category(self, registry):
        research = registry.by_category("research")
        assert "researcher" in [p.id for p in research]

    def test_complete_dependencies_closure(self, registry):
        # Selecting risk_controller pulls backtest → strategist →
        # researcher through the requires closure.
        out = registry.complete_dependencies(["risk_controller"])
        assert set(out) >= {"risk_controller", "backtest", "strategist",
                            "researcher"}
        # Topological order: researcher before strategist before
        # backtest before risk_controller.
        assert out.index("researcher") < out.index("strategist")
        assert out.index("strategist") < out.index("backtest")
        assert out.index("backtest") < out.index("risk_controller")

    def test_complete_dependencies_drops_unknown(self, registry):
        out = registry.complete_dependencies(["researcher", "ghost"])
        assert "ghost" not in out
        assert "researcher" in out

    def test_validate_selection_ok(self, registry):
        errors = registry.validate_selection(
            standard_pipeline_plugin_ids()
        )
        assert errors == []

    def test_validate_selection_unknown_id(self, registry):
        errors = registry.validate_selection(["researcher", "ghost"])
        assert any("ghost" in e for e in errors)

    def test_validate_selection_missing_required(self, registry):
        # strategist is required (optional=False); selecting only
        # researcher misses it.
        errors = registry.validate_selection(["researcher"])
        assert any("strategist" in e for e in errors)

    def test_required_plugins_set(self, registry):
        required = {p.id for p in registry.list_plugins() if not p.optional}
        assert required == {"researcher", "strategist", "backtest",
                            "risk_controller"}


# ── Builtin plugins sanity ───────────────────────────────────────────


class TestBuiltinPlugins:
    def test_prompt_files_exist(self):
        for p in BUILTIN_PLUGINS:
            if not p.prompt_file:
                continue
            f = TEMPLATES_DIR / p.prompt_file
            assert f.is_file(), f"missing prompt file: {p.prompt_file}"

    def test_non_llm_plugins_declare_function(self):
        for p in BUILTIN_PLUGINS:
            if p.executor_type != "llm":
                assert p.python_function, f"{p.id} needs python_function"

    def test_standard_pipeline_valid(self, registry):
        adj = standard_pipeline_adjacency()
        ids = standard_pipeline_plugin_ids()
        assert set(adj.keys()) == set(ids)
        for nid, deps in adj.items():
            assert registry.has(nid)
            for d in deps:
                assert registry.has(d)

    def test_ids_unique(self):
        ids = [p.id for p in BUILTIN_PLUGINS]
        assert len(ids) == len(set(ids))


# ── AgentDAGConfig ───────────────────────────────────────────────────


class TestAgentDAGConfig:
    def make_standard(self, registry) -> AgentDAGConfig:
        adj = standard_pipeline_adjacency()
        return AgentDAGConfig(
            name="standard",
            description="test",
            nodes=[AgentNodeConfig(id=i) for i in standard_pipeline_plugin_ids()],
            dag=adj,
        )

    def test_validate_ok(self, registry):
        cfg = self.make_standard(registry)
        assert cfg.validate() == []

    def test_validate_unknown_node(self, registry):
        cfg = AgentDAGConfig(
            name="x",
            nodes=[AgentNodeConfig(id="ghost")],
            dag={"ghost": []},
        )
        errs = cfg.validate()
        assert any("ghost" in e for e in errs)

    def test_validate_cycle(self, registry):
        cfg = AgentDAGConfig(
            name="cyclic",
            nodes=[AgentNodeConfig(id="a"), AgentNodeConfig(id="b")],
            dag={"a": ["b"], "b": ["a"]},
        )
        errs = cfg.validate()
        assert any("cycle" in e for e in errs)

    def test_dict_round_trip(self, registry):
        cfg = self.make_standard(registry)
        cfg.nodes[0].timeout = 99
        cfg.nodes[1].tools_override = ["read"]
        cfg2 = AgentDAGConfig.from_dict(cfg.to_dict())
        assert cfg2.name == cfg.name
        assert cfg2.nodes[0].timeout == 99
        assert cfg2.nodes[1].tools_override == ["read"]
        assert cfg2.dag == cfg.dag
        assert cfg2.validate() == []

    # test_study_graph_round_trip / test_node_overrides_in_graph_config
    # 已随 apps/study 移除（2026-11-05 批次 3）：它们测的是
    # ``AgentDAGConfig.to_study_graph`` / ``from_study_graph``，而
    # ``StudyGraph`` 与 ``study.graph`` capability kind 都已归档/删除。

    def test_enabled_adjacency_drops_disabled(self, registry):
        cfg = self.make_standard(registry)
        for n in cfg.nodes:
            if n.id == "data_quality":
                n.enabled = False
        adj = cfg.enabled_adjacency()
        assert "data_quality" not in adj
        # factor_analyst's dep on data_quality is dropped.
        assert "data_quality" not in adj["factor_analyst"]
        # strategist's dep list loses data_quality too.
        assert "data_quality" not in adj["strategist"]

    def test_effective_plugin_lookup(self, registry):
        cfg = self.make_standard(registry)
        node = cfg.node_map()["strategist"]
        p = cfg.effective_plugin(node, registry)
        assert p is not None and p.id == "strategist"
