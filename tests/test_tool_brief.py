"""Paradigm v2: 工具说明书简略版 (brief) 与 tool_help 的测试。

- 注册时从 docstring 首行 + execute 签名/parameters + effects 生成 brief
- tool_help 按需返回 docstring 原文（详细版说明书）
- system prompt 工具目录使用 brief 渲染（轻量目录，细节按需 help）
"""
from __future__ import annotations

import json

import pytest

from agentnodes.kernel.agent.builtin_tools import (
    ToolHelpTool,
    build_default_registry,
)
from agentnodes.kernel.agent.context import ContextBuilder
from agentnodes.kernel.agent.tools import (
    EFFECT_DB,
    EFFECT_NET,
    BaseTool,
    ToolRegistry,
)
from agentnodes.foundation.llm import LLMConfig


class SampleEffectTool(BaseTool):
    """Sample tool with declared effects and explicit signature."""

    name = "sample_effect"
    category = "行情"
    effects = frozenset({EFFECT_DB, EFFECT_NET})

    def execute(self, strategy_name: str, limit: int = 5) -> str:
        return json.dumps({"status": "ok"})


class SampleKeywordTool(BaseTool):
    """Legacy tool with **kwargs signature (fallback to parameters.required)."""

    name = "sample_kwargs"
    parameters = {
        "type": "object",
        "properties": {"codes": {"type": "array", "description": "codes"}},
        "required": ["codes"],
    }

    def execute(self, **kwargs):
        return json.dumps({"status": "ok"})


class SampleReadonlyTool(BaseTool):
    """No effects declared → derived from is_readonly (True)."""

    name = "sample_ro"
    is_readonly = True

    def execute(self, **kwargs):
        return json.dumps({"status": "ok"})


@pytest.fixture
def registry() -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(SampleEffectTool())
    reg.register(SampleKeywordTool())
    reg.register(SampleReadonlyTool())
    return reg


# ── brief 生成 ───────────────────────────────────────────────────────


class TestBriefGeneration:

    def test_uses_docstring_first_line(self, registry):
        brief = registry.get("sample_effect").brief
        assert "Sample tool with declared effects and explicit signature." in brief

    def test_required_params_from_explicit_signature(self, registry):
        brief = registry.get("sample_effect").brief
        # limit has a default → not required; self excluded
        assert "必填: strategy_name" in brief
        assert "limit" not in brief

    def test_required_params_fallback_to_parameters(self, registry):
        brief = registry.get("sample_kwargs").brief
        assert "必填: codes" in brief

    def test_effects_labels(self, registry):
        brief = registry.get("sample_effect").brief
        assert "副作用: 写DB,网络" in brief
        assert "只读" not in brief

    def test_readonly_fallback(self, registry):
        brief = registry.get("sample_ro").brief
        assert "副作用: 只读" in brief

    def test_category_in_brief(self, registry):
        assert "[行情]" in registry.get("sample_effect").brief

    def test_all_registered_tools_have_brief(self):
        reg = build_default_registry()
        for name in reg.tool_names:
            tool = reg.get(name)
            assert tool.brief, f"{name} has no brief"
            assert f"- {name}[" in tool.brief, f"{name} brief malformed"
            assert "副作用:" in tool.brief, f"{name} brief lacks effects"


# ── tool_help ────────────────────────────────────────────────────────


class TestToolHelp:

    def test_returns_docstring_raw(self):
        # 批次 5：run_backtest 已随 apps/backtest 移除。改用仍存在的
        # read 工具验证「返回 docstring」这条契约。
        reg = build_default_registry()
        result = json.loads(reg.get("tool_help").execute(ctx=None, name="read"))
        assert result["status"] == "ok"
        assert result["name"] == "read"
        assert result["doc"]

    def test_unknown_tool_returns_error(self):
        """查询不存在的工具必须返回 error，而不是崩。

        2026-11-05 批次 6：run_backtest 已随业务工具归位 L2 重新注册，
        改用真正不存在的名字。
        """
        reg = build_default_registry()
        result = json.loads(reg.get("tool_help").execute(ctx=None, name="definitely_gone_tool_xyz"))
        assert result["status"] == "error"

    def test_unknown_tool(self):
        reg = build_default_registry()
        result = json.loads(reg.get("tool_help").execute(ctx=None, name="nope"))
        assert result["status"] == "error"
        assert "not found" in result["error"]
        assert "available" in result

    def test_missing_name(self):
        """缺必填参数由框架拦截 (TypeError → loop 重试/兜底)。"""
        reg = build_default_registry()
        with pytest.raises(TypeError):
            reg.get("tool_help").execute(ctx=None)

    def test_self_referential(self):
        reg = build_default_registry()
        result = json.loads(reg.get("tool_help").execute(ctx=None, name="tool_help"))
        assert result["status"] == "ok"
        assert "详细版说明书" in result["doc"]

    def test_registered_in_default_registry(self):
        reg = build_default_registry()
        tool = reg.get("tool_help")
        assert isinstance(tool, ToolHelpTool)
        assert tool.is_readonly is True
        assert tool.category == "技能"


# ── system prompt 目录渲染 ───────────────────────────────────────────


class TestToolListRendering:

    def test_tool_list_uses_briefs(self):
        reg = build_default_registry()
        builder = ContextBuilder(
            config=LLMConfig(api_key="sk-test"),
            registry=reg,
        )
        rendered = builder._format_tool_list()
        lines = [ln for ln in rendered.splitlines() if ln.strip()]
        assert len(lines) == len(reg.tool_names)
        for ln in lines:
            assert ln.startswith("- "), ln
            assert "[" in ln, ln
            assert "副作用:" in ln, ln

    def test_tool_list_contains_tool_help(self):
        reg = build_default_registry()
        builder = ContextBuilder(
            config=LLMConfig(api_key="sk-test"),
            registry=reg,
        )
        rendered = builder._format_tool_list()
        assert "- tool_help[技能]" in rendered
