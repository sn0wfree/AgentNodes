# -*- coding: utf-8 -*-
"""缺口①③ 的出口回归（裁决 3：2026-11-18）。

缺口① attempt 级超时（Gateway 等价语义）——三层验证：
1. **执行器检查点**：``AgentLoop._check_attempt_timeout`` 到点抛
   ``AttemptTimeoutError``（检查点中止，不代表阻塞工具可被强制中断）；
2. **引擎挂钩**：默认引擎（skeleton）的每轮开头动作会调该检查点
   （``engine_adapters.LoopIterationStart``）；
3. **控制面收尾**：超时异常经 controller 的既有失败路径 →
   ``run.failed`` + ``run.settled{ok:False}`` + Assignment settle +
   fence release（明确失败终态 / 完成事件 / 资源释放）。

缺口③ 历史读取严格失败：读取失败 → 中止提交（无 run 事件）+ 明确错误。
"""
from __future__ import annotations

import asyncio
import threading
import time

import pytest

from agentnodes.foundation.llm import LLMConfig
from agentnodes.kernel.agent.loop.core import AgentLoop
from agentnodes.kernel.agent.loop.errors import AttemptTimeoutError
from agentnodes.kernel.agent.tools.core import ToolRegistry
from agentnodes.kernel.gateway.gateway import Gateway

pytestmark = pytest.mark.asyncio


def _make_loop(*, timeout_s, max_iterations=5, stream_mode=True):
    loop = AgentLoop(
        config=LLMConfig(api_key=""),
        registry=ToolRegistry(),
        workspace=None,
        max_iterations=max_iterations,
        stream_mode=stream_mode,
    )
    loop._attempt_timeout_s = timeout_s
    return loop


# ── 缺口①-1：执行器检查点 ───────────────────────────────────────────


class TestAttemptTimeoutCheckPoint:
    async def test_budget_read_from_env_default_600(self, monkeypatch):
        """预算来自 AGENTNODES_CHAT_ATTEMPT_TIMEOUT（默认 600；0 = 禁用）。"""
        from agentnodes.kernel.gateway.session_controller import SessionController

        ctrl = SessionController.__new__(SessionController)
        monkeypatch.setenv("AGENTNODES_CHAT_ATTEMPT_TIMEOUT", "600")
        assert ctrl._resolve_attempt_timeout_s() == 600.0

        monkeypatch.setenv("AGENTNODES_CHAT_ATTEMPT_TIMEOUT", "0")
        ctrl2 = SessionController.__new__(SessionController)
        assert ctrl2._resolve_attempt_timeout_s() is None, "0 = 禁用"

        monkeypatch.delenv("AGENTNODES_CHAT_ATTEMPT_TIMEOUT", raising=False)
        ctrl3 = SessionController.__new__(SessionController)
        assert ctrl3._resolve_attempt_timeout_s() == 600.0

    async def test_check_point_raises_when_deadline_passed(self):
        """到点 → 抛 AttemptTimeoutError（执行器内部中断）。"""
        loop = _make_loop(timeout_s=0.2)
        loop._begin_attempt_deadline()
        time.sleep(1.2)
        with pytest.raises(AttemptTimeoutError):
            loop._check_attempt_timeout(1)

    async def test_check_point_passes_before_deadline(self):
        """未到点 → 不抛（正常执行不受影响）。"""
        loop = _make_loop(timeout_s=5.0)
        loop._begin_attempt_deadline()
        loop._check_attempt_timeout(1)  # 不抛

    async def test_timeout_message_states_budget(self):
        """错误信息含预算秒数（与旧路径 "attempt timed out after Ns" 对齐）。"""
        loop = _make_loop(timeout_s=0.2)
        loop._begin_attempt_deadline()
        time.sleep(1.2)
        with pytest.raises(AttemptTimeoutError) as exc:
            loop._check_attempt_timeout(2)
        assert "timed out" in str(exc.value)

    async def test_deadline_starts_at_execution_not_queueing(self):
        """计时从**实际执行**开始——排队时长不计入预算。"""
        loop = _make_loop(timeout_s=5.0)
        # 模拟"排队期已过"：把 deadline 拨到过去，再重新起算 → 不应超时
        loop._attempt_deadline = time.monotonic() - 100
        loop._begin_attempt_deadline()
        loop._check_attempt_timeout(1)  # 不抛

    async def test_zero_budget_disables_check(self):
        """None = 禁用（不因预算缺失误杀正常执行）。"""
        loop = _make_loop(timeout_s=None)
        loop._begin_attempt_deadline()
        assert loop._attempt_deadline is None
        loop._check_attempt_timeout(1)  # 不抛


