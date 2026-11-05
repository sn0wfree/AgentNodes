"""fencing（A3）—— per-session 写栅栏：activeRunId 认领 + 提交校验。

解决的问题（问题⑧，现有代码 0 实现的唯一核心件）：
被取代的 run（同 turn 重试、interrupt 后的新 run）不得把陈旧数据写入存储。

模型（对照 OpenClaw）：
- 认领：`claim(session_id, run_id)` 把该 session 的 activeRunId 换成新 run，
  返回 FenceToken（认领凭证，幂等：同 run 重复认领返回同 token）。
- 校验：`validate(token)` 只有当 token 仍指向当前 activeRunId 时为真。
  旧 run 的提交（token 已被超越）→ False，调用方拒绝落库。
- 释放：`release(token)` turn 正常收尾时清栅栏。

不变量 8（P-4）：同一 turn 同时至多一个活跃 run——claim 即超越，
旧 run 的下一次 validate 必然失败。
"""
from __future__ import annotations

import threading
from dataclasses import dataclass


@dataclass(frozen=True)
class FenceToken:
    """认领凭证：run 的写权限凭据（提交时出示）。"""

    session_id: str
    run_id: str
    claim_seq: int  # 全局单调认领序号（审计/排序用）


@dataclass(frozen=True)
class FenceVerdict:
    """提交校验裁决。"""

    ok: bool
    reason: str | None = None

    @staticmethod
    def allow() -> "FenceVerdict":
        return FenceVerdict(ok=True)

    @staticmethod
    def deny(reason: str) -> "FenceVerdict":
        return FenceVerdict(ok=False, reason=reason)


class SessionFence:
    """全局栅栏管理器：session_id → activeRunId（线程安全）。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._active: dict[str, str] = {}  # session_id -> run_id
        self._seq = 0

    def claim(self, session_id: str, run_id: str) -> FenceToken:
        """认领写权限：超越该 session 的当前 activeRunId。"""
        with self._lock:
            self._seq += 1
            self._active[session_id] = run_id
            return FenceToken(session_id=session_id, run_id=run_id, claim_seq=self._seq)

    def validate(self, token: FenceToken) -> FenceVerdict:
        """提交校验：token 是否仍是当前 active。"""
        with self._lock:
            active = self._active.get(token.session_id)
        if active is None:
            return FenceVerdict.deny("fence: no active run（turn 已收尾）")
        if active != token.run_id:
            return FenceVerdict.deny(
                f"fence: superseded by {active}（陈旧 run 提交被拦）"
            )
        return FenceVerdict.allow()

    def release(self, token: FenceToken) -> None:
        """turn 收尾释放：仅当 token 仍是 active（避免误释放后继者）。"""
        with self._lock:
            if self._active.get(token.session_id) == token.run_id:
                del self._active[token.session_id]

    def active_run(self, session_id: str) -> str | None:
        with self._lock:
            return self._active.get(session_id)
