"""Todo / task tracking tools (opencode-style).

Tools:
    TodoWriteTool  - replace the session's todo list (full snapshot)

The list is held in a process-wide per-session cache (``TodoStore``) so
it survives across attempts within a session; every write emits a
``todo_updated`` SSE snapshot so the frontend drawer stays in sync.
"""

from __future__ import annotations

import json
import logging
import threading
from typing import Any

from ..tools import EFFECT_DB, BaseTool, ToolContext
from .utils import err_actionable, try_unwrap_list

logger = logging.getLogger(__name__)

VALID_STATUSES = ("pending", "in_progress", "completed")


# ── Per-session todo cache ───────────────────────────────────────────


class TodoStore:
    """Process-wide per-session todo lists (single source of truth).

    In-memory only for now; not persisted to disk (a server restart
    drops the lists). Keyed by session id.
    """

    _lock = threading.Lock()
    _todos: dict[str, list[dict[str, Any]]] = {}

    @classmethod
    def get(cls, session_id: str) -> list[dict[str, Any]]:
        with cls._lock:
            return list(cls._todos.get(session_id, []))

    @classmethod
    def set(cls, session_id: str, todos: list[dict[str, Any]]) -> None:
        with cls._lock:
            if todos:
                cls._todos[session_id] = list(todos)
            else:
                cls._todos.pop(session_id, None)

    @classmethod
    def clear(cls, session_id: str) -> None:
        with cls._lock:
            cls._todos.pop(session_id, None)

    @classmethod
    def reset_all(cls) -> None:
        """Test helper: drop every session's todos."""
        with cls._lock:
            cls._todos.clear()


def _format_todos_snapshot(todos: list[dict[str, Any]]) -> str:
    """Render the todo list as a compact prompt block."""
    lines = ["<current-todos>"]
    if not todos:
        lines.append("（无）")
    for i, t in enumerate(todos, start=1):
        lines.append(f"{i}. [{t['status']}] {t['content']} (id={t['id']})")
    lines.append("</current-todos>")
    return "\n".join(lines)


def _normalize_todos(raw: Any) -> tuple[list[dict[str, Any]] | None, str]:
    """Validate/normalize the todos parameter.

    Returns (todos, "") on success or (None, error_message).
    """
    if not isinstance(raw, list):
        return None, "parameter 'todos' must be a list of {id, content, status}"
    out: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            return None, f"invalid todo item (not an object): {item!r}"
        tid = item.get("id")
        content = item.get("content")
        status = item.get("status")
        if not isinstance(tid, str) or not tid:
            return None, f"todo item missing string 'id': {item!r}"
        if not isinstance(content, str) or not content:
            return None, f"todo item missing string 'content': {item!r}"
        if status not in VALID_STATUSES:
            return None, (
                f"todo item status must be one of {list(VALID_STATUSES)}, "
                f"got {status!r} for id={tid!r}"
            )
        out.append({"id": tid, "content": content, "status": status})
    return out, ""


# ── TodoWriteTool ────────────────────────────────────────────────────


class TodoWriteTool(BaseTool):
    """维护当前会话的任务清单（全量替换）。

    # ── 工具说明书 ──────────────────────────────
    # 版本: 1.0.0
    # 变更: v1.0.0 新增 (opencode 风格 todo 跟踪)
    #
    # ## 用途
    # 为多步任务创建/更新任务清单。每次调用传入完整列表（全量替换），
    # 用于跟踪长程任务的进度；前端会实时展示任务抽屉。
    #
    # ## 参数
    # - todos: 任务数组 (必填) — 每项 {id, content, status}；
    #   status ∈ pending | in_progress | completed
    #
    # ## 示例
    # {"todos": [{"id": "t1", "content": "加载数据", "status": "in_progress"},
    #            {"id": "t2", "content": "计算因子", "status": "pending"}]}
    #
    # ## 边界
    # - 全量替换语义：列表内移除某 id 即视为删除该任务
    # - 空数组清空任务清单
    # - 长任务开始前创建清单，每完成一步更新对应项状态
    #
    # ## 错误处理范式
    # - todos 非数组 / 缺 id / 缺 content / status 非法 → 明确报错
    # - 错误为确定性错误，修正参数后重试
    #
    # ## 相关工具
    # create_goal / add_evidence: 研究目标跟踪; delegate_to_agent: 子任务委派
    # ─────────────────────────────────────────────
    """

    name = "todowrite"
    category = "agent"
    # Mutates shared todo state → serial execution (avoids race with
    # parallel readonly tools racing on the snapshot).
    effects = frozenset({EFFECT_DB})
    description = (
        "维护当前会话的任务清单（全量替换）。用于长程任务跟踪："
        "开始前创建清单，每完成一步更新状态，全部完成后再结束回复。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "todos": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "content": {"type": "string"},
                        "status": {"type": "string"},
                    },
                    "required": ["id", "content", "status"],
                },
                "description": "完整的任务列表（全量替换语义）",
            },
        },
        "required": ["todos"],
    }

    def execute(self, ctx: ToolContext, **kwargs: Any) -> str:
        # Session attribution (B5 B-a): explicit kwarg wins (the AgentLoop
        # path injects it via _build_tool_kwargs); the ToolContext carried
        # by the react path is the fallback; bare callers (scripts/REPL)
        # stay in the shared "default" bucket.
        session_id = str(
            kwargs.get("session_id")
            or getattr(ctx, "session_id", None)
            or "default"
        )
        emit_event = kwargs.get("emit_event")

        raw = kwargs.get("todos")
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                raw = None
        if isinstance(raw, dict):
            unwrapped = try_unwrap_list(raw)
            if unwrapped is not None:
                raw = unwrapped

        if raw is None:
            return err_actionable(
                "missing required parameter 'todos'",
                expected="list of {id, content, status}",
                tool=self.name,
            )

        todos, error = _normalize_todos(raw)
        if error:
            return err_actionable(error, received=raw, tool=self.name)
        todos = todos or []

        TodoStore.set(session_id, todos)

        # Emit full snapshot so the frontend drawer updates live
        if emit_event is not None:
            try:
                emit_event("todo_updated", {"todos": todos})
            except Exception:  # noqa: BLE001
                logger.debug("emit_event failed for todo_updated", exc_info=True)

        return json.dumps(
            {
                "status": "ok",
                "count": len(todos),
                "todos": todos,
            },
            ensure_ascii=False,
        )