@pytest.mark.parametrize("engine", ["skeleton", "legacy"])
async def test_attempt_deadline_interrupts_last_iteration(engine):
    from agentnodes.foundation.llm import LLMResponse

    loop = _make_loop(timeout_s=0.2, max_iterations=1, stream_mode=False)
    started = asyncio.Event()
    cancelled = asyncio.Event()
    events = []
    loop._on_event = lambda kind, data: events.append((kind, data))

    async def slow_chat(messages, **kwargs):
        started.set()
        try:
            await asyncio.sleep(1.2)
        except asyncio.CancelledError:
            cancelled.set()
            raise
        return LLMResponse(content="late", tool_calls=[], finish_reason="stop")

    loop.client.achat = slow_chat
    result = await loop.arun("deadline probe", engine=engine)

    if not started.is_set():
        pytest.fail("the probe must execute the real LLM call")
    assert cancelled.is_set()
    assert result.finished_reason == "timeout"
    assert result.error and "timed out" in result.error
    assert not result.success
    assert sum(kind == "loop_end" for kind, _ in events) == 1
    assert sum(kind == "iter_end" for kind, _ in events) == 1


async def test_iteration_timeout_remains_distinct_from_attempt_timeout():
    from agentnodes.kernel.agent.loop.core import LoopResult

    loop = _make_loop(timeout_s=1.0)
    loop._iteration_timeout_s = 0.05
    loop._begin_attempt_deadline()
    result = LoopResult()
    cancelled = asyncio.Event()

    async def slow_call():
        try:
            await asyncio.sleep(1.2)
        finally:
            cancelled.set()

    assert await loop._with_iteration_timeout(slow_call(), 1, result) is None
    assert cancelled.is_set()
    assert result.finished_reason == "timeout"
    assert result.error and "iteration 1 exceeded" in result.error


async def test_provider_timeout_is_not_reported_as_attempt_deadline():
    from agentnodes.kernel.agent.loop.core import LoopResult

    loop = _make_loop(timeout_s=1.0)
    loop._iteration_timeout_s = None
    loop._begin_attempt_deadline()

    async def provider_timeout():
        raise TimeoutError("provider timeout")

    with pytest.raises(TimeoutError, match="provider timeout"):
        await loop._with_iteration_timeout(provider_timeout(), 1, LoopResult())


@pytest.mark.parametrize("engine", ["skeleton", "legacy"])
async def test_attempt_deadline_interrupts_stream(engine):
    from agentnodes.foundation.llm.parser import StreamChunk

    loop = _make_loop(timeout_s=0.2, max_iterations=1, stream_mode=True)
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def slow_stream(messages, **kwargs):
        started.set()
        try:
            await asyncio.sleep(1.2)
        except asyncio.CancelledError:
            cancelled.set()
            raise
        yield StreamChunk(delta_content="late", finish_reason="stop")

    loop.client.astream = slow_stream
    result = await loop.arun("deadline probe", engine=engine)
    if not started.is_set():
        pytest.fail("the probe must execute the real streaming call")
    assert cancelled.is_set()
    assert result.finished_reason == "timeout"
    assert result.error and "attempt timed out" in result.error
    assert not result.success


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="synchronous tools cannot be interrupted by the attempt deadline",
)
async def test_attempt_deadline_stops_inflight_tool_side_effect():
    from agentnodes.foundation.llm import LLMResponse, ToolCall
    from agentnodes.kernel.agent.tools.core import BaseTool

    calls = []

    class SlowTool(BaseTool):
        name = "slow_probe"
        description = "bounded timeout probe"
        parameters = {"type": "object", "properties": {}}

        def execute(self, **kwargs):
            calls.append("started")
            time.sleep(1.2)
            calls.append("side_effect")
            return "ok"

    loop = _make_loop(timeout_s=0.2, max_iterations=2, stream_mode=False)
    loop.registry.register(SlowTool())

    async def tool_request(messages, **kwargs):
        return LLMResponse(
            content="",
            tool_calls=[ToolCall(id="probe", name="slow_probe", arguments={})],
            finish_reason="tool_calls",
        )

    loop.client.achat = tool_request
    result = await loop.arun("deadline probe")

    if "started" not in calls:
        pytest.fail("the probe must execute the real tool")
    assert "side_effect" not in calls
    assert result.finished_reason == "timeout"
    assert result.error and "timed out" in result.error


@pytest.mark.parametrize("engine", ["skeleton", "legacy"])
async def test_finished_slow_tool_does_not_report_success(engine):
    from agentnodes.foundation.llm import LLMResponse, ToolCall
    from agentnodes.kernel.agent.tools.core import BaseTool

    calls = []
    events = []

    class SlowTool(BaseTool):
        name = "slow_probe"
        description = "bounded timeout probe"
        parameters = {"type": "object", "properties": {}}

        def execute(self, **kwargs):
            calls.append("started")
            time.sleep(0.6)
            calls.append("finished")
            return "ok"

    loop = _make_loop(timeout_s=0.2, max_iterations=1, stream_mode=False)
    loop._on_event = lambda kind, data: events.append((kind, data))
    loop.registry.register(SlowTool())

    async def tool_request(messages, **kwargs):
        return LLMResponse(
            content="",
            tool_calls=[ToolCall(id="probe", name="slow_probe", arguments={})],
            finish_reason="tool_calls",
        )

    loop.client.achat = tool_request
    result = await loop.arun("deadline probe", engine=engine)
    assert calls == ["started", "finished"]
    assert result.finished_reason == "timeout"
    assert result.error and "attempt timed out" in result.error
    assert not result.success
    assert sum(kind == "loop_end" for kind, _ in events) == 1
    assert sum(kind == "iter_end" for kind, _ in events) == 1


