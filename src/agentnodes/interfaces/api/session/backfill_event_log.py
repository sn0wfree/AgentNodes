"""Backfill: messages + message_parts → event_log (Level 3, B3 commit 4).

**位置（2026-11-16）**：本模块已下沉到 ``kernel/session/backfill_event_log.py``（L3 平台层）
—— 会话领域实现不属于接口层私有代码。
此处是 **re-export 垫片**（L1→L3 是合法方向），保证老调用点零改动。

新代码请用 ``from agentnodes.kernel.session.backfill_event_log import ...``。
"""

from agentnodes.kernel.session.backfill_event_log import (  # noqa: F401
    backfill_event_log,
    logger,
    main,
    _generate_events_for_session,
)

__all__ = [
    "backfill_event_log",
    "logger",
    "main",
    "_generate_events_for_session",
]
