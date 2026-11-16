"""Projected state models (Level 3 read-side view).

**位置（2026-11-16）**：本模块已下沉到 `kernel/session/projection_models.py`（L3）。
此处是 **re-export 垫片**（L1→L3 合法方向），保证老调用点零改动。

新代码请用 `from agentnodes.kernel.session.projection_models import ...`。
"""

from agentnodes.kernel.session.projection_models import (  # noqa: F401
    ProjectedMessage,
    ProjectedPart,
    ProjectedSession,
)

__all__ = [
    "ProjectedMessage",
    "ProjectedPart",
    "ProjectedSession",
]
