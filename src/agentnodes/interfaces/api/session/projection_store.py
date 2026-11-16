"""Projected-state persistence: connection, snapshots, flush.

**位置（2026-11-16）**：本模块已下沉到 ``kernel/session/projection_store.py``（L3 平台层）
—— 会话领域实现不属于接口层私有代码。
此处是 **re-export 垫片**（L1→L3 是合法方向），保证老调用点零改动。

新代码请用 ``from agentnodes.kernel.session.projection_store import ...``。
"""

from agentnodes.kernel.session.projection_store import (  # noqa: F401
    ProjectionStore,
    logger,
)

__all__ = [
    "ProjectionStore",
    "logger",
]
