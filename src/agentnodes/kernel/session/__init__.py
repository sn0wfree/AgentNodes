"""Session 领域内核（L3）。

**演进（2026-11-16）**：会话生命周期开始从 `interfaces/api/session/`（L1）下沉到本包（L3）。
理由：会话是**领域概念**，不是接口层私有实现。下沉后 L1 只保留薄适配层，
CLI / MCP / TUI / web 四通道共用同一套会话能力。

当前状态：
  · **批次 A**：词汇表 `models` / `projection_models` / `part_accumulate`
    （数据模型与纯计算，无项目内依赖）
  · **批次 B1**：叶子 `projection_handlers` / `projection_store` / `history_convert` /
    `trace_projection` / `backfill_event_log`（无出向相对依赖）
  · **批次 B2**：`db`（← `interfaces/api/session/web_session_db.py`）——
    schema / 迁移 / 连接 / 行映射。HTTP 鉴权部分 `_fetch_session_owned`
    留在 L1 路由层，本包因此**零 web 框架依赖**
  · `reader`（← `unified`，2026-11-16 C2' 改名）仍是只读浏览门面
    （直连 SQLite，批次 D 评估改走 store）
  · 行为层（`service` / `store` / `projector`）**尚未迁移**，仍在 L1（批次 B3）

**旧路径仍然可用**：`interfaces/api/session/*.py` 是 re-export 垫片，
与这里指向同一对象（契约测试 `tests/test_session_kernel_migration.py` 钉住）。
"""
from __future__ import annotations

from agentnodes.kernel.session.backfill_event_log import backfill_event_log
from agentnodes.kernel.session.db import ensure_session_row
from agentnodes.kernel.session.history_convert import MAX_HISTORY_CHARS
from agentnodes.kernel.session.loop_events import (
    _bootstrap_workspace,
    _build_attempt_metrics,
    _LoopEventForwarder,
    _PLAN_READONLY_TOOLS,
    _plan_mode_allowed_tools,
    _resolve_loop_prompt,
    _thinking_instructions,
)
from agentnodes.kernel.session.models import (
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
from agentnodes.kernel.session.part_accumulate import (
    _accumulate_part,
    _accumulate_text_delta,
    _accumulate_text_ended,
    _accumulate_text_started,
    _accumulate_thinking_delta,
    _accumulate_tool_result,
    _build_tool_call_part,
)
from agentnodes.kernel.session.projection_handlers import HANDLERS, apply_event
from agentnodes.kernel.session.projection_models import ProjectedMessage, ProjectedPart, ProjectedSession
from agentnodes.kernel.session.projection_store import ProjectionStore
from agentnodes.kernel.session.projector import Projector
from agentnodes.kernel.session.service import (
    SessionService,
    _CHAT_ATTEMPT_TIMEOUT,
    _QUEUE_LIMIT,
    _friendly_error_text,
    auto_title_session,
)
from agentnodes.kernel.session.store import SessionStore, _row_to_attempt
from agentnodes.kernel.session.trace_projection import TraceProjection
from agentnodes.kernel.session.reader import (
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
    # 批次 B1：叶子
    "MAX_HISTORY_CHARS",
    "HANDLERS",
    "apply_event",
    "ProjectionStore",
    "TraceProjection",
    "backfill_event_log",
    # 批次 B2：会话库（schema / 迁移 / 连接 / 行映射）
    "ensure_session_row",
    # 批次 B3：行为簇（service / store / projector / loop_events）
    "SessionService",
    "SessionStore",
    "Projector",
    "auto_title_session",
    # 只读浏览门面 reader（← unified，C2' 改名；批次 D 前仍是直连 DB）
    "delete_session",
    "get_messages",
    "get_session",
    "list_sessions",
    "search_messages",
]
