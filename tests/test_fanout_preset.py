"""fanout preset v1（F3）测试 —— 子代理并行 / 预算切片真生效 / 失败策略 / steer 承载。

对应 docs/stage-f3-fanout-preset-design-20261027.md §6 验收。
用 Fake client + Fake registry（无网络）；真 LLM 见 test_fanout_live.py。
"""
from __future__ import annotations

import pytest

from strategy_research.kernel.agent.strategy.budget import BudgetLimits
from strategy_research.kernel.runtimes.graph_engine import INTERRUPT_KEY
from strategy_research.kernel.runtimes.presets.fanout import build_fanout_preset
from strategy_research.kernel.runtimes.run_budget import RunBudget


class FakeResp:
    def __init__(self, content: str, usage: dict | None = None,
                 tool_calls: list | None = None) -> None:
        self.content = content
        self.usage = usage or {"total_tokens": 4, "prompt_tokens": 2,
                               "completion_tokens": 2}
        self.tool_calls = tool_calls or []

    def has_tool_calls(self) -> bool:
        return bool(self.tool_calls)


class FakeClient:
    """按消息内容回答；可注入异常/工具调用序列。"""

    def __init__(self, responder=None, usage: dict | None = None) -> None:
        self.responder = responder or (lambda msgs: f"回答:{msgs[-1]['content']}")
        self.usage = usage
        self.calls: list[list[dict]] = []

    def chat(self, messages, tools=None, **kwargs):
        self.calls.append([dict(m) for m in messages])
        content = self.responder(messages)
        if isinstance(content, Exception):
            raise content
        return FakeResp(content, self.usage)


class FakeRegistry:
    def __init__(self) -> None:
        self.executed: list[tuple[str, dict]] = []

    def get_definitions(self) -> list[dict]:
        return [{"type": "function", "function": {"name": "noop", "parameters": {}}}]

    def execute(self, name: str, params: dict) -> str:
        self.executed.append((name, params))
        return '{"ok": true}'


class ToolThenAnswer:
    """第一次调用返回工具调用，之后返回答案。"""

    def __init__(self) -> None:
        self.n = 0

    def __call__(self, messages):
        self.n += 1
        if self.n == 1:
            class TC:
                id = "call_1"
                name = "noop"
                arguments = {"x": 1}

            return FakeResp("", tool_calls=[TC()])
        return FakeResp("最终答案")


class MultiCallClient:
    def __init__(self, per_call_responder) -> None:
        self.n = 0
        self.responder = per_call_responder

    def chat(self, messages, tools=None, **kwargs):
        self.n += 1
        return self.responder(self.n, messages)


def make_preset(client=None, registry=None, **kwargs):
    return build_fanout_preset(
        client or FakeClient(), registry or FakeRegistry(),
        split=kwargs.pop("split", lambda s: ["q1", "q2", "q3"]),
        **kwargs,
    )


# ── 正常路径：N 路并行子代理 ─────────────────────────────


def test_three_branches_answer_and_merge_in_order():
    engine = make_preset()
    out = engine.run({"messages": [{"role": "user", "content": "总任务"}]})

    assert out["fanout_ok"] is True
    assert out["fanout_answers"] == ["回答:q1", "回答:q2", "回答:q3"]
    assert len(out["fanout_results"]) == 3
    assert all(r["status"] == "ok" for r in out["fanout_results"])


def test_each_branch_is_an_independent_subagent():
    client = FakeClient()
    engine = make_preset(client=client)
    out = engine.run({})

    # 每支一次 LLM 调用（无工具），互不共享消息历史
    assert len(client.calls) == 3
    for call, payload in zip(client.calls, ["q1", "q2", "q3"]):
        assert len(call) == 1 and call[0]["content"] == payload
    assert out["fanout_ok"] is True


def test_system_prompt_applied_per_branch():
    client = FakeClient()
    engine = make_preset(client=client, system="你是子研究员")
    engine.run({})
    assert all(call[0]["role"] == "system" and call[0]["content"] == "你是子研究员"
               for call in client.calls)


