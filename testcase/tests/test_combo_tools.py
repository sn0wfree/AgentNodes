"""Paradigm v2 P4: 组合工具（声明式配置 + 运行时实例化）。

- tools/combo/*.yml → CompositeTool → 注册 (简略版预置 + help 详细版同机制)
- 执行: registry 工具级调用子工具, 中间结果不进上下文
- 报错完整透传 + combo_step/combo_tool 定位
- effects = 子工具并集; 嵌套深度 = 1
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from strategy_research.kernel.agent.builtin_tools import build_default_registry
from strategy_research.kernel.agent.combo import (
    ComboConfigError,
    CompositeTool,
    _resolve_param,
    load_combo_tools,
)
from strategy_research.kernel.agent.tools import ToolContext, ToolRegistry


def _write_combo(workspace: Path, name: str, content: str) -> Path:
    d = workspace / "tools" / "combo"
    d.mkdir(parents=True, exist_ok=True)
    f = d / f"{name}.yml"
    f.write_text(content, encoding="utf-8")
    return f


READ_READ_COMBO = """\
name: read_two
description: 读取两个文件并返回第二个的内容
category: 文件
steps:
  - tool: read_file
    params:
      path: input.path1
  - tool: read_file
    params:
      path: input.path2
"""


class TestComboLoading:

    def test_load_registers_and_generates_brief(self, tmp_path: Path):
        ws = tmp_path / "ws"
        ws.mkdir()
        _write_combo(ws, "read_two", READ_READ_COMBO)
        reg = build_default_registry(workspace=ws)
        tool = reg.get("read_two")
        assert isinstance(tool, CompositeTool)
        assert tool.category == "文件"
        assert "read_two" in tool.brief
        assert "副作用: 只读" in tool.brief

    # test_effects_union 已随批次 5 删除：它用 get_market_data + run_backtest
    # 两个业务工具推导 effects 并集，两者都在批次 4/5 移除。

    def test_nesting_depth_one_rejected(self, tmp_path: Path):
        ws = tmp_path / "ws"
        ws.mkdir()
        _write_combo(ws, "combo1", READ_READ_COMBO)
        _write_combo(ws, "combo2", """\
name: nested
description: 引用组合工具
steps:
  - tool: read_two
    params: {}
""")
        reg = build_default_registry(workspace=ws)
        assert reg.get("nested") is None  # 被拒绝
        assert reg.get("read_two") is not None

    def test_invalid_config_skipped(self, tmp_path: Path):
        ws = tmp_path / "ws"
        ws.mkdir()
        _write_combo(ws, "bad", "name: bad\n")  # 缺 steps
        reg = build_default_registry(workspace=ws)
        assert reg.get("bad") is None

    def test_unknown_subtool_rejected(self, tmp_path: Path):
        ws = tmp_path / "ws"
        ws.mkdir()
        _write_combo(ws, "ghost", """\
name: ghost
description: 引用不存在的工具
steps:
  - tool: no_such_tool
    params: {}
