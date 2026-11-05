"""E1 统一入口契约（v6 两态版）。

依据 docs/design/entry-controller-contract.md（v6，取代 v5.1 place()/WorkItem/五态回执）。

设计要点（与文档一一对应）：
- WorkRequest 是唯一对外契约；payload 不得携带协议概念（白名单不变量）。
- 回执只有 accepted / rejected(reason) 两态——收下即进 mailbox，
  其余命运（准入/排队/降级/过期）全部走事件流，不再同步返回。
- 身份四层派生见 P-4（p3-p5-data-plane-design.md）：本模块只到 session 解析，
  turn_id/run_id 由 SessionController（A2）准入时签发。
- 全部方法快同步：无 await、无网络 IO；异步性只存在于事件流订阅。

这个模块刻意不含任何 I/O 与存储：mailbox 的持久化属于 0.4 数据面，
这里只定义数据形状与纯内存语义，存储适配器后续实现 PersistenceContract。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Mapping

# 事件协议版本契约已下沉 foundation（2026-11-03 W4b）：
# 它是纯协议、零业务，却被 L4 的 storage 与 L3 的 events/agent 使用；
# 留在 L3 会造成 L4→L3 上行违规。此处 re-export 保持本模块对外 API 不变
# （既有 `from .contracts import PROTOCOL_VERSION` 调用方无需改动）。
from strategy_research.foundation.protocol import (  # noqa: F401
    PROTOCOL_VERSION,
    ProtocolVersionError,
    check_event_version,
)


class WorkKind(str, Enum):
    """工单种类。session 不分 turn 类型（P-2 定案），kind 只影响 payload 窄化与路由。"""

    CHAT = "chat"
    STUDY = "study"
    AUTOMATION = "automation"
    DIRECTOR_PROPOSE = "director_propose"


class SourceKind(str, Enum):
    """来源渠道。决定默认 queue_mode（五行表，见契约 §2.1）。"""

    CLI = "cli"
    API_CHAT = "api_chat"
    SCHEDULER = "scheduler"
    MCP = "mcp"
    DIRECTOR = "director"


class QueueMode(str, Enum):
    """运行中消息的四种处置（OpenClaw 语义，collect v1 拒绝留位）。"""

    STEER = "steer"
    FOLLOWUP = "followup"
    COLLECT = "collect"
    INTERRUPT = "interrupt"


class RejectReason(str, Enum):
    """入口拒绝原因（两级拒绝的第一级：入口侧，无副作用直接返回）。"""

    MALFORMED = "malformed"
    UNSUPPORTED_QUEUE_MODE = "unsupported_queue_mode"
    IDEMPOTENCY_CONFLICT = "idempotency_conflict"
    PAYLOAD_PROTOCOL_FIELD = "payload_protocol_field"


DEFAULT_QUEUE_MODE: dict[SourceKind, QueueMode] = {
    SourceKind.CLI: QueueMode.FOLLOWUP,
    SourceKind.API_CHAT: QueueMode.STEER,
    SourceKind.SCHEDULER: QueueMode.INTERRUPT,
    SourceKind.MCP: QueueMode.FOLLOWUP,
    SourceKind.DIRECTOR: QueueMode.FOLLOWUP,
}

# payload 白名单不变量：这些协议概念键禁止出现（渠道 adapter 负责剥除）。
_PROTOCOL_KEYS = frozenset(
    {"http", "https", "sse", "websocket", "transport", "raw_request", "headers"}
)

# v1 恒 None；B1（ExecutionProfile）启用后在此填字段位。
@dataclass(frozen=True)
class ExecutionSpec:
    pass


@dataclass(frozen=True)
class BudgetSpec:
    """预算上限声明；None 字段=不设限，缺省值由会话默认补齐（ConfigHub）。"""

    max_tokens: int | None = None
    max_llm_calls: int | None = None
    wallclock_seconds: float | None = None


@dataclass(frozen=True)
class WorkRequest:
    """唯一对外契约。frozen：工单提交后不可变（审计与幂等的前提）。"""

    kind: WorkKind
    source: SourceKind
    session_key: str
    payload: Mapping[str, Any] = field(default_factory=dict)
    queue_mode: QueueMode | None = None  # None = 用来源默认（五行表）
    budget: BudgetSpec | None = None
    idempotency_key: str | None = None
    deadline: datetime | None = None
    execution: ExecutionSpec | None = None

    def payload_digest(self) -> str:
        """载荷规范化摘要：幂等比较用（同键同摘要=等价重试）。"""
        canonical = json.dumps(self.payload, sort_keys=True, default=str)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Receipt:
    """两态回执：accepted / rejected(reason)。

    session_id 为惰性创建后回填的实际物理会话 id（uuid，文件名）；
    rejected 时无副作用（不建文件、不进队列、不发事件）。
    """

    accepted: bool
    session_id: str = ""
    reason: RejectReason | None = None

    @staticmethod
    def ok(session_id: str) -> "Receipt":
        return Receipt(accepted=True, session_id=session_id)

    @staticmethod
    def reject(reason: RejectReason) -> "Receipt":
        return Receipt(accepted=False, reason=reason)


def validate_work_request(req: WorkRequest) -> RejectReason | None:
    """入口校验：返回拒绝原因，None=通过。

    校验顺序即失败优先级：结构 → queue_mode → payload 白名单。
    """
    if not req.session_key or not req.session_key.strip():
        return RejectReason.MALFORMED
    # 逻辑会话名约定 <domain>:<name>，且两侧都必须非空
    #（"chat:" / ":main" / "a:b:c" 之外的空段都视为 malformed）
    domain, sep, name = req.session_key.partition(":")
    if not sep or not domain.strip() or not name.strip():
        return RejectReason.MALFORMED
    if req.queue_mode is QueueMode.COLLECT:
        # 枚举位保留，v1 拒绝（启用条件见 queue-mode 调研 §4）
        return RejectReason.UNSUPPORTED_QUEUE_MODE
    lowered = {str(k).lower() for k in req.payload}
    if lowered & _PROTOCOL_KEYS:
        return RejectReason.PAYLOAD_PROTOCOL_FIELD
    return None
