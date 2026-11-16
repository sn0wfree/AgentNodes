"""Session data models for the core Session, Message, and Attempt entities.

**位置（2026-11-16）**：本模块已下沉到 `kernel/session/models.py`（L3 平台层）——
会话数据模型是**领域词汇表**，不属于接口层私有实现。
此处是 **re-export 垫片**（L1→L3 是合法方向），保证老调用点零改动。

新代码请用 `from agentnodes.kernel.session.models import ...`。
"""

from agentnodes.kernel.session.models import (  # noqa: F401
    Attempt,
    AttemptId,
    AttemptStatus,
    Message,
    MessageId,
    Session,
    SessionId,
    SessionStatus,
    _utc_now_iso,
)

__all__ = [
    "Attempt",
    "AttemptId",
    "AttemptStatus",
    "Message",
    "MessageId",
    "Session",
    "SessionId",
    "SessionStatus",
    "_utc_now_iso",
]
