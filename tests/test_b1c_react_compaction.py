"""B1-c: mid-loop compaction。

**V3-c**:react 节点已删除(双执行路径收敛到 AgentLoop)。原「react 节点
压缩时机/失败隔离」测试对象消失,等价语义改写为 AgentLoop 版(压缩走
``DefaultCompactionStep`` → ``_maybe_compact``,同一 ``make_compactor``
实现);``make_compactor`` 引擎级与 controller 接线级测试保留原样。
"""
from __future__ import annotations
from llm_client_mock import patch_client_achat

from types import SimpleNamespace


# ── AgentLoop:压缩时机(原 react 级语义的等价改写)──────────


def _recording_loop(client):
    from agentnodes.foundation.llm import LLMConfig
    from agentnodes.kernel.agent.loop import AgentLoop
    from agentnodes.kernel.agent.tools.core import ToolRegistry

    reg = ToolRegistry()
    loop = AgentLoop(
        config=LLMConfig(api_key=""),
        registry=reg,
        enable_goal_injection=False,
        enable_memory_injection=False,
        injectors=[],
        stream_mode=False,
        on_event=lambda kind, data: None,
        max_iterations=2,
    )
    loop.client = client
    # 统一引擎 U5：``run()`` 走 async 路径 → 自定义 client 必须有 async 入口
    # （否则 LLM 调用落到真客户端，``calls`` 为空 → IndexError）。
    patch_client_achat(client, client.chat)
    return loop


class _FakeResponse:
    def __init__(self, content: str = "", tool_calls: list | None = None):
        self.content = content
        self.tool_calls = tool_calls or []
        self.usage = None
        self.finish_reason = "stop"

    def has_tool_calls(self) -> bool:
        return bool(self.tool_calls)


class _RecordingClient:
    """Records the messages it was called with; one tool call then answer."""

    def __init__(self) -> None:
        self.calls: list[list[dict]] = []

    def chat(self, messages, tools=None):
        self.calls.append([dict(m) for m in messages])
        if len(self.calls) == 1:
            tc = SimpleNamespace(id="c1", name="noop", arguments={})
            return _FakeResponse(tool_calls=[tc])
        return _FakeResponse(content="done")

    def parse_response(self, raw):
        raise AssertionError("non-stream path must not hit parse_response")


def test_compactor_runs_before_llm_call():
    """压缩必须发生在 LLM 调用之前——本次调用才放得下。"""
    client = _RecordingClient()
    order: list[str] = []

    def spy_compact(messages):
        order.append("compact")
        return messages, []

    real_chat = client.chat

    def spy_chat(messages, tools=None, **kw):
        order.append("llm")
        return real_chat(messages, tools=tools)

    client.chat = spy_chat  # type: ignore[method-assign]
    # 统一引擎 U5：``run()`` 走 async 路径 → 需 async 入口（否则落到真网络）
    patch_client_achat(client, spy_chat)

    loop = _recording_loop(client)
    loop._maybe_compact = spy_compact  # type: ignore[method-assign]
    loop.run("go")

    # 第一次迭代:compact -> llm
    assert order[:2] == ["compact", "llm"]


def test_compacted_messages_reach_the_llm():
    """压缩后的消息列表(不是原始列表)传给 LLM。"""
    client = _RecordingClient()

    def compact_replacement(messages):
        return [{"role": "system", "content": "SUMMARY"}], ["llm_summarize(10->1)"]

    loop = _recording_loop(client)
    loop._maybe_compact = compact_replacement  # type: ignore[method-assign]
    loop.run("go")

    assert client.calls[0][-1]["content"] == "SUMMARY"


# ── 共享工厂:语义与 mixin 对齐 ───────────────────────────


def test_make_compactor_rolls_back_on_failure(monkeypatch):
    """L4 失败 → 返回原始消息(模型保留完整历史,而非半压缩状态)。"""
    from agentnodes.kernel.agent.compact import runtime as cr

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
    from agentnodes.kernel.agent.compact import runtime as cr

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
    """持久化失败 → 回滚到原始消息(与 mixin 的 rollback 一致)。"""
    from agentnodes.kernel.agent.compact import runtime as cr

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


def test_controller_compaction_wiring_goes_through_loop():
    """**批次 V3-b**:压缩器由 AgentLoop 内部构造(同一 ``make_compactor``
    实现),controller 只负责把 compact_config 传给 loop。

    - 未传 compact_config → loop 回落 ``config.compact_config``(无则
      ``CompactConfig()`` 默认)——与 chat_loop 的取值序一致;
    - 显式传入 → loop.cc 就是该对象(空 signature 时 harness 覆盖按
      ``apply_compaction_overrides`` 契约原样返回同一实例)。
    """
    from agentnodes.foundation.compact_config import CompactConfig
    from agentnodes.foundation.llm import LLMConfig
    from agentnodes.kernel.agent.loop import AgentLoop
    from agentnodes.kernel.gateway.session_controller import SessionController

    class _EP:
        def iter_mailboxes(self):
            return []

        def session_id_for(self, key):
            return key

    class _FakeRegistry:
        """AgentLoop 构造期触碰的最小面(_tools/all_tools/get_definitions)。"""

        def __init__(self) -> None:
            self._tools: dict = {}

        def get_definitions(self):
            return []

        def all_tools(self):
            return list(self._tools.values())

        def get(self, name):
            return self._tools.get(name)

    def _loop_of(ctrl: SessionController) -> AgentLoop:
        node = ctrl._build_engine()._fns["react"]
        return next(
            c.cell_contents for c in node.__closure__
            if isinstance(c.cell_contents, AgentLoop)
        )

    no_cfg = SessionController(_EP(), client=object(), registry=_FakeRegistry())
    loop = _loop_of(no_cfg)
    assert isinstance(loop.cc, CompactConfig)

    marker = SimpleNamespace(overflow_ratio=0.8)
    with_cfg = SessionController(
        _EP(), client=object(), registry=_FakeRegistry(),
        compact_config=marker,
    )
    assert _loop_of(with_cfg).cc is marker
