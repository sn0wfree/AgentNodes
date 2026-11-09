"""B5 前置接线：todos 在 chat 生产路径可见（R-1 + B-a + B-c + W1-p）。

契约：docs/archive/2026-10/stage-c1-b5-wiring-20261029.md

背景（B5 调查实测）：生产 chat 的 controller._context_hub 恒为 None——
B1-b 的上下文注入只在旧 /send_async 路径活着；react 路径的 todowrite
落共享 "default" 桶，按 session 永远读不到。
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from agentnodes.kernel.agent.builtin_tools.todo_tools import (
    TodoStore,
    TodoWriteTool,
)
from agentnodes.kernel.agent.context_providers import TodoContextProvider
from agentnodes.kernel.context.hub import ContextHub
from agentnodes.kernel.context.protocol import ContextQuery
from agentnodes.kernel.gateway.gateway import Gateway
from agentnodes.kernel.gateway.singleton import reset_gateway


@pytest.fixture(autouse=True)
def _clean_todos():
    TodoStore.clear("default")
    TodoStore.clear("sess-1")
    TodoStore.clear("sess-kw")
    yield
    TodoStore.clear("default")
    TodoStore.clear("sess-1")
    TodoStore.clear("sess-kw")


class _Ctx:
    """Minimal ToolContext stand-in (session attribution only)."""

    def __init__(self, session_id: str | None = None):
        self.session_id = session_id


# ── B-a: session attribution ─────────────────────────────────


def _write_via_tool(ctx, kwargs=None) -> str:
    tool = TodoWriteTool()
    return tool.execute(
        ctx,
        todos=[{"id": "1", "content": "t", "status": "pending"}],
        **(kwargs or {}),
    )


def test_ctx_session_id_used_when_no_kwarg():
    out = json.loads(_write_via_tool(_Ctx("sess-1")))
    assert out["status"] == "ok"
    assert TodoStore.get("sess-1"), "todo must land in the ctx session"
    assert TodoStore.get("default") == []


def test_explicit_kwarg_still_wins():
    """AgentLoop 路径经 _build_tool_kwargs 注入 kwargs.session_id——不能回归。"""
    json.loads(_write_via_tool(_Ctx("sess-1"), {"session_id": "sess-kw"}))
    assert TodoStore.get("sess-kw")
    assert TodoStore.get("sess-1") == []


def test_bare_caller_falls_back_to_default():
    """脚本/REPL 直调（无 ctx.session_id）维持现状：共享 default 桶。"""
    json.loads(_write_via_tool(_Ctx(None)))
    assert TodoStore.get("default")


# ── R-1: Gateway accepts and forwards the hub ────────────────


def _hub_with_todos() -> ContextHub:
    hub = ContextHub()
    hub.register(TodoContextProvider())
    return hub


def test_gateway_forwards_context_hub():
    hub = _hub_with_todos()
    gw = Gateway(
        client=MagicMock(),
        registry=MagicMock(),
        context_hub=hub,
    )
    assert gw.controller._context_hub is hub


def test_gateway_without_hub_keeps_none():
    gw = Gateway(client=MagicMock(), registry=MagicMock())
    assert gw.controller._context_hub is None


def test_singleton_assembles_hub_with_todos_provider():
    reset_gateway()
    try:
        from agentnodes.kernel.gateway.singleton import get_gateway

        gw = get_gateway(client=MagicMock(), registry=MagicMock())
        hub = gw.controller._context_hub
        assert hub is not None, "singleton must wire the hub (option A)"
        names = [p.name for p in hub._providers]
        assert "todos" in names
    finally:
        reset_gateway()


# ── B-c + T7: end-to-end through the react node ──────────────


class _ToolCallClient:
    """First call returns a todowrite tool_call, then a final answer."""

    def __init__(self) -> None:
        self.calls = 0

    class _TC:
        id = "c1"
        name = "todowrite"
        arguments = {"todos": [{"id": "1", "content": "wiring", "status": "pending"}]}

    class _Resp:
        def __init__(self, tool_calls):
            self.content = ""
            self.usage = {}
            self.tool_calls = tool_calls
            # **批次 V3-b**：loop 的 tool_calls 分支读它（缺了会以
            # llm_call step failed 收场，工具根本不会执行）。
            self.finish_reason = "stop"

        def has_tool_calls(self):
            return bool(self.tool_calls)

    def chat(self, messages, tools=None, **kw):
        self.calls += 1
        if self.calls == 1:
            return self._Resp([self._TC()])
        return self._Resp([])


class _RealRegistry:
    """Real ToolRegistry carrying only todowrite — B-c must exercise the
    real execute(name, params, ctx=ctx) path, duck-typed fakes can't.

    **批次 V3-b**：默认图换成 AgentLoop 后，registry 契约从 react 的
    ``{get_definitions, execute}`` 扩到 loop 的
    ``{get, all_tools, check_guards}``——这里如实补齐委托（生产注册表
    本来就是 ToolRegistry，方法齐全）。"""

    def __init__(self) -> None:
        from agentnodes.kernel.agent.tools import ToolRegistry

        self._reg = ToolRegistry()
        self._reg.register(TodoWriteTool())

    def get(self, name):
        return self._reg.get(name)

    def get_definitions(self):
        return self._reg.get_definitions()

    def all_tools(self):
        return self._reg.all_tools()

    def check_guards(self, name, arguments):
        return self._reg.check_guards(name, arguments)

    def execute(self, name, params, ctx=None):
        return self._reg.execute(name, params, ctx=ctx)


def _controller(tmp_path: Path, hub):
    from agentnodes.kernel.gateway.session_controller import SessionController

    class _FakeEP:
        def iter_mailboxes(self):
            return []

        def session_id_for(self, key):
            return key

    return SessionController(
        _FakeEP(), client=_ToolCallClient(), registry=_RealRegistry(),
        workspace=tmp_path, context_hub=hub,
    )


def _submit(controller, session_id: str):
    from agentnodes.kernel.orchestration import (
        EntryPoint,
        SourceKind,
        WorkKind,
        WorkRequest,
    )

    controller._ep = EntryPoint()
    # session_key 需 <domain>:<name>（contracts.py 校验）；物理会话 id 由
    # EntryPoint 惰性分配——B-c 的归属看它，不用测试指定的 key。
    controller._ep.submit(WorkRequest(
        kind=WorkKind.CHAT, source=SourceKind.API_CHAT,
        session_key=f"chat:{session_id}", payload={"text": "make a todo"},
    ))
    return controller.run_pending()


def _all_todo_buckets():
    from agentnodes.kernel.agent.builtin_tools.todo_tools import TodoStore

    return {k: v for k, v in TodoStore._todos.items() if v}


def test_react_todowrite_lands_in_session_bucket(tmp_path):
    """端到端：hub 已接线 → ctx 带 session_id → todo 落专属桶（非 default）。

    **批次 V3-b**：断言收紧到「物理会话 id 的专属桶里确实有这条 todo」
    ——此前只断言「default 不在桶里」，结果共享 TodoStore 里任何其他
    测试的残留桶都能让断言假绿（_clean_todos 只清 default/sess-1，
    'sess-kw' 泄漏过——现在 fixture 清干净 + 这里按物理 id 精确断言）。"""
    ctrl = _controller(tmp_path, _hub_with_todos())
    _submit(ctrl, "sess-e2e")
    buckets = _all_todo_buckets()
    assert buckets, "todo must be written"
    assert "default" not in buckets, f"must not leak into shared bucket: {buckets}"
    physical = ctrl._ep.session_id_for("chat:sess-e2e")
    assert buckets.get(physical), (
        f"todo must land in the physical session bucket {physical!r}: {buckets}"
    )


def test_todowrite_without_hub_still_lands_in_session_bucket(tmp_path):
    """hub 未接线（旧默认构造）→ todo 仍归属会话桶（不再落共享 default）。

    **批次 V3-b 行为变更（有意）**：默认图换成 AgentLoop 后，ToolContext
    由 ``loop._build_tool_kwargs`` 无条件构造（session_id 随 loop 构造参数
    进入 ctx）——react 时代的「无 hub/workspace 就无 ctx → 落 default」
    条件不复存在。归属会话是 B-c 想要的方向性结果（否则 todowrite 泄进
    共享桶）；本测试从「固化 react 的条件接线」改为「固化归属总是生效」。
    """
    from agentnodes.kernel.gateway.session_controller import SessionController

    class _FakeEP:
        def iter_mailboxes(self):
            return []

        def session_id_for(self, key):
            return key

    controller = SessionController(
        _FakeEP(), client=_ToolCallClient(), registry=_RealRegistry(),
        context_hub=None,
    )
    _submit(controller, "sess-legacy")
    buckets = _all_todo_buckets()
    assert buckets, "todo must be written"
    assert "default" not in buckets, (
        f"session attribution is unconditional now: {buckets}"
    )
    # 物理会话 id 由 EntryPoint 惰性分配（见 _submit 注释）——按它断言归属
    physical = controller._ep.session_id_for("chat:sess-legacy")
    assert buckets.get(physical), f"todo must land in the session bucket: {buckets}"


def test_hub_build_returns_todo_item_for_session():
    """W1-p：provider 产出 kind=todo 的单条 item（tokens 由中枢估算）。"""
    TodoStore.set("sess-1", [{"id": "1", "content": "x", "status": "pending"}])
    bundle = _hub_with_todos().build(
        ContextQuery(task_text="t", session_id="sess-1")
    )
    assert len(bundle.items) == 1
    item = bundle.items[0]
    assert item.kind == "todo"
    assert item.provider == "todos"
    assert item.tokens > 0, "hub estimates tokens (W0 contract)"
    assert item.meta == {"count": 1}
    assert "<current-todos>" in item.content, "shared formatter, no drift"


def test_provider_empty_for_other_session_or_blank():
    assert _hub_with_todos().build(
        ContextQuery(task_text="t", session_id="sess-other")
    ).items == []
    assert _hub_with_todos().build(ContextQuery(task_text="t")).items == []
