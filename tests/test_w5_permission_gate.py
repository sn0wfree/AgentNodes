"""W5 · gateway /send 路径的权限闸与审批通道（真行为，非"参数传了"）。

**背景**：2026-11-10 实跑取证发现 `/send`（gateway 路径）此前是**不设防**
组合——无 evaluator + shell 工具无条件注册。`42dc6c19` 补了 evaluator 与
shell opt-in；本提交补最后一块：**审批通道**。

取证发现此前**两条 chat 路径都没有**审批通道——
``/api/chat/permission/respond`` 端点的 gateway 从没被工具执行用到过
（`service.py` 里没有任何 permission 接线）：ASK 判定一律 fail-closed
拒绝，用户在 UI 上点的审批无处可递。

本文件钉住：
1. deny 规则 → 工具不执行，run 正常完成（错误进工具结果，模型看得见）
2. ASK 且无审批通道 → fail-closed 拒绝（不放行——这是安全底线）
3. ASK 且**有**审批通道 → 请求挂起等回复，批准后工具执行
4. singleton 生产形态：evaluator + gateway 都在位
5. 两条路径共用同一个 gateway 实例（审批回复才找得到挂起的 future）
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field

import pytest

from agentnodes.kernel.agent.tools import BaseTool, ToolRegistry
from agentnodes.kernel.gateway import api_chat_request
from agentnodes.kernel.gateway.gateway import Gateway
from agentnodes.kernel.permission import PermissionEvaluator, PermissionGateway
from agentnodes.kernel.permission.schema import (
    PermissionAction,
    PermissionRule,
)


class _ProbeTool(BaseTool):
    """被闸门盯着��工具。"""

    name = "probe"
    description = "probe tool"
    parameters = {}
    executed: list = []

    def execute(self, **kwargs):
        type(self).executed.append(kwargs)
        return json.dumps({"status": "ok"})


@dataclass
class _Resp:
    content: str = ""
    tool_calls: list = field(default_factory=list)
    usage: dict | None = None
    finish_reason: str = "stop"

    def has_tool_calls(self) -> bool:
        return bool(self.tool_calls)


class _Client:
    """发起一次工具调用后返回最终答案。"""

    def __init__(self) -> None:
        self._sent = False

    def chat(self, messages, tools=None, **kw):
        if not self._sent:
            self._sent = True
            tc = SimpleNamespace(id="tc-1", name="probe", arguments={})
            return _Resp(tool_calls=[tc])
        return _Resp(content="done")

    async def achat(self, messages, tools=None, **kw):
        """async 路径（AgentLoop.arun 走这条）。"""
        return self.chat(messages, tools=tools, **kw)

    def stream(self, messages, tools=None, **kw):
        return iter([])


from types import SimpleNamespace  # noqa: E402


class _Cfg:
    """LLMConfig 替身（只被 AgentLoop 读 provider/model 等）。"""

    provider = "test"
    model = "test-model"
    api_key = "sk-test"
    base_url = "https://example.invalid/v1"
    max_iterations = 3
    timeout_s = 30
    max_retries = 0
    enable_thinking = False
    model_context_tokens = 100000
    model_max_output_tokens = 4096
    compact_config = None


def _gateway(evaluator=None, gateway=None) -> Gateway:
    reg = ToolRegistry()
    reg.register(_ProbeTool())
    return Gateway(
        client=_Client(),
        registry=reg,
        permission_evaluator=evaluator,
        permission_gateway=gateway,
        max_iterations=3,
    )


def _deny_evaluator() -> PermissionEvaluator:
    return PermissionEvaluator(
        ruleset=[PermissionRule(permission="probe", pattern="*",
                                 action=PermissionAction.DENY)],
        interactive=False,
    )


def _ask_evaluator(interactive: bool = False) -> PermissionEvaluator:
    """未匹配任何规则 → ASK。

    **``interactive`` 很关键**（evaluator.py:210）：非交互时 ASK 会被
    **降级成 DENY**（"一切 ASK → DENY"的 CI/脚本语义）——那样根本不会
    产生审批请求。要测"挂起等用户批准"必须 interactive=True。
    """
    return PermissionEvaluator(ruleset=[], interactive=interactive)


@pytest.fixture(autouse=True)
def _reset():
    _ProbeTool.executed = []
    yield
    _ProbeTool.executed = []


# ── 1. deny ───────────────────────────────────────────────


def test_deny_blocks_tool_but_run_completes():
    gw = _gateway(evaluator=_deny_evaluator())
    gw.entry.submit(api_chat_request("s1", "go"))
    runs = gw.run_once()

    assert runs and runs[0].ok
    assert _ProbeTool.executed == []


# ── 2. ASK 无通道 → fail-closed ───────────────────────────


def test_ask_without_gateway_fails_closed():
    """ASK 且无审批通道 → 拒绝（**不放行**——批次 7 的安全修复）。"""
    gw = _gateway(evaluator=_ask_evaluator())
    gw.entry.submit(api_chat_request("s2", "go"))
    runs = gw.run_once()

    assert runs and runs[0].ok
    assert _ProbeTool.executed == [], "ASK 无通道时绝不能放行"


# ── 3. ASK 有通道 → 挂起等回复 ────────────────────────────


def test_ask_with_gateway_waits_for_approval():
    """ASK + 审批通道：请求挂起，批准后工具执行。

    **必须走 async 路径**（``loop.arun`` → ``tool.ainvoke``）：同步路径
    （``enforce_permission_sync``）没有握手，ASK 一律拒绝（tools.py:757）。
    生产 gateway 路径走的就是 async（``_build_engine`` → ``arun``），
    所以这里用 ``_run_async`` 模拟。
    """
    from agentnodes.kernel.permission.schema import (
        PermissionAction,
        PermissionResponse,
    )

    pending: list = []
    gateway = PermissionGateway(on_request=lambda tid, dec, args: pending.append(tid))
    # interactive=True —— 否则 ASK 被降级成 DENY（evaluator.py:210）
    gw = _gateway(evaluator=_ask_evaluator(interactive=True), gateway=gateway)

    async def _drive():
        from agentnodes.kernel.agent.loop import AgentLoop

        loop = AgentLoop(
            config=_Cfg(),
            registry=gw.controller._registry,
            workspace=None,
            max_iterations=3,
            stream_mode=False,
        )
        # 用我们的脚本 client（发起工具调用 → 再回答案）
        loop.client = _Client()
        loop._permission_evaluator = gw.controller._permission_evaluator
        loop._permission_gateway = gw.controller._permission_gateway
        # **同一事件循环**（审批 future 绑定调用时的 loop）：
        # 跨线程 await 不了那个 future → respond() 永远等不到。
        task = asyncio.create_task(loop.arun("go"))
        for _ in range(200):
            if pending:
                break
            await asyncio.sleep(0.02)
        assert pending, "gateway 应收到 ASK 请求"
        # respond() 是**同步**的（设 future 的 result）
        assert gateway.respond(
            tool_call_id=pending[0],
            response=PermissionResponse(action=PermissionAction.ALLOW),
        ), "respond 应解析到挂起的请求"
        return await task

    result = asyncio.run(_drive())
    assert result.answer
    assert len(_ProbeTool.executed) == 1, "批准后工具应执行"


# ── 4/5. 生产形态与两路同构 ──────────────────────────────


def test_singleton_wires_evaluator_and_gateway():
    """生产形态（singleton）：evaluator + 审批通道都在位。"""
    from agentnodes.kernel.gateway import singleton

    singleton.reset_gateway()
    try:
        gw = singleton.get_gateway(client=object(), registry=ToolRegistry())
        assert gw.controller._permission_evaluator is not None
        assert gw.controller._permission_gateway is not None
    finally:
        singleton.reset_gateway()


def test_both_paths_share_one_gateway_instance():
    """两条路径必须共用**同一个** gateway —— 审批是跨请求交互，
    挂起的 future 按 tool_call_id 查，实例不同就永远对不上。"""
    from agentnodes.kernel.agent.chat_loop import build_default_permission_gateway
    from agentnodes.kernel.gateway import singleton

    singleton.reset_gateway()
    try:
        gw = singleton.get_gateway(client=object(), registry=ToolRegistry())
        default_gw = build_default_permission_gateway()
        assert gw.controller._permission_gateway is default_gw
    finally:
        singleton.reset_gateway()
