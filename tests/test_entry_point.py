"""EntryPoint 专项测试 —— 分桶、惰性建会话、幂等索引、多会话管理。

契约测试覆盖了「入口拒绝无副作用」等边界；这里补 EntryPoint 自身的
状态管理语义（此前散落在别的测试里）。
"""
from __future__ import annotations

import pytest

from strategy_research.kernel.orchestration import (
    EntryPoint,
    QueueMode,
    RejectReason,
    SourceKind,
    WorkKind,
    WorkRequest,
)


def req(
    *,
    key: str = "chat:s",
    text: str = "q",
    idem: str | None = None,
    mode: QueueMode | None = None,
) -> WorkRequest:
    return WorkRequest(
        kind=WorkKind.CHAT,
        source=SourceKind.CLI,
        session_key=key,
        payload={"text": text},
        idempotency_key=idem,
        queue_mode=mode,
    )


# ── 分桶 ────────────────────────────────────────────────


def test_mailboxes_created_per_session_key():
    ep = EntryPoint(sink=lambda k, d: None)
    ep.submit(req(key="chat:a"))
    ep.submit(req(key="chat:b"))
    assert ep.mailbox_for("chat:a") is not ep.mailbox_for("chat:b")


def test_same_key_reuses_mailbox():
    ep = EntryPoint(sink=lambda k, d: None)
    ep.submit(req(key="chat:x"))
    first = ep.mailbox_for("chat:x")
    ep.submit(req(key="chat:x"))
    assert ep.mailbox_for("chat:x") is first


def test_mailbox_for_unknown_key_is_none():
    ep = EntryPoint(sink=lambda k, d: None)
    assert ep.mailbox_for("chat:never") is None


def test_iter_mailboxes_snapshot():
    ep = EntryPoint(sink=lambda k, d: None)
    ep.submit(req(key="chat:a"))
    ep.submit(req(key="chat:b"))
    snapshot = list(ep.iter_mailboxes())
    assert len(snapshot) == 2
    assert {k for k, _ in snapshot} == {"chat:a", "chat:b"}
    # 返回 tuple（快照语义：外部增删不影响已取出的列表）
    assert isinstance(ep.iter_mailboxes(), tuple)


# ── 惰性建会话 ──────────────────────────────────────────


def test_session_id_created_lazily():
    ep = EntryPoint(sink=lambda k, d: None)
    assert ep.session_id_for("chat:s") is None
    receipt = ep.submit(req())
    assert receipt.session_id
    assert ep.session_id_for("chat:s") == receipt.session_id


def test_session_id_stable_across_submissions():
    ep = EntryPoint(sink=lambda k, d: None)
    r1 = ep.submit(req(key="chat:s"))
    r2 = ep.submit(req(key="chat:s"))
    assert r1.session_id == r2.session_id


def test_session_ids_unique_across_keys():
    ep = EntryPoint(sink=lambda k, d: None)
    r1 = ep.submit(req(key="chat:a"))
    r2 = ep.submit(req(key="chat:b"))
    assert r1.session_id != r2.session_id


def test_rejected_request_creates_no_session():
    ep = EntryPoint(sink=lambda k, d: None)
    receipt = ep.submit(req(key="chat:", mode=QueueMode.FOLLOWUP))
    assert not receipt.accepted
    assert ep.session_id_for("chat:") is None
    assert ep.mailbox_for("chat:") is None


# ── 幂等索引 ────────────────────────────────────────────


def test_idempotency_index_records_only_accepted():
    ep = EntryPoint(sink=lambda k, d: None)
    # 被拒的请求不该进幂等索引
    ep.submit(req(idem="k1", mode=QueueMode.COLLECT))
    # 之后同键同载荷仍应能成功（未被占用）
    receipt = ep.submit(req(idem="k1"))
    assert receipt.accepted


def test_idempotency_without_key_no_dedupe():
    ep = EntryPoint(sink=lambda k, d: None)
    r1 = ep.submit(req(text="same"))
    r2 = ep.submit(req(text="same"))
    # 都能入队（无幂等键 → 不合并）
    mailbox = ep.mailbox_for("chat:s")
    assert mailbox is not None and mailbox.peek_depth() == 2
    assert r1.accepted and r2.accepted


def test_idempotency_key_scope_is_global_across_sessions():
    """幂等键是全局命名空间（同键跨会话也判重）。"""
    ep = EntryPoint(sink=lambda k, d: None)
    r1 = ep.submit(req(key="chat:a", idem="shared"))
    r2 = ep.submit(req(key="chat:b", idem="shared"))
    assert r1 == r2  # 后者视为重放


def test_idempotency_digest_sensitive_to_text():
    ep = EntryPoint(sink=lambda k, d: None)
    ep.submit(req(text="a", idem="k"))
    conflict = ep.submit(req(text="b", idem="k"))
    assert not conflict.accepted
    assert conflict.reason is RejectReason.IDEMPOTENCY_CONFLICT


# ── 校验透传 ────────────────────────────────────────────


@pytest.mark.parametrize("bad_key", ["", "   ", "nocolon", "chat:", ":name"])
def test_malformed_keys_rejected(bad_key):
    ep = EntryPoint(sink=lambda k, d: None)
    receipt = ep.submit(req(key=bad_key))
    assert not receipt.accepted
    assert receipt.reason is RejectReason.MALFORMED


def test_collect_rejected_before_mailbox_creation():
    ep = EntryPoint(sink=lambda k, d: None)
    receipt = ep.submit(req(mode=QueueMode.COLLECT))
    assert receipt.reason is RejectReason.UNSUPPORTED_QUEUE_MODE
    assert ep.mailbox_for("chat:s") is None


# ── cap 透传 ────────────────────────────────────────────


def test_custom_cap_respected():
    ep = EntryPoint(cap=3, sink=lambda k, d: None)
    for i in range(6):
        ep.submit(req(text=f"q{i}", mode=QueueMode.FOLLOWUP, key="chat:c"))
    mailbox = ep.mailbox_for("chat:c")
    assert mailbox is not None and mailbox.peek_depth() <= 3