def test_branch_run_id_is_child_tree_id():
    captured: list[str] = []

    class RecordingClient(FakeClient):
        def chat(self, messages, tools=None, **kwargs):
            captured.append(messages[-1]["content"])
            return super().chat(messages, tools=tools, **kwargs)

    engine = make_preset(client=RecordingClient())
    out = engine.run({"run_id": "turn:r0"})
    ids = [r["branch_id"] for r in out["fanout_results"]]
    assert ids == ["turn:r0:1", "turn:r0:2", "turn:r0:3"]


# ── 预算切片真生效（F3 修的真缺陷）──────────────────────


def test_branch_budget_slice_actually_used():
    """修复回归：react 节点必须用注入的切片对象，消耗回到父账。"""
    engine = make_preset()
    parent = RunBudget(limits=BudgetLimits(max_tokens=300))

    out = engine.run({"run_budget": parent})

    assert out["fanout_ok"] is True
    # 每支 react 一次调用记 4 tokens；3 支 = 12，settle 回父
    assert parent.usage.tokens == 12


def test_branch_budget_exhaustion_cascades():
    """切片耗尽 → GraphInterrupt → 连坐（join 不执行、后续分支取消）。"""
    engine = make_preset()
    parent = RunBudget(limits=BudgetLimits(max_tokens=1))
    # 1 切 3 → 每支 0 → 预标 exhausted → 第一支即预算中断
    out = engine.run({"run_budget": parent})

    assert INTERRUPT_KEY in out
    assert out[INTERRUPT_KEY]["reason"] == "branch_budget_exhausted"
    assert "fanout_ok" not in out  # join 未执行
    statuses = [r["status"] for r in out["fanout_results"]]
    assert statuses[0] == "interrupted"
    assert statuses[1:] == ["cancelled", "cancelled"]


def test_zero_branches_with_budget():
    engine = make_preset(split=lambda s: [])
    parent = RunBudget(limits=BudgetLimits(max_tokens=100))
    out = engine.run({"run_budget": parent})
    assert out["fanout_ok"] is True and out["fanout_answers"] == []
    assert parent.usage.tokens == 0


# ── 失败策略（D2）───────────────────────────────────────


def test_abort_turn_marks_round_failed():
    def responder(messages):
        if messages[-1]["content"] == "q2":
            raise RuntimeError("回测崩了")
        return "ok"

    engine = make_preset(client=FakeClient(responder=responder))
    out = engine.run({})

    assert out["fanout_ok"] is False
    assert out["aborted"] is True and out["reason"] == "branch_failed"
    assert out["fanout_answers"] == ["ok", "ok"]  # 成功分支的结果仍在
    assert len(out["fanout_errors"]) == 1
    assert "回测崩了" in out["fanout_errors"][0]["error"]


def test_continue_policy_skips_bad_branch():
    def responder(messages):
        if messages[-1]["content"] == "q2":
            raise RuntimeError("boom")
        return "ok"

    engine = make_preset(
        client=FakeClient(responder=responder), failure_policy="continue",
    )
    out = engine.run({})

    assert out["fanout_ok"] is False  # 仍有错误记录
    assert "aborted" not in out      # 但不中止整轮
    assert out["fanout_answers"] == ["ok", "ok"]
    assert len(out["fanout_errors"]) == 1


def test_invalid_failure_policy_rejected():
    with pytest.raises(ValueError, match="failure_policy"):
        build_fanout_preset(FakeClient(), FakeRegistry(), split=lambda s: [],
                            failure_policy="retry")


# ── steer 承载点（D3）───────────────────────────────────


def test_steer_after_join_appended_to_messages():
    events: list[tuple[str, dict]] = []
    engine = make_preset(sink=lambda k, d: events.append((k, d)))
    out = engine.run({
        "messages": [{"role": "user", "content": "初始任务"}],
        "steer_after_join": ["补充要求 A", "补充要求 B"],
    })

    contents = [m["content"] for m in out["messages"]]
    assert contents[0] == "初始任务"
    assert contents[-2:] == ["补充要求 A", "补充要求 B"]
    assert out["steer_after_join"] == []
    assert [d for k, d in events if k == "steer.injected_after_join"] == [{"count": 2}]


