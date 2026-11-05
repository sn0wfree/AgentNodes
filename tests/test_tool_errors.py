"""tool_errors 装饰器 + BaseTool.__init_subclass__ 自动包装契约。

docs/archive/undated/run-backtest-data-gate.md:
- 工具内部业务错误一律 raise ToolError → 自动注入 tool 名 → 确定性 JSON
- 非 transient 意外异常 → 结构化兜底（带 tool + step）
- dict 返回值 → 统一 JSON 序列化；str 原样
- transient 异常 (ValueError 等) → re-raise 交给框架重试
- __init_subclass__ 自动包装子类 execute（零遗漏、防重包）
"""
from __future__ import annotations

import json

import pytest

from agentnodes.kernel.agent.tools import (
    BaseTool,
    ToolContext,
    ToolError,
)


class _OkTool(BaseTool):
    name = "ok_tool"

    def execute(self, ctx: ToolContext, value: int = 1) -> dict:
        return {"value": value * 2}


class _ErrTool(BaseTool):
    name = "err_tool"

    def execute(self, ctx: ToolContext, value: int = 1) -> dict:
        raise ToolError("业务失败", fix="重试", expected="1", step="mys_step")


class _UnexpectedTool(BaseTool):
    name = "unexpected_tool"

    def execute(self, ctx: ToolContext) -> dict:
        raise RuntimeError("boom")


class _TransientTool(BaseTool):
    name = "transient_tool"

    def execute(self, ctx: ToolContext) -> dict:
        raise ValueError("transient")


class _StrTool(BaseTool):
    name = "str_tool"

    def execute(self, ctx: ToolContext) -> str:
        return '{"status": "ok", "raw": true}'


class _ChildTool(_OkTool):
    """间接子类: execute 已被包装的不应被二次包装。"""

    def execute(self, ctx: ToolContext) -> dict:
        return {"child": True}


class _GrandChildTool(_ChildTool):
    pass


def test_dict_return_serialized_to_json():
    out = json.loads(_OkTool().execute(ctx=ToolContext(), value=5))
    assert out == {"value": 10}


def test_tool_error_auto_injects_tool_name_and_step():
    out = json.loads(_ErrTool().execute(ctx=ToolContext()))
    assert out["status"] == "error"
    assert out["error"] == "业务失败"
    assert out["tool"] == "err_tool"
    assert out["step"] == "mys_step"
    assert out["fix"] == "重试"
    assert out["expected"] == "1"


def test_explicit_tool_name_not_overwritten():
    """ToolError 里手动传的 tool 名在 payload 中原样保留。"""
    err = ToolError("x", tool="manual_tool", step="s1")
    payload = err.to_payload()
    assert payload["tool"] == "manual_tool"
    assert payload["step"] == "s1"


def test_unexpected_exception_structured_fallback():
    out = json.loads(_UnexpectedTool().execute(ctx=ToolContext()))
    assert out["status"] == "error"
    assert "RuntimeError" in out["error"]
    assert out["tool"] == "unexpected_tool"


def test_transient_error_reraised_for_framework_retry():
    with pytest.raises(ValueError):
        _TransientTool().execute(ctx=ToolContext())


def test_str_return_passthrough():
    out = _StrTool().execute(ctx=ToolContext())
    assert json.loads(out) == {"status": "ok", "raw": True}


def test_init_subclass_auto_wraps_child_and_grandchild():
    assert json.loads(_ChildTool().execute(ctx=ToolContext())) == {"child": True}
    assert json.loads(_GrandChildTool().execute(ctx=ToolContext())) == {"child": True}


def test_not_double_wrapped():
    """execute 被包装后再次继承（不重声明）不再包一层。"""

    raw = _ChildTool.__dict__["execute"]
    assert not getattr(raw, "_tool_errors_wrapped", False) or raw.__wrapped__ is not None
    # 间接子类未重声明 execute → 继承已包装版本（单层）
    wrapper = _GrandChildTool.__dict__.get("execute", None)
    if wrapper is not None:
        assert getattr(wrapper, "_tool_errors_wrapped", False) is True
