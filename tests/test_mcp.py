"""Tests for MCP server."""

from __future__ import annotations

import json

import pytest

from agentnodes.interfaces.mcp.server import MCPServer, MCPTool


class TestMCPTool:
    def test_to_schema(self):
        tool = MCPTool(
            name="test_tool",
            description="A test tool",
            parameters={"type": "object", "properties": {"x": {"type": "string"}}},
            handler=lambda **kw: "ok",
        )
        schema = tool.to_schema()
        assert schema["name"] == "test_tool"
        assert schema["description"] == "A test tool"
        assert "properties" in schema["inputSchema"]


class TestMCPServer:
    def test_register_tool(self):
        server = MCPServer()
        tool = MCPTool(
            name="t1",
            description="Tool 1",
            parameters={"type": "object", "properties": {}},
            handler=lambda **kw: "result1",
        )
        server.register(tool)
        assert len(server.list_tools()) == 1

    def test_list_tools(self):
        server = MCPServer()
        server.register(MCPTool("a", "desc a", {"type": "object", "properties": {}}, lambda **kw: ""))
        server.register(MCPTool("b", "desc b", {"type": "object", "properties": {}}, lambda **kw: ""))
        tools = server.list_tools()
        assert len(tools) == 2
        names = {t["name"] for t in tools}
        assert "a" in names
        assert "b" in names

    def test_call_tool(self):
        server = MCPServer()
        server.register(MCPTool(
            "add",
            "Add two numbers",
            {"type": "object", "properties": {"a": {"type": "number"}, "b": {"type": "number"}}},
            lambda a=0, b=0, **kw: str(a + b),
        ))
        result = server.call_tool("add", {"a": 2, "b": 3})
        assert "5" in str(result)

    def test_call_tool_not_found(self):
        server = MCPServer()
        result = server.call_tool("nonexistent", {})
        assert "error" in result

    def test_call_tool_error(self):
        server = MCPServer()
        server.register(MCPTool(
            "fail",
            "Always fails",
            {"type": "object", "properties": {}},
            lambda **kw: 1 / 0,
        ))
        result = server.call_tool("fail", {})
        assert "error" in result


class TestMCPServerDefaultTools:
    def test_register_default_tools(self):
        server = MCPServer()
        server.register_default_tools()
        tools = server.list_tools()
        assert len(tools) >= 10  # At least 10 tools

    def test_tool_names(self):
        server = MCPServer()
        server.register_default_tools()
        tool_names = {t["name"] for t in server.list_tools()}
        assert "list_skills" in tool_names
        assert "skill" in tool_names
        # W1（2026-11-06 · 理念三能力自检）：run_backtest 属 tool.backtest 组，
        # 依赖 config.runner / data.readiness / backtest.from_yaml（批次 4 已删）
        # → 装配期整组不注册，MCP 面同步消失（server.py 的 try/except 降级路径）。
        assert "run_backtest" not in tool_names
        assert "search_memory" in tool_names
        assert "list_sessions" in tool_names
        assert "list_swarm_presets" in tool_names

    def test_list_skills_tool(self):
        server = MCPServer()
        server.register_default_tools()
        result = server.call_tool("list_skills", {})
        assert "content" in result
        # Should return JSON list
        text = result["content"][0]["text"]
        skills = json.loads(text)
        assert isinstance(skills, list)

    def test_list_swarm_presets_tool(self):
        server = MCPServer()
        server.register_default_tools()
        result = server.call_tool("list_swarm_presets", {})
        assert "content" in result
        text = result["content"][0]["text"]
        presets = json.loads(text)
        assert isinstance(presets, list)
        assert len(presets) >= 3  # At least 3 presets


class TestMCPCLI:
    def test_mcp_help(self):
        from agentnodes.interfaces.cli import main
        with pytest.raises(SystemExit) as exc_info:
            import sys
            sys.argv = ["agentnodes", "mcp", "--help"]
            main()
        assert exc_info.value.code == 0

    def test_mcp_list_tools_help(self):
        from agentnodes.interfaces.cli import main
        with pytest.raises(SystemExit) as exc_info:
            import sys
            sys.argv = ["agentnodes", "mcp", "list-tools", "--help"]
            main()
        assert exc_info.value.code == 0
