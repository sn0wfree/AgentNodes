"""react 节点单元测试 —— 工具循环全路径（假 client + 假 registry，零网络零引擎）。"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import pytest

from strategy_research.core.agent.strategy.budget import BudgetLimits
from strategy_research.kernel.runtimes.nodes.react import ReactConfig, make_react_node


# ── 假件 ─────────────────────────────────────────────────


@dataclass
class TC:
    id: str
    name: str
    arguments: dict = field(default_factory=dict)


@dataclass
class Resp:
    content: str = ""
    tool_calls: list = field(default_factory=list)
    usage: dict = field(default_factory=dict)

    def has_tool_calls(self) -> bool:
        return len(self.tool_calls) > 0


class ScriptedClient:
    """按脚本回复：第 i 次调用返回 responses[i]。"""

    def __init__(self, responses: list[Resp]) -> None:
        self.responses = responses
        self.calls: list[dict] = []

    def chat(self, messages, *, tools=None, **kw):
        self.calls.append({"messages": [dict(m) for m in messages], "tools": tools})
        return self.responses.pop(0)


class FakeRegistry:
    """假注册表：name → 固定 JSON 结果；记录调用。"""

    def __init__(self, results: dict[str, str], fail_names: set[str] | None = None) -> None:
        self.results = results
        self.fail_names = fail_names or set()
        self.executed: list[tuple[str, dict]] = []

    def get_definitions(self):
        return [{"type": "function", "function": {"name": n}} for n in self.results]

    def execute(self, name: str, params: dict) -> str:
        self.executed.append((name, params))
        if name in self.fail_names:
            return '{"error": "boom"}'
        return self.results.get(name, "{}")


class Probe:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def __call__(self, kind, data):
        self.events.append((kind, data))

    def of(self, kind):
        return [d for k, d in self.events if k == kind]


def tc(id_: str, name: str, **args) -> TC:
    return TC(id=id_, name=name, arguments=args)


# ── 路径 1：无工具调用，直出答案 ──────────────────────────


def test_no_tool_calls_single_iteration():
    client = ScriptedClient([Resp(content="直接回答")])
    registry = FakeRegistry({})  # 空 registry：get_definitions 返回 []，行为仍正确
    probe = Probe()
    node = make_react_node(client, registry, sink=probe, config=ReactConfig(system="sys"))

    out = node({"messages": [{"role": "user", "content": "q"}], "run_id": "t1:r0"})

    assert out["answer"] == "直接回答"
    assert out["stop_reason"] == "completed"
    assert out["usage"]["iterations"] == 1
    assert registry.executed == []  # 无工具执行
    # system 注入
    assert client.calls[0]["messages"][0] == {"role": "system", "content": "sys"}
    # 空 registry 时 tools 为空列表（真实场景 registry 至少有一个工具）


# ── 路径 2：一次工具调用 → 结果回填 → 最终回答 ────────────


def test_single_tool_call_then_answer():
    client = ScriptedClient([
        Resp(content="", tool_calls=[tc("c1", "calc", expr="17*23")]),
        Resp(content="等于 391"),
    ])
    registry = FakeRegistry({"calc": '{"result": 391}'})
    probe = Probe()
    node = make_react_node(client, registry, sink=probe)

    out = node({"messages": [{"role": "user", "content": "17*23=?"}], "run_id": "t:r0"})

    assert out["answer"] == "等于 391"
    assert out["usage"]["iterations"] == 2
    assert registry.executed == [("calc", {"expr": "17*23"})]
    # OpenAI 协议顺序：assistant(tool_calls) 后紧跟 role=tool
    msgs = out["messages"]
    assert msgs[1]["role"] == "assistant" and msgs[1]["tool_calls"][0]["id"] == "c1"
    assert msgs[2] == {"role": "tool", "tool_call_id": "c1", "content": '{"result": 391}'}
    # 事件成对
    assert len(probe.of("tool.started")) == 1
    assert len(probe.of("tool.completed")) == 1
    assert probe.of("tool.started")[0]["tool"] == "calc"


def test_tool_call_wire_format_is_openai_shape():
    """tool_calls 必须是 {id,type,function:{name,arguments:str}} 的 OpenAI 形状。"""
    client = ScriptedClient([
        Resp(content="", tool_calls=[tc("c1", "calc", a=1, b=[2])]),
        Resp(content="done"),
    ])
    node = make_react_node(client, FakeRegistry({"calc": "1"}))
    out = node({"messages": [{"role": "user", "content": "q"}]})
    wire = out["messages"][1]["tool_calls"][0]
    assert wire["type"] == "function"
    assert wire["function"]["name"] == "calc"
    assert json.loads(wire["function"]["arguments"]) == {"a": 1, "b": [2]}


# ── 路径 3：多轮工具链 ───────────────────────────────────


def test_multi_tool_chain():
    client = ScriptedClient([
        Resp(content="", tool_calls=[tc("c1", "search", q="x")]),
        Resp(content="", tool_calls=[tc("c2", "calc", expr="1+1")]),
        Resp(content="搜索并计算完成"),
    ])
    registry = FakeRegistry({"search": '["result"]', "calc": '{"result": 2}'})
    node = make_react_node(client, registry)

    out = node({"messages": [{"role": "user", "content": "q"}], "run_id": "r"})
    assert out["answer"] == "搜索并计算完成"
    assert [name for name, _ in registry.executed] == ["search", "calc"]
    assert out["usage"]["iterations"] == 3


# ── 路径 4：工具失败不炸循环（registry 容错保证） ──────────


def test_tool_failure_returns_error_to_llm_and_continues():
    client = ScriptedClient([
        Resp(content="", tool_calls=[tc("c1", "calc", expr="bad")]),
        Resp(content="出错了，直接回答"),
    ])
    registry = FakeRegistry({"calc": '{"error": "boom"}'}, fail_names={"calc"})
    node = make_react_node(client, registry)

    out = node({"messages": [{"role": "user", "content": "q"}], "run_id": "r"})
    assert out["answer"] == "出错了，直接回答"
    assert out["stop_reason"] == "completed"


# ── 路径 5：max_iterations 截断 ──────────────────────────


def test_max_iterations_stops_with_marker():
    responses = [Resp(content="", tool_calls=[tc(f"c{i}", "loop")]) for i in range(10)]
    client = ScriptedClient(responses + [Resp(content="不应到达")])
    registry = FakeRegistry({"loop": "{}"})
    node = make_react_node(client, registry, config=ReactConfig(max_iterations=3))

    out = node({"messages": [{"role": "user", "content": "q"}], "run_id": "r"})
    assert out["stop_reason"] == "max_iterations(3) exceeded"
    assert "未完成" in out["answer"]
    assert len(registry.executed) == 3


# ── 路径 6：预算耗尽 ─────────────────────────────────────


def test_budget_exhausted_stops_and_emits():
    zero_budget = BudgetLimits(max_tokens=1)
    responses = [Resp(content="", tool_calls=[tc("c1", "loop")], usage={"total_tokens": 500})
                 for _ in range(5)]
    client = ScriptedClient(responses)
    registry = FakeRegistry({"loop": "{}"})
    probe = Probe()
    node = make_react_node(
        client, registry, sink=probe,
        config=ReactConfig(max_iterations=10, budget_limits=zero_budget),
    )

    out = node({"messages": [{"role": "user", "content": "q"}], "run_id": "r"})
    assert out["stop_reason"].startswith("budget:")
    assert probe.of("budget.exhausted")


# ── 路径 7：并行工具调用（一次返回多个 tool_calls） ────────


def test_parallel_tool_calls_all_executed_in_order():
    client = ScriptedClient([
        Resp(content="", tool_calls=[tc("c1", "t1"), tc("c2", "t2"), tc("c3", "t3")]),
        Resp(content="三个都做完"),
    ])
    registry = FakeRegistry({"t1": "1", "t2": "2", "t3": "3"})
    node = make_react_node(client, registry)

    out = node({"messages": [{"role": "user", "content": "q"}], "run_id": "r"})
    assert out["answer"] == "三个都做完"
    assert [name for name, _ in registry.executed] == ["t1", "t2", "t3"]
    # 三条 role=tool 紧跟 assistant
    msgs = out["messages"]
    assert msgs[1]["role"] == "assistant"
    assert [m["role"] for m in msgs[2:5]] == ["tool", "tool", "tool"]
    assert [m["tool_call_id"] for m in msgs[2:5]] == ["c1", "c2", "c3"]
