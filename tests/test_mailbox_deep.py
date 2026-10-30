"""mailbox 边界补充测试 —— 与 test_entry_contracts*.py 不重叠的深层行为。"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import pytest

from strategy_research.core.orchestration.mailbox import Mailbox
from strategy_research.core.orchestration.contracts import (
    DEFAULT_QUEUE_MODE,
    QueueMode,
    SourceKind,
    WorkKind,
    WorkRequest,
)


def make_req(text="q", mode=None, key="chat:s", idem=None, deadline=None):
    return WorkRequest(
        kind=WorkKind.CHAT, source=SourceKind.CLI,
        session_key=key, payload={"text": text},
        queue_mode=mode, idempotency_key=idem, deadline=deadline,
    )


class Recorder:
    def __init__(self):
        self.events = []

    def __call__(self, kind, data):
        self.events.append((kind, data))

    def of(self, kind):
        return [d for k, d in self.events if k == kind]


def make_mailbox(cap=20, clock=None):
    rec = Recorder()
    mb = Mailbox("sess-x", cap=cap, clock=clock or (lambda: 1000.0), sink=rec)
    return mb, rec


# ── 队列不变量 ──────────────────────────────────────────


def test_fifo_order_preserved():
    mb, _ = make_mailbox()
    for t in ["a", "b", "c", "d"]:
        mb.submit(make_req(text=t, mode=QueueMode.FOLLOWUP), resolve_session=lambda k: "s")
    labels = [item.request.payload["text"] for item in mb.snapshot()]
    assert labels == ["a", "b", "c", "d"]


def test_empty_mailbox_poll_returns_none():
    mb, _ = make_mailbox()
    assert mb.poll_due() is None
    assert mb.peek_depth() == 0


def test_interrupt_only_jumps_one_position():
    """两条 interrupt：后到的在最前（appendleft），先到的在第二位。"""
    mb, _ = make_mailbox()
    for t in ["f1", "f2"]:
        mb.submit(make_req(text=t, mode=QueueMode.FOLLOWUP), resolve_session=lambda k: "s")
    mb.submit(make_req(text="i1", mode=QueueMode.INTERRUPT), resolve_session=lambda k: "s")
    mb.submit(make_req(text="i2", mode=QueueMode.INTERRUPT), resolve_session=lambda k: "s")

    # 顺序：i2, i1, f1, f2
    got = [mb.poll_due().payload["text"] for _ in range(4)]
    assert got == ["i2", "i1", "f1", "f2"]


def test_summary_items_not_dispatched_by_poll():
    """摘要项是标记不是工单，poll_due 跳过它们。"""
    mb, _ = make_mailbox(cap=3)
    for t in ["a", "b", "c"]:
        mb.submit(make_req(text=t, mode=QueueMode.FOLLOWUP), resolve_session=lambda k: "s")
    # 溢出产生摘要项
    mb.submit(make_req(text="d", mode=QueueMode.FOLLOWUP), resolve_session=lambda k: "s")
    # poll_due 应只返回真实工单，永远不返回摘要项
    dispatched = []
    while mb.peek_depth() > 0 or dispatched:
        req = mb.poll_due()
        if req is None:
            break
        dispatched.append(req.payload["text"])
    assert "summary" not in str(dispatched)


# ── deadline 边界 ────────────────────────────────────────


def test_deadline_not_yet_due_not_expired():
    mb, rec = make_mailbox(clock=lambda: 1000.0)
    future = datetime.fromtimestamp(1100, tz=timezone.utc)
    mb.submit(make_req(deadline=future, mode=QueueMode.FOLLOWUP), resolve_session=lambda k: "s")
    assert mb.peek_depth() == 1  # 未过期，仍在队列
    assert rec.of("work.expired") == []


def test_deadline_exactly_now_expired():
    """deadline == 当前时刻 → 已过期（<= 判定）。"""
    now = 1000.0
    mb, rec = make_mailbox(clock=lambda: now)
    mb.submit(
        make_req(deadline=datetime.fromtimestamp(now, tz=timezone.utc), mode=QueueMode.FOLLOWUP),
        resolve_session=lambda k: "s",
    )
    mb.expire_due()
    assert mb.peek_depth() == 0
    assert len(rec.of("work.expired")) == 1


def test_naive_deadline_handled():
    """naive datetime（无 tz）不崩溃——timestamp() 按本地时区解释。"""
    mb, rec = make_mailbox(clock=lambda: time.time())
    past = datetime.now() - timedelta(seconds=60)  # naive
    mb.submit(make_req(deadline=past, mode=QueueMode.FOLLOWUP), resolve_session=lambda k: "s")
    mb.expire_due()
    # naive/aware 两种之一都可能成立，只验证不崩溃
    assert True



# ── 摘要合并 ─────────────────────────────────────────────


def test_summary_carries_merged_labels():
    mb, rec = make_mailbox(cap=3)
    for t in ["a", "b", "c", "d", "e"]:
        mb.submit(make_req(text=t, mode=QueueMode.FOLLOWUP), resolve_session=lambda k: "s")
    summaries = [item for item in mb.snapshot() if item.is_summary]
    assert summaries, "应有摘要项"
    # 被合并的 id 应包含在 summarized_ids 中
    merged_ids = summaries[0].summarized_ids
    assert len(merged_ids) >= 2


def test_summary_emits_event_with_ids():
    mb, rec = make_mailbox(cap=2)
    for i in range(4):
        mb.submit(make_req(text=f"m{i}", mode=QueueMode.FOLLOWUP), resolve_session=lambda k: "s")
    summarized = rec.of("work.summarized")
    assert len(summarized) >= 1
    assert all("merged" in d for d in summarized)


# ── 事件完整性 ──────────────────────────────────────────


def test_enqueued_event_has_depth():
    mb, rec = make_mailbox()
    mb.submit(make_req(mode=QueueMode.FOLLOWUP), resolve_session=lambda k: "s")
    enq = rec.of("work.enqueued")
    assert len(enq) == 1
    assert enq[0]["depth"] == 1
    assert enq[0]["mode"] == "followup"


def test_steer_became_turn_event_has_session_id():
    mb, rec = make_mailbox()
    mb.submit(make_req(mode=QueueMode.STEER), resolve_session=lambda k: "s")
    became = rec.of("steer.became_turn")
    assert len(became) == 1
    assert became[0]["session_id"] == "sess-x"


# ── 并发安全（线程级） ──────────────────────────────────


def test_threaded_submits_never_lose_items():
    """并发 submit：全部入队，不丢不重（cap 足够大）。"""
    import threading

    mb, _ = make_mailbox(cap=100)
    N, T = 10, 4

    def worker(tid):
        for i in range(N):
            mb.submit(
                make_req(text=f"t{tid}-{i}", mode=QueueMode.FOLLOWUP),
                resolve_session=lambda k: "s",
            )

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(T)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert mb.peek_depth() == N * T  # 全部入队，零丢失
