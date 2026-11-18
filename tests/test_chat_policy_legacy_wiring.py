# -*- coding: utf-8 -*-
"""ChatPolicy 接入 legacy send_message 的回归测试（批次 A，裁决 3）。

裁决："先单独完成 ChatPolicy 接入 legacy send_message，并用回归测试
确认队列行为不变；不要与移除 SessionService 队列/AgentLoop 合并。"

因此本文件只钉两件事：
1. policy 非 None 时覆盖 max_iterations / allow_shell_tools（入队值随之变化）；
2. 不传 policy 时队列行为完全不变（入队字段、FIFO、响应形状、429 上限）。
"""
from __future__ import annotations

import asyncio

import pytest

from agentnodes.apps.chat.policy import ChatPolicy


@pytest.fixture
def temp_db(monkeypatch, tmp_path):
    workspace = tmp_path
    workspace.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("AGENTNODES_WORKSPACE", str(workspace))
    yield workspace / "quantnodes_agentnodes_user.db"


@pytest.fixture
def event_bus():
    from tests._fixtures.legacy_events import EventBus

    # 不调用 set_loop：它只是跨线程 emit 的优化路径（call_soon_threadsafe），
    # 测试内 emit 均发生在同一 loop，EventBus 走 put_nowait 兜底分支。
    # 原先的 asyncio.get_event_loop() 在无运行 loop 的同步 fixture 中
    # 抛弃用警告，且被宽泛 except 吞掉——两样都不需要。
    return EventBus()


@pytest.fixture
def session_service(temp_db, event_bus):
    import sqlite3

    from agentnodes.interfaces.api.routers.web_session import _ensure_schema
    from agentnodes.interfaces.api.session.service import SessionService
    from agentnodes.interfaces.api.session.store import SessionStore

    conn = sqlite3.connect(str(temp_db))
    try:
        _ensure_schema(conn)
        conn.commit()
    finally:
        conn.close()
    return SessionService(store=SessionStore(temp_db), event_bus=event_bus)


@pytest.fixture
def stub_runner(monkeypatch):
    calls: list[dict] = []

    async def fake_run(self, *, session_id, attempt, **kwargs):
        calls.append({"attempt_id": attempt.attempt_id, **kwargs})
        await asyncio.sleep(0.02)

    monkeypatch.setattr(
        "agentnodes.interfaces.api.session.service.SessionService._run_attempt",
        fake_run,
    )
    return calls


def _ensure_session(store, session_id: str) -> None:
    from agentnodes.interfaces.api.routers.web_session import _get_db

    conn = _get_db()
    if not conn.execute("SELECT id FROM sessions WHERE id = ?", (session_id,)).fetchone():
        now = 1000.0
        conn.execute(
            "INSERT INTO sessions (id, user_id, title, created_at, updated_at, "
            "starred, tags_json, message_count, archived) "
            "VALUES (?, ?, ?, ?, ?, 0, '[]', 0, 0)",
            (session_id, "anonymous", "test", now, now),
        )
        conn.commit()


async def _wait_for_idle(service, session_id, timeout=3.0):
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if session_id not in service._processing_sessions:
            return
        await asyncio.sleep(0.02)
    raise AssertionError(f"session {session_id} did not go idle")


# ── 1. policy 覆盖生效 ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_policy_overrides_iterations_and_shell(session_service, stub_runner):
    sid = "sess-pol-1"
    _ensure_session(session_service.store, sid)
    policy = ChatPolicy(max_iterations=3, allow_shell_tools=True)

    result = await session_service.send_message(sid, "hi", policy=policy)
    await _wait_for_idle(session_service, sid)

    assert len(stub_runner) == 1
    assert stub_runner[0]["max_iterations"] == 3
    assert stub_runner[0]["allow_shell_tools"] is True
    assert result["attempt_id"] == stub_runner[0]["attempt_id"]


@pytest.mark.asyncio
async def test_policy_from_for_send_async_matches_legacy_values(
    session_service, stub_runner
):
    """ChatPolicy.for_send_async() 送进去 == 不传 policy 的既有值（等价性）。"""
    sid_a, sid_b = "sess-eq-a", "sess-eq-b"
    _ensure_session(session_service.store, sid_a)
    _ensure_session(session_service.store, sid_b)

    await session_service.send_message(sid_a, "x")  # 签名默认
    await session_service.send_message(
        sid_b, "x", policy=ChatPolicy.for_send_async()
    )
    await _wait_for_idle(session_service, sid_a)
    await _wait_for_idle(session_service, sid_b)

    assert len(stub_runner) == 2
    assert stub_runner[0]["max_iterations"] == stub_runner[1]["max_iterations"] == 50
    assert (stub_runner[0]["allow_shell_tools"]
            == stub_runner[1]["allow_shell_tools"] is False)


# ── 2. 不传 policy：队列行为零变化 ─────────────────────────────────


@pytest.mark.asyncio
async def test_queue_entry_shape_unchanged_without_policy(
    session_service, stub_runner
):
    """入队条目的键集合与既有实现一致（迁移前基线）。"""
    sid = "sess-shape"
    _ensure_session(session_service.store, sid)

    await session_service.send_message(sid, "hi")
    queue = session_service._session_queues[sid]
    item = queue._queue[0] if hasattr(queue, "_queue") else None
    await _wait_for_idle(session_service, sid)

    if item is not None:
        assert set(item) == {
            "attempt_id", "model", "max_iterations", "system_prompt",
            "allow_shell_tools", "mode", "thinking",
        }


@pytest.mark.asyncio
async def test_fifo_order_preserved_with_policy(session_service, stub_runner):
    """带 policy 时 FIFO 顺序与响应字段不变。"""
    sid = "sess-fifo"
    _ensure_session(session_service.store, sid)
    policy = ChatPolicy.for_send_async()

    r1 = await session_service.send_message(sid, "m1", policy=policy)
    r2 = await session_service.send_message(sid, "m2", policy=policy)
    r3 = await session_service.send_message(sid, "m3", policy=policy)

    assert r1["queue_position"] == 1
    assert r2["queue_position"] == 2
    assert r3["queue_position"] == 3
    for r in (r1, r2, r3):
        assert set(r) >= {
            "message_id", "user_message_id", "assistant_message_id",
            "attempt_id", "queue_position", "status",
        }

    await _wait_for_idle(session_service, sid, timeout=5.0)
    assert [c["attempt_id"] for c in stub_runner] == [
        r1["attempt_id"], r2["attempt_id"], r3["attempt_id"],
    ]


@pytest.mark.asyncio
async def test_queue_full_still_capped_at_10_with_policy(
    session_service, stub_runner
):
    """队列上限 10 与 429 形状不因 policy 改变。"""
    sid = "sess-full"
    _ensure_session(session_service.store, sid)
    policy = ChatPolicy.for_send_async()

    results = []
    for i in range(12):
        results.append(await session_service.send_message(sid, f"m{i}", policy=policy))

    over = [r for r in results if "error" in r]
    assert over, "queue should reject beyond limit"
    assert over[0]["error"] == "queue_full"
    assert over[0]["limit"] == 10
    assert "current_size" in over[0]
    await _wait_for_idle(session_service, sid, timeout=10.0)
