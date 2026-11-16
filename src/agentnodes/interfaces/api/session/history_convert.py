"""W-split: message to LLM history conversion (from SessionService).

**位置（2026-11-16）**：本模块已下沉到 ``kernel/session/history_convert.py``（L3 平台层）
—— 会话领域实现不属于接口层私有代码。
此处是 **re-export 垫片**（L1→L3 是合法方向），保证老调用点零改动。

新代码请用 ``from agentnodes.kernel.session.history_convert import ...``。
"""

from agentnodes.kernel.session.history_convert import (  # noqa: F401
    MAX_HISTORY_CHARS,
    logger,
    _HistoryConvertContext,
    _append_tool_results,
    _build_tool_indexes,
    _convert_one_history_message,
    _decide_kept_compactions,
    _defer_or_drop_tool_message,
    _extract_tool_calls,
    _find_compaction_indices,
    _hidden_until_seq,
    _trim_history_groups,
)

__all__ = [
    "MAX_HISTORY_CHARS",
    "logger",
    "_HistoryConvertContext",
    "_append_tool_results",
    "_build_tool_indexes",
    "_convert_one_history_message",
    "_decide_kept_compactions",
    "_defer_or_drop_tool_message",
    "_extract_tool_calls",
    "_find_compaction_indices",
    "_hidden_until_seq",
    "_trim_history_groups",
]
