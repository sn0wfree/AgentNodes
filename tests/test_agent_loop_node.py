"""批次 V3-b · AgentLoop → GraphEngine 节点适配器（默认图换引擎）。

等价性锚点（docs/design/v3-path-convergence-decision-20261109.md §3 V3-b）：
controller 默认图（registry 存在）从 ``make_react_node`` 换成 AgentLoop
适配器后——

- 消息序列不变：system + history + 当前输入（history 原样插回）
- 错误冒泡语义不变：LLM 失败 → ``result.error`` → 节点抛出 → run.failed
- state 契约不变：answer / usage（RunBudget.snapshot 形状）/ stop_reason
- steer 注入通道在安全点生效（``_inbox_drain_hook``）
- 权限闸经 loop 挂载生效（deny → 工具不执行，run 照常完成）
- async 兼容壳：只有 chat/stream 的 client（测试/脚本契约）也能驱动
  骨架引擎的 achat/astream
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from types import SimpleNamespace

import pytest

from agentnodes.kernel.agent.tools.core import BaseTool, ToolRegistry
from agentnodes.kernel.gateway.session_controller import SessionController
from agentnodes.kernel.orchestration import (
    EntryPoint,
    SourceKind,
    WorkKind,
    WorkRequest,
)
from agentnodes.kernel.runtimes.nodes.agent_loop_node import (
    make_agent_loop_node,
    make_loop_event_sink,
    wrap_async_capable_client,
)


# ── 假件 ─────────────────────────────────────────────────


@dataclass
class Resp:
    """LLMResponse 形状的最小替身（loop 直接消费，不经 parse）。

    ``finish_reason`` 必须有——loop 在 tool_calls 分支读它。"""

    content: str = ""
    tool_calls: list = field(default_factory=list)
    usage: dict | None = None
    finish_reason: str = "stop"

    def has_tool_calls(self) -> bool:
        return bool(self.tool_calls)


class RecordingTool(BaseTool):
    name = "recorder"
    description = "records executions"
    parameters = {}

    executed: list[dict] = []

    def execute(self, **kwargs):
        RecordingTool.executed.append(dict(kwargs))
        return json.dumps({"status": "ok"})


class ScriptedClient:
    """按脚本回放；记录每次调用看到的 messages。"""

    def __init__(self, script):
        self._script = list(script)
        self.calls: list[list[dict]] = []

    def chat(self, messages, tools=None, **kw):
        self.calls.append([dict(m) for m in messages])
        if self._script:
            return self._script.pop(0)
        return Resp(content="done")

    def stream(self, messages, tools=None, **kw):
        self.chat(messages, tools=tools, **kw)
        return iter([])


def _registry() -> ToolRegistry:
    reg = ToolRegistry()
    RecordingTool.executed = []
    reg.register(RecordingTool())
    return reg


def _controller(client, registry, sink, **kw) -> SessionController:
    return SessionController(
        EntryPoint(sink=sink), client=client, registry=registry,
        sink=sink, max_iterations=3, **kw,
    )


def _request(text: str, history=None) -> WorkRequest:
    payload = {"text": text}
    if history:
        payload["history"] = history
    return WorkRequest(
        kind=WorkKind.CHAT, source=SourceKind.API_CHAT,
        session_key="chat:s1", payload=payload,
    )


def _loop_for(client, registry, **kw):
    from agentnodes.foundation.llm import LLMConfig
    from agentnodes.kernel.agent.loop.core import AgentLoop

    loop = AgentLoop(
        config=LLMConfig(api_key=""),
        registry=registry,
        # 非 stream：假 client 走 achat 桥（stream 路径还要 parse_response）
        stream_mode=False,
        enable_goal_injection=False,
        enable_memory_injection=False,
        injectors=[],
        approval_timeout=30.0,
        approval_on_timeout="continue",
        on_event=lambda kind, data: None,
        **kw,
    )
    loop.client = wrap_async_capable_client(client)
    return loop


# ── async 兼容壳 ─────────────────────────────────────────


def test_native_client_passes_through_unwrapped():
    """四件套齐全的 client 原样返回（不包壳）。"""

    class Native(ScriptedClient):
        async def achat(self, messages, tools=None, **kw):
            return "native"

        async def astream(self, messages, tools=None, **kw):
            yield "native"

    client = Native([])
    assert wrap_async_capable_client(client) is client


def test_chat_only_client_gets_achat_shim():
    """只有 chat 的 client：achat 桥到 chat，返回同一响应。"""
    client = ScriptedClient([Resp(content="hi")])
    wrapped = wrap_async_capable_client(client)
    assert wrapped is not client

    out = asyncio.run(wrapped.achat([{"role": "user", "content": "q"}]))
    assert out.content == "hi"
    # chat 被真实调用（消息透传）
    assert client.calls[0][-1]["content"] == "q"


def test_stream_only_client_gets_astream_shim():
    """只有 stream 的 client：astream 桥到 stream，逐 chunk 透传。"""

    class StreamOnly:
        def __init__(self):
            self.seen = []

        def stream(self, messages, tools=None, **kw):
            self.seen.append(list(messages))
            return iter(["c1", "c2"])

    wrapped = wrap_async_capable_client(StreamOnly())

    async def _collect():
        return [c async for c in wrapped.astream([{"role": "user", "content": "q"}])]

    chunks = asyncio.run(_collect())
    assert chunks == ["c1", "c2"]


# ── 节点契约 ─────────────────────────────────────────────


def test_node_writes_answer_usage_and_stop_reason():
    client = ScriptedClient([Resp(content="答案", usage={"total_tokens": 12})])
    loop = _loop_for(client, _registry())
    node = make_agent_loop_node(loop)

    out = node({
        "messages": [{"role": "user", "content": "q"}],
        "run_id": "s#1:r0",
    })

    assert out["answer"] == "答案"
    assert out["stop_reason"] == "stop"
    assert out["usage"]["iterations"] == 1
    assert out["usage"]["tokens"] >= 0
    # messages 写回（含 system + user + assistant）
    roles = [m["role"] for m in out["messages"]]
    assert roles == ["system", "user", "assistant"]
    assert out["messages"][-1]["content"] == "答案"


def test_node_splits_history_and_task():
    """history 在前、当前输入在后（与 react 的整列表直发字节序一致）。"""
    client = ScriptedClient([Resp(content="ok")])
    loop = _loop_for(client, _registry())
    node = make_agent_loop_node(loop)

    node({"messages": [
        {"role": "user", "content": "older"},
        {"role": "assistant", "content": "reply"},
        {"role": "user", "content": "current"},
    ]})

    contents = [m["content"] for m in client.calls[0] if m["role"] != "system"]
    assert contents == ["older", "reply", "current"]


def test_node_raises_on_llm_error():
    """LLM 抛错 → result.error → 节点重新抛出（controller 捕获为 run.failed）。"""

    class Boom:
        def chat(self, messages, tools=None, **kw):
            raise RuntimeError("provider down")

    loop = _loop_for(Boom(), _registry())
    node = make_agent_loop_node(loop)

    with pytest.raises(RuntimeError, match="provider down"):
        node({"messages": [{"role": "user", "content": "q"}]})


def test_node_wires_steer_inbox_hook():
    """state 的 steer_inbox 挂到 loop 的每轮安全点（合批形状同 react）。"""
    client = ScriptedClient([Resp(content="ok")])
    loop = _loop_for(client, _registry())
    node = make_agent_loop_node(loop)

    drained: list[list[str]] = []

    class FakeInbox:
        def drain(self):
            drained.append(["补一句", "再补一句"])
            return drained[-1]

    node({"messages": [{"role": "user", "content": "q"}], "steer_inbox": FakeInbox()})

    assert drained, "hook must have been invoked"
    # hook 形状：_drain_inbox 把 drain 结果合批为一条 user 消息
    msgs: list[dict] = []
    loop._inbox_drain_hook = FakeInbox().drain
    loop._drain_inbox(msgs)
    assert msgs == [{"role": "user", "content": "补一句\n\n再补一句"}]


def test_loop_event_sink_strips_llm_request():
    """llm_request 的大字段被剥离，其余事件原样透传。"""
    seen: list[tuple[str, dict]] = []
    sink = make_loop_event_sink(lambda k, d: seen.append((k, d)))

    sink("llm_request", {"system_prompt": "BIG", "tools_schema": [1], "iter": 3})
    sink("text_delta", {"text": "x", "text_id": "t1"})

    assert seen[0][0] == "llm_request"
    assert "system_prompt" not in seen[0][1]
    assert "tools_schema" not in seen[0][1]
    assert seen[0][1]["iter"] == 3
    assert seen[1] == ("text_delta", {"text": "x", "text_id": "t1"})


# ── controller 级（默认图换引擎后的端到端）─────────────────


def test_controller_default_graph_still_named_react_and_answers():
    """默认图节点名仍叫 react（test_react_node_receives_assembled_prompt 钉住）。"""
    probe: list[tuple[str, dict]] = []
    client = ScriptedClient([Resp(content="final answer")])
    ctrl = _controller(client, _registry(), lambda k, d: probe.append((k, d)))
    ctrl._ep.submit(_request("hello"))
    [run] = ctrl.run_pending()

    assert run.ok
    assert run.answer == "final answer"
    node_started = [d["node"] for k, d in probe if k == "node.started"]
    assert node_started == ["react"]
    assert "run.failed" not in [k for k, _ in probe]


def test_controller_history_order_preserved():
    ctrl = _controller(
        ScriptedClient([Resp(content="ok")]), _registry(), lambda k, d: None,
    )
    ctrl._ep.submit(_request("current", history=[
        {"role": "user", "content": "older"},
        {"role": "assistant", "content": "reply"},
    ]))
    ctrl.run_pending()
    # ScriptedClient.calls 由 _controller 的 client 持有
    # （_controller 里 client 即 ScriptedClient）
    seen = ctrl._client.calls[0]
    contents = [m["content"] for m in seen if m["role"] != "system"]
    assert contents == ["older", "reply", "current"]


def test_tool_events_emitted_in_order():
    """tool_call 必在 tool_result 之前,且携带工具名。

    **V3-c**:语义迁自原 test_b1f_equivalence 的 react 段——词表换
    AgentLoop 的 tool_call/tool_result(产品词表)。"""

    class _EchoTool(BaseTool):
        name = "echo"
        description = "echo"
        parameters = {}

        def execute(self, **kwargs):
            return json.dumps({"status": "ok"})

    reg = ToolRegistry()
    RecordingTool.executed = []
    reg.register(_EchoTool())
    tc = SimpleNamespace(id="c1", name="echo", arguments={})
    client = ScriptedClient([
        Resp(tool_calls=[tc]),
        Resp(content="fin"),
    ])
    loop = _loop_for(client, reg)
    events: list[tuple[str, dict]] = []
    loop._on_event = lambda kind, data: events.append((kind, data))
    node = make_agent_loop_node(loop)
    node({"messages": [{"role": "user", "content": "go"}]})

    tool_events = [(k, d) for k, d in events if k in ("tool_call", "tool_result")]
    assert [k for k, _ in tool_events] == ["tool_call", "tool_result"]
    assert tool_events[0][1]["tool"] == "echo"
    assert tool_events[1][1]["tool"] == "echo"


def test_controller_llm_failure_becomes_run_failed():
    class Boom:
        def chat(self, messages, tools=None, **kw):
            raise RuntimeError("provider down")

    probe: list[tuple[str, dict]] = []
    ctrl = _controller(Boom(), _registry(), lambda k, d: probe.append((k, d)))
    ctrl._ep.submit(_request("hi"))
    [run] = ctrl.run_pending()

    assert run.ok is False
    assert "provider down" in (run.error or "")
    kinds = [k for k, _ in probe]
    assert "run.failed" in kinds
    settled = [d for k, d in probe if k == "run.settled"]
    assert settled[-1]["ok"] is False


def test_controller_permission_deny_blocks_tool_execution():
    """权限闸经 loop 挂载生效：DENY → 工具不执行，run 照常完成。

    空 ruleset + 非交互 → 未匹配工具落 ASK → fail-closed DENY
    （evaluator.py:111 的安全默认）。
    """
    from agentnodes.kernel.permission import PermissionEvaluator

    evaluator = PermissionEvaluator(ruleset=[], interactive=False)
    tc = SimpleNamespace(
        id="c1", name="recorder", arguments={"x": 1},
    )
    client = ScriptedClient([
        Resp(tool_calls=[tc]),
        Resp(content="adapted after deny"),
    ])
    probe: list[tuple[str, dict]] = []
    ctrl = _controller(client, _registry(), lambda k, d: probe.append((k, d)))
    ctrl._permission_evaluator = evaluator
    ctrl._ep.submit(_request("go"))
    [run] = ctrl.run_pending()

    assert run.ok
    assert run.answer == "adapted after deny"
    # 未匹配 → ASK → 非交互 DENY → recorder 从未执行
    assert RecordingTool.executed == []


def test_controller_permission_allow_executes_tool():
    """放行路径：显式 allow 规则 → 工具执行，ctx 携带 session_id。"""
    from agentnodes.kernel.permission import PermissionEvaluator
    from agentnodes.kernel.permission.schema import (
        PermissionAction,
        PermissionRule,
    )

    evaluator = PermissionEvaluator(
        ruleset=[PermissionRule(
            permission="recorder", pattern="*",
            action=PermissionAction.ALLOW,
        )],
        interactive=False,
    )
    tc = SimpleNamespace(id="c1", name="recorder", arguments={"x": 1})
    client = ScriptedClient([
        Resp(tool_calls=[tc]),
        Resp(content="done"),
    ])
    ctrl = _controller(client, _registry(), lambda k, d: None)
    ctrl._permission_evaluator = evaluator
    ctrl._ep.submit(_request("go"))
    [run] = ctrl.run_pending()

    assert run.ok and run.answer == "done"
    assert len(RecordingTool.executed) == 1
    # ctx 形状：session_id 经 ToolContext 携带（B-c 归属契约）——
    # 物理会话 id 由 EntryPoint 惰性分配（UUID），按 ep 反查
    physical = ctrl._ep.session_id_for("chat:s1")
    assert RecordingTool.executed[0]["ctx"].session_id == physical


# ── 真实 LLM（与 V2 同门禁：QNODES_LIVE_LLM=1 + live_llm marker）────────


@pytest.mark.live_llm
def test_live_controller_default_graph_tool_loop(live_client):
    """**V3-b 端到端**：controller 默认图（换 AgentLoop 后）× 真实模型。

    与 ``test_react_live`` 的 calculator 场景同构——真实 LLM 发起工具调用，
    registry 执行，答案回填。这是换引擎后生产 web chat 路径的真身。
    """
    from agentnodes.kernel.agent.tools.core import ToolRegistry as _TR

    class CalculatorTool(BaseTool):
        name = "calculator"
        description = "计算四则运算。参数 expr：如 '17*23'"
        parameters = {
            "type": "object",
            "properties": {"expr": {"type": "string"}},
            "required": ["expr"],
        }
        _ALLOWED = set("0123456789+-*/(). ")

        def execute(self, *, expr: str = "", **_: object) -> str:
            cleaned = expr.strip()
            if not cleaned or set(cleaned) - self._ALLOWED:
                return '{"error": "bad expr"}'
            try:
                result = eval(cleaned, {"__builtins__": {}}, {})  # noqa: S307
            except Exception as exc:  # noqa: BLE001
                return f'{{"error": "{exc}"}}'
            return f'{{"result": {result}}}'

    registry = _TR()
    registry.register(CalculatorTool())

    probe: list[tuple[str, dict]] = []
    ctrl = _controller(
        live_client, registry, lambda k, d: probe.append((k, d)),
    )
    ctrl._system = "你是计算助手。凡是数学计算必须调用 calculator 工具，不要心算。回答用中文，不超过 30 字。"
    ctrl._ep.submit(_request("请计算 17*23+100 的结果"))
    [run] = ctrl.run_pending()

    assert run.ok, run.error
    assert "491" in run.answer, f"答案应含 491: {run.answer}"
    # AgentLoop 词表的事件真实流出（tool_call / tool_result 成对）
    calls = [d for k, d in probe if k == "tool_call"]
    results = [d for k, d in probe if k == "tool_result"]
    assert calls and results
    assert calls[0]["tool"] == "calculator"
