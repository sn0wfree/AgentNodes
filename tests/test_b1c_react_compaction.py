"""B1-c: mid-loop compaction in the react node.

AgentLoop compacts once per iteration before the LLM call
(strategy/steps/compaction.py). The react node must do the same, through
an injected compactor (platform layer must not import the domain).
"""
from __future__ import annotations

import json
from types import SimpleNamespace

from strategy_research.core.agent.tools import BaseTool, ToolRegistry


class _NoopTool(BaseTool):
    name = "noop"
    description = "noop"
    parameters = {}

    def execute(self, **kwargs):
        return json.dumps({"status": "ok"})


class _FakeResponse:
    def __init__(self, content: str = "", tool_calls: list | None = None):
        self.content = content
        self.tool_calls = tool_calls or []
        self.usage = None

    def has_tool_calls(self) -> bool:
        return bool(self.tool_calls)


class _RecordingClient:
    """Records the messages it was called with; one tool call then answer."""

    def __init__(self) -> None:
        self.calls: list[list[dict]] = []

    def chat(self, messages, tools=None):
        # Deep-ish snapshot of what the LLM actually saw.
        self.calls.append([dict(m) for m in messages])
        if len(self.calls) == 1:
            tc = SimpleNamespace(id="c1", name="noop", arguments={})
            return _FakeResponse(tool_calls=[tc])
        return _FakeResponse(content="done")


def _registry() -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(_NoopTool())
    return reg


# ── react 节点：压缩时机与失败隔离 ────────────────────────


def test_compactor_runs_before_llm_call():
    """压缩必须发生在 LLM 调用之前——本次调用才放得下。"""
    from strategy_research.kernel.runtimes.nodes.react import (
        ReactConfig,
        make_react_node,
    )

    client = _RecordingClient()
    order: list[str] = []

    def compactor(messages):
        order.append("compact")
        return messages, ["llm_summarize(10->2)"]

    real_chat = client.chat

    def spy_chat(messages, tools=None):
        order.append("llm")
        return real_chat(messages, tools)

    client.chat = spy_chat  # type: ignore[method-assign]

    node = make_react_node(
        client, _registry(),
        config=ReactConfig(max_iterations=2, compactor=compactor),
    )
    node({"messages": [{"role": "user", "content": "go"}]})

    # 第一次迭代：compact -> llm
    assert order[:2] == ["compact", "llm"]


def test_compacted_messages_reach_the_llm():
    """压缩后的消息列表（不是原始列表）传给 LLM。"""
    from strategy_research.kernel.runtimes.nodes.react import (
        ReactConfig,
        make_react_node,
    )

    client = _RecordingClient()
    big = [{"role": "user", "content": "x" * 500} for _ in range(10)]

    def compactor(messages):
        return [{"role": "system", "content": "SUMMARY"}], ["llm_summarize(10->1)"]

    node = make_react_node(
        client, _registry(),
        config=ReactConfig(max_iterations=1, compactor=compactor),
    )
    node({"messages": big})

    assert client.calls[0][-1]["content"] == "SUMMARY"


def test_no_layers_applied_keeps_messages_and_emits_nothing():
    """未超阈值（applied 为空）：消息不变、不发 compacted 事件。"""
    from strategy_research.kernel.runtimes.nodes.react import (
        ReactConfig,
        make_react_node,
    )

    events: list[tuple[str, dict]] = []
    client = _RecordingClient()
    original = [{"role": "user", "content": "hi"}]

    node = make_react_node(
        client, _registry(), sink=lambda k, d: events.append((k, d)),
        config=ReactConfig(
            max_iterations=1,
            compactor=lambda m: (m, []),  # 未触发
        ),
    )
    node({"messages": original})

    assert not [e for e in events if e[0] == "context.compacted"]
    assert client.calls[0][-1]["content"] == "hi"


