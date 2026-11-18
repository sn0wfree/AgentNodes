# -*- coding: utf-8 -*-
"""活跃 Attempt 查询用例（顺序④，裁决 4：2026-11-18）。

裁决 4：Attempt 是**持久化领域记录**，不等同 Gateway Run——Gateway 管实时
queued/running/cancel 状态，Attempt 保存可恢复的身份、输入与终态。
`/attempts` 最终由 **apps/chat 的查询用例**组合"存储记录 × Gateway 实时
状态"，**API router 不负责拼接**。

本模块因此只做一件事：给定存储与 Gateway 句柄，产出 reload 恢复所需的
活跃 Attempt 视图。资源（store / gateway）由入口（组合根）传入，本模块
不取单例、不做鉴权、不碰 HTTP。

兼容不变量（迁移前后必须一致，由 router 契约测试守护）：
- 只返回 PENDING / RUNNING / FAILED 三种状态的记录，oldest first；
- FAILED 最多 5 条；
- 非 FAILED 的记录必须经 ``gateway.attempt_state`` 确认仍在实时状态里，
  否则丢弃（进程重启后的僵尸 pending/running 行不得误报为活跃）；
- ``prompt`` 截断 200 字符；``attempt_id`` 与 ``run_id`` 保持不同。
"""
from __future__ import annotations

from typing import Any

from agentnodes.kernel.session.models import AttemptStatus

__all__ = ["QUERY_STATUSES", "MAX_FAILED", "PROMPT_LIMIT", "query_live_attempts"]

#: 参与 reload 恢复的状态集合（与迁移前 router 内联逻辑一致）。
QUERY_STATUSES: tuple[str, ...] = (
    AttemptStatus.PENDING.value,
    AttemptStatus.RUNNING.value,
    AttemptStatus.FAILED.value,
)

#: 最多返回的失败记录数（前端只展示最近几次失败）。
MAX_FAILED = 5

#: prompt 在响应里的截断长度。
PROMPT_LIMIT = 200


def query_live_attempts(
    session_id: str,
    *,
    store: Any,
    gateway: Any,
) -> list[dict[str, Any]]:
    """组合存储快照与 Gateway 实时状态，返回活跃 Attempt 视图。

    Args:
        session_id: 目标会话。
        store: SessionStore 形状的对象（需有 ``list_attempts_by_status``）。
        gateway: Gateway 形状的对象（需有 ``attempt_state(session_id, attempt_id)``）。

    Returns:
        ``[{attempt_id, message_id, status, prompt, created_at, error}, ...]``，
        按 created_at 升序。
    """
    attempts = store.list_attempts_by_status(session_id, list(QUERY_STATUSES))
    live: list[dict[str, Any]] = []
    failed_count = 0
    for attempt in attempts:
        if attempt.status == AttemptStatus.FAILED:
            failed_count += 1
            if failed_count > MAX_FAILED:
                continue
            status = "failed"
        else:
            status = gateway.attempt_state(session_id, str(attempt.attempt_id))
            if status is None:
                # 僵尸行：存储里还挂着 pending/running，但执行上下文已丢
                # （进程重启）。不得当作活跃返回。
                continue
        live.append({
            "attempt_id": str(attempt.attempt_id),
            "message_id": str(attempt.message_id or ""),
            "status": status,
            "prompt": attempt.prompt[:PROMPT_LIMIT] if attempt.prompt else "",
            "created_at": attempt.created_at,
            "error": attempt.error or "",
        })
    return live
