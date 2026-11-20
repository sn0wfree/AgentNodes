# -*- coding: utf-8 -*-
"""goal 完成审计的判据面（L2）——从 slash 命令下沉的领域逻辑。

M5（三前端一核心批 3c）：``/goal evidence`` 的 criterion 引用解析与
``/goal complete`` 的审计行合成原先住在 ``interfaces/cli/commands/
slash_goal.py``——纯领域判定，与 UI 无关；下沉到 ``apps/goal`` 使
api/mcp 宿主可复用，也让覆盖判定回归 :mod:`apps.goal.context` 单源
（``criterion_is_covered`` / ``goal_progress_tuple``）。

**行为注记（M5.2 已声明）**：原 CLI 的完成闸读快照的 ``all_covered``
键——该键无任何生产方（幻影键，M5 审计），导致非 lite 完成在有
criteria 时**恒拒绝**。闸现改用 ``goal_progress_tuple`` 判全覆盖；
行形（result="satisfied" + 全量 evidence_ids）与原实现逐字等价，
收敛到 AutoCompleteStrategy 的按条目过滤口径**不在本批**（避免扩大
行为面，另立批裁决）。
"""
from __future__ import annotations

from typing import Any

__all__ = [
    "all_criteria_covered",
    "build_completion_audit_rows",
    "resolve_criterion_ref",
]


def resolve_criterion_ref(
    criteria: list[dict[str, Any]], ref: str,
) -> str | None:
    """把用户引用（1-based 序号 / 精确 id / 前缀）解析为 ``criterion_id``。

    解析顺序（与原 CLI 实现一致）：整数按**1-based**下标取；否则精确
    id 匹配；否则前缀匹配（首个命中）。解析不到返回 ``None``。
    """
    try:
        idx = int(ref) - 1
        if 0 <= idx < len(criteria):
            return criteria[idx].get("criterion_id")
    except ValueError:
        pass
    for c in criteria:
        cid = str(c.get("criterion_id", ""))
        if cid == ref or cid.startswith(ref):
            return cid
    return None


def all_criteria_covered(snapshot: dict[str, Any]) -> bool:
    """全部 criteria 都已被证据/状态覆盖（完成闸的单一事实源）。

    以 :func:`apps.goal.context.goal_progress_tuple` 为准——原 CLI 读的
    ``all_covered`` 是幻影键（无生产方），恒 False 使非 lite 完成在有
    criteria 时恒拒绝。无 criteria 时视为已覆盖（nothing to prove）。
    """
    from agentnodes.apps.goal.context import goal_progress_tuple

    criteria = snapshot.get("criteria") or []
    if not criteria:
        return True
    covered, _ = goal_progress_tuple(snapshot)
    return covered >= len(criteria)


def build_completion_audit_rows(
    snapshot: dict[str, Any],
    recap: str = "",
    *,
    evidence_ids: list[str] | None = None,
) -> list[Any]:
    """为每条 criterion 合成一行 ``AuditRow``（与原 CLI 实现逐字等价）。

    ``evidence_ids`` 缺省取快照内全部证据 id（原行为）；显式传入可做
    按条目过滤（AutoCompleteStrategy 口径，本批不用）。调用方应先用
    :func:`all_criteria_covered` 过闸——本函数不重复判定。
    """
    from agentnodes.apps.goal.models import AuditRow

    ids = (
        evidence_ids
        if evidence_ids is not None
        else [e.get("evidence_id", "") for e in snapshot.get("evidence", [])]
    )
    criteria = snapshot.get("criteria", [])
    return [
        AuditRow(
            criterion_id=str(c.get("criterion_id", "")),
            result="satisfied",
            evidence_ids=ids,
            notes=recap or "",
        )
        for c in criteria
    ]