def test_compactor_failure_does_not_break_the_turn():
    """压缩抛异常：原样继续 + compact_failed 事件，本轮照常完成。"""
    from strategy_research.kernel.runtimes.nodes.react import (
        ReactConfig,
        make_react_node,
    )

    events: list[tuple[str, dict]] = []
    client = _RecordingClient()

    def boom(messages):
        raise RuntimeError("compact exploded")

    node = make_react_node(
        client, _registry(), sink=lambda k, d: events.append((k, d)),
        config=ReactConfig(max_iterations=2, compactor=boom),
    )
    out = node({"messages": [{"role": "user", "content": "hi"}]})

    assert out["answer"] == "done"
    failures = [e for e in events if e[0] == "context.compact_failed"]
    assert failures and "compact exploded" in failures[0][1]["error"]


def test_no_compactor_keeps_legacy_behaviour():
    """无 compactor：行为与迁移前一致（回归保护）。"""
    from strategy_research.kernel.runtimes.nodes.react import (
        ReactConfig,
        make_react_node,
    )

    events: list[tuple[str, dict]] = []
    client = _RecordingClient()
    node = make_react_node(
        client, _registry(), sink=lambda k, d: events.append((k, d)),
        config=ReactConfig(max_iterations=2),
    )
    out = node({"messages": [{"role": "user", "content": "hi"}]})

    assert out["answer"] == "done"
    assert not [e for e in events if e[0].startswith("context.")]


# ── 共享工厂：语义与 mixin 对齐 ───────────────────────────


def test_make_compactor_rolls_back_on_failure(monkeypatch):
    """L4 失败 → 返回原始消息（模型保留完整历史，而非半压缩状态）。"""
    from strategy_research.core.agent import compaction_runtime as cr

    def boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(cr._compact_mod, "compact_messages", boom)

    compactor = cr.make_compactor(
        compact_config=SimpleNamespace(overflow_ratio=0.8),
        model_context_tokens=1000,
        model_max_output_tokens=100,
        llm_client=object(),
    )
    original = [{"role": "user", "content": "a"}]

    out, applied = compactor(original)

    assert out == original
    assert applied == []


def test_make_compactor_returns_compacted_and_layers(monkeypatch):
    from strategy_research.core.agent import compaction_runtime as cr

    compacted = [{"role": "system", "content": "S"}]

    monkeypatch.setattr(
        cr._compact_mod, "compact_messages",
        lambda *a, **k: (compacted, ["llm_summarize(5->1)"], "summary", "recent"),
    )

    persisted: list = []
    compactor = cr.make_compactor(
        compact_config=SimpleNamespace(overflow_ratio=0.8),
        model_context_tokens=1000,
        model_max_output_tokens=100,
        llm_client=object(),
        persist=lambda s, r, m: persisted.append((s, r, m)),
    )

    out, applied = compactor([{"role": "user", "content": "a"}])

    assert out == compacted
    assert applied == ["llm_summarize(5->1)"]
    assert persisted and persisted[0][0] == "summary"


def test_make_compactor_rolls_back_when_persistence_fails(monkeypatch):
    """持久化失败 → 回滚到原始消息（与 mixin 的 rollback 一致）。"""
    from strategy_research.core.agent import compaction_runtime as cr

    monkeypatch.setattr(
        cr._compact_mod, "compact_messages",
        lambda *a, **k: ([{"role": "system", "content": "S"}],
                         ["llm_summarize(5->1)"], "summary", "recent"),
    )

    def bad_persist(*a, **k):
        raise RuntimeError("persist failed")

    compactor = cr.make_compactor(
        compact_config=SimpleNamespace(overflow_ratio=0.8),
        model_context_tokens=1000,
        model_max_output_tokens=100,
        llm_client=object(),
        persist=bad_persist,
    )
    original = [{"role": "user", "content": "a"}]

    out, applied = compactor(original)

    assert out == original
    assert applied == []


# ── SessionController 接线 ─────────────────────────────────


def test_controller_builds_compactor_only_when_configured():
    from strategy_research.kernel.gateway.session_controller import SessionController

    class _EP:
        def iter_mailboxes(self):
            return []

        def session_id_for(self, key):
            return key

    no_cfg = SessionController(_EP(), client=object())
    assert no_cfg._build_compactor() is None

    with_cfg = SessionController(
        _EP(), client=object(), compact_config=SimpleNamespace(overflow_ratio=0.8),
    )
    compactor = with_cfg._build_compactor()
    assert callable(compactor)