""")
        reg = build_default_registry(workspace=ws)
        # 未知工具注册后执行时报错; 注册本身允许 (执行时定位)
        tool = reg.get("ghost")
        result = json.loads(tool.execute(ctx=ToolContext(workspace=ws)))
        assert result["status"] == "error"
        assert "no_such_tool" in result["error"]
        assert result["combo_step"] == 1

    def test_no_combo_dir_returns_zero(self, tmp_path: Path):
        ws = tmp_path / "ws"
        ws.mkdir()
        assert load_combo_tools(ws, ToolRegistry()) == 0

    def test_tool_help_returns_generated_spec(self, tmp_path: Path):
        ws = tmp_path / "ws"
        ws.mkdir()
        _write_combo(ws, "read_two", READ_READ_COMBO)
        reg = build_default_registry(workspace=ws)
        result = json.loads(reg.get("tool_help").execute(ctx=None, name="read_two"))
        assert result["status"] == "ok"
        assert "组合步骤" in result["doc"]
        assert "错误处理范式" in result["doc"]


class TestComboExecution:

    @pytest.fixture
    def ctx_ws(self, tmp_path: Path):
        ws = tmp_path / "ws"
        ws.mkdir()
        (ws / "a.txt").write_text("AAA")
        (ws / "b.txt").write_text("BBB")
        _write_combo(ws, "read_two", READ_READ_COMBO)
        reg = build_default_registry(workspace=ws)
        return reg, ws

    def test_success_returns_last_step_only(self, ctx_ws):
        """默认 (a): 中间结果不进上下文, 只返回最后一步。"""
        reg, ws = ctx_ws
        tool = reg.get("read_two")
        result = json.loads(tool.execute(
            ctx=ToolContext(workspace=ws), path1="a.txt", path2="b.txt",
        ))
        assert result["status"] == "ok"
        assert result["content"] == "BBB"
        # 第一步的 content 不在结果中
        assert "AAA" not in json.dumps(result)
        assert "combo_summary" not in result

    def test_with_summary(self, tmp_path: Path):
        ws = tmp_path / "ws"
        ws.mkdir()
        (ws / "a.txt").write_text("AAA")
        _write_combo(ws, "read_two", READ_READ_COMBO + "with_summary: true\n")
        reg = build_default_registry(workspace=ws)
        result = json.loads(reg.get("read_two").execute(
            ctx=ToolContext(workspace=ws), path1="a.txt", path2="a.txt",
        ))
        assert result["status"] == "ok"
        assert result["combo_summary"] == [
            {"step": 1, "tool": "read"},
            {"step": 2, "tool": "read"},
        ]

    def test_failure_passthrough_with_location(self, ctx_ws):
        """第二步失败 → 完整错误 + 失败步骤定位。"""
        reg, ws = ctx_ws
        result = json.loads(reg.get("read_two").execute(
            ctx=ToolContext(workspace=ws), path1="a.txt", path2="missing.txt",
        ))
        assert result["status"] == "error"
        assert "not found" in result["error"]
        assert result["combo_step"] == 2
        assert result["combo_tool"] == "read"
        assert result["combo"] == "read_two"

    def test_missing_input_reference(self, ctx_ws):
        reg, ws = ctx_ws
        result = json.loads(reg.get("read_two").execute(
            ctx=ToolContext(workspace=ws), path1="a.txt",
        ))
        assert result["status"] == "error"
        assert "missing reference" in result["error"]
        assert result["combo_step"] == 2

    def test_literal_params_passthrough(self, tmp_path: Path):
        ws = tmp_path / "ws"
        ws.mkdir()
        (ws / "x.txt").write_text("LIT")
        _write_combo(ws, "lit", """\
name: read_lit
description: 常量路径读取
category: 文件
steps:
  - tool: read_file
    params:
      path: x.txt
""")
        reg = build_default_registry(workspace=ws)
        result = json.loads(reg.get("read_lit").execute(ctx=ToolContext(workspace=ws)))
        assert result["status"] == "ok"
        assert result["content"] == "LIT"


class TestParamResolution:

    def test_input_ref(self):
        assert _resolve_param("input.codes", {"codes": ["A"]}, []) == ["A"]

    def test_input_nested(self):
        assert _resolve_param("input.a.b", {"a": {"b": 1}}, []) == 1

    def test_step_result_ref(self):
        results = [{"code": "600519.SH"}]
        assert _resolve_param("step1.result.code", {}, results) == "600519.SH"

    def test_literal_untouched(self):
        assert _resolve_param("hello", {}, []) == "hello"
        assert _resolve_param(5, {}, []) == 5
        assert _resolve_param([1, "input.a"], {"a": 2}, []) == [1, 2]

    def test_missing_ref_raises(self):
        with pytest.raises(KeyError):
            _resolve_param("input.nope", {}, [])


class TestComboConfigValidation:

    def test_missing_name(self, tmp_path: Path):
        ws = tmp_path / "ws"
        ws.mkdir()
        f = _write_combo(ws, "c", "steps:\n  - tool: read_file\n    params: {}\n")
        with pytest.raises(ComboConfigError):
            from strategy_research.kernel.agent.combo import _validate_config
            _validate_config({"steps": []}, f)
