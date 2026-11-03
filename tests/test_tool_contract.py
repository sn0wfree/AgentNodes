"""Paradigm v2 P5: 工具契约测试。

保证 注册表 ↔ 说明书 ↔ 副作用声明 ↔ schema 的一致性:
- 每个注册工具: name 唯一 / brief 完整 / category 已声明 / docstring 首行 = 用途
- 副作用: 写工具 effects 非空, 只读工具 effects 为空 (is_readonly 派生一致)
- schema: 必填参数与签名无默认值参数一致; 注入参数 (workspace 等) 不在 schema
- 引导同源: run_backtest/compute_factor 的说明书"错误处理范式"与代码 fix 的
  workflow 提示一致 (get_market_data 一步流程, 无 commit_market_data 残留)
"""
from __future__ import annotations

import inspect

import pytest

from strategy_research.kernel.agent.builtin_tools import build_default_registry
from strategy_research.core.agent.tools import (
    _INJECTED_PARAMS,
    EFFECT_DB,
    EFFECT_FS,
    EFFECT_NET,
)


def _all_tools():
    return build_default_registry().all_tools()


# ── 注册一致性 ───────────────────────────────────────────────────────


class TestRegistryContract:

    def test_names_unique(self):
        reg = build_default_registry()
        names = reg.tool_names
        assert len(names) == len(set(names))

    def test_brief_present_for_all(self):
        for tool in _all_tools():
            assert tool.brief, f"{tool.name} has no brief"
            assert tool.brief.startswith(f"- {tool.name}["), tool.name

    def test_category_declared(self):
        for tool in _all_tools():
            assert tool.category, f"{tool.name} has no category"
            assert tool.category != "other", f"{tool.name} still 'other'"

    def test_docstring_first_line_is_brief_summary(self):
        """docstring 首行与 brief 的用途一句话同源。"""
        for tool in _all_tools():
            first_line = inspect.getdoc(tool).strip().splitlines()[0]
            assert first_line, f"{tool.name} docstring empty"
            assert first_line[:20] in tool.brief, f"{tool.name} brief out of sync"

    def test_spec_sections_complete(self):
        """说明书 8 节完整性: 版本/变更行 + 6 个 ## 章节 (v2 范式模板)。"""
        sections = [
            "## 用途", "## 参数", "## 示例", "## 边界",
            "## 错误处理范式", "## 相关工具",
        ]
        for tool in _all_tools():
            doc = inspect.getdoc(tool) or ""
            assert "版本:" in doc, f"{tool.name} missing 版本 line"
            assert "变更:" in doc, f"{tool.name} missing 变更 line"
            missing = [s for s in sections if s not in doc]
            assert not missing, f"{tool.name} spec missing sections: {missing}"


# ── 副作用契约 ───────────────────────────────────────────────────────


class TestEffectsContract:

    @pytest.mark.parametrize("write_tool", [
        "write", "run_backtest", "get_market_data", "import_data",
        "clean_data", "create_goal", "add_evidence", "complete_goal",
        "shell",
    ])
    def test_write_tools_declare_effects(self, write_tool):
        tool = build_default_registry().get(write_tool)
        if tool is None:
            pytest.skip(f"{write_tool} not registered")
        assert tool.effects, f"{write_tool} is a write tool but has no effects"
        assert tool.is_readonly is False

    def test_readonly_tools_have_no_effects(self):
        for tool in _all_tools():
            if not tool.effects:
                assert tool.is_readonly is True, tool.name

    def test_effects_labels_valid(self):
        valid = {EFFECT_DB, EFFECT_FS, EFFECT_NET}
        for tool in _all_tools():
            for e in tool.effects:
                assert e in valid, f"{tool.name} invalid effect {e!r}"


# ── schema 契约 ──────────────────────────────────────────────────────


class TestSchemaContract:

    def test_injected_params_not_in_schema(self):
        for tool in _all_tools():
            schema = tool.to_openai_schema()["function"]["parameters"]
            props = schema.get("properties", {})
            for injected in _INJECTED_PARAMS - {"ctx", "self"}:
                assert injected not in props, f"{tool.name} schema leaks {injected}"

    def test_required_matches_signature_defaults(self):
        for tool in _all_tools():
            try:
                sig = inspect.signature(tool.execute)
            except (TypeError, ValueError, AttributeError):
                continue
            if any(
                p.kind in (inspect.Parameter.VAR_KEYWORD, inspect.Parameter.VAR_POSITIONAL)
                for p in sig.parameters.values()
            ):
                continue
            schema = tool.to_openai_schema()["function"]["parameters"]
            if tool.strict:
                # strict 模式 make_strict_schema 强制全部属性必填 (设计如此)
                continue
            required = set(schema.get("required", []))
            expected = {
                name for name, p in sig.parameters.items()
                if name not in _INJECTED_PARAMS and p.default is p.empty
            }
            assert required == expected, f"{tool.name} required {required} != {expected}"


# ── 引导同源 (fix_msg 与说明书错误处理范式) ─────────────────────────


class TestGuidanceConsistency:

    def test_run_backtest_spec_matches_workflow_fix(self):
        """run_backtest 说明书错误处理范式与代码 fix 的 workflow 一致。"""
        tool = build_default_registry().get("run_backtest")
        doc = inspect.getdoc(tool)
        assert "get_market_data" in doc
        assert "commit_market_data" not in doc
        # 代码 fix_msg 无残留（v1.2.0 拆分为组合步骤后, workflow 文案在
        # EngineRunStep / DataReadinessStep 中）
        from strategy_research.kernel.agent.builtin_tools import (
            DataReadinessStep,
            EngineRunStep,
            RunBacktestTool,
        )
        src = inspect.getsource(RunBacktestTool.execute)
        src += inspect.getsource(EngineRunStep.execute)
        src += inspect.getsource(DataReadinessStep.execute)
        assert "commit_market_data" not in src
        assert "get_market_data" in src

    def test_compute_factor_spec_matches_workflow_fix(self):
        tool = build_default_registry().get("compute_factor")
        doc = inspect.getdoc(tool)
        assert "get_market_data" in doc
        assert "commit_market_data" not in doc
        from strategy_research.kernel.agent.builtin_tools import ComputeFactorTool
        src = inspect.getsource(ComputeFactorTool.execute)
        assert "commit_market_data" not in src

    def test_no_commit_market_data_anywhere(self):
        """commit_market_data 已彻底退役。"""
        reg = build_default_registry()
        assert "commit_market_data" not in reg.tool_names
        for tool in reg.all_tools():
            doc = inspect.getdoc(tool) or ""
            assert "commit_market_data" not in doc, tool.name
