"""Session lifecycle service (SessionService) —— the session domain owner.

**位置（2026-11-16 批次 B3）**：本模块已下沉到 ``kernel/session/service.py``
（L3 平台层）—— 会话生命周期服务是领域 owner，不属于接口层私有实现。此处是 **re-export 垫片**（L1→L3 是合法方向），
保证老调用点零改动。

新代码请用 ``from agentnodes.kernel.session.service import ...``。
"""

from agentnodes.kernel.session.service import (  # noqa: F401
    logger,
    MAX_HISTORY_CHARS,
    _QUEUE_LIMIT,
    _CHAT_ATTEMPT_TIMEOUT,
    SessionService,
    _utc_now_iso,
    auto_title_session,
    _friendly_error_text,
    _bootstrap_workspace,
    _build_attempt_metrics,
    _LoopEventForwarder,
    _plan_mode_allowed_tools,
    _resolve_loop_prompt,
    _thinking_instructions,
    _build_tool_indexes,
    _convert_one_history_message,
    _decide_kept_compactions,
    _defer_or_drop_tool_message,
    _extract_tool_calls,
    _find_compaction_indices,
    _hidden_until_seq,
    _HistoryConvertContext,
    _trim_history_groups,
    _accumulate_part,
    _accumulate_text_delta,
    _accumulate_text_ended,
    _accumulate_text_started,
    _accumulate_thinking_delta,
    _accumulate_tool_result,
    _build_tool_call_part
)

__all__ = [
    "logger",
    "MAX_HISTORY_CHARS",
    "_QUEUE_LIMIT",
    "_CHAT_ATTEMPT_TIMEOUT",
    "SessionService",
    "_utc_now_iso",
    "auto_title_session",
    "_friendly_error_text",
    "_bootstrap_workspace",
    "_build_attempt_metrics",
    "_LoopEventForwarder",
    "_plan_mode_allowed_tools",
    "_resolve_loop_prompt",
    "_thinking_instructions",
    "_build_tool_indexes",
    "_convert_one_history_message",
    "_decide_kept_compactions",
    "_defer_or_drop_tool_message",
    "_extract_tool_calls",
    "_find_compaction_indices",
    "_hidden_until_seq",
    "_HistoryConvertContext",
    "_trim_history_groups",
    "_accumulate_part",
    "_accumulate_text_delta",
    "_accumulate_text_ended",
    "_accumulate_text_started",
    "_accumulate_thinking_delta",
    "_accumulate_tool_result",
    "_build_tool_call_part"
]