@pytest.mark.parametrize("engine", ["skeleton", "legacy"])
async def test_expired_deadline_does_not_start_next_write_tool(engine):
    from agentnodes.foundation.llm import LLMResponse, ToolCall
    from agentnodes.kernel.agent.tools.core import BaseTool

    calls = []
    events = []

    class SlowWrite(BaseTool):
        name = "slow_write_probe"
        description = "bounded timeout probe"
        parameters = {"type": "object", "properties": {}}
        effects = frozenset({"fs"})

        def execute(self, **kwargs):
            calls.append("slow_started")
            time.sleep(0.6)
            calls.append("slow_finished")
            return "ok"

    class NextWrite(BaseTool):
        name = "next_write_probe"
        description = "second timeout probe"
        parameters = {"type": "object", "properties": {}}
        effects = frozenset({"fs"})

        def execute(self, **kwargs):
            calls.append("next_started")
            return "ok"

    loop = _make_loop(timeout_s=0.2, max_iterations=1, stream_mode=False)
    loop._on_event = lambda kind, data: events.append((kind, data))
    loop.registry.register(SlowWrite())
    loop.registry.register(NextWrite())

    async def tool_request(messages, **kwargs):
        return LLMResponse(
            content="",
            tool_calls=[
                ToolCall(id="first", name="slow_write_probe", arguments={}),
                ToolCall(id="second", name="next_write_probe", arguments={}),
            ],
            finish_reason="tool_calls",
        )

    loop.client.achat = tool_request
    result = await loop.arun("deadline probe", engine=engine)
    assert calls == ["slow_started", "slow_finished"]
    assert result.finished_reason == "timeout"
    assert result.error and "attempt timed out" in result.error
    endings = [data for kind, data in events if kind == "iter_end"]
    assert len(endings) == 1 and endings[0]["iteration"] == 1


@pytest.mark.parametrize("engine", ["skeleton", "legacy"])
async def test_readonly_batch_timeout_stops_before_next_llm_call(engine):
    from agentnodes.foundation.llm import LLMResponse, ToolCall
    from agentnodes.kernel.agent.tools.core import BaseTool

    calls = []

    class AsyncRead(BaseTool):
        name = "async_read_probe"
        description = "bounded parallel timeout probe"
        parameters = {"type": "object", "properties": {}}

        def execute(self, **kwargs):
            pytest.fail("the probe must execute the async path")

        async def ainvoke(self, kwargs, ctx=None):
            calls.append("started")
            await asyncio.sleep(0.5)
            calls.append("finished")
            return "ok"

    loop = _make_loop(timeout_s=0.2, max_iterations=2, stream_mode=False)
    loop.registry.register(AsyncRead())
    requests = []

    async def tool_request(messages, **kwargs):
        requests.append("request")
        return LLMResponse(
            content="",
            tool_calls=[
                ToolCall(id="first", name="async_read_probe", arguments={}),
                ToolCall(id="second", name="async_read_probe", arguments={}),
            ],
            finish_reason="tool_calls",
        )

    loop.client.achat = tool_request
    result = await loop.arun("deadline probe", engine=engine)
    assert calls.count("started") == 2 and calls.count("finished") == 2
    assert requests == ["request"]
    assert result.finished_reason == "timeout"
    assert result.error and "attempt timed out" in result.error


@pytest.mark.parametrize("engine", ["skeleton", "legacy"])
async def test_expired_deadline_stops_tool_retry(engine, monkeypatch):
    from agentnodes.foundation.llm import LLMResponse, ToolCall
    from agentnodes.kernel.agent.loop import tools as loop_tools
    from agentnodes.kernel.agent.tools.core import BaseTool

    calls = []

    class SlowFailure(BaseTool):
        name = "slow_retry_probe"
        description = "bounded retry timeout probe"
        parameters = {"type": "object", "properties": {}}

        def execute(self, **kwargs):
            calls.append("started")
            time.sleep(0.6)
            raise OSError("transient probe failure")

    monkeypatch.setattr(loop_tools, "_TOOL_RETRY_DELAY", 0)
    loop = _make_loop(timeout_s=0.2, max_iterations=1, stream_mode=False)
    loop.registry.register(SlowFailure())

    async def tool_request(messages, **kwargs):
        return LLMResponse(
            content="",
            tool_calls=[ToolCall(id="probe", name="slow_retry_probe", arguments={})],
            finish_reason="tool_calls",
        )

    loop.client.achat = tool_request
    result = await loop.arun("deadline probe", engine=engine)
    assert calls == ["started"]
    assert result.finished_reason == "timeout"
    assert result.error and "attempt timed out" in result.error


