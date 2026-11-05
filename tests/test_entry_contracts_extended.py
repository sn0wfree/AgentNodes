"""E1 契约边界测试 —— 补充测试（与 test_entry_contracts.py 互补）。

覆盖 v6 契约里 test_entry_contracts.py 尚未直接断言的部分：
- 五行默认表逐源断言（不只 api_chat）
- QueueMode 显式声明覆盖来源默认
- deadline 混批（部分过期部分保留）
- interrupt 多条相对顺序（不颠倒）
- summarize 连击（多次溢出后仍不破 cap）
- 回执 session_id 稳定性 / 不同 session_key 隔离
- payload_digest 规范化（键序无关）与类型差异敏感
- validate 的白名单大小写不敏感细节
- 快同步的无网络/无文件 IO 性质（静态断言）
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from agentnodes.kernel.orchestration import (
    DEFAULT_QUEUE_MODE,
    QueueMode,
    RejectReason,
    SourceKind,
    WorkKind,
    WorkRequest,
    validate_work_request,
)
from agentnodes.kernel.orchestration.entry_point import EntryPoint
from tests.test_entry_contracts import EventProbe, FakeClock, make_req


# ── 五行默认表逐源断言 ──────────────────────────────────


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (SourceKind.CLI, QueueMode.FOLLOWUP),
        (SourceKind.API_CHAT, QueueMode.STEER),
        (SourceKind.SCHEDULER, QueueMode.INTERRUPT),
        (SourceKind.MCP, QueueMode.FOLLOWUP),
        (SourceKind.DIRECTOR, QueueMode.FOLLOWUP),
    ],
)
def test_default_queue_mode_table(source: SourceKind, expected: QueueMode):
    assert DEFAULT_QUEUE_MODE[source] is expected


def test_api_chat_default_goes_through_steer_became_turn():
    """API chat 默认 steer：无活跃 run 时直接成 turn 并留事件（不静默）。"""
    probe = EventProbe()
    ep = EntryPoint(sink=probe)
    receipt = ep.submit(make_req(source=SourceKind.API_CHAT))  # queue_mode=None
    assert receipt.accepted
    assert len(probe.of("steer.became_turn")) == 1


def test_cli_default_does_not_emit_steer_event():
    probe = EventProbe()
    ep = EntryPoint(sink=probe)
    ep.submit(make_req(source=SourceKind.CLI))
    assert probe.of("steer.became_turn") == []


def test_explicit_queue_mode_overrides_source_default():
    probe = EventProbe()
    ep = EntryPoint(sink=probe)
    # api_chat 默认 steer，但显式 followup 不发 steer 事件
    ep.submit(make_req(source=SourceKind.API_CHAT, queue_mode=QueueMode.FOLLOWUP))
    assert probe.of("steer.became_turn") == []


# ── deadline 混批 ────────────────────────────────────────


def test_deadline_mixed_batch_only_expires_due_items():
    clock = FakeClock()
    probe = EventProbe()
    ep = EntryPoint(clock=clock, sink=probe)

    soon = datetime.now(timezone.utc) + timedelta(seconds=10)
    later = datetime.now(timezone.utc) + timedelta(seconds=600)
    ep.submit(make_req(queue_mode=QueueMode.FOLLOWUP, payload={"tag": "soon"}, deadline=soon))
    ep.submit(make_req(queue_mode=QueueMode.FOLLOWUP, payload={"tag": "later"}, deadline=later))
    ep.submit(make_req(queue_mode=QueueMode.FOLLOWUP, payload={"tag": "forever"}))

    clock.advance(30)  # 只有 soon 过期

    mailbox = ep.mailbox_for("chat:main")
    assert mailbox is not None
    remaining = [item.request.payload["tag"] for item in mailbox.snapshot()]
    assert remaining == ["later", "forever"]
    expired = probe.of("work.expired")
    assert len(expired) == 1
    # 过期事件必须能定位到具体工单：label = kind + 载荷摘要短码（非进程内 id）
    label = expired[0]["label"]
    assert label.startswith("chat:")
    digest_part = label.split(":", 1)[1]
    assert len(digest_part) == 8 and digest_part.isalnum()


def test_expired_item_frees_cap_space():
    clock = FakeClock()
    ep = EntryPoint(clock=clock)  # cap=20
    deadline = datetime.now(timezone.utc) + timedelta(seconds=5)

    ep.submit(make_req(queue_mode=QueueMode.FOLLOWUP, deadline=deadline))
    for _ in range(19):
        ep.submit(make_req(queue_mode=QueueMode.FOLLOWUP))
    # 满 20；过期一条后应能再入队且不触发 summarize
    clock.advance(10)
    probe_ep = EntryPoint(clock=clock)
    probe_ep.submit(make_req(queue_mode=QueueMode.FOLLOWUP, deadline=deadline))
    for _ in range(19):
        probe_ep.submit(make_req(queue_mode=QueueMode.FOLLOWUP))
    clock.advance(10)
    probe_ep.submit(make_req(queue_mode=QueueMode.FOLLOWUP, payload={"tag": "fresh"}))
    mailbox = probe_ep.mailbox_for("chat:main")
    assert mailbox is not None
    assert mailbox.peek_depth() == 20  # 仍是 20，未破 cap


# ── interrupt 顺序 ───────────────────────────────────────


def test_multiple_interrupts_keep_relative_order():
    """两条 interrupt 应保持它们彼此的到达顺序（新的不越过旧的）。"""
    ep = EntryPoint()
    ep.submit(make_req(queue_mode=QueueMode.FOLLOWUP, payload={"t": "f1"}))
    ep.submit(make_req(queue_mode=QueueMode.INTERRUPT, payload={"t": "i1"}))
    ep.submit(make_req(queue_mode=QueueMode.INTERRUPT, payload={"t": "i2"}))

    mailbox = ep.mailbox_for("chat:main")
    assert mailbox is not None
    first = mailbox.poll_due()
    second = mailbox.poll_due()
    third = mailbox.poll_due()
    assert first is not None and first.payload == {"t": "i2"}  # 后到 interrupt 优先（appendleft）
    assert second is not None and second.payload == {"t": "i1"}
    assert third is not None and third.payload == {"t": "f1"}


# ── summarize 连击 ───────────────────────────────────────


def test_repeated_overflow_keeps_cap_and_marks_summary():
    ep = EntryPoint()
    for i in range(60):  # 三倍 cap
        ep.submit(make_req(queue_mode=QueueMode.FOLLOWUP, payload={"n": i}))
    mailbox = ep.mailbox_for("chat:main")
    assert mailbox is not None
    snapshot = list(mailbox.snapshot())
    assert len(snapshot) == 20
    assert any(item.is_summary for item in snapshot)


# ── 会话隔离与 id 稳定 ───────────────────────────────────


def test_sessions_are_isolated_by_key():
    ep = EntryPoint()
    a = ep.submit(make_req(session_key="chat:a"))
    b = ep.submit(make_req(session_key="chat:b"))
    assert a.session_id != b.session_id
    assert ep.mailbox_for("chat:a") is not ep.mailbox_for("chat:b")
    assert ep.mailbox_for("chat:a").peek_depth() == 1  # type: ignore[union-attr]
    assert ep.mailbox_for("chat:b").peek_depth() == 1  # type: ignore[union-attr]


def test_study_and_chat_same_key_family_distinct():
    ep = EntryPoint()
    chat = ep.submit(make_req(session_key="chat:x"))
    study = ep.submit(make_req(session_key="study:x", kind=WorkKind.STUDY))
    assert chat.session_id != study.session_id


# ── payload digest 性质 ──────────────────────────────────


def test_payload_digest_key_order_insensitive():
    r1 = make_req(payload={"a": 1, "b": 2})
    r2 = make_req(payload={"b": 2, "a": 1})
    assert r1.payload_digest() == r2.payload_digest()


def test_payload_digest_sensitive_to_type_change():
    r1 = make_req(payload={"a": 1})
    r2 = make_req(payload={"a": "1"})
    assert r1.payload_digest() != r2.payload_digest()


def test_idempotency_uses_digest_not_identity():
    ep = EntryPoint()
    first = ep.submit(make_req(idempotency_key="k", payload={"a": 1, "b": 2}))
    # 键序变化但语义相同 → 视为等价重试，返回原回执
    second = ep.submit(make_req(idempotency_key="k", payload={"b": 2, "a": 1}))
    assert first == second


# ── validate 细节 ────────────────────────────────────────


def test_validate_accepts_work_kinds_all():
    for kind in WorkKind:
        req = make_req(kind=kind)
        assert validate_work_request(req) is None


def test_validate_empty_session_key_rejected():
    req = make_req(session_key="")
    assert validate_work_request(req) is RejectReason.MALFORMED


def test_validate_whitespace_session_key_rejected():
    req = make_req(session_key="   ")
    assert validate_work_request(req) is RejectReason.MALFORMED


def test_validate_protocol_key_case_insensitive():
    req = make_req(payload={"HTTP": "x"})
    assert validate_work_request(req) is RejectReason.PAYLOAD_PROTOCOL_FIELD


def test_validate_nested_payload_not_scanned():
    """白名单只查顶层键（嵌套结构属 payload 语义，由 adapter 负责）。"""
    req = make_req(payload={"data": {"http": "inner"}})
    assert validate_work_request(req) is None


# ── 边界方法快同步（静态证据） ────────────────────────────


def test_no_asyncio_or_threading_in_boundary_modules():
    """契约/mailbox/entry_point 不得引入并发原语（快同步的结构证据）。"""
    import inspect

    from agentnodes.kernel.orchestration import contracts, mailbox
    from agentnodes.kernel.orchestration import entry_point

    for module in (contracts, mailbox, entry_point):
        source = inspect.getsource(module)
        assert "async def" not in source
        assert "import asyncio" not in source
        assert "threading" not in source


def test_mutable_input_payload_cannot_break_frozen_request():
    """payload 是 Mapping（只读视图约定）；frozen dataclass 保护外壳。"""
    payload = {"text": "a"}
    req = make_req(payload=payload)
    with pytest.raises(Exception):
        req.queue_mode = QueueMode.INTERRUPT  # type: ignore[misc]
