# -*- coding: utf-8 -*-
"""B2 回归：goal 持有 lease 时 chat 工单留 mailbox，释放后可靠消费。

裁决 2 硬要求（2026-11-18 第二轮）：goal 持有 → chat 请求留在 mailbox
（不拒绝、不丢单）；lease 释放后消费者可靠唤醒，不空转；回归覆盖
「持有期间入队 → 释放 → 次条正常消费」。

测试策略（对照 test_b1_interrupt.py 的直构 Controller 模式）：
- goal 占用 = 在 controller 注入的同一 ``_lease`` 上 acquire("goal:...")
- 执行器 stub 断言「goal 持有期间 turn 从未执行」
- 释放后 run_pending 执行 mailbox 里滞留的工单
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agentnodes.kernel.gateway.fencing import SessionFence
from agentnodes.kernel.gateway.session_controller import SessionController
from agentnodes.kernel.gateway.session_lease import SessionExecutionLease
from agentnodes.kernel.orchestration import (
    EntryPoint,
    QueueMode,
    SourceKind,
    WorkKind,
    WorkRequest,
)


class FakeClient:
    """最小 LLM 客户端替身。"""


def _build_controller(lease: SessionExecutionLease, sink, engine_factory=None):
    """直构 EntryPoint + Controller（同 test_b1_interrupt 模式）。"""
    ep = EntryPoint(sink=sink)
    return SessionController(
        ep,
        client=MagicMock(),
        sink=sink,
        fence=SessionFence(),
        cancel_registry={},
        engine_factory=engine_factory,
        lease=lease,
    )


def _submit_chat(ep, session_key: str, text: str) -> None:
    ep.submit(WorkRequest(
        kind=WorkKind.CHAT,
        source=SourceKind.SCHEDULER,
        queue_mode=QueueMode.FOLLOWUP,
        session_key=session_key,
        payload={"text": text},
    ))


# ── 1. goal 持有 → 工单留 mailbox 不执行 ───────────────────────────


def test_goal_holds_lease_chat_stays_in_mailbox():
    events: list[tuple[str, dict]] = []
    sink = lambda kind, data: events.append((kind, data))  # noqa: E731
    lease = SessionExecutionLease()

    token = lease.acquire("s1", "goal:g1")  # goal 占用（B3 的等价形态）
    assert token is not None

    controller = _build_controller(lease, sink)
    _submit_chat(controller._ep, "chat:s1", "goal 运行期间的消息")

    results = controller.run_pending()
    assert results == []  # 未执行
    assert controller._ep.mailbox_for("chat:s1").has_pending() is True  # 留队不丢单
    assert any(k == "lease.held" for k, _ in events)  # 结构证据：观测事件


# ── 2. 释放 → 滞留工单被可靠消费（不丢单不空转）────────────────────


def test_release_leads_to_pending_item_consumed():
    class _Engine:
        def run(self, state):
            return {"answer": "done-after-goal"}

    sink = lambda kind, data: None  # noqa: E731
    lease = SessionExecutionLease()
    controller = _build_controller(lease, sink, engine_factory=lambda: _Engine())
    ep = controller._ep

    token = lease.acquire("s1", "goal:g1")
    _submit_chat(ep, "chat:s1", "queued during goal")
    assert controller.run_pending() == []  # goal 持有 → 不领

    lease.release("s1", token)  # goal 释放
    results = controller.run_pending()
    assert len(results) == 1  # 滞留工单被消费，不空转
    assert ep.mailbox_for("chat:s1").has_pending() is False


# ── 3. goal 未持有 → 行为与迁移前一致（直通执行）────────────────────


def test_no_goal_holds_chat_executes_normally():
    class _Engine:
        def run(self, state):
            return {"answer": "ok"}

    sink = lambda kind, data: None  # noqa: E731
    lease = SessionExecutionLease()  # 无人持有
    controller = _build_controller(lease, sink, engine_factory=lambda: _Engine())
    _submit_chat(controller._ep, "chat:s1", "hi")
    results = controller.run_pending()
    assert len(results) == 1


# ── 4. 交错序列：goal 持有→chat 入队→goal 释放→chat 执行→goal 再获取──


def test_interleaved_goal_chat_goal_cycle():
    class _Engine:
        def run(self, state):
            return {"answer": "ok"}

    sink = lambda kind, data: None  # noqa: E731
    lease = SessionExecutionLease()
    controller = _build_controller(lease, sink, engine_factory=lambda: _Engine())
    ep = controller._ep

    t1 = lease.acquire("s1", "goal:g1")
    _submit_chat(ep, "chat:s1", "during-goal")
    assert controller.run_pending() == []
    lease.release("s1", t1)
    assert len(controller.run_pending()) == 1  # 释放后消费

    t2 = lease.acquire("s1", "goal:g2")  # goal 再次持有
    assert t2 is not None
    _submit_chat(ep, "chat:s1", "second-goal-window")
    assert controller.run_pending() == []
    lease.release("s1", t2)
    assert len(controller.run_pending()) == 1
    assert ep.mailbox_for("chat:s1").has_pending() is False


# ── 5. 异常路径：controller 异常时 lease 仍被释放（不悬挂死锁）──────


def test_lease_released_even_when_execute_raises():
    sink = lambda kind, data: None  # noqa: E731
    lease = SessionExecutionLease()
    controller = _build_controller(lease, sink)

    # 让 _execute 抛错：session_id_for 抛错（最早触达点，leak 检测最直接）。
    def broken(sid_key):
        raise RuntimeError("boom")

    controller._ep.session_id_for = broken
    _submit_chat(controller._ep, "chat:s1", "x")

    with pytest.raises(Exception):
        controller.run_pending()

    # 硬要求：异常路径 lease 必须已释放，否则 goal 永久无法获取（死锁）。
    assert lease.holding("s1") is False