@pytest.mark.parametrize("engine", ["skeleton", "legacy"])
async def test_expired_deadline_stops_not_yet_started_readonly_tool(engine):
    from agentnodes.foundation.llm import LLMResponse, ToolCall
    from agentnodes.kernel.agent.tools.core import BaseTool

    calls = []

    class SlowRead(BaseTool):
        name = "slow_read_probe"
        description = "bounded readonly timeout probe"
        parameters = {"type": "object", "properties": {}}

        def execute(self, **kwargs):
            calls.append("slow_started")
            time.sleep(0.6)
            calls.append("slow_finished")
            return "ok"

    class NextRead(BaseTool):
        name = "next_read_probe"
        description = "second readonly timeout probe"
        parameters = {"type": "object", "properties": {}}

        def execute(self, **kwargs):
            calls.append("next_started")
            return "ok"

    loop = _make_loop(timeout_s=0.2, max_iterations=1, stream_mode=False)
    loop.registry.register(SlowRead())
    loop.registry.register(NextRead())

    async def tool_request(messages, **kwargs):
        return LLMResponse(
            content="",
            tool_calls=[
                ToolCall(id="first", name="slow_read_probe", arguments={}),
                ToolCall(id="second", name="next_read_probe", arguments={}),
            ],
            finish_reason="tool_calls",
        )

    loop.client.achat = tool_request
    result = await loop.arun("deadline probe", engine=engine)
    assert calls == ["slow_started", "slow_finished"]
    assert result.finished_reason == "timeout"
    assert result.error and "attempt timed out" in result.error


@pytest.mark.parametrize("engine", ["skeleton", "legacy"])
async def test_expired_deadline_stops_next_composite_step(engine):
    from agentnodes.foundation.llm import LLMResponse, ToolCall
    from agentnodes.kernel.agent.plugins.combo import CompositeTool
    from agentnodes.kernel.agent.tools.core import BaseTool

    calls = []

    class SlowStep(BaseTool):
        name = "slow_step_probe"
        description = "bounded composite timeout probe"
        parameters = {"type": "object", "properties": {}}

        def execute(self, **kwargs):
            calls.append("slow_started")
            time.sleep(0.6)
            calls.append("slow_finished")
            return '{"status":"ok"}'

    class NextStep(BaseTool):
        name = "next_step_probe"
        description = "second composite timeout probe"
        parameters = {"type": "object", "properties": {}}
        effects = frozenset({"fs"})

        def execute(self, **kwargs):
            calls.append("next_started")
            return '{"status":"ok"}'

    loop = _make_loop(timeout_s=0.2, max_iterations=1, stream_mode=False)
    loop.registry.register(SlowStep())
    loop.registry.register(NextStep())
    loop.registry.register(CompositeTool(
        name="combo_probe",
        description="composite probe",
        category="test",
        steps=[
            {"tool": "slow_step_probe", "params": {}},
            {"tool": "next_step_probe", "params": {}},
        ],
        registry=loop.registry,
    ))

    async def tool_request(messages, **kwargs):
        return LLMResponse(
            content="",
            tool_calls=[ToolCall(id="probe", name="combo_probe", arguments={})],
            finish_reason="tool_calls",
        )

    loop.client.achat = tool_request
    result = await loop.arun("deadline probe", engine=engine)
    assert calls == ["slow_started", "slow_finished"]
    assert result.finished_reason == "timeout"
    assert result.error and "attempt timed out" in result.error


@pytest.mark.parametrize("engine", ["skeleton", "legacy"])
async def test_parallel_tools_finish_before_timeout_ending(engine):
    from agentnodes.foundation.llm import LLMResponse, ToolCall
    from agentnodes.kernel.agent.tools.core import BaseTool

    order = []

    class DelayedStart(BaseTool):
        name = "delayed_start_probe"
        description = "delayed tool entry probe"
        parameters = {"type": "object", "properties": {}}

        def execute(self, **kwargs):
            pytest.fail("the expired tool must not execute")

        async def ainvoke(self, kwargs, ctx=None):
            await asyncio.sleep(0.5)
            return await super().ainvoke(kwargs, ctx)

    class RunningTool(BaseTool):
        name = "running_tool_probe"
        description = "running tool probe"
        parameters = {"type": "object", "properties": {}}

        def execute(self, **kwargs):
            pytest.fail("the probe must execute the async path")

        async def ainvoke(self, kwargs, ctx=None):
            order.append("started")
            await asyncio.sleep(0.8)
            order.append("finished")
            return "ok"

    loop = _make_loop(timeout_s=0.2, max_iterations=1, stream_mode=False)
    loop._on_event = lambda kind, data: order.append(kind)
    loop.registry.register(DelayedStart())
    loop.registry.register(RunningTool())

    async def tool_request(messages, **kwargs):
        return LLMResponse(
            content="",
            tool_calls=[
                ToolCall(id="first", name="delayed_start_probe", arguments={}),
                ToolCall(id="second", name="running_tool_probe", arguments={}),
            ],
            finish_reason="tool_calls",
        )

    loop.client.achat = tool_request
    result = await loop.arun("deadline probe", engine=engine)
    assert "started" in order and "finished" in order
    assert order.index("finished") < order.index("loop_end")
    assert order.count("loop_end") == 1
    assert result.finished_reason == "timeout"


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="cancelling the sync-only client bridge does not stop its worker thread",
)
async def test_attempt_deadline_stops_sync_only_client_worker():
    from agentnodes.foundation.llm import LLMResponse
    from agentnodes.kernel.runtimes.nodes.agent_loop_node import wrap_async_capable_client

    started = threading.Event()
    finished = threading.Event()

    class SyncClient:
        def chat(self, messages, **kwargs):
            started.set()
            time.sleep(0.8)
            finished.set()
            return LLMResponse(content="late", tool_calls=[], finish_reason="stop")

    loop = _make_loop(timeout_s=0.2, max_iterations=1, stream_mode=False)
    loop.client = wrap_async_capable_client(SyncClient())
    result = await loop.arun("deadline probe")
    if not started.is_set():
        pytest.fail("the probe must execute the sync client")
    # xfail 探针：等待窗口须 > 工具 sleep（现 0.8s），让工作线程有机会
    # 自然结束并设置 finished → `assert not finished` 失败 → xfail 成立。
    # 时间整体放大 4x 后这里必须同步放大，否则 finished 来不及设置会
    # 变成 XPASS(strict) 失败。
    await asyncio.to_thread(finished.wait, 1.2)
    assert not finished.is_set()
    assert result.finished_reason == "timeout"


