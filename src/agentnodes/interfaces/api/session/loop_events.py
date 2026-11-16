"""W-split: loop event forwarding helpers (from SessionService).

**位置（2026-11-16 批次 B3）**：本模块已下沉到 ``kernel/session/loop_events.py``
（L3 平台层）—— loop 事件转发是会话域行为。此处是 **re-export 垫片**（L1→L3 是合法方向），
保证老调用点零改动。

新代码请用 ``from agentnodes.kernel.session.loop_events import ...``。
"""

from agentnodes.kernel.session.loop_events import (  # noqa: F401
    logger,
    _build_attempt_metrics,
    _LoopEventForwarder,
    _bootstrap_workspace,
    _PLAN_READONLY_TOOLS,
    _plan_mode_allowed_tools,
    _thinking_instructions,
    _resolve_loop_prompt
)

__all__ = [
    "logger",
    "_build_attempt_metrics",
    "_LoopEventForwarder",
    "_bootstrap_workspace",
    "_PLAN_READONLY_TOOLS",
    "_plan_mode_allowed_tools",
    "_thinking_instructions",
    "_resolve_loop_prompt"
]
