"""C 档收尾定向测试：补齐从 89.96% 跨过 90.0% 的最后几条未覆盖分支。

覆盖点（对应 cov_z4.log term-missing）：
- tool_result.from_any 未知 status 回退 (98)
- tool_result._json_preview 序列化失败回退 + 截断 (178-179, 182)
- tool_result._table_md 空表 (189)
- combo._resolve_param dict 递归 (47)
- combo schema 提取失败回退 (98-99) + _infer_type 默认 (128)
- context.build_initial_messages history 注入 (149)
- file_tools.ReadFileTool OSError 回退 (136-137)
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentnodes.kernel.agent.builtin_tools.file_tools import ReadFileTool
from agentnodes.kernel.agent.combo import CompositeTool
from agentnodes.kernel.agent.context import ContextBuilder
from agentnodes.kernel.agent.tool_result import ToolResult, from_any, render_for_llm
from agentnodes.kernel.agent.tools import ToolContext, ToolRegistry
from agentnodes.foundation.llm import LLMConfig


# ── tool_result.py ───────────────────────────────────────────────


def test_from_any_unknown_status_falls_back_to_ok():
    """status 不是 ok/error/success → 按 ok 处理（宽容 coercion）。"""
    r = from_any({"status": "weird", "result": 42})
    assert r.status == "ok"
    assert r.data == 42


def test_render_for_llm_empty_table():
    r = ToolResult(status="ok", table={"columns": [], "rows": []})
    out = render_for_llm(r)
    assert "(empty table)" in out


def test_json_preview_unserializable_falls_back_to_str():
    """循环引用让 json.dumps 抛 ValueError → str(data) 兜底。"""
    from agentnodes.kernel.agent.tool_result import _json_preview

    d: dict = {}
    d["self"] = d
    s = _json_preview(d)
    assert "self" in s


def test_json_preview_truncates_large_payload():
    from agentnodes.kernel.agent.tool_result import _json_preview

    big = {"k": "x" * 5000}
    s = _json_preview(big)
    assert "json truncated" in s
    assert len(s) < len(json.dumps(big))


# ── combo.py ─────────────────────────────────────────────────────


class _EchoTool:
    name = "echo"
    description = "echo input"
    category = "测试"
    effects = frozenset()

    def to_openai_schema(self):
        return {"function": {"name": "echo", "parameters": {
            "type": "object", "properties": {"text": {"type": "string"}},
            "required": []}}}

    def invoke(self, args):
        return json.dumps({"status": "ok", "echo": args.get("text", "")},
                          ensure_ascii=False)


class _BoomSchemaTool:
    """to_openai_schema 抛异常 → combo 初始化 schema=None 兜底。"""

    name = "boom"
    description = "schema boom"
    category = "测试"
    effects = frozenset()

    def to_openai_schema(self):
        raise RuntimeError("schema broken")

    def invoke(self, args):
        return json.dumps({"status": "ok", "payload": args.get("payload", "")},
                          ensure_ascii=False)


def _ctx(tmp_path):
    return ToolContext(workspace=tmp_path, session_id="s-cfinal")


def test_combo_dict_param_recursion(tmp_path):
    """step 参数值是嵌套 dict → _resolve_param 递归解析内部引用。"""
    reg = ToolRegistry()
    reg.register(_EchoTool())
    combo = CompositeTool(
        name="echo_dict", description="dict param",
        category="测试", registry=reg,
        steps=[{"tool": "echo", "params": {
            "opts": {"text": "input.text"}}}],
    )
    out = json.loads(combo.invoke({"text": "hi", "ctx": _ctx(tmp_path)}))
    assert out["status"] == "ok"


def test_combo_schema_extraction_failure_uses_string_type(tmp_path):
    """子工具 schema 抛异常 → _input_params 退化为 string 类型，执行不受影响。"""
    reg = ToolRegistry()
    reg.register(_BoomSchemaTool())
    combo = CompositeTool(
        name="boom_call", description="call boom",
        category="测试", registry=reg,
        steps=[{"tool": "boom", "params": {"payload": "input.payload"}}],
    )
    # _infer_type 拿不到 schema → 默认 "string"
    assert combo.parameters["properties"]["payload"] == {"type": "string"}
    out = json.loads(combo.invoke({"payload": "p", "ctx": _ctx(tmp_path)}))
    assert out["status"] == "ok"


# ── context.py ───────────────────────────────────────────────────


def test_build_initial_messages_extends_history():
    cfg = LLMConfig(provider="minimax", model="test-model", api_key="sk-x")
    cb = ContextBuilder(cfg, ToolRegistry(), system_prompt="sys")
    history = [{"role": "assistant", "content": "earlier turn"}]
    msgs = cb.build_initial_messages("task", history=history)
    # system + history + user
    assert msgs[0]["role"] == "system"
    assert msgs[1] == history[0]
    assert msgs[-1]["role"] == "user"


# ── file_tools.py ────────────────────────────────────────────────


def test_read_file_oserror_returns_actionable_error(tmp_path, monkeypatch):
    """read_text 抛 OSError (权限等) → err_actionable，而非崩溃。"""
    target = tmp_path / "notes.md"
    target.write_text("content", encoding="utf-8")

    def _deny(self, *a, **kw):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(Path, "read_text", _deny)
    tool = ReadFileTool()
    out = tool.execute(ToolContext(workspace=tmp_path, session_id="s"), path="notes.md")
    payload = json.loads(out)
    assert payload["status"] == "error"
    assert "read failed" in payload["error"]