@pytest.mark.parametrize("engine", ["skeleton", "legacy"])
@pytest.mark.parametrize(
    ("timeout_s", "approval_delay", "should_execute"),
    [(0.2, 0.5, False), (1.0, 0.0, True), (None, 0.5, True)],
)
async def test_attempt_deadline_after_permission(
    tmp_path, engine, timeout_s, approval_delay, should_execute,
):
    from agentnodes.foundation.llm import LLMResponse, ToolCall
    from agentnodes.kernel.agent.tools.core import BaseTool
    from agentnodes.kernel.permission import (
        PermissionAction,
        PermissionGateway,
        PermissionResponse,
    )
    from agentnodes.kernel.permission.schema import PermissionRule

    calls = []
    requested = asyncio.Event()

    class ApprovalTool(BaseTool):
        name = "approval_probe"
        description = "bounded approval timeout probe"
        parameters = {"type": "object", "properties": {}}
        effects = frozenset({"fs"})

        def execute(self, **kwargs):
            calls.append("started")
            return "ok"

    gateway = PermissionGateway(
        rules_path=tmp_path / "permission.yaml",
        on_request=lambda *args: requested.set(),
    )
    gateway.evaluator.add_rule(PermissionRule(
        permission="approval_probe", pattern="*", action=PermissionAction.ASK,
    ))
    loop = _make_loop(timeout_s=timeout_s, max_iterations=1, stream_mode=False)
    loop._permission_evaluator = gateway.evaluator
    loop._permission_gateway = gateway
    loop.registry.register(ApprovalTool())
    events = []
    loop._on_event = lambda kind, data: events.append((kind, data))

    async def tool_request(messages, **kwargs):
        return LLMResponse(
            content="",
            tool_calls=[ToolCall(id="probe", name="approval_probe", arguments={})],
            finish_reason="tool_calls",
        )

    loop.client.achat = tool_request
    running = asyncio.create_task(loop.arun("deadline probe", engine=engine))
    await asyncio.wait_for(requested.wait(), 5)
    await asyncio.sleep(approval_delay)
    gateway.respond("probe", PermissionResponse(action=PermissionAction.ALLOW))
    result = await asyncio.wait_for(running, 5)
    if should_execute:
        assert calls == ["started"]
        assert result.finished_reason != "timeout"
        assert not result.error
    else:
        assert not calls
        assert result.finished_reason == "timeout"
        assert result.error and "attempt timed out" in result.error
    assert sum(kind == "loop_end" for kind, _ in events) == 1
    assert sum(kind == "iter_end" for kind, _ in events) == 1


# ── 缺口①-2：引擎挂钩（默认引擎每轮开头检查）─────────────────────────


class TestEngineIterationStartHook:
    async def test_iteration_start_triggers_timeout_check(self):
        """默认引擎的每轮开头动作会调检查点（skeleton 路径覆盖）。"""
        from agentnodes.kernel.agent.loop.engine_adapters import LoopIterationStart

        class _Loop:
            _attempt_timeout_s = 0.2
            _attempt_deadline = None
            _started_at = 0.0
            _wrap_up_nudge = False

            def _check_attempt_timeout(self, iteration, result=None):
                raise AttemptTimeoutError("attempt timed out after 0.2s")

        with pytest.raises(AttemptTimeoutError):
            LoopIterationStart(_Loop())(None, 1)

    async def test_agent_loop_exposes_check_point(self):
        """AgentLoop 提供检查点（供引擎 adapter 调用）。"""
        loop = _make_loop(timeout_s=1.0)
        assert callable(loop._check_attempt_timeout)


