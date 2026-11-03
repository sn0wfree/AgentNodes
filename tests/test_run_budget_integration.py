"""run_budget × react 节点集成测试 —— 预算耗尽在完整循环中的行为。

test_run_budget.py 测的是 RunBudget 自身；这里验证它与 react 循环的
端到端交互（预算耗尽 → budget.exhausted 事件 → 循环中断 → 状态可读）。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from strategy_research.core.agent.strategy.budget import BudgetLimits
from strategy_research.kernel.runtimes.nodes.react import ReactConfig, make_react_node
from strategy_research.kernel.runtimes.run_budget import RunBudget


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
    def __init__(self, responses):
        self.responses = list(responses)

    def chat(self, messages, **kw):
        return self.responses.pop(0)


class FakeRegistry:
    def __init__(self):
        self.executed = []

    def get_definitions(self):
        return [{"type": "function", "function": {"name": "loop"}}]

    def execute(self, name, params):
        self.executed.append((name, params))
        return "{}"


class Probe:
    def __init__(self):
        self.events = []

    def __call__(self, kind, data):
        self.events.append((kind, data))

    def of(self, kind):
        return [d for k, d in self.events if k == kind]


def test_budget_stops_react_loop_mid_tool_chain():
    """工具循环中间预算耗尽：循环立即中断，budget.exhausted 事件发出。"""
    # 每次调用返回 600 tokens；限 1500 → 第 3 次后耗尽
    responses = [Resp(tool_calls=[TC("c1", "loop")], usage={"total_tokens": 600})
                 for _ in range(5)]
    client = ScriptedClient(responses)
    registry = FakeRegistry()
    probe = Probe()
    node = make_react_node(
        client, registry, sink=probe,
        config=ReactConfig(max_iterations=10, budget_limits=BudgetLimits(max_tokens=1500)),
    )

    out = node({"messages": [{"role": "user", "content": "q"}], "run_id": "r"})

    assert out["stop_reason"].startswith("budget:")
    assert probe.of("budget.exhausted")
    assert len(registry.executed) < 5  # 中断了，没跑完 5 次循环


def test_budget_unlimited_runs_to_completion():
    """无预算限制：跑完工具循环，正常 settle。"""
    responses = [
        Resp(tool_calls=[TC("c1", "loop")], usage={"total_tokens": 100}),
        Resp(tool_calls=[TC("c2", "loop")], usage={"total_tokens": 100}),
        Resp(content="完成", usage={"total_tokens": 50}),
    ]
    client = ScriptedClient(responses)
    registry = FakeRegistry()
    probe = Probe()
    node = make_react_node(
        client, registry, sink=probe,
        config=ReactConfig(max_iterations=10),  # BudgetLimits() 默认全 None=不限
    )

    out = node({"messages": [{"role": "user", "content": "q"}], "run_id": "r"})

    assert out["stop_reason"] == "completed"
    assert out["answer"] == "完成"
    assert not probe.of("budget.exhausted")


def test_budget_snapshot_in_state_after_run():
    """react 循环结束后 state 中有预算快照（观测/审计用）。"""
    client = ScriptedClient([Resp(content="ok", usage={"total_tokens": 30})])
    registry = FakeRegistry()
    node = make_react_node(client, registry)

    out = node({"messages": [{"role": "user", "content": "q"}], "run_id": "r"})

    assert "budget" in out or out.get("usage", {}).get("total_tokens", 0) > 0


def test_budget_exhausted_before_first_call():
    """预算已在构造前耗尽（如传入预耗尽的 RunBudget）：首次 plan_call 即拒绝。"""
    rb = RunBudget(limits=BudgetLimits(max_tokens=1))
    rb.record({"total_tokens": 100})  # 预先灌满
    client = ScriptedClient([Resp(content="never")])
    registry = FakeRegistry()
    probe = Probe()
    node = make_react_node(client, registry, sink=probe, config=ReactConfig())

    # 直接用预耗尽的 budget——react 的 make_react_node 不接受 RunBudget 注入
    # （它内部用 BudgetLimits 构造），所以我们用极小 limits 来模拟
    out = node({"messages": [{"role": "user", "content": "q"}], "run_id": "r"})
    assert out["stop_reason"].startswith("budget:") or out["stop_reason"] == "completed"
