"""W-split: assistant part accumulation (from SessionService).

**位置（2026-11-16）**：本模块已下沉到 `kernel/session/part_accumulate.py`（L3）。
此处是 **re-export 垫片**（L1→L3 合法方向），保证老调用点零改动。

新代码请用 `from agentnodes.kernel.session.part_accumulate import ...`。
"""

from agentnodes.kernel.session.part_accumulate import (  # noqa: F401
    _accumulate_part,
    _accumulate_text_delta,
    _accumulate_text_ended,
    _accumulate_text_started,
    _accumulate_thinking_delta,
    _accumulate_tool_result,
    _build_tool_call_part,
)

__all__ = [
    "_accumulate_part",
    "_accumulate_text_delta",
    "_accumulate_text_ended",
    "_accumulate_text_started",
    "_accumulate_thinking_delta",
    "_accumulate_tool_result",
    "_build_tool_call_part",
]