# ── 缺口①-3：控制面收尾（明确失败终态 + 事件 + 资源释放）────────────


class _TimeoutEngine:
    """模拟"执行器在超时后中断"——抛 AttemptTimeoutError。"""

    def __init__(self) -> None:
        self.calls = 0

    def run(self, state, **kw):  # noqa: ANN003 — GraphEngine 节点契约
        self.calls += 1
        raise AttemptTimeoutError("attempt timed out after 0.2s")


class TestGatewayTimeoutSettlement:
    async def test_sync_client_worker_holds_gateway_lease_until_it_returns(self):
        from agentnodes.foundation.llm import LLMResponse
        from agentnodes.kernel.gateway.entry_adapters import api_chat_request
        from agentnodes.kernel.runtimes.nodes.agent_loop_node import (
            make_agent_loop_node,
            wrap_async_capable_client,
        )

        started = threading.Event()
        release = threading.Event()

        class SyncClient:
            def chat(self, messages, **kwargs):
                started.set()
                if not release.wait(5):
                    raise TimeoutError("probe release was not signalled")
                return LLMResponse(content="late", tool_calls=[], finish_reason="stop")

        loop = _make_loop(timeout_s=0.2, max_iterations=1, stream_mode=False)
        loop.client = wrap_async_capable_client(SyncClient())
        node = make_agent_loop_node(loop)

        class Engine:
            def run(self, state):
                return node(state)

        gw = Gateway(client=object(), sink=lambda kind, data: None)
        gw.controller._engine_factory = Engine
        gw.entry.submit(api_chat_request("s1", "hi"))
        running = asyncio.create_task(asyncio.to_thread(gw.run_once))
        try:
            assert await asyncio.to_thread(started.wait, 2), "sync client did not start"
            await asyncio.sleep(0.5)
            assert not running.done()
            assert gw.execution_lease.holding("s1")
            assert gw.fence.active_run("s1") is not None
        finally:
            release.set()
            results = await asyncio.wait_for(running, 5)

        assert len(results) == 1 and not results[0].ok
        assert "attempt timed out" in results[0].error
        assert gw.fence.active_run("s1") is None
        assert not gw.execution_lease.holding("s1")

    async def test_blocking_tool_holds_lease_until_it_returns(self):
        from agentnodes.foundation.llm import LLMResponse, ToolCall
        from agentnodes.kernel.agent.tools.core import BaseTool
        from agentnodes.kernel.gateway.entry_adapters import api_chat_request
        from agentnodes.kernel.runtimes.nodes.agent_loop_node import make_agent_loop_node

        started = threading.Event()
        release = threading.Event()
        events = []

        class BlockingTool(BaseTool):
            name = "blocking_probe"
            description = "bounded lease probe"
            parameters = {"type": "object", "properties": {}}

            def execute(self, **kwargs):
                started.set()
                if not release.wait(5):
                    raise TimeoutError("probe release was not signalled")
                return "ok"

        loop = _make_loop(timeout_s=0.2, max_iterations=1, stream_mode=False)
        loop.registry.register(BlockingTool())

        async def tool_request(messages, **kwargs):
            return LLMResponse(
                content="",
                tool_calls=[ToolCall(id="probe", name="blocking_probe", arguments={})],
                finish_reason="tool_calls",
            )

        loop.client.achat = tool_request
        node = make_agent_loop_node(loop)

        class Engine:
            def run(self, state):
                return node(state)

        gw = Gateway(client=object(), sink=lambda kind, data: events.append((kind, data)))
        gw.controller._engine_factory = Engine
        gw.entry.submit(api_chat_request("s1", "hi", attempt_id="a1"))
        running = asyncio.create_task(asyncio.to_thread(gw.run_once))
        try:
            assert await asyncio.to_thread(started.wait, 2), "tool did not start"
            await asyncio.sleep(0.5)
            assert not running.done()
            assert gw.execution_lease.holding("s1")
            assert gw.fence.active_run("s1") is not None
        finally:
            release.set()
            results = await asyncio.wait_for(running, 5)

        assert len(results) == 1 and not results[0].ok
        assert "attempt timed out" in results[0].error
        assert sum(kind == "run.failed" for kind, _ in events) == 1
        assert sum(kind == "run.settled" for kind, _ in events) == 1
        assert gw.fence.active_run("s1") is None
        assert not gw.execution_lease.holding("s1")

    async def test_real_agent_timeout_yields_failed_events_and_release(self):
        from agentnodes.kernel.gateway.entry_adapters import api_chat_request
        from agentnodes.kernel.runtimes.nodes.agent_loop_node import make_agent_loop_node

        events = []
        started = threading.Event()
        cancelled = threading.Event()
        loop = _make_loop(timeout_s=0.2, max_iterations=1, stream_mode=False)

        async def slow_chat(messages, **kwargs):
            started.set()
            try:
                await asyncio.sleep(1.2)
            except asyncio.CancelledError:
                cancelled.set()
                raise

        loop.client.achat = slow_chat
        node = make_agent_loop_node(loop)

        class Engine:
            def run(self, state):
                return node(state)

        gw = Gateway(client=object(), sink=lambda kind, data: events.append((kind, data)))
        gw.controller._engine_factory = Engine
        gw.entry.submit(api_chat_request("s1", "hi", attempt_id="a1"))
        results = gw.run_once()

        assert started.is_set()
        assert cancelled.is_set()
        assert len(results) == 1 and not results[0].ok
        assert "attempt timed out" in results[0].error
        failed = [data for kind, data in events if kind == "run.failed"]
        settled = [data for kind, data in events if kind == "run.settled"]
        assert len(failed) == 1 and failed[0]["attempt_id"] == "a1"
        assert len(settled) == 1 and settled[0]["ok"] is False
        assert gw.fence.active_run("s1") is None
        assert not gw.execution_lease.holding("s1")

    async def test_timeout_yields_failed_events_and_release(self):
        """超时 → run.failed + run.settled{ok:False} + fence 释放。"""
        events: list[tuple[str, dict]] = []
        engine = _TimeoutEngine()
        gw = Gateway(
            client=object(),
            sink=lambda k, d: events.append((k, d)),
        )
        gw.controller._engine_factory = lambda: engine  # type: ignore[attr-assign]

        from agentnodes.kernel.gateway.entry_adapters import api_chat_request

        gw.entry.submit(api_chat_request("s1", "hi"))
        results = gw.run_once()

        kinds = [k for k, _ in events]
        assert "run.failed" in kinds, f"超时必须发 run.failed（实际 {kinds}）"
        assert "run.settled" in kinds
        failed = [d for k, d in events if k == "run.failed"][-1]
        assert "timed out" in failed["error"]
        settled = [d for k, d in events if k == "run.settled"][-1]
        assert settled["ok"] is False
        assert results and results[0].ok is False
        assert "timed out" in (results[0].error or "")
        # 资源释放：栅栏不再持有活跃 run（后续 turn 可进）
        assert gw.fence.active_run("s1") is None

    async def test_no_orphan_execution_after_timeout(self):
        """超时后不再执行后续轮次（无孤儿执行）。"""
        engine = _TimeoutEngine()
        gw = Gateway(client=object(), sink=lambda k, d: None)
        gw.controller._engine_factory = lambda: engine  # type: ignore[attr-assign]

        from agentnodes.kernel.gateway.entry_adapters import api_chat_request

        gw.entry.submit(api_chat_request("s1", "hi"))
        gw.run_once()
        calls_after = engine.calls
        time.sleep(0.8)
        assert engine.calls == calls_after, "超时后不得继续执行"


