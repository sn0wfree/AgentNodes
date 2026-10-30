"""EntryPoint —— per-session 分桶门面（协议端点的 E1 形态）。

契约文档 §1/§3：协议端点做 契约校验 + 幂等去重 + 惰性建会话 + 转发 mailbox，
全部快同步。E1 的幂等去重是进程内存实现（E5 换持久化存储，接口不变）；
session 解析由注入的 resolver 完成（E1 用内存表，全局登记索引属 0.4/0.5）。
"""
from __future__ import annotations

import uuid
from typing import Callable, Iterable

from .contracts import Receipt, RejectReason, WorkRequest, validate_work_request
from .mailbox import DEFAULT_AGING_S, DEFAULT_CAP, EventSink, Mailbox


class EntryPoint:
    """门面：session_key → Mailbox 分桶 + 幂等索引 + 惰性会话注册。"""

    def __init__(
        self,
        *,
        cap: int = DEFAULT_CAP,
        aging_s: float = DEFAULT_AGING_S,
        clock: Callable[[], float] | None = None,
        sink: EventSink | None = None,
        is_session_active: Callable[[str], object] | None = None,
    ) -> None:
        import time

        self._clock = clock or time.time
        self._sink = sink
        self._cap = cap
        self._aging_s = aging_s
        # R2.2：steer 路由查询（session_id -> 是否有活跃 run）。生产由
        # Gateway 注入 fence.active_run；None = 全部按「无活跃」处理。
        self._is_active = is_session_active
        self._mailboxes: dict[str, Mailbox] = {}
        self._sessions: dict[str, str] = {}  # session_key -> session_id
        self._idempotency: dict[str, tuple[str, Receipt]] = {}  # key -> (digest, receipt)

    # ── 提交（快同步，契约 §5 不变量 8） ─────────────────

    def submit(self, request: WorkRequest) -> Receipt:
        reason = validate_work_request(request)
        if reason is not None:
            return Receipt.reject(reason)

        if request.idempotency_key:
            digest = request.payload_digest()
            prior = self._idempotency.get(request.idempotency_key)
            if prior is not None:
                prior_digest, prior_receipt = prior
                if prior_digest != digest:
                    return Receipt.reject(RejectReason.IDEMPOTENCY_CONFLICT)
                return prior_receipt  # 等价重试：原回执原样返回

        mailbox = self._mailbox_for(request.session_key)
        # R2.2/R3：steer 路由——fence 域 = 物理 session id（剥 kind 前缀，
        # 与 session_controller._fence_domain 同一约定），chat 与 automation
        # 的活跃 run 由此同域互斥。
        physical = (
            request.session_key.split(":", 1)[1]
            if ":" in request.session_key
            else request.session_key
        )
        if self._is_active is not None:
            active = bool(self._is_active(physical))
        else:
            active = None
        receipt = mailbox.submit(
            request,
            resolve_session=self._resolve_session,
            is_session_active=active,
        )

        if request.idempotency_key and receipt.accepted:
            self._idempotency[request.idempotency_key] = (
                request.payload_digest(),
                receipt,
            )
        return receipt

    # ── 观测/消费（A2/E5 接线） ─────────────────────────

    def mailbox_for(self, session_key: str) -> Mailbox | None:
        return self._mailboxes.get(session_key)

    def iter_mailboxes(self) -> Iterable[tuple[str, Mailbox]]:
        """遍历所有已建 mailbox（消费者清空队列用；快照语义）。"""
        return tuple(self._mailboxes.items())

    def session_id_for(self, session_key: str) -> str | None:
        return self._sessions.get(session_key)

    # ── 内部 ─────────────────────────────────────────────

    def _mailbox_for(self, session_key: str) -> Mailbox:
        mailbox = self._mailboxes.get(session_key)
        if mailbox is None:
            mailbox = Mailbox(
                session_key,
                cap=self._cap,
                aging_s=self._aging_s,
                clock=self._clock,
                sink=self._sink,
            )
            self._mailboxes[session_key] = mailbox
        return mailbox

    def _resolve_session(self, session_key: str) -> str:
        """惰性创建：首次出现分配 uuid 物理会话 id（永不复用，P-4）。"""
        session_id = self._sessions.get(session_key)
        if session_id is None:
            session_id = str(uuid.uuid4())
            self._sessions[session_key] = session_id
        return session_id
