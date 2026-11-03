"""Paradigm v2 P2: ToolContext + 框架统一容错层 + 错误兜底 + effects 派生。

- invoke 是统一执行入口: 容错 → execute → 意外异常结构化兜底
- transient 异常 re-raise 给 loop 重试; ToolError/其他异常 → 结构化 JSON
- is_readonly 由 effects 派生 (存量类属性覆盖 property)
- AgentLoop 注入 ToolContext (sync/async 一致)
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from strategy_research.core.agent.loop import AgentLoop, LoopResult
from strategy_research.core.agent.tools import (
    EFFECT_DB,
    EFFECT_NET,
    BaseTool,
    ToolContext,
    ToolError,
    ToolRegistry,
)
from strategy_research.foundation.llm import LLMConfig, ToolCall


class V2Tool(BaseTool):
    """v2 形态: 显式签名 + 注解 + effects。"""

    name = "v2tool"
    category = "测试"
    effects = frozenset({EFFECT_DB})

    def execute(self, ctx: ToolContext, codes: list[str], limit: int = 5) -> str:
        return json.dumps({
            "status": "ok",
            "codes": codes,
            "limit": limit,
            "ws": str(ctx.workspace) if ctx.workspace else None,
            "sid": ctx.session_id,
        })


class BoomTool(BaseTool):
    """按类型抛错的工具。"""

    name = "boom"
    effects = frozenset({EFFECT_NET})

    def __init__(self, exc_cls: type[Exception] = RuntimeError):
        self.exc_cls = exc_cls

    def execute(self, ctx: ToolContext, count: int = 1) -> str:
        raise self.exc_cls("kaboom")


class LegacyWriteTool(BaseTool):
    """存量形态: **kwargs + is_readonly 类属性 (应覆盖 effects 派生)。"""

    name = "legacy_write"
    is_readonly = False

    def execute(self, **kwargs) -> str:
        return json.dumps({"status": "ok"})


@pytest.fixture
def registry() -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(V2Tool())
    reg.register(BoomTool())
    reg.register(LegacyWriteTool())
    return reg


def _ctx(workspace: Path | None = None) -> ToolContext:
    return ToolContext(workspace=workspace or Path("/ws"), session_id="s1")


# ── ToolContext ──────────────────────────────────────────────────────


class TestToolContext:
    def test_fields(self):
        ctx = ToolContext(workspace=Path("/w"), session_id="s", emit_progress=lambda d: None)
        assert ctx.workspace == Path("/w")
        assert ctx.session_id == "s"
        assert ctx.emit_progress is not None

    def test_injected_ctx_is_visible_to_tool(self, registry):
        tool = registry.get("v2tool")
        out = json.loads(tool.invoke({"codes": ["A"], "ctx": _ctx()}))
        assert out["ws"] == "/ws"
        assert out["sid"] == "s1"


# ── 框架统一容错层 ──────────────────────────────────────────────────


class TestCoercion:
    def test_json_string_list(self, registry):
        tool = registry.get("v2tool")
        out = json.loads(tool.invoke({"codes": '["A","B"]', "ctx": _ctx()}))
        assert out["codes"] == ["A", "B"]

    def test_single_key_wrapped_list(self, registry):
        tool = registry.get("v2tool")
        out = json.loads(tool.invoke({"codes": {"item": ["A"]}, "ctx": _ctx()}))
        assert out["codes"] == ["A"]

    def test_int_coercion(self, registry):
        tool = registry.get("v2tool")
        out = json.loads(tool.invoke({"codes": ["A"], "limit": "7", "ctx": _ctx()}))
        assert out["limit"] == 7

    def test_int_coercion_failure_is_structured(self, registry):
        tool = registry.get("v2tool")
        out = json.loads(tool.invoke({"codes": ["A"], "limit": "abc", "ctx": _ctx()}))
        assert out["status"] == "error"
        assert "limit" in out["error"]
        assert out["received"] == "abc"
        assert "integer" in out["expected"]

    def test_correct_type_untouched(self, registry):
        tool = registry.get("v2tool")
        out = json.loads(tool.invoke({"codes": ["A"], "limit": 3, "ctx": _ctx()}))
        assert out["limit"] == 3

    def test_legacy_kwargs_tool_skips_coercion(self, registry):
        tool = registry.get("legacy_write")
        assert json.loads(tool.invoke({"weird": "value"}))["status"] == "ok"


# ── 错误兜底 ────────────────────────────────────────────────────────


class TestErrorFallback:
    def test_surprise_exception_becomes_structured(self, registry):
        tool = registry.get("boom")
        out = json.loads(tool.invoke({"ctx": _ctx()}))
        assert out["status"] == "error"
        assert "RuntimeError" in out["error"]
        assert out["tool"] == "boom"

    def test_transient_exception_re_raised_for_retry(self, registry):
        registry.get("boom").exc_cls = ValueError
        tool = registry.get("boom")
        with pytest.raises(ValueError):
            tool.invoke({"ctx": _ctx()})

    def test_tool_error_payload_shape(self):
        err = ToolError("bad input", received={"x": 1}, expected="list", fix="pass list")
        payload = err.to_payload()
        assert payload["status"] == "error"
        assert payload["error"] == "bad input"
        assert payload["received"] == {"x": 1}
        assert payload["expected"] == "list"
        assert payload["fix"] == "pass list"


# ── effects 派生 is_readonly ─────────────────────────────────────────


class TestEffectsDerivedReadonly:
    def test_effects_nonempty_means_writable(self, registry):
        assert registry.get("v2tool").is_readonly is False
        assert registry.get("boom").is_readonly is False

    def test_no_effects_means_readonly(self):
        class ROTool(BaseTool):
            name = "ro"
            effects = frozenset()

            def execute(self, **kwargs) -> str:
                return json.dumps({"status": "ok"})

        assert ROTool().is_readonly is True

    def test_legacy_class_attr_overrides_property(self, registry):
        assert registry.get("legacy_write").is_readonly is False


# ── loop 集成: ctx 注入 + invoke 走容错 ─────────────────────────────


class TestLoopIntegration:
    def test_loop_injects_ctx_and_invokes_coercion(self, registry, tmp_path: Path):
        loop = AgentLoop(
            stream_mode=False,
            config=LLMConfig(api_key="sk-test"),
            registry=registry,
            workspace=tmp_path,
        )
        tc = ToolCall(id="c1", name="v2tool", arguments={
            "codes": '["600519.SH"]',
            "limit": "9",
        })
        out = loop._execute_tool_call(tc, LoopResult())
        content = json.loads(out["content"])
        assert content["status"] == "ok"
        assert content["codes"] == ["600519.SH"]
        assert content["limit"] == 9
        assert content["ws"] == str(tmp_path)

    async def test_async_loop_injects_ctx(self, registry, tmp_path: Path):
        loop = AgentLoop(
            stream_mode=False,
            config=LLMConfig(api_key="sk-test"),
            registry=registry,
            workspace=tmp_path,
        )
        tc = ToolCall(id="c1", name="v2tool", arguments={"codes": ["A"]})
        out = await loop._aexecute_tool_call(tc, LoopResult())
        content = json.loads(out["content"])
        assert content["status"] == "ok"
        assert content["ws"] == str(tmp_path)

    def test_registry_execute_routes_through_invoke(self, registry):
        out = json.loads(registry.execute("v2tool", {"codes": '["A","B"]', "ctx": _ctx()}))
        assert out["codes"] == ["A", "B"]