# ── 缺口③：历史读取严格失败 ─────────────────────────────────────────


class _FakeServiceWithBrokenHistory:
    """store.get_messages 抛错（模拟历史读失败）。"""

    class store:  # noqa: N801 — 替身形状
        @staticmethod
        def get_messages(session_id, limit=100):
            raise RuntimeError("event_log replay failed")

    @staticmethod
    def _convert_messages_to_history(stored, keep_all_compactions=False):
        return []


class TestHistoryStrictFailure:
    async def test_history_failure_aborts_submission(self, monkeypatch):
        """历史读取失败 → 中止提交（无 run 事件）+ 明确错误。"""
        from fastapi import HTTPException

        from agentnodes.interfaces.api.routers import chat as chat_mod

        events: list[tuple[str, dict]] = []
        monkeypatch.setattr(
            chat_mod, "_get_session_service",
            lambda: _FakeServiceWithBrokenHistory(),
        )
        monkeypatch.setattr(
            chat_mod, "_get_gateway",
            lambda: Gateway(
                client=object(),
                sink=lambda k, d: events.append((k, d)),
            ),
        )

        with pytest.raises(HTTPException) as exc:
            await chat_mod._send_via_gateway("s1", "hello")

        detail = exc.value.detail
        assert isinstance(detail, dict) and detail["error"] == "history_unavailable"
        assert detail["session_id"] == "s1"
        assert exc.value.status_code == 500
        assert not events, "中止提交不得产生任何 run 事件"


# ── 批 4a（路线步骤 4 · 契约 §10）：实际状态事件面 ────────────────────
# 声明 ≠ 事实：事件按每次调用的实际状态记录（stopped / still_running /
# returned / unknown），不由注册表声明推断；未知显式为 unknown。


def _loop_end_outcomes(events) -> list:
    for kind, data in events:
        if kind == "loop_end" and data.get("reason") == "attempt_timeout":
            return list(data.get("timeout_tool_outcomes") or [])
    return []


