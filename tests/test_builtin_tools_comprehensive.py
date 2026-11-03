"""Builtin tools comprehensive tests — tool existence, schema, registry."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from strategy_research.core.agent.tools import ToolRegistry, BaseTool
from strategy_research.kernel.agent.builtin_tools import build_default_registry


# ── Options Pricing Tool ─────────────────────────────────────────


class TestOptionsPricingTool:
    def test_tool_exists(self):
        from strategy_research.kernel.agent.builtin_tools.options_tools import OptionsPricingTool
        tool = OptionsPricingTool()
        assert tool.name == "options_pricing"

    def test_tool_schema(self):
        from strategy_research.kernel.agent.builtin_tools.options_tools import OptionsPricingTool
        tool = OptionsPricingTool()
        schema = tool.to_openai_schema()
        assert "function" in schema
        props = schema["function"]["parameters"]["properties"]
        assert "spot" in props
        assert "strike" in props


# ── Data Clean Tool ──────────────────────────────────────────────


class TestDataCleanTool:
    def test_tool_exists(self):
        from strategy_research.kernel.agent.builtin_tools.data_clean_tools import DataCleanTool
        tool = DataCleanTool()
        assert tool.name == "clean_data"

    def test_tool_schema(self):
        from strategy_research.kernel.agent.builtin_tools.data_clean_tools import DataCleanTool
        tool = DataCleanTool()
        schema = tool.to_openai_schema()
        assert "function" in schema


# ── Help Tool ─────────────────────────────────────────────────────


class TestHelpTool:
    def test_tool_exists(self):
        from strategy_research.kernel.agent.builtin_tools.help_tools import ToolHelpTool
        r = ToolRegistry()
        tool = ToolHelpTool(r)
        assert tool.name == "tool_help"

    def test_tool_schema(self):
        from strategy_research.kernel.agent.builtin_tools.help_tools import ToolHelpTool
        r = ToolRegistry()
        tool = ToolHelpTool(r)
        schema = tool.to_openai_schema()
        assert "function" in schema


# ── File Tools ────────────────────────────────────────────────────


class TestFileTools:
    def test_read_file_exists(self):
        from strategy_research.kernel.agent.builtin_tools.file_tools import ReadFileTool
        tool = ReadFileTool()
        assert tool.name == "read"

    def test_list_files_exists(self):
        from strategy_research.kernel.agent.builtin_tools.file_tools import ListFilesTool
        tool = ListFilesTool()
        assert tool.name == "list"

    def test_write_file_exists(self):
        from strategy_research.kernel.agent.builtin_tools.file_tools import WriteFileTool
        tool = WriteFileTool()
        assert tool.name == "write"

    def test_read_file_schema(self):
        from strategy_research.kernel.agent.builtin_tools.file_tools import ReadFileTool
        tool = ReadFileTool()
        schema = tool.to_openai_schema()
        props = schema["function"]["parameters"]["properties"]
        assert "path" in props

    def test_write_file_schema(self):
        from strategy_research.kernel.agent.builtin_tools.file_tools import WriteFileTool
        tool = WriteFileTool()
        schema = tool.to_openai_schema()
        props = schema["function"]["parameters"]["properties"]
        assert "path" in props
        assert "content" in props


# ── Workspace Tools ──────────────────────────────────────────────


class TestWorkspaceTools:
    def test_git_diff_exists(self):
        from strategy_research.kernel.agent.builtin_tools.workspace_tools import GitDiffTool
        tool = GitDiffTool()
        assert tool.name == "git_diff"

    def test_list_history_exists(self):
        from strategy_research.kernel.agent.builtin_tools.workspace_tools import ListHistoryTool
        tool = ListHistoryTool()
        assert tool.name == "list_history"

    def test_list_skills_exists(self):
        from strategy_research.kernel.agent.builtin_tools.workspace_tools import ListSkillsTool
        tool = ListSkillsTool()
        assert tool.name == "list_skills"

    def test_load_skill_exists(self):
        from strategy_research.kernel.agent.builtin_tools.workspace_tools import LoadSkillTool
        tool = LoadSkillTool()
        assert tool.name == "skill"


# ── Todo Tools ────────────────────────────────────────────────────


class TestTodoTools:
    def test_tool_exists(self):
        from strategy_research.kernel.agent.builtin_tools.todo_tools import TodoWriteTool
        tool = TodoWriteTool()
        assert tool.name == "todowrite"

    def test_todo_store_set_and_get(self):
        from strategy_research.kernel.agent.builtin_tools.todo_tools import TodoStore
        TodoStore.set("test-session", [{"task": "item1", "done": False}])
        todos = TodoStore.get("test-session")
        assert len(todos) == 1
        assert todos[0]["task"] == "item1"
        TodoStore.clear("test-session")

    def test_todo_store_clear(self):
        from strategy_research.kernel.agent.builtin_tools.todo_tools import TodoStore
        TodoStore.set("test-session", [{"task": "item1", "done": False}])
        TodoStore.clear("test-session")
        todos = TodoStore.get("test-session")
        assert len(todos) == 0


# ── SubAgent Tool ─────────────────────────────────────────────────


class TestSubAgentTool:
    def test_tool_exists(self):
        from strategy_research.kernel.agent.builtin_tools.subagent_tool import SubAgentTool
        tool = SubAgentTool()
        assert tool.name == "task"


# ── Build Default Registry ────────────────────────────────────────


class TestBuildDefaultRegistry:
    def test_returns_tool_registry(self):
        r = build_default_registry()
        assert isinstance(r, ToolRegistry)

    def test_contains_core_tools(self):
        r = build_default_registry()
        assert r.get("read") is not None
        assert r.get("list") is not None
        assert r.get("write") is not None
        assert r.get("tool_help") is not None
        assert r.get("task") is not None
        assert r.get("todowrite") is not None

    def test_contains_analysis_tools(self):
        r = build_default_registry()
        assert r.get("options_pricing") is not None
        assert r.get("clean_data") is not None

    def test_tool_count(self):
        r = build_default_registry()
        assert len(r) >= 15
