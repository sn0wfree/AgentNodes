"""Frontend-facing session state shaping.

**2026-11-04 做减法批次 2**：goal / workflow 面板已随 apps/goal、
apps/hypothesis、apps/validation 移除。本模块原先的六个 shape 函数
（_build_goal_snapshot / _shape_goal_for_frontend /
_compute_goal_progress_percent / _find_active_workflow_runner /
_shape_workflow_for_frontend / _shape_workflow_for_frontend_from_config）
整体失效，已归档于 testcase/golden/interfaces/（随 web_session.py 快照）。

``GET /{session_id}/state`` 端点保留，goal/workflow 恒为 null——
前端面板将显示空态；chat 主面板由 SSE 供数，不受影响。
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def _shape_agents_for_frontend(
    session_id: str,
    workflow: dict | None,
) -> list[dict]:
    """Build the per-session Agent store entries.

    workflow 机制已移除，恒返回空列表（保留函数以兼容既有调用点，
    避免路由层再改）。SSE 流在 reload 后刷新真实 agent 状态。
    """
    return []
