"""Unit tests for A2 ToolGateway + named toolsets."""
from __future__ import annotations

import json

from agentnodes.kernel.agent.gateway import ToolGateway, toolset_for_role
from agentnodes.kernel.agent.tools import ToolRegistry
from agentnodes.kernel.agent.toolsets import (
    MINIMAL,
    TOOLSETS,
    apply_toolset,
    resolve_toolset,
    toolset_names,
)


class _FakeTool:
    def __init__(self, name: str, readonly: bool = True) -> None:
        self.name = name
        self.is_readonly = readonly
        self.brief = ""

    def to_openai_schema(self) -> dict:
        return {"type": "function", "function": {"name": self.name}}

    def invoke(self, params: dict) -> str:
        return json.dumps({"status": "ok", "tool": self.name})


def _registry(*names: str) -> ToolRegistry:
    r = ToolRegistry()
    for n in names:
        r.register(_FakeTool(n))
    return r


class TestResolveToolset:
    def test_none_is_full(self):
        assert resolve_toolset(None) is None

    def test_full_alias(self):
        assert resolve_toolset("full") is None
        assert resolve_toolset("all") is None

    def test_known_name(self):
        assert resolve_toolset("minimal") == TOOLSETS["minimal"]

    def test_unknown_falls_back_to_minimal(self):
        assert resolve_toolset("no_such_role") == TOOLSETS[MINIMAL]

    def test_explicit_list_passthrough(self):
        assert resolve_toolset(["a", "b"]) == ["a", "b"]

    def test_role_names_present(self):
        for role in ("researcher", "factor_analyst", "strategist", "critic"):
            assert role in TOOLSETS


class TestApplyToolset:
    def test_full_keeps_everything(self):
        r = _registry("read", "write", "run_backtest")
        assert len(apply_toolset(r, None)) == 3

    def test_filters_to_toolset(self):
        r = _registry("read", "write", "run_backtest")
        out = apply_toolset(r, ["read", "write"])
        assert set(out.tool_names) == {"read", "write"}

    def test_deny_overrides_allow(self):
        r = _registry("read", "write", "secret")
        out = apply_toolset(r, ["read", "write", "secret"], deny=["secret"])
        assert "secret" not in out.tool_names

    def test_deny_only(self):
        r = _registry("read", "write")
        out = apply_toolset(r, None, deny=["write"])
        assert out.tool_names == ["read"]


class TestToolGateway:
    def test_definitions_filtered(self):
        r = _registry("read", "write", "run_backtest")
        gw = ToolGateway(r, toolset=["read"])
        assert [d["function"]["name"] for d in gw.definitions()] == ["read"]

    def test_names_property(self):
        gw = ToolGateway(_registry("a", "b"), toolset=["a", "b"])
        assert gw.names == ["a", "b"]
        assert "a" in gw and "b" in gw

    def test_execute_available(self):
        gw = ToolGateway(_registry("read"), toolset=["read"])
        out = json.loads(gw.execute("read", {}))
        assert out["status"] == "ok"

    def test_execute_unavailable_returns_error_json(self):
        gw = ToolGateway(_registry("read", "write"), toolset=["read"])
        out = json.loads(gw.execute("write", {}))
        assert out["status"] == "error"
        assert "not available" in out["error"]

    def test_allow_narrows_toolset(self):
        r = _registry("read", "write", "help")
        gw = ToolGateway(r, toolset=["read", "write"], allow=["read"])
        assert gw.names == ["read"]

    def test_deny_applies_last(self):
        r = _registry("read", "write", "dangerous")
        gw = ToolGateway(r, toolset=["read", "write", "dangerous"], deny=["dangerous"])
        assert "dangerous" not in gw.names

    def test_describe(self):
        gw = ToolGateway(_registry("read"), toolset="minimal")
        info = gw.describe()
        assert info["toolset"] == "minimal"
        assert "read" in info["tools"] or info["tools"] == ["read"]


class TestRoleToolset:
    def test_known_roles_resolve(self):
        for role in ("researcher", "factor_analyst", "strategist"):
            assert toolset_for_role(role) == TOOLSETS[role]

    def test_unknown_role_is_minimal(self):
        assert toolset_for_role("nobody") == TOOLSETS[MINIMAL]

    def test_toolset_names_sorted(self):
        names = toolset_names()
        assert names == sorted(names)
        assert "minimal" in names


class TestToolsetHygiene:
    def test_all_toolsets_are_lists_of_str(self):
        for name, tools in TOOLSETS.items():
            assert isinstance(tools, list), name
            assert all(isinstance(t, str) and t for t in tools), name

    def test_minimal_is_small(self):
        assert len(TOOLSETS[MINIMAL]) <= 5

    def test_no_duplicate_tools_within_toolset(self):
        for name, tools in TOOLSETS.items():
            assert len(tools) == len(set(tools)), name
