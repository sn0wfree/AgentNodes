"""E1 一致性测试 —— 契约文档 §5 十条不变量的验收。

对应 docs/entry-controller-contract.md §8。每条测试名对应一条不变量，
失败即违反边界契约。全部纯内存、快同步（不变量 8 本身也在此验证）。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from strategy_research.core.orchestration import (
    Mailbox,
    QueueMode,
    Receipt,
    RejectReason,
    SourceKind,
    WorkKind,
    WorkRequest,
)
from strategy_research.core.orchestration.entry_point import EntryPoint


def make_req(**overrides) -> WorkRequest:
    defaults = dict(
        kind=WorkKind.CHAT,
        source=SourceKind.API_CHAT,
        session_key="chat:main",
        payload={"text": "hello"},
    )
    defaults.update(overrides)
    return WorkRequest(**defaults)


class EventProbe:
    """事件探针：收集 sink 回调，供断言。"""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def __call__(self, kind: str, data: dict) -> None:
        self.events.append((kind, data))

    def of(self, kind: str) -> list[dict]:
        return [data for k, data in self.events if k == kind]


class FakeClock:
    def __init__(self) -> None:
        import time

        self.now = time.time()  # 对齐真实时间，使 datetime.now() 构造的 deadline 可比较

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


# ── 不变量 1：回执只有两态 ──────────────────────────────


def test_receipt_has_exactly_two_states():
    ok = Receipt.ok("sid")
    bad = Receipt.reject(RejectReason.MALFORMED)
    assert ok.accepted and ok.reason is None
    assert not bad.accepted and bad.reason is not None
    # frozen：回执不可变
    with pytest.raises(Exception):
        ok.accepted = False  # type: ignore[misc]


# ── 不变量 2：入口拒绝无副作用 ────────────────────────────


def test_entry_rejection_has_no_side_effects():
    probe = EventProbe()
    ep = EntryPoint(sink=probe)
    bad = make_req(queue_mode=QueueMode.COLLECT)

    receipt = ep.submit(bad)

    assert not receipt.accepted
    assert receipt.reason is RejectReason.UNSUPPORTED_QUEUE_MODE
    assert receipt.session_id == ""  # 未建会话
    assert ep.session_id_for("chat:main") is None  # 无惰性创建
    assert ep.mailbox_for("chat:main") is None  # 未进 mailbox
    assert probe.events == []  # 无事件


# ── 不变量 3：幂等（同键同摘要等价 / 异摘要冲突） ──────────


def test_idempotency_same_key_same_payload_returns_same_receipt():
    ep = EntryPoint()
    first = ep.submit(make_req(idempotency_key="op-1"))
    second = ep.submit(make_req(idempotency_key="op-1"))
    assert first.accepted and second.accepted
    assert first == second  # 原回执原样返回


def test_idempotency_same_key_different_payload_conflicts():
    ep = EntryPoint()
    ep.submit(make_req(idempotency_key="op-1", payload={"text": "a"}))
    conflict = ep.submit(make_req(idempotency_key="op-1", payload={"text": "b"}))
    assert not conflict.accepted
    assert conflict.reason is RejectReason.IDEMPOTENCY_CONFLICT


# ── 不变量 4：collect 拒绝（枚举位保留） ──────────────────


def test_collect_rejected_with_dedicated_reason():
    ep = EntryPoint()
    receipt = ep.submit(make_req(queue_mode=QueueMode.COLLECT))
    assert not receipt.accepted
    assert receipt.reason is RejectReason.UNSUPPORTED_QUEUE_MODE


# ── 不变量 5/10：payload 白名单 ──────────────────────────


@pytest.mark.parametrize("bad_key", ["http", "SSE", "Headers", "raw_request"])
def test_payload_protocol_fields_rejected(bad_key: str):
    ep = EntryPoint()
    receipt = ep.submit(make_req(payload={bad_key: "x"}))
    assert not receipt.accepted
    assert receipt.reason is RejectReason.PAYLOAD_PROTOCOL_FIELD


def test_clean_payload_passes():
    ep = EntryPoint()
    receipt = ep.submit(make_req(payload={"text": "hi", "factor": "mom12"}))
    assert receipt.accepted


# ── 不变量 6：rejected/EXPIRED/降级必有事件 ───────────────


def test_entry_rejection_emits_no_event_but_sidepaths_do():
    # 入口拒绝无事件（不变量 2）
    probe = EventProbe()
    ep = EntryPoint(sink=probe)
    ep.submit(make_req(queue_mode=QueueMode.COLLECT))
    assert probe.events == []

    # steer 无活跃 run → 直接成正常 turn，必须发事件（不静默，R2.2）
    probe2 = EventProbe()
    ep2 = EntryPoint(sink=probe2)
    ep2.submit(make_req(queue_mode=QueueMode.STEER))
    became = probe2.of("steer.became_turn")
    assert len(became) == 1
    assert became[0]["session_id"]


def test_deadline_expiry_emits_expired_event():
    clock = FakeClock()
    probe = EventProbe()
    ep = EntryPoint(clock=clock, sink=probe)

    ep.submit(
        make_req(
            queue_mode=QueueMode.FOLLOWUP,
            deadline=datetime.now(timezone.utc) + timedelta(seconds=60),
        )
    )
    assert probe.of("work.expired") == []  # 未到期

    clock.advance(120)  # 越过 deadline
    mailbox = ep.mailbox_for("chat:main")
    assert mailbox is not None
    assert mailbox.poll_due() is None  # 惰性出清
    expired = probe.of("work.expired")
    assert len(expired) == 1
    assert expired[0]["deadline"] is not None


# ── 不变量 7：QUEUED 不继承 run 超时 / 不表现为 executing ──


def test_queued_items_are_not_executing_runs():
    ep = EntryPoint()
    ep.submit(make_req(queue_mode=QueueMode.FOLLOWUP))
    mailbox = ep.mailbox_for("chat:main")
    assert mailbox is not None
    # 队列深度=1 但没有任何 executing run 的概念暴露（无 run_id 字段可取）
    snapshot = list(mailbox.snapshot())
    assert len(snapshot) == 1
    assert not hasattr(snapshot[0], "run_id")


# ── 不变量 8：快同步（无 await 入口） ────────────────────


def test_boundary_methods_are_synchronous():
    ep = EntryPoint()
    import inspect

    assert not inspect.iscoroutinefunction(EntryPoint.submit)
    assert not inspect.iscoroutinefunction(Mailbox.submit)
    assert not inspect.iscoroutinefunction(Mailbox.poll_due)


# ── 不变量 9：cap 溢出 → summarize（拒绝点唯一） ──────────


def test_cap_overflow_summarizes_oldest():
    clock = FakeClock()
    probe = EventProbe()
    ep = EntryPoint(clock=clock, sink=probe)

    for i in range(21):  # cap=20，第 21 条触发合并
        ep.submit(make_req(queue_mode=QueueMode.FOLLOWUP, payload={"n": i}))

    mailbox = ep.mailbox_for("chat:main")
    assert mailbox is not None
    snapshot = list(mailbox.snapshot())
    assert len(snapshot) == 20  # cap 不破
    assert snapshot[0].is_summary  # 最旧者被摘要合并
    assert probe.of("work.summarized")  # 合并有事件可见


# ── interrupt 插队 + FIFO ───────────────────────────────


def test_interrupt_jumps_queue_fifo_elsewhere():
    ep = EntryPoint()
    ep.submit(make_req(queue_mode=QueueMode.FOLLOWUP, payload={"text": "a"}))
    ep.submit(make_req(queue_mode=QueueMode.FOLLOWUP, payload={"text": "b"}))
    ep.submit(make_req(queue_mode=QueueMode.INTERRUPT, payload={"text": "urgent"}))

    mailbox = ep.mailbox_for("chat:main")
    assert mailbox is not None
    first = mailbox.poll_due()
    assert first is not None and first.payload == {"text": "urgent"}
    second = mailbox.poll_due()
    assert second is not None and second.payload == {"text": "a"}  # FIFO 恢复


# ── 惰性创建回填 session_id（契约 §3） ───────────────────


def test_lazy_session_creation_backfills_session_id():
    ep = EntryPoint()
    receipt = ep.submit(make_req(session_key="study:momentum"))
    assert receipt.accepted
    assert receipt.session_id  # 回填
    assert receipt.session_id == ep.session_id_for("study:momentum")
    # 同 key 再提交 → 同一物理会话
    again = ep.submit(make_req(session_key="study:momentum"))
    assert again.session_id == receipt.session_id


# ── malformed：session_key 约定 ─────────────────────────


def test_malformed_session_key_rejected():
    ep = EntryPoint()
    receipt = ep.submit(make_req(session_key="nosuchdomain"))
    assert not receipt.accepted
    assert receipt.reason is RejectReason.MALFORMED