def test_no_steer_means_no_event_and_messages_unchanged():
    events: list[tuple[str, dict]] = []
    engine = make_preset(sink=lambda k, d: events.append((k, d)))
    out = engine.run({"messages": [{"role": "user", "content": "任务"}]})

    assert [m["content"] for m in out["messages"]] == ["任务"]
    assert not [k for k, _ in events if k == "steer.injected_after_join"]


# ── 分支事件回流（规则⑥）────────────────────────────────


def test_branch_tool_events_forwarded_from_parent_thread():
    """分支线程的 tool.started/completed 经队列回流到父线程 sink。"""
    import threading

    sink_threads: list[int] = []
    events: list[tuple[str, dict]] = []

    def sink(kind: str, data: dict) -> None:
        if kind.startswith("tool."):
            sink_threads.append(threading.get_ident())
        events.append((kind, data))

    engine = make_preset(
        client=MultiCallClient(lambda n, msgs: _ToolState(n)(msgs)),
        sink=sink,
        split=lambda s: ["t1"],
    )
    out = engine.run({})

    tool_events = [d for k, d in events if k.startswith("tool.")]
    assert len(tool_events) == 2  # started + completed
    assert out["fanout_ok"] is True
    # 回流发生在父线程（fanout 完成后的 join 里）
    main_thread = threading.main_thread().ident
    assert all(t == main_thread for t in sink_threads)


class _ToolState:
    def __init__(self, n: int) -> None:
        self.n = n

    def __call__(self, messages):
        if self.n == 1:
            class TC:
                id = "c1"
                name = "noop"
                arguments = {}

            return FakeResp("", tool_calls=[TC()])
        return FakeResp("done")


def test_branch_events_carry_run_id():
    events: list[tuple[str, dict]] = []
    client = MultiCallClient(lambda n, msgs: _ToolState(n)(msgs))
    engine = build_fanout_preset(
        client, FakeRegistry(), split=lambda s: ["p"], sink=lambda k, d: events.append((k, d)),
    )
    engine.run({"run_id": "t:r0"})

    tool_started = [d for k, d in events if k == "tool.started"]
    assert tool_started and tool_started[0]["run_id"] == "t:r0:1"


def test_branch_event_count_reported_in_join():
    client = MultiCallClient(lambda n, msgs: _ToolState(n)(msgs))
    engine = build_fanout_preset(client, FakeRegistry(), split=lambda s: ["p"])
    out = engine.run({})
    assert out["fanout_branch_events"] >= 2  # tool.started + tool.completed


# ── 自定义合并与图可组合性 ───────────────────────────────


def test_custom_join_fn_overrides_default_merge():
    def join_fn(state: dict) -> dict:
        return {**state, "custom": len(state["fanout_answers"])}

    engine = make_preset(join_fn=join_fn)
    out = engine.run({})
    assert out["custom"] == 3 and out["fanout_ok"] is True


def test_preset_engine_topology_is_two_nodes():
    engine = make_preset()
    out = engine.run({})
    # split/join 都执行过（join 的 joined 痕迹在此处体现为 fanout_ok）
    assert out["fanout_ok"] is True


def test_max_parallel_default_is_eight():
    """D5：默认并发与 subagent lane 同源（8）。"""
    from strategy_research.kernel.runtimes.graph_engine import _FanoutSpec

    engine = make_preset()
    spec = engine._fanouts["split"]  # 白盒：确认默认值
    assert isinstance(spec, _FanoutSpec) and spec.max_parallel == 8


def test_branches_run_within_parallel_bound():
    import threading
    import time

    lock = threading.Lock()
    active = {"n": 0, "peak": 0}

    def responder(messages):
        with lock:
            active["n"] += 1
            active["peak"] = max(active["peak"], active["n"])
        time.sleep(0.05)
        with lock:
            active["n"] -= 1
        return "ok"

    engine = make_preset(
        client=FakeClient(responder=responder), max_parallel=2,
        split=lambda s: [1, 2, 3, 4],
    )
    engine.run({})
    assert active["peak"] == 2
