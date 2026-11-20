"""HTTP contract baselines for chat submission and queue controls."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi.testclient import TestClient
from fastapi import HTTPException

from agentnodes.interfaces.api.app import create_app
from agentnodes.interfaces.api.auth_tokens import create_token
from agentnodes.kernel.orchestration import QueueMode, Receipt, RejectReason
from agentnodes.kernel.session.models import Attempt, AttemptId, AttemptStatus, SessionId


@pytest.fixture
def client(tmp_path, monkeypatch):
    import agentnodes.interfaces.api.routers.web_session as web_session
    import agentnodes.kernel.session.db as session_db

    db_path = str(tmp_path / "chat.db")
    monkeypatch.setattr(web_session, "_get_db_path", lambda: db_path)
    monkeypatch.setattr(session_db, "_get_db_path", lambda: db_path)
    c = TestClient(create_app())
    c.headers.update({"Authorization": f"Bearer {create_token('admin')}"})
    return c


def _new_session(client: TestClient) -> str:
    response = client.post(
        "/api/chat/session",
        json={"user_id": "anonymous", "title": "chat contract"},
    )
    assert response.status_code == 200, response.text
    return response.json()["id"]


def _install_service(monkeypatch, service):
    import agentnodes.interfaces.api.routers.chat as chat_router

    monkeypatch.setattr(chat_router, "_get_session_service", lambda: service)
    return chat_router


def _fake_update_attempt_status(lookup):
    """C2 port 的 fake 实现：对内存 attempts 推进模型状态机（mark_*）。

    lookup(session_id, attempt_id) -> Attempt | None——与 kernel
    SessionService.update_attempt_status 同契约（缺失返回 None）。
    """
    def _update(session_id, attempt_id, status, *, summary=None, error=""):
        attempt = lookup(session_id, attempt_id)
        if attempt is None:
            return None
        if status == "running":
            attempt.mark_running()
        elif status == "completed":
            attempt.mark_completed(summary)
        elif status == "cancelled":
            attempt.mark_cancelled()
        elif status == "failed":
            attempt.mark_failed(error or "Gateway run failed")
        else:
            raise ValueError(f"unsupported Attempt status: {status}")
        return attempt
    return _update


def test_send_async_prechecks_history_only_once(client, monkeypatch):
    """重复 A 修复（路由级）：/send_async 先预检历史、创建 Attempt，再把
    预检结果传 send_turn——整个请求只读一次历史（不是入口+用例各一次）。"""
    session_id = _new_session(client)
    counts = {"load_history": 0}

    def _load_history(session_id, keep_all_compactions=False):
        counts["load_history"] += 1
        return []

    service = SimpleNamespace(
        store=SimpleNamespace(
            create_attempt=Mock(), delete_attempt=Mock(),
            get_attempt=Mock(return_value=SimpleNamespace(status="pending")),
        ),
        load_history=_load_history,
        update_attempt_status=lambda *a, **k: None,
        event_bus=SimpleNamespace(emit=Mock()),
    )
    chat_router = _install_service(monkeypatch, service)

    class _Engine:
        def run(self, state):
            return {"answer": "ok", "messages": state.get("messages", [])}

    from agentnodes.kernel.gateway.gateway import Gateway

    gateway = Gateway(client=object(), engine_factory=_Engine())
    monkeypatch.setattr(chat_router, "_get_gateway", lambda: gateway)

    response = client.post(
        "/api/chat/send_async",
        json={"session_id": session_id, "content": "question"},
    )
    assert response.status_code == 200, response.text
    assert counts["load_history"] == 1, "入口 + send_turn 不得各读一次历史"


@pytest.mark.asyncio
@pytest.mark.parametrize("second_session", ["s1", "s2"])
@pytest.mark.parametrize("engine_kind", ["custom", "llm", "agent", "agent_tool", "failed"])
async def test_gateway_chat_bridges_are_turn_scoped(monkeypatch, second_session, engine_kind):
    from agentnodes.interfaces.api.routers import chat as chat_router
    from agentnodes.foundation.llm.parser import StreamChunk, parse_chat_response
    from agentnodes.kernel.agent.tools.core import BaseTool, ToolRegistry
    from agentnodes.kernel.gateway.gateway import Gateway

    persisted = []
    pushed = []
    attempts = {
        ("s1", "a1"): Attempt(
            attempt_id=AttemptId("a1"), session_id=SessionId("s1"),
        ),
        (second_session, "a2"): Attempt(
            attempt_id=AttemptId("a2"), session_id=SessionId(second_session),
        ),
    }

    class Engine:
        def run(self, state):
            content = state["messages"][-1]["content"]
            if engine_kind == "failed" and content == "first":
                raise RuntimeError("first failed")
            gateway._combined_sink("text_delta", {
                "text": content,
                "event_correlation_id": state["event_correlation_id"],
            })
            return {"answer": content}

    class Client:
        parse_response = staticmethod(parse_chat_response)

        def stream(self, messages, **kwargs):
            yield StreamChunk(delta_content=messages[-1]["content"], finish_reason="stop")

        async def astream(self, messages, **kwargs):
            if engine_kind == "agent_tool" and not any(
                message.get("role") == "tool" for message in messages
            ):
                yield StreamChunk(
                    delta_tool_calls=[{
                        "index": 0, "id": "call", "type": "function",
                        "function": {"name": "echo", "arguments": "{}"},
                    }],
                    finish_reason="tool_calls",
                )
            else:
                content = next(m["content"] for m in reversed(messages) if m["role"] == "user")
                yield StreamChunk(delta_content=content, finish_reason="stop")

    class EchoTool(BaseTool):
        name = "echo"
        description = "echo"
        parameters = {}

        def execute(self, **kwargs):
            return "ok"

    registry = ToolRegistry() if engine_kind in ("agent", "agent_tool") else None
    if engine_kind == "agent_tool":
        registry.register(EchoTool())
    gateway = Gateway(
        client=Client(),
        engine_factory=Engine if engine_kind in ("custom", "failed") else None,
        registry=registry,
        stream=True,
        max_iterations=2,
    )
    service = SimpleNamespace(
        store=SimpleNamespace(
            get_messages=lambda *args, **kwargs: [],
            get_attempt=lambda sid, aid: attempts.get((sid, aid)),
            update_attempt=Mock(),
        ),
        load_history=lambda *args, **kwargs: [],
        update_attempt_status=_fake_update_attempt_status(
            lambda sid, aid: attempts.get((sid, aid)),
        ),
        event_bus=SimpleNamespace(
            emit=lambda sid, kind, data: persisted.append((sid, kind, data))
        ),
    )
    monkeypatch.setattr(chat_router, "_get_gateway", lambda: gateway)
    monkeypatch.setattr(chat_router, "_get_session_service", lambda: service)
    monkeypatch.setattr(chat_router, "_build_llm_config", lambda: None)
    monkeypatch.setattr(
        chat_router, "_push_sse", lambda kind, data, sid: pushed.append((sid, kind, data)),
    )

    await chat_router._send_via_gateway(
        "s1", "first", receipt_only=True, attempt_id="a1", assistant_message_id="m1",
        queue_mode=QueueMode.FOLLOWUP,
    )
    await chat_router._send_via_gateway(
        second_session, "second", receipt_only=True, attempt_id="a2",
        assistant_message_id="m2", queue_mode=QueueMode.FOLLOWUP,
    )
    gateway._combined_sink("text_delta", {"text": "unscoped"})
    gateway._combined_sink("run.answer", {"run_id": "unscoped", "content": "unscoped"})
    # 批1-S2：_send_via_gateway 会安装 1 个进程单例落库观察器（两请求共用
    # 同一 gateway 实例只装一次）→ 2 桥 + 1 观察器 = 3
    assert len(gateway._sinks) == 3
    runs = gateway.run_once()
    assert len(runs) == 2
    assert [run.ok for run in runs] == [engine_kind != "failed", True]
    answers = [(sid, data["content"]) for sid, kind, data in persisted
               if kind == "assistant_message"]
    expected_answers = [(second_session, "second")]
    if engine_kind != "failed":
        expected_answers.insert(0, ("s1", "first"))
    assert answers == expected_answers
    endings = [(sid, data["message_id"]) for sid, kind, data in pushed
               if kind == "agent_done"]
    assert endings == [("s1", "m1"), (second_session, "m2")]
    assert attempts[("s1", "a1")].status == (
        AttemptStatus.FAILED if engine_kind == "failed" else AttemptStatus.COMPLETED
    )
    assert attempts[(second_session, "a2")].status == AttemptStatus.COMPLETED
    # 桥在终态自注销；落库观察器随 gateway 实例存活 → 剩 1
    assert len(gateway._sinks) == 1
    if engine_kind == "failed":
        errors = [(sid, data["message_id"]) for sid, kind, data in pushed if kind == "error"]
        assert errors == [("s1", "m1")]
    if engine_kind != "failed":
        deltas = [(sid, data.get("text", data.get("delta")))
                  for sid, kind, data in pushed if kind == "text_delta"]
        assert deltas == [("s1", "first"), (second_session, "second")]
        assert all("event_correlation_id" not in data for _, kind, data in pushed
                   if kind == "text_delta")
    if engine_kind == "agent_tool":
        for event_kind in ("tool_call", "tool_result"):
            tool_events = [(sid, data["message_id"])
                           for sid, kind, data in pushed if kind == event_kind]
            assert tool_events == [("s1", "m1"), (second_session, "m2")]


@pytest.mark.asyncio
async def test_gateway_sync_returns_its_own_turn_when_other_sessions_are_queued(monkeypatch):
    from agentnodes.interfaces.api.routers import chat as chat_router
    from agentnodes.kernel.gateway import api_chat_request
    from agentnodes.kernel.gateway.gateway import Gateway

    class Engine:
        def run(self, state):
            return {"answer": state["messages"][-1]["content"]}

    gateway = Gateway(client=object(), engine_factory=Engine)
    gateway.entry.submit(api_chat_request("s1", "earlier", queue_mode=QueueMode.FOLLOWUP))
    gateway.entry.submit(api_chat_request("s2", "other", queue_mode=QueueMode.FOLLOWUP))
    persisted = []
    service = SimpleNamespace(
        store=SimpleNamespace(get_messages=lambda *args, **kwargs: []),
        load_history=lambda *args, **kwargs: [],
        update_attempt_status=lambda *args, **kwargs: None,
        event_bus=SimpleNamespace(
            emit=lambda sid, kind, data: persisted.append((sid, kind, data))
        ),
    )
    monkeypatch.setattr(chat_router, "_get_gateway", lambda: gateway)
    monkeypatch.setattr(chat_router, "_get_session_service", lambda: service)
    monkeypatch.setattr(chat_router, "_build_llm_config", lambda: None)
    monkeypatch.setattr(chat_router, "_push_sse", lambda *args: None)

    response = await chat_router._send_via_gateway(
        "s1", "mine", wait=True, queue_mode=QueueMode.FOLLOWUP,
    )
    assert response["reply"] == "mine"
    # 批1-S2（B2）：外部直提的 run（earlier/other，无 HTTP 桥）也由单例
    # 观察器恰一次落库——这正是"外部启动 run"场景的验收面。
    assert [data["content"] for sid, kind, data in persisted
            if sid == "s1" and kind == "assistant_message"] == ["earlier", "mine"]
    assert [data["content"] for sid, kind, data in persisted
            if sid == "s2" and kind == "assistant_message"] == ["other"]


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", ["queued", "early", "failed", "timeout"])
async def test_gateway_sync_waits_for_exact_turn_with_consumer(monkeypatch, scenario):
    import asyncio
    import threading

    from agentnodes.interfaces.api.routers import chat as chat_router
    from agentnodes.kernel.gateway import api_chat_request
    from agentnodes.kernel.gateway.gateway import Gateway

    started = threading.Event()
    release = threading.Event()
    settled = threading.Event()
    persisted = []

    class Engine:
        def run(self, state):
            content = state["messages"][-1]["content"]
            if (scenario == "queued" and content == "earlier") or scenario == "timeout":
                started.set()
                if not release.wait(5):
                    raise RuntimeError("test release timed out")
            if scenario == "failed":
                raise RuntimeError("own failure")
            return {"answer": content}

    gateway = Gateway(client=object(), engine_factory=Engine)
    service = SimpleNamespace(
        store=SimpleNamespace(get_messages=lambda *args, **kwargs: []),
        load_history=lambda *args, **kwargs: [],
        update_attempt_status=lambda *args, **kwargs: None,
        event_bus=SimpleNamespace(
            emit=lambda sid, kind, data: persisted.append((sid, kind, data))
        ),
    )
    monkeypatch.setattr(chat_router, "_get_gateway", lambda: gateway)
    monkeypatch.setattr(chat_router, "_get_session_service", lambda: service)
    monkeypatch.setattr(chat_router, "_build_llm_config", lambda: None)
    monkeypatch.setattr(chat_router, "_push_sse", lambda *args: None)
    unsubscribe = None
    if scenario == "early":
        unsubscribe = gateway.add_sink(
            lambda kind, data: settled.set() if kind == "run.settled" else None
        )
        submit = gateway.entry.submit

        def submit_after_settlement(*args, **kwargs):
            receipt = submit(*args, **kwargs)
            assert settled.wait(3)
            return receipt

        monkeypatch.setattr(gateway.entry, "submit", submit_after_settlement)
    if scenario == "timeout":
        monkeypatch.setattr(chat_router, "_chat_wait_timeout", lambda: 0.1)
    if scenario == "queued":
        gateway.entry.submit(
            api_chat_request("s1", "earlier", queue_mode=QueueMode.FOLLOWUP),
        )
    gateway.start_consumer(poll_interval=0.01)
    try:
        if scenario == "queued":
            assert await asyncio.to_thread(started.wait, 3)
        task = asyncio.create_task(chat_router._send_via_gateway(
            "s1", "mine", wait=True, queue_mode=QueueMode.FOLLOWUP,
        ))
        if scenario == "queued":
            mailbox = gateway.entry.mailbox_for("chat:s1")
            for _ in range(300):
                if mailbox.peek_depth():
                    break
                await asyncio.sleep(0.01)
            assert mailbox.peek_depth() == 1
            release.set()
        if scenario in ("failed", "timeout"):
            with pytest.raises(HTTPException) as exc_info:
                await task
            assert exc_info.value.status_code == (502 if scenario == "failed" else 504)
            assert not [item for item in persisted if item[1] == "assistant_message"]
        else:
            response = await task
            assert response["reply"] == "mine"
            # 批1-S2（B2）：queued 场景里先行的外部直提 run 也落库
            expected = ["earlier", "mine"] if scenario == "queued" else ["mine"]
            assert [data["content"] for _, kind, data in persisted
                    if kind == "assistant_message"] == expected
    finally:
        release.set()
        if unsubscribe is not None:
            unsubscribe()
        gateway.stop_consumer(grace=3)
    assert not gateway.consumer_running()
    # 批1-S2：落库观察器随 gateway 实例存活（桥已全部注销）
    assert len(gateway._sinks) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("wait", [False, True])
@pytest.mark.parametrize("engine_kind", ["custom", "agent", "promoted"])
@pytest.mark.parametrize("origin", ["chat", "external"])
async def test_gateway_chat_steer_follows_the_run_that_applies_it(
    monkeypatch, wait, engine_kind, origin,
):
    import asyncio
    import threading

    from agentnodes.interfaces.api.routers import chat as chat_router
    from agentnodes.foundation.llm.parser import StreamChunk, parse_chat_response
    from agentnodes.kernel.agent.tools.core import BaseTool, ToolRegistry
    from agentnodes.kernel.gateway import api_chat_request
    from agentnodes.kernel.gateway.gateway import Gateway

    started = threading.Event()
    release = threading.Event()
    queued = threading.Event()
    settled = threading.Event()
    pushed = []

    class Engine:
        def run(self, state):
            if engine_kind == "promoted" and state["messages"][-1]["content"] == "steer":
                return {"answer": "steer"}
            started.set()
            if not release.wait(5):
                raise RuntimeError("test release timed out")
            if engine_kind == "promoted":
                return {"answer": "first"}
            return {"answer": "|".join(
                [state["messages"][-1]["content"], *state["steer_inbox"].drain()]
            )}

    class Client:
        parse_response = staticmethod(parse_chat_response)

        async def astream(self, messages, **kwargs):
            if not any(message.get("role") == "tool" for message in messages):
                started.set()
                if not await asyncio.to_thread(release.wait, 5):
                    raise RuntimeError("test release timed out")
                yield StreamChunk(
                    delta_tool_calls=[{
                        "index": 0, "id": "call", "type": "function",
                        "function": {"name": "echo", "arguments": "{}"},
                    }],
                    finish_reason="tool_calls",
                )
            else:
                content = "|".join(m["content"] for m in messages if m["role"] == "user")
                yield StreamChunk(delta_content=content, finish_reason="stop")

    class EchoTool(BaseTool):
        name = "echo"
        description = "echo"
        parameters = {}

        def execute(self, **kwargs):
            return "ok"

    registry = ToolRegistry() if engine_kind == "agent" else None
    if registry is not None:
        registry.register(EchoTool())
    gateway = Gateway(
        client=Client(), engine_factory=Engine if engine_kind != "agent" else None,
        registry=registry, stream=True, max_iterations=2,
    )
    unsubscribe = gateway.add_sink(
        lambda kind, data: (
            queued.set() if kind == "steer.queued"
            else settled.set() if kind == "run.settled"
            else None
        )
    )
    service = SimpleNamespace(
        store=SimpleNamespace(get_messages=lambda *args, **kwargs: []),
        load_history=lambda *args, **kwargs: [],
        update_attempt_status=lambda *args, **kwargs: None,
        event_bus=SimpleNamespace(emit=Mock()),
    )
    monkeypatch.setattr(chat_router, "_get_gateway", lambda: gateway)
    monkeypatch.setattr(chat_router, "_get_session_service", lambda: service)
    monkeypatch.setattr(chat_router, "_build_llm_config", lambda: None)
    monkeypatch.setattr(chat_router, "_chat_wait_timeout", lambda: 1.0)
    monkeypatch.setattr(
        chat_router, "_push_sse", lambda kind, data, sid: pushed.append((sid, kind, data)),
    )
    gateway.start_consumer(poll_interval=0.01)
    try:
        if origin == "chat":
            await chat_router._send_via_gateway(
                "s1", "first", receipt_only=True, queue_mode=QueueMode.FOLLOWUP,
                assistant_message_id="m-active",
            )
        else:
            gateway.entry.submit(
                api_chat_request("s1", "first", queue_mode=QueueMode.FOLLOWUP),
            )
        assert await asyncio.to_thread(started.wait, 3)
        task = asyncio.create_task(chat_router._send_via_gateway("s1", "steer", wait=wait))
        assert await asyncio.to_thread(queued.wait, 3)
        release.set()
        response = await asyncio.wait_for(task, 3)
        assert await asyncio.to_thread(settled.wait, 3)
        if wait:
            assert response["reply"] == ("steer" if engine_kind == "promoted" else "first|steer")
        else:
            assert response["accepted"] is True
        expected_done = 2 if origin == "chat" else 1
        for _ in range(300):
            if len([item for item in pushed if item[1] == "agent_done"]) == expected_done:
                break
            await asyncio.sleep(0.01)
        assert len([item for item in pushed if item[1] == "agent_done"]) == expected_done
        # 批1-S2（A1+B2）：多等待方各自通知（expected_done），但落库单点——
        # promoted 场景两个独立 run 各一次；absorbed 场景同一 run 恰一次
        # （此前被 A/B 两桥分别落库两次）。
        expected_persists = 2 if engine_kind == "promoted" else 1
        persist_calls = [c for c in service.event_bus.emit.call_args_list
                         if c.args[1] == "assistant_message"]
        assert len(persist_calls) == expected_persists
        assert all(c.args[0] == "s1" for c in persist_calls)
        if engine_kind != "promoted":
            assert "first|steer" in persist_calls[0].args[2]["content"]
        if origin == "chat" and engine_kind != "promoted" and not wait:
            assert all(data["message_id"] == "m-active"
                       for _, kind, data in pushed if kind == "agent_done")
    finally:
        release.set()
        unsubscribe()
        gateway.stop_consumer(grace=3)
    assert not gateway.consumer_running()
    # 批1-S2：落库观察器随 gateway 实例存活
    assert len(gateway._sinks) == 1


@pytest.mark.asyncio
async def test_gateway_summarized_request_fails_its_own_attempt_and_closes_bridge(monkeypatch):
    from agentnodes.interfaces.api.routers import chat as chat_router
    from agentnodes.kernel.gateway.gateway import Gateway

    class Engine:
        def run(self, state):
            return {"answer": state["messages"][-1]["content"]}

    gateway = Gateway(client=object(), engine_factory=Engine)
    gateway.entry._cap = 2
    attempts = {
        f"a{i}": Attempt(attempt_id=AttemptId(f"a{i}"), session_id=SessionId("s1"))
        for i in range(3)
    }
    pushed = []
    service = SimpleNamespace(
        store=SimpleNamespace(
            get_messages=lambda *args, **kwargs: [],
            get_attempt=lambda sid, aid: attempts.get(aid) if sid == "s1" else None,
            update_attempt=Mock(),
        ),
        load_history=lambda *args, **kwargs: [],
        update_attempt_status=_fake_update_attempt_status(
            lambda sid, aid: attempts.get(aid) if sid == "s1" else None,
        ),
        event_bus=SimpleNamespace(emit=Mock()),
    )
    monkeypatch.setattr(chat_router, "_get_gateway", lambda: gateway)
    monkeypatch.setattr(chat_router, "_get_session_service", lambda: service)
    monkeypatch.setattr(chat_router, "_build_llm_config", lambda: None)
    monkeypatch.setattr(
        chat_router, "_push_sse", lambda kind, data, sid: pushed.append((sid, kind, data)),
    )
    for i in range(3):
        await chat_router._send_via_gateway(
            "s1", f"message-{i}", receipt_only=True, queue_mode=QueueMode.FOLLOWUP,
            attempt_id=f"a{i}", assistant_message_id=f"m{i}",
        )

    assert attempts["a0"].status == AttemptStatus.FAILED
    assert [(sid, data["message_id"]) for sid, kind, data in pushed if kind == "error"] == [
        ("s1", "m0"),
    ]
    runs = gateway.run_once()
    assert [run.answer for run in runs] == ["message-1", "message-2"]
    assert attempts["a1"].status == attempts["a2"].status == AttemptStatus.COMPLETED
    # 批1-S2：落库观察器随 gateway 实例存活（两个 receipt 桥已注销）
    assert len(gateway._sinks) == 1


@pytest.mark.asyncio
async def test_gateway_sync_waiter_fails_if_its_request_is_summarized(monkeypatch):
    import asyncio
    import threading

    from agentnodes.interfaces.api.routers import chat as chat_router
    from agentnodes.kernel.gateway import api_chat_request
    from agentnodes.kernel.gateway.gateway import Gateway

    started = threading.Event()
    release = threading.Event()

    class Engine:
        def run(self, state):
            content = state["messages"][-1]["content"]
            if content == "active":
                started.set()
                if not release.wait(5):
                    raise RuntimeError("test release timed out")
            return {"answer": content}

    gateway = Gateway(client=object(), engine_factory=Engine)
    gateway.entry._cap = 2
    service = SimpleNamespace(
        store=SimpleNamespace(get_messages=lambda *args, **kwargs: []),
        load_history=lambda *args, **kwargs: [],
        update_attempt_status=lambda *args, **kwargs: None,
        event_bus=SimpleNamespace(emit=Mock()),
    )
    monkeypatch.setattr(chat_router, "_get_gateway", lambda: gateway)
    monkeypatch.setattr(chat_router, "_get_session_service", lambda: service)
    monkeypatch.setattr(chat_router, "_build_llm_config", lambda: None)
    monkeypatch.setattr(chat_router, "_chat_wait_timeout", lambda: 3.0)
    monkeypatch.setattr(chat_router, "_push_sse", lambda *args: None)
    gateway.entry.submit(api_chat_request("s1", "active", queue_mode=QueueMode.FOLLOWUP))
    gateway.start_consumer(poll_interval=0.01)
    try:
        assert await asyncio.to_thread(started.wait, 3)
        task = asyncio.create_task(chat_router._send_via_gateway(
            "s1", "mine", wait=True, queue_mode=QueueMode.FOLLOWUP,
        ))
        mailbox = gateway.entry.mailbox_for("chat:s1")
        for _ in range(300):
            if mailbox.peek_depth() == 1:
                break
            await asyncio.sleep(0.01)
        assert mailbox.peek_depth() == 1
        gateway.entry.submit(api_chat_request("s1", "other1", queue_mode=QueueMode.FOLLOWUP))
        gateway.entry.submit(api_chat_request("s1", "other2", queue_mode=QueueMode.FOLLOWUP))
        with pytest.raises(HTTPException) as exc_info:
            await asyncio.wait_for(task, 3)
        assert exc_info.value.status_code == 502
        assert exc_info.value.detail["error"] == "request_summarized"
        # 批1-S2：被摘要请求无 assistant_message 落库（用户消息已在提交点
        # 落库——"提交即事实"），其余 run 未到终态 → 观察器静默
        assert not [c for c in service.event_bus.emit.call_args_list
                    if c.args[1] == "assistant_message"]
        # 批1-S2：落库观察器随 gateway 实例存活（被摘要请求的桥已注销）
        assert len(gateway._sinks) == 1
    finally:
        release.set()
        gateway.stop_consumer(grace=3)
    assert not gateway.consumer_running()


def test_send_async_response_and_execution_options(client, monkeypatch):
    session_id = _new_session(client)
    service = SimpleNamespace(
        store=SimpleNamespace(
            create_attempt=Mock(),
            delete_attempt=Mock(),
            get_attempt=Mock(return_value=SimpleNamespace(status="pending")),
            # 3a.3b：send_async 新增历史预检（决策 5），fake 补最小面
            get_messages=Mock(return_value=[]),
        ),
        load_history=Mock(return_value=[]),
        update_attempt_status=Mock(return_value=None),
        event_bus=SimpleNamespace(emit=Mock()),
    )
    _install_service(monkeypatch, service)
    import agentnodes.interfaces.api.routers.chat as chat_router
    gateway = SimpleNamespace(
        attempt_state=Mock(return_value="queued"),
        entry=SimpleNamespace(
            mailbox_for=Mock(return_value=SimpleNamespace(peek_depth=lambda: 2)),
        ),
    )
    submit = AsyncMock(return_value={"accepted": True})
    monkeypatch.setattr(chat_router, "_get_gateway", lambda: gateway)
    monkeypatch.setattr(chat_router, "_send_via_gateway", submit)
    monkeypatch.setattr(
        chat_router,
        "_gateway_chat_options",
        lambda body: {
            "mode": "plan",
            "system_prompt": "persona prompt",
            "role": "researcher",
            "allowed_tools": ["read_file"],
            "thinking": "on",
            "model": "model-x",
        },
    )

    response = client.post(
        "/api/chat/send_async",
        json={
            "session_id": session_id,
            "content": "question",
            "agent_id": "researcher",
            "mode": "plan",
            "model": "model-x",
            "thinking": "on",
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["message_id"] == body["user_message_id"]
    assert body["assistant_message_id"]
    assert body["event_id"] == ""
    assert body["status"] == "queued"
    assert body["attempt_id"]
    submit.assert_awaited_once_with(
        session_id,
        "question",
        queue_mode=QueueMode.FOLLOWUP,
        max_pending=10,
        user_message_id=body["user_message_id"],
        attempt_id=body["attempt_id"],
        assistant_message_id=body["assistant_message_id"],
        receipt_only=True,
        # 重复 A 修复：预检结果透传（send_turn 跳过二次读历史）
        prevalidated_history=[],
        mode="plan",
        system_prompt="persona prompt",
        role="researcher",
        allowed_tools=["read_file"],
        thinking="on",
        model="model-x",
    )
    service.store.create_attempt.assert_called_once()
    emitted = [call.args[1] for call in service.event_bus.emit.call_args_list]
    assert emitted == ["attempt.created", "message_received", "queue_state"]
    created_attempt = service.store.create_attempt.call_args.args[0]
    assert created_attempt.attempt_id == body["attempt_id"]
    assert created_attempt.message_id == body["assistant_message_id"]
    assert created_attempt.model_override == "model-x"
    assert created_attempt.thinking == "on"


def test_gateway_chat_options_reuse_persona_plan_and_thinking_policy(monkeypatch):
    import agentnodes.interfaces.api.routers.chat as chat_router
    import agentnodes.kernel.session.loop_events as loop_events
    from agentnodes.interfaces.api.schemas.chat import ChatMessageRequest

    monkeypatch.setattr(
        loop_events,
        "_resolve_loop_prompt",
        lambda _prompt, persona: ("persona system", persona or "chat"),
    )
    monkeypatch.setattr(
        loop_events, "_thinking_instructions", lambda thinking: f"<{thinking}>",
    )
    monkeypatch.setattr(
        loop_events, "_plan_mode_allowed_tools", lambda mode: ["read_file"] if mode == "plan" else None,
    )

    options = chat_router._gateway_chat_options(ChatMessageRequest(
        session_id="s",
        content="q",
        agent_id="researcher",
        mode=" PLAN ",
        model="model-x",
        thinking="on",
    ))

    assert options == {
        "mode": "plan",
        "system_prompt": "persona system<on>",
        "role": "researcher",
        "allowed_tools": ["read_file"],
        "thinking": "on",
        "model": "model-x",
    }


def test_send_async_queue_full_preserves_429_detail(client, monkeypatch):
    session_id = _new_session(client)
    service = SimpleNamespace(
        store=SimpleNamespace(
            create_attempt=Mock(), delete_attempt=Mock(),
            # 3a.3b：send_async 新增历史预检（决策 5），fake 补最小面
            get_messages=Mock(return_value=[]),
        ),
        load_history=Mock(return_value=[]),
        update_attempt_status=Mock(return_value=None),
        event_bus=SimpleNamespace(emit=Mock()),
    )
    _install_service(monkeypatch, service)
    import agentnodes.interfaces.api.routers.chat as chat_router
    monkeypatch.setattr(
        chat_router,
        "_send_via_gateway",
        AsyncMock(side_effect=HTTPException(
            status_code=429,
            detail={"error": "queue_full", "limit": 10, "current_size": 10},
        )),
    )

    response = client.post(
        "/api/chat/send_async",
        json={"session_id": session_id, "content": "question"},
    )

    assert response.status_code == 429
    assert response.json()["detail"] == {
        "error": "queue_full",
        "limit": 10,
        "current_size": 10,
    }
    service.store.delete_attempt.assert_called_once()
    assert [
        call.args[1] for call in service.event_bus.emit.call_args_list
    ] == ["attempt.created"]


def test_cancel_and_resume_response_shapes(client, monkeypatch):
    session_id = _new_session(client)
    gateway = SimpleNamespace(
        attempt_state=Mock(return_value="running"),
        cancel_session=Mock(return_value=True),
        resume_session=Mock(return_value=True),
    )
    import agentnodes.interfaces.api.routers.chat as chat_router
    monkeypatch.setattr(chat_router, "_get_gateway", lambda: gateway)

    cancelled = client.post(
        "/api/chat/cancel",
        json={"session_id": session_id, "attempt_id": "attempt-1"},
    )
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json() == {
        "status": "cancelled",
        "session_id": session_id,
        "attempt_id": "attempt-1",
    }

    resumed = client.post(
        "/api/chat/queue/resume",
        json={"session_id": session_id},
    )
    assert resumed.status_code == 200, resumed.text
    assert resumed.json() == {"ok": True, "session_id": session_id}
    gateway.cancel_session.assert_called_once_with(session_id)
    gateway.resume_session.assert_called_once_with(session_id)


@pytest.mark.parametrize(
    ("state", "expected_actions"),
    [
        (
            {"active": False, "pending": False, "paused": False},
            ["send"],
        ),
        (
            {"active": True, "pending": False, "paused": False},
            ["cancel"],
        ),
        (
            {"active": False, "pending": True, "paused": True},
            ["cancel", "resume"],
        ),
    ],
)
def test_available_actions_use_gateway_chat_state(
    client, monkeypatch, state, expected_actions,
):
    session_id = _new_session(client)
    gateway = SimpleNamespace(
        chat_session_state=Mock(return_value=state),
    )
    import agentnodes.interfaces.api.routers.chat as chat_router
    monkeypatch.setattr(chat_router, "_get_gateway", lambda: gateway)

    response = client.get(f"/api/chat/session/{session_id}/available_actions")

    assert response.status_code == 200, response.text
    assert [
        action["name"] for action in response.json()["actions"]
    ] == expected_actions
    gateway.chat_session_state.assert_called_once_with(session_id)


def test_attempt_reload_uses_live_gateway_state(client, monkeypatch):
    session_id = _new_session(client)
    persisted = [
        SimpleNamespace(
            attempt_id="queued-1",
            message_id="message-1",
            prompt="queued prompt",
            created_at="2026-01-01T00:00:00Z",
            error=None,
            status="pending",
        ),
        SimpleNamespace(
            attempt_id="running-1",
            message_id="message-2",
            prompt="running prompt",
            created_at="2026-01-01T00:00:01Z",
            error=None,
            status="running",
        ),
        SimpleNamespace(
            attempt_id="stale-1",
            message_id="message-3",
            prompt="stale prompt",
            created_at="2026-01-01T00:00:02Z",
            error=None,
            status="pending",
        ),
        SimpleNamespace(
            attempt_id="failed-1",
            message_id="message-4",
            prompt="failed prompt",
            created_at="2026-01-01T00:00:03Z",
            error="provider failed",
            status="failed",
        ),
    ]
    store = SimpleNamespace(
        list_attempts_by_status=Mock(return_value=persisted),
    )
    service = SimpleNamespace(store=store)
    _install_service(monkeypatch, service)
    import agentnodes.interfaces.api.routers.chat as chat_router
    states = {
        "queued-1": "queued",
        "running-1": "running",
        "stale-1": None,
    }
    gateway = SimpleNamespace(
        attempt_state=Mock(side_effect=lambda _session, attempt: states[attempt]),
    )
    monkeypatch.setattr(chat_router, "_get_gateway", lambda: gateway)

    response = client.get(
        "/api/chat/attempts",
        params={"session_id": session_id},
    )

    assert response.status_code == 200, response.text
    assert response.json()["attempts"] == [
        {
            "attempt_id": "queued-1",
            "message_id": "message-1",
            "status": "queued",
            "prompt": "queued prompt",
            "created_at": "2026-01-01T00:00:00Z",
            "error": "",
        },
        {
            "attempt_id": "running-1",
            "message_id": "message-2",
            "status": "running",
            "prompt": "running prompt",
            "created_at": "2026-01-01T00:00:01Z",
            "error": "",
        },
        {
            "attempt_id": "failed-1",
            "message_id": "message-4",
            "status": "failed",
            "prompt": "failed prompt",
            "created_at": "2026-01-01T00:00:03Z",
            "error": "provider failed",
        },
    ]


def test_gateway_event_bridge_updates_attempt_without_persisting(monkeypatch):
    """批1-S2（A1+B2）：桥只做 Attempt 生命周期 + SSE 收尾；
    assistant_message 落库已收归进程单例观察器（_install_answer_persister），
    桥不再 emit——STEER 被吸收时跟随桥与所有者桥不会重复落库。"""
    import agentnodes.interfaces.api.routers.chat as chat_router

    session_id = "session-1"
    attempt = Attempt(
        attempt_id=AttemptId("attempt-1"),
        session_id=SessionId(session_id),
        status=AttemptStatus.PENDING,
    )
    store = SimpleNamespace(
        get_attempt=Mock(return_value=attempt),
        update_attempt=Mock(),
    )
    service = SimpleNamespace(
        store=store,
        event_bus=SimpleNamespace(emit=Mock()),
    )
    sink_holder = {}
    unsubscribed = []
    gateway = SimpleNamespace(
        add_sink=lambda sink: (
            sink_holder.update({"sink": sink})
            or (lambda: unsubscribed.append(True))
        ),
    )
    sse = []
    monkeypatch.setattr(
        chat_router,
        "_push_sse",
        lambda kind, data, _session_id: sse.append((kind, data)),
    )
    message_id_holder = {}
    chat_router._install_chat_event_bridge(
        gateway,
        session_id,
        message_id_holder,
        service=service,
        persist=True,
    )

    sink_holder["sink"]("run.started", {
        "run_id": "run-1",
        "attempt_id": "attempt-1",
        "assistant_message_id": "assistant-1",
    })
    assert attempt.status is AttemptStatus.RUNNING
    sink_holder["sink"]("run.answer", {
        "run_id": "run-1",
        "attempt_id": "attempt-1",
        "assistant_message_id": "assistant-1",
        "content": "answer",
    })

    assert attempt.status is AttemptStatus.COMPLETED
    assert attempt.summary == "answer"
    assert message_id_holder["id"] == "assistant-1"
    # 桥不再落库（B2）：assistant_message 由单例观察器负责。
    # 桥仍 emit Attempt 生命周期事件（attempt.started/completed）——只断言
    # 没有 assistant_message。
    assert not [c for c in service.event_bus.emit.call_args_list
                if c.args[1] == "assistant_message"]
    assert ("agent_done", {
        "message_id": "assistant-1",
        "status": "completed",
    }) in sse
    assert unsubscribed == [True]


@pytest.mark.asyncio
async def test_answer_persister_persists_once_per_run_and_skips_unscoped(monkeypatch):
    """批1-S2（A1+B2）：单例观察器按 run_id 去重、按 session_id 归属；
    外部启动 run（无 HTTP 桥）与原桥超时场景照常恰一次落库。"""
    import agentnodes.interfaces.api.routers.chat as chat_router

    emitted = []
    service = SimpleNamespace(
        event_bus=SimpleNamespace(
            emit=lambda sid, kind, data: emitted.append((sid, kind, data)),
        ),
    )
    monkeypatch.setattr(chat_router, "_get_session_service", lambda: service)

    sinks = []
    gateway = SimpleNamespace(
        add_sink=lambda sink: sinks.append(sink) or (lambda: None),
    )
    chat_router._install_answer_persister(gateway)
    chat_router._install_answer_persister(gateway)  # 旗标幂等：只装一次
    assert len(sinks) == 1

    persist = sinks[0]
    persist("run.answer", {
        "run_id": "r1", "business_session_id": "s1",
        "assistant_message_id": "m1", "content": "answer-1",
    })
    persist("run.answer", {  # 同 run 双源触发（桥内外）→ run_id 去重
        "run_id": "r1", "business_session_id": "s1",
        "assistant_message_id": "m1", "content": "answer-1",
    })
    persist("run.answer", {"run_id": "r2", "content": "no-session"})  # 无归属 → 跳过
    persist("run.failed", {"run_id": "r3", "business_session_id": "s1"})  # 非答案 → 跳过
    persist("run.answer", {  # 外部启动 run：无桥无 HTTP 请求 → 照常落库
        "run_id": "r4", "business_session_id": "s9",
        "content": "external",
    })

    assert [(sid, data["message_id"], data["content"])
            for sid, kind, data in emitted if kind == "assistant_message"] == [
        ("s1", "m1", "answer-1"),
        ("s9", "r4", "external"),
    ]


@pytest.mark.asyncio
async def test_gateway_receipt_path_does_not_run_inline_without_consumer(monkeypatch):
    import agentnodes.interfaces.api.routers.chat as chat_router

    entry = SimpleNamespace(submit=Mock(return_value=Receipt.ok("session-1")))
    gateway = SimpleNamespace(
        entry=entry,
        consumer_running=Mock(return_value=False),
        add_sink=Mock(return_value=lambda: None),
        run_once=Mock(side_effect=AssertionError("receipt path must not run inline")),
    )
    service = SimpleNamespace(
        store=SimpleNamespace(get_messages=Mock(return_value=[])),
        load_history=Mock(return_value=[]),
        update_attempt_status=Mock(return_value=None),
    )
    monkeypatch.setattr(chat_router, "_get_gateway", lambda: gateway)
    monkeypatch.setattr(chat_router, "_get_session_service", lambda: service)
    monkeypatch.setattr(chat_router, "_build_llm_config", lambda: None)

    result = await chat_router._send_via_gateway(
        "session-1",
        "question",
        queue_mode=QueueMode.FOLLOWUP,
        max_pending=10,
        attempt_id="attempt-1",
        assistant_message_id="assistant-1",
        receipt_only=True,
    )

    assert result["accepted"] is True
    gateway.run_once.assert_not_called()
    args, kwargs = entry.submit.call_args
    request = args[0]
    assert request.queue_mode is QueueMode.FOLLOWUP
    assert request.payload["attempt_id"] == "attempt-1"
    assert kwargs == {"max_pending": 10}


@pytest.mark.asyncio
async def test_gateway_queue_full_maps_to_429_with_current_size(monkeypatch):
    import agentnodes.interfaces.api.routers.chat as chat_router

    entry = SimpleNamespace(submit=Mock(return_value=Receipt.reject(
        RejectReason.QUEUE_FULL,
        current_size=10,
    )))
    gateway = SimpleNamespace(
        entry=entry,
        consumer_running=Mock(return_value=False),
        add_sink=Mock(return_value=lambda: None),
    )
    service = SimpleNamespace(
        store=SimpleNamespace(get_messages=Mock(return_value=[])),
        load_history=Mock(return_value=[]),
        update_attempt_status=Mock(return_value=None),
    )
    monkeypatch.setattr(chat_router, "_get_gateway", lambda: gateway)
    monkeypatch.setattr(chat_router, "_get_session_service", lambda: service)
    monkeypatch.setattr(chat_router, "_build_llm_config", lambda: None)

    with pytest.raises(HTTPException) as exc_info:
        await chat_router._send_via_gateway(
            "session-1",
            "question",
            queue_mode=QueueMode.FOLLOWUP,
            max_pending=10,
            receipt_only=True,
        )

    assert exc_info.value.status_code == 429
    assert exc_info.value.detail == {
        "error": "queue_full",
        "limit": 10,
        "current_size": 10,
    }