class TestTimeoutToolOutcomes:
    """验收（契约 §10.6）：真实 loop 分别产出四种实际状态。"""

    async def test_stopped_cancellable_llm(self):
        """原生异步 LLM：取消已交付并等待完成 → stopped。"""
        from agentnodes.foundation.llm import LLMResponse

        loop = _make_loop(timeout_s=0.2, max_iterations=1, stream_mode=False)
        events = []
        loop._on_event = lambda kind, data: events.append((kind, data))

        async def slow_chat(messages, **kwargs):
            await asyncio.sleep(1.2)
            return LLMResponse(content="late", tool_calls=[], finish_reason="stop")

        loop.client.achat = slow_chat
        await loop.arun("deadline probe")

        outcomes = _loop_end_outcomes(events)
        assert outcomes == [{
            "kind": "llm",
            "declared": "cancellable",
            "state": "stopped",
        }] or (len(outcomes) == 1 and outcomes[0]["kind"] == "llm"
               and outcomes[0]["declared"] == "cancellable"
               and outcomes[0]["state"] == "stopped")
        assert outcomes[0]["name"]

    async def test_still_running_abandonable_bridge(self):
        """sync-only 桥：弃等、工作线程机制已知继续 → still_running。"""
        from agentnodes.foundation.llm import LLMResponse
        from agentnodes.kernel.runtimes.nodes.agent_loop_node import (
            wrap_async_capable_client,
        )

        class SyncClient:
            def chat(self, messages, **kwargs):
                time.sleep(0.8)
                return LLMResponse(content="late", tool_calls=[], finish_reason="stop")

        loop = _make_loop(timeout_s=0.2, max_iterations=1, stream_mode=False)
        events = []
        loop._on_event = lambda kind, data: events.append((kind, data))
        loop.client = wrap_async_capable_client(SyncClient())
        await loop.arun("deadline probe")

        outcomes = _loop_end_outcomes(events)
        assert len(outcomes) == 1
        assert outcomes[0]["kind"] == "llm"
        assert outcomes[0]["declared"] == "abandonable"
        assert outcomes[0]["state"] == "still_running"

    async def test_returned_late_tool(self):
        """同步工具阻塞到自然返回后才被发现超时 → returned（结果拒收），
        **不得记"仍在运行"**。"""
        from agentnodes.foundation.llm import LLMResponse, ToolCall
        from agentnodes.kernel.agent.tools.core import BaseTool

        class SlowTool(BaseTool):
            name = "slow_probe"
            description = "late-return probe"
            parameters = {"type": "object", "properties": {}}
            effects = frozenset({"fs"})

            def execute(self, **kwargs):
                time.sleep(0.8)
                return "late-ok"

        loop = _make_loop(timeout_s=0.2, max_iterations=2, stream_mode=False)
        events = []
        loop._on_event = lambda kind, data: events.append((kind, data))
        calls = {"n": 0}

        async def tool_then_stop(messages, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                return LLMResponse(
                    content="",
                    tool_calls=[ToolCall(id="t1", name="slow_probe", arguments={})],
                    finish_reason="tool_calls",
                )
            return LLMResponse(content="done", tool_calls=[], finish_reason="stop")

        loop.client.achat = tool_then_stop
        loop.registry.register(SlowTool())
        await loop.arun("deadline probe")

        outcomes = _loop_end_outcomes(events)
        tool_entries = [o for o in outcomes if o["kind"] == "tool"]
        assert tool_entries == [{
            "kind": "tool",
            "name": "slow_probe",
            "declared": "unstoppable",
            "state": "returned",
        }]

    async def test_unknown_state_is_first_class(self):
        """unknown 是一等实际状态：无观察通道时显式记 unknown，
        终态事件原样透出（不由声明推断）。"""
        loop = _make_loop(timeout_s=0.2)
        events = []
        loop._on_event = lambda kind, data: events.append((kind, data))
        loop._begin_attempt_deadline()
        time.sleep(1.2)
        loop._timeout_tool_outcomes = [{
            "kind": "tool", "name": "opaque_probe",
            "declared": "isolatable", "state": "unknown",
        }]
        with pytest.raises(AttemptTimeoutError):
            loop._check_attempt_timeout(1)

        outcomes = _loop_end_outcomes(events)
        assert outcomes == [{
            "kind": "tool", "name": "opaque_probe",
            "declared": "isolatable", "state": "unknown",
        }]

    async def test_declared_capability_defaults_and_registry_surface(self):
        """未声明 → unstoppable（诚实默认）；非法声明值 → unstoppable；
        registry 聚合面按名读取。"""
        from agentnodes.kernel.agent.tools.core import BaseTool

        class PlainTool(BaseTool):
            name = "plain_probe"
            description = "no capability declared"
            parameters = {"type": "object", "properties": {}}

            def execute(self, **kwargs):
                return "ok"

        class LiarTool(BaseTool):
            name = "liar_probe"
            description = "declares an unknown level"
            parameters = {"type": "object", "properties": {}}
            timeout_capability = "wall_clock_guaranteed"  # 非法值

            def execute(self, **kwargs):
                return "ok"

        registry = ToolRegistry()
        registry.register(PlainTool())
        registry.register(LiarTool())
        assert registry.timeout_capability("plain_probe") == "unstoppable"
        assert registry.timeout_capability("liar_probe") == "unstoppable"
        assert registry.timeout_capability("never_registered") == "unstoppable"
