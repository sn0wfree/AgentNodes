"""Event -> ProjectedSession handlers (pure updates, zero IO).

**位置（2026-11-16）**：本模块已下沉到 ``kernel/session/projection_handlers.py``（L3 平台层）
—— 会话领域实现不属于接口层私有代码。
此处是 **re-export 垫片**（L1→L3 是合法方向），保证老调用点零改动。

新代码请用 ``from agentnodes.kernel.session.projection_handlers import ...``。
"""

from agentnodes.kernel.session.projection_handlers import (  # noqa: F401
    HANDLERS,
    apply_event,
    logger,
    _GOAL_CHANGE_LABELS,
    _ensure_assistant_message,
    _format_goal_content,
    _on_assistant_message,
    _on_chart,
    _on_compact,
    _on_file_edit,
    _on_goal_updated,
    _on_html,
    _on_image,
    _on_message_received,
    _on_table,
    _on_text_delta,
    _on_text_ended,
    _on_text_started,
    _on_thinking_delta,
    _on_thinking_done,
    _on_thinking_end,
    _on_thinking_start,
    _on_tool_call,
    _on_tool_progress,
    _on_tool_result,
)

__all__ = [
    "HANDLERS",
    "apply_event",
    "logger",
    "_GOAL_CHANGE_LABELS",
    "_ensure_assistant_message",
    "_format_goal_content",
    "_on_assistant_message",
    "_on_chart",
    "_on_compact",
    "_on_file_edit",
    "_on_goal_updated",
    "_on_html",
    "_on_image",
    "_on_message_received",
    "_on_table",
    "_on_text_delta",
    "_on_text_ended",
    "_on_text_started",
    "_on_thinking_delta",
    "_on_thinking_done",
    "_on_thinking_end",
    "_on_thinking_start",
    "_on_tool_call",
    "_on_tool_progress",
    "_on_tool_result",
]
