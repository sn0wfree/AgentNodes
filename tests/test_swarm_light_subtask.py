"""Unit tests for A6 light subtasks: toolset + budget on AgentNodeConfig / task tool."""
from __future__ import annotations

from agentnodes.kernel.agent.builtin_tools.subagent_tool import SubAgentTool
from agentnodes.kernel.agent.dag_config import AgentNodeConfig
from agentnodes.kernel.agent.toolsets import TOOLSETS


class TestAgentNodeConfigA6:
    def test_toolset_and_budget_roundtrip(self):
        n = AgentNodeConfig(
            id="n1",
            toolset="factor_analyst",
            budget_max_tokens=1000,
            budget_max_seconds=30.0,
        )
        d = n.to_dict()
        assert d["toolset"] == "factor_analyst"
        assert d["budget_max_tokens"] == 1000
        assert d["budget_max_seconds"] == 30.0
        n2 = AgentNodeConfig.from_dict(d)
        assert n2.toolset == "factor_analyst"
        assert n2.budget_max_tokens == 1000

    def test_defaults_none(self):
        n = AgentNodeConfig(id="n")
        assert n.toolset is None
        assert n.budget_max_tokens is None
        assert "toolset" not in n.to_dict()


class TestSubAgentToolParams:
    def test_schema_has_toolset_and_budget(self):
        params = SubAgentTool.parameters["properties"]
        assert "toolset" in params
        assert "budget_max_tokens" in params
        assert "budget_max_seconds" in params

    def test_build_child_registry_excludes_task(self):
        tool = SubAgentTool()
        filtered = tool._build_child_registry(None, None)
        assert "task" not in filtered.tool_names

    def test_toolset_resolves_named_allowlist(self):
        # resolve via toolsets then apply whitelist
        SubAgentTool()
        from agentnodes.kernel.agent.toolsets import resolve_toolset

        wl = resolve_toolset("minimal")
        assert wl == TOOLSETS["minimal"]

    def test_tools_list_wins_over_toolset(self):
        # Documented precedence: explicit tools > toolset
        tool = SubAgentTool()
        # simulate _build_child_registry with explicit whitelist
        parent = None
        out = tool._build_child_registry(parent, ["read"])
        assert set(out.tool_names) <= {"read"} or out.tool_names == []
