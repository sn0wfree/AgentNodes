"""Event-log projector: event_log → sessions/messages 投影。

**位置（2026-11-16 批次 B3）**：本模块已下沉到 ``kernel/session/projector.py``
（L3 平台层）—— 投影器是领域设施。此处是 **re-export 垫片**（L1→L3 是合法方向），
保证老调用点零改动。

新代码请用 ``from agentnodes.kernel.session.projector import ...``。
"""

from agentnodes.kernel.session.projector import (  # noqa: F401
    logger,
    Projector,
    ProjectedMessage,
    ProjectedPart,
    ProjectedSession
)

__all__ = [
    "logger",
    "Projector",
    "ProjectedMessage",
    "ProjectedPart",
    "ProjectedSession"
]
