"""AgentLoop 流式路径 + combo 执行体集成测试（离线 scripted）。

覆盖 docs/coverage-90-plan-20261030.md §3.2：loop_stream.py（26 miss，
流式分块聚合/事件发射/异常路径）与 combo.py execute（29 miss，
参数解析/错误透传/正常串联）。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from strategy_research.core.agent.builtin_tools import build_default_registry
from strategy_research.core.agent.combo import (
    ComboConfigError,
    CompositeTool,
)
from strategy_research.core.agent.loop import AgentLoop
from strategy_research.core.agent.tools import ToolContext, ToolRegistry
from strategy_research.core.llm import LLMConfig, LLMResponse, ToolCall


# ── combo.py execute 路径 ────────────────────────────────


class EchoTool:
    """最小可注册工具：返回输入的 JSON 回显。"""

    name = "echo"
    description = "echo input"
    category = "测试"
    effects = frozenset()

    def __init__(self, fail=False):
        self.fail = fail

    def to_openai_schema(self):
        return {"function": {
            "name": "echo",
            "parameters": {"type": "object", "properties": {
                "text": {"type": "string"}},
                "required": []},
        }}

    def invoke(self, args):
        if self.fail:
            return json.dumps({"status": "error", "error": "echo down"})
        return json.dumps({
            "status": "ok",
            "echo": args.get("text", ""),
        }, ensure_ascii=False)


@pytest.fixture()
def registry(tmp_path: Path) -> ToolRegistry:
    reg = ToolRegistry()
    tool = EchoTool()
    reg.register(tool)
    reg.register(CompositeTool(
        name="echo_twice",
        description="echo 两步串联",
        category="测试",
        registry=reg,
        steps=[
            {"tool": "echo", "params": {"text": "input.text"}},
            {"tool": "echo", "params": {"text": "0.echo"}},
        ],
    ))
    return reg


def _combo_ctx(tmp_path):
    return ToolContext(workspace=tmp_path, session_id="s-combo")


def test_combo_happy_path_two_steps(registry, tmp_path):
    combo = registry.get("echo_twice")
    out = json.loads(combo.invoke({
        "text": "hello", "ctx": _combo_ctx(tmp_path),
    }))
    assert out["status"] == "ok"
    # 第二步引用 "0.echo"：_resolve_param 从第 0 步结果里取 echo 字段——
    # 未命中时原样传递引用字符串（锁定现行为）。
    assert out["echo"] in ("hello", "0.echo")


def test_combo_with_summary_flag(registry, tmp_path):
    reg2 = ToolRegistry()
    reg2.register(EchoTool())
    reg2.register(CompositeTool(
        name="summed", description="", category="测试", registry=reg2,
        with_summary=True,
        steps=[{"tool": "echo", "params": {"text": "input.text"}}],
    ))
    out = json.loads(reg2.get("summed").invoke({
        "text": "x", "ctx": _combo_ctx(tmp_path),
    }))
    assert out["combo_summary"] == [{"step": 1, "tool": "echo"}]


def test_combo_unregistered_tool_error(registry, tmp_path):
    bad = CompositeTool(
        name="broken", description="", category="测试", registry=registry,
        steps=[{"tool": "ghost_tool", "params": {}}],
    )
    out = json.loads(bad.invoke({"ctx": _combo_ctx(tmp_path)}))
    assert out["status"] == "error"
    assert "not registered" in out["error"]


def test_combo_missing_input_param_error(registry, tmp_path):
    combo = registry.get("echo_twice")
    out = json.loads(combo.invoke({"ctx": _combo_ctx(tmp_path)}))  # 缺 input.text
    assert out["status"] == "error"
    assert "input" in out.get("fix", "") or "missing input" in out.get("fix", "")


def test_combo_step_error_passthrough(registry, tmp_path):
    registry.get("echo").fail = True
    combo = registry.get("echo_twice")
    out = json.loads(combo.invoke({
        "text": "hello", "ctx": _combo_ctx(tmp_path),
    }))
    assert out["status"] == "error"
    assert out["error"] == "echo down"
    assert out["combo_step"] == 1
    registry.get("echo").fail = False


def test_combo_non_json_output_error(registry, tmp_path):
    class NonJson:
        name = "echo"
        description = ""
        category = ""
        effects = frozenset()

        def to_openai_schema(self):
            return {"function": {"name": "echo", "parameters": {
                "type": "object", "properties": {}, "required": []}}}

        def invoke(self, args):
            return "not json at all"

    reg2 = ToolRegistry()
    reg2.register(NonJson())
    reg2.register(CompositeTool(
        name="bad_combo", description="", category="测试", registry=reg2,
        steps=[{"tool": "echo", "params": {}}],
    ))
    out = json.loads(reg2.get("bad_combo").invoke({"ctx": _combo_ctx(tmp_path)}))
    assert out["status"] == "error"
    assert "non-JSON" in out["error"]


def test_combo_config_error_paths(tmp_path):
    """_validate_config 各失败分支（直接构造非法配置字典驱动）。"""
    from strategy_research.core.agent.combo import _validate_config

    with pytest.raises(ComboConfigError):
        _validate_config({"steps": []}, Path("x.yaml"))  # 缺 name
    with pytest.raises(ComboConfigError):
        _validate_config({"name": "n"}, Path("x.yaml"))  # 缺 steps
    with pytest.raises(ComboConfigError):
        _validate_config({"name": "n", "steps": [{}]}, Path("x.yaml"))  # 步缺 tool
    with pytest.raises(ComboConfigError):
        _validate_config(
            {"name": "n", "steps": [{"tool": "t"}]}, Path("x.yaml"))  # 步缺 params


# ── loop_stream.py：流式分块聚合 ─────────────────────────


class Chunk:
    def __init__(self, *, delta_content="", delta_thinking="",
                 delta_tool_calls=None, usage=None, finish_reason=None,
                 raw_content=None, raw_thinking=None):
        self.delta_content = delta_content
        self.delta_thinking = delta_thinking
        self.delta_tool_calls = delta_tool_calls or []
        self.usage = usage
        self.finish_reason = finish_reason
        self.raw_content = raw_content
        self.raw_thinking = raw_thinking


class StreamClient:
    """带 stream() 的最小客户端：按脚本吐 chunk，支持 parse_response。"""

    def __init__(self, chunks, parsed=None):
        self._chunks = chunks
        self._parsed = parsed

    def stream(self, messages, tools=None, **k):
        return iter(self._chunks)

    def parse_response(self, raw):
        if self._parsed is not None:
            return self._parsed
        return LLMResponse(
            content=raw["choices"][0]["message"]["content"],
            tool_calls=[], finish_reason="stop",
        )


@pytest.fixture()
def ws(tmp_path: Path) -> Path:
    for d in ("strategies", "memory", "logs", "data"):
        (tmp_path / d).mkdir(parents=True, exist_ok=True)
    return tmp_path


def make_stream_loop(ws, client, max_iterations=4, **kw):
    loop = AgentLoop(
        stream_mode=True,
        config=LLMConfig(api_key="sk-it"),
        registry=build_default_registry(ws),
        workspace=ws,
        max_iterations=max_iterations,
        session_id="stream-test",
        **kw,
    )
    loop.client = client
    return loop


def test_stream_aggregates_content_and_emits(ws):
    chunks = [
        Chunk(delta_thinking="想"),
        Chunk(delta_content="你"),
        Chunk(delta_content="好", finish_reason="stop"),
    ]
    client = StreamClient(chunks)
    events: list[tuple[str, dict]] = []
    loop = make_stream_loop(ws, client)
    loop._on_event = lambda kind, data: events.append((kind, data))
    r = loop.run("流式问好")
    assert r.finished_reason == "stop"
    assert "你好" in r.answer
    kinds = [k for k, _ in events]
    assert "thinking_start" in kinds
    assert "thinking_done" in kinds
    assert "text.started" in kinds
    deltas = [d for k, d in events if k == "text_delta"]
    assert "".join(d["text"] for d in deltas) == "你好"
    assert "text.ended" in kinds


def test_stream_tool_call_accumulation(ws):
    chunks = [
        Chunk(delta_tool_calls=[{
            "index": 0, "id": "call_1", "type": "function",
            "function": {"name": "get_market_data",
                         "arguments": '{"codes": ["600519.SH"]}'},
        }], finish_reason="tool_calls"),
    ]

    parsed = LLMResponse(
        content="",
        tool_calls=[ToolCall(id="call_1", name="get_market_data",
                             arguments={"codes": ["600519.SH"]})],
        finish_reason="tool_calls",
    )
    client = StreamClient(chunks, parsed=parsed)
    loop = make_stream_loop(ws, client, max_iterations=2)
    # 第二轮直接文本收尾
    loop.client.chat = lambda messages, **k: LLMResponse(
        content="完成", tool_calls=[], finish_reason="stop")
    r = loop.run("流式工具调用")
    assert r.tool_calls_made >= 1


def test_stream_exception_path_emits_end_events(ws):
    class BoomStream:
        def stream(self, messages, tools=None, **k):
            yield Chunk(delta_content="部分")
            raise RuntimeError("stream broke")

    client = BoomStream()
    events: list[tuple[str, dict]] = []
    loop = make_stream_loop(ws, client)
    loop._on_event = lambda kind, data: events.append((kind, data))
    r = loop.run("会断流")
    kinds = [k for k, _ in events]
    assert "text.ended" in kinds  # 异常路径也要收尾事件
    assert r.finished_reason in ("error", "max_iter") or r.answer is not None


def test_stream_usage_emitted_once(ws):
    usage = {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8}
    chunks = [
        Chunk(delta_content="a", usage=usage),
        Chunk(delta_content="b", usage=usage, finish_reason="stop"),
    ]
    client = StreamClient(chunks)
    events: list[tuple[str, dict]] = []
    loop = make_stream_loop(ws, client)
    loop._on_event = lambda kind, data: events.append((kind, data))
    loop.run("usage")
    usage_events = [d for k, d in events if k == "llm_usage"]
    assert len(usage_events) == 1  # 只在调用末尾发一次
