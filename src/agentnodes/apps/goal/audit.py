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
    "rows_for",
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
    """全部 **required** criterion 已被证据/状态覆盖（完成闸单一事实源）。

    逐条用 :func:`apps.goal.context.criterion_is_covered` 判定——统一 status
    + evidence 口径（修 workflow_hook 漏判 status 覆盖），且只算 required
    （非必填未覆盖不阻塞完成，修 goal_progress_tuple 的 len 口径）。无
    required criteria 时返回 True（nothing to prove）。

    原 CLI 读的 ``all_covered`` 是幻影键（无生产方），恒 False 使非 lite
    完成在有 criteria 时恒拒绝——M5 已修，本函数是该修复的口径收尾。
    """
    from agentnodes.apps.goal.context import criterion_is_covered

    criteria = snapshot.get("criteria") or []
    required = [c for c in criteria if c.get("required", True)]
    if not required:
        return True
    return all(criterion_is_covered(snapshot, c) for c in required)


def rows_for(
    criteria: list[dict[str, Any]],
    evidence: list[dict[str, Any]],
    notes: str,
) -> list[Any]:
    """从 criteria/evidence 两 list 合成 AuditRow（行形单源，per-criterion）。

    只处理 required criterion；每条 evidence_ids 取该 criterion 匹配的，
    无匹配则回退全量首个（与 AutoCompleteStrategy 原行为一致）。供有
    criteria/evidence list 但无 snapshot 的调用方（completion_strategy）。
    """
    from agentnodes.apps.goal.models import AuditRow

    all_ids = [e.get("evidence_id", "") for e in evidence]
    rows = []
    for c in criteria:
        if not c.get("required", True):
            continue
        cid = str(c.get("criterion_id", ""))
        cid_ids = [
            e.get("evidence_id", "") for e in evidence
            if e.get("criterion_id") == cid
        ]
        rows.append(AuditRow(
            criterion_id=cid, result="satisfied",
            evidence_ids=cid_ids or all_ids[:1], notes=notes,
        ))
    return rows


def build_completion_audit_rows(
    snapshot: dict[str, Any],
    recap: str = "",
    *,
    evidence_ids: list[str] | None = None,
    per_criterion: bool = False,
) -> list[Any]:
    """为每条 criterion 合成一行 ``AuditRow``。

    ``per_criterion=False``（默认，原 CLI 行为）：全量 evidence_ids 或显式传入。
    ``per_criterion=True``：委托 :func:`rows_for`（按 criterion 过滤，行形统一）。
    调用方应先用 :func:`all_criteria_covered` 过闸——本函数不重复判定。
    """
    if per_criterion:
        return rows_for(
            snapshot.get("criteria", []), snapshot.get("evidence", []), recap,
        )

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
