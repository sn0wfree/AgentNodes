"""Session 领域内核（L3）。

**演进（2026-11-16）**：会话生命周期开始从 `interfaces/api/session/`（L1）下沉到本包（L3）。
理由：会话是**领域概念**，不是接口层私有实现。下沉后 L1 只保留薄适配层，
CLI / MCP / TUI / web 四通道共用同一套会话能力。

当前状态（批次 A 已完成）：
  · **词汇表已就位**：`models` / `projection_models` / `part_accumulate`
    （数据模型与纯计算，无项目内依赖）
  · `unified` 仍是只读浏览门面（直连 SQLite，批次 D 改为走 store）
  · 行为层（service / store / projector / db）**尚未迁移**，仍在 L1（批次 B）

**旧路径仍然可用**：`interfaces/api/session/{models,projection_models,part_accumulate}.py`
是 re-export 垫片，与这里指向同一对象。
"""
from __future__ import annotations

from .models import (
    Attempt,
    AttemptId,
    AttemptStatus,
    Message,
    MessageId,
    Session,
    SessionId,
    SessionStatus,
    _utc_now_iso,
)
from .part_accumulate import (
    _accumulate_part,
    _accumulate_text_delta,
    _accumulate_text_ended,
    _accumulate_text_started,
    _accumulate_thinking_delta,
    _accumulate_tool_result,
    _build_tool_call_part,
)
from .projection_models import ProjectedMessage, ProjectedPart, ProjectedSession
from .unified import (
    delete_session,
    get_messages,
    get_session,
    list_sessions,
    search_messages,
)

__all__ = [
    # 数据模型
    "Attempt",
    "AttemptId",
    "AttemptStatus",
    "Message",
    "MessageId",
    "Session",
    "SessionId",
    "SessionStatus",
    "_utc_now_iso",
    # 投影模型
    "ProjectedMessage",
    "ProjectedPart",
    "ProjectedSession",
    # part 累积
    "_accumulate_part",
    "_accumulate_text_delta",
    "_accumulate_text_ended",
    "_accumulate_text_started",
    "_accumulate_thinking_delta",
    "_accumulate_tool_result",
    "_build_tool_call_part",
    # 只读浏览门面（批次 D 前仍是直连 DB）
    "delete_session",
    "get_messages",
    "get_session",
    "list_sessions",
    "search_messages",
]
