"""CLI handlers for the goal subsystem (P3-a).

Subcommands:
  start    — create a new goal (supersedes any current goal for the session)
  status   — show current goal state, criteria, evidence counts
  evidence — append evidence to a criterion
  audit    — write a completion audit row
  complete — complete the goal (requires full audit + verified evidence)
  list     — list all goals for a session
  cancel   — cancel the current goal (status -> cancelled)
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from .context import format_goal_context
from .models import AuditRow, EvidenceInput, GoalStatus, RiskTier
from .policy import normalize_required_text
from .store import GoalStore


def cmd_goal_start(args: argparse.Namespace) -> int:
    """Create a new research goal for the session."""
    session_id = normalize_required_text(args.session_id, "session_id")
    objective = normalize_required_text(args.objective, "objective")
    risk_tier = RiskTier(args.risk_tier)

    criteria = args.criterion or []
    if not criteria:
        from .context import default_goal_criteria
        criteria = default_goal_criteria()

    try:
        store = GoalStore(db_path=_resolve_db(args.db))
        goal = store.replace_goal(
            session_id=session_id,
            objective=objective,
            criteria=criteria,
            ui_summary=args.summary or "",
            source=args.source,
            protocol=args.protocol,
            risk_tier=risk_tier,
            token_budget=args.token_budget,
            turn_budget=args.turn_budget,
            time_budget_seconds=args.time_budget,
        )
    except ValueError as e:
        print(f"✗ {e}", file=sys.stderr)
        return 1
    print(f"✓ Goal created: {goal.goal_id}")
    print(f"  session_id: {goal.session_id}")
    print(f"  objective:  {goal.objective}")
    print(f"  status:     {goal.status.value}")
    print(f"  risk_tier:  {goal.risk_tier.value}")
    print(f"  criteria:   {len(criteria)}")
    return 0


def cmd_goal_status(args: argparse.Namespace) -> int:
    """Show goal status (snapshot for a session or detail for an id)."""
    store = GoalStore(db_path=_resolve_db(args.db))
    if args.goal_id:
        snap = store.get_goal_snapshot(args.goal_id)
        if snap is None:
            print(f"goal not found: {args.goal_id}", file=sys.stderr)
            return 1
    elif args.session_id:
        snap = store.get_current_snapshot(args.session_id)
        if snap is None:
            print(f"no current goal for session: {args.session_id}", file=sys.stderr)
            return 1
    else:
        print("either --goal-id or --session-id required", file=sys.stderr)
        return 1

    print(format_goal_context(snap))
    print()
    print(f"snapshot_progress: {snap['evidence_count']} evidence rows, {len(snap['criteria'])} criteria")
    return 0


def cmd_goal_evidence(args: argparse.Namespace) -> int:
    """Append evidence to a criterion."""
    session_id = normalize_required_text(args.session_id, "session_id")
    text = normalize_required_text(args.text, "text")

    store = GoalStore(db_path=_resolve_db(args.db))
    current = store.get_current_goal(session_id)
    if current is None:
        print(f"no current goal for session: {session_id}", file=sys.stderr)
        return 1

    ev_in = EvidenceInput(
        text=text,
        criterion_id=args.criterion_id,
        evidence_type=args.type,
        artifact_path=args.artifact,
        artifact_hash=args.artifact_hash,
        data_as_of=args.data_as_of,
        confidence=args.confidence,
        caveat=args.caveat,
        symbol_universe=args.symbol or [],
        benchmark=args.benchmark or [],
        timeframe=args.timeframe,
        method=args.method,
    )
    record = store.append_evidence(
        session_id=session_id,
        goal_id=current.goal_id,
        expected_goal_id=current.goal_id,
        evidence=ev_in,
    )
    print(f"✓ Evidence appended: {record.evidence_id}")
    print(f"  criterion_id: {record.criterion_id}")
    print(f"  verification: {record.verification_status}")
    return 0


def cmd_goal_audit(args: argparse.Namespace) -> int:
    """Write a completion audit row for one criterion."""
    session_id = normalize_required_text(args.session_id, "session_id")
    criterion_id = normalize_required_text(args.criterion_id, "criterion-id")

    valid_results = {"satisfied", "satisfied_with_caveat", "not_applicable_user_accepted"}
    if args.result not in valid_results:
        print(
            f"invalid result {args.result!r}. Must be one of: {sorted(valid_results)}",
            file=sys.stderr,
        )
        return 1

    store = GoalStore(db_path=_resolve_db(args.db))
    current = store.get_current_goal(session_id)
    if current is None:
        print(f"no current goal for session: {session_id}", file=sys.stderr)
        return 1

    # Write audit row directly via raw SQL (no status change)
    from datetime import datetime, timezone

    from strategy_research.foundation.storage.sqlite import json_dumps, new_id, write_transaction

    audit_id = new_id("audit")
    now = datetime.now(timezone.utc).isoformat()
    with write_transaction(store._conn):
        store._conn.execute(
            """
            INSERT INTO goal_audits (
                audit_id, goal_id, session_id, audit_type, result,
                rows_json, created_at
            )
            VALUES (?, ?, ?, 'completion', ?, ?, ?)
            """,
            (
                audit_id,
                current.goal_id,
                session_id,
                args.result,
                json_dumps(
                    [{"criterion_id": criterion_id, "result": args.result,
                      "evidence_ids": args.evidence or [], "notes": args.notes or ""}]
                ),
                now,
            ),
        )
        # Update criterion status to match audit result
        store._conn.execute(
            "UPDATE goal_criteria SET status = ?, updated_at = ? "
            "WHERE goal_id = ? AND criterion_id = ?",
            (args.result, now, current.goal_id, criterion_id),
        )
    print(f"✓ Audit row written: {criterion_id} → {args.result}")
    print(f"  audit_id: {audit_id}")
    return 0


def cmd_goal_complete(args: argparse.Namespace) -> int:
    """Complete a goal (requires full audit + verified evidence)."""
    session_id = normalize_required_text(args.session_id, "session_id")

    # Parse audit rows from --audit JSON file or build from CLI flags
    audit_rows: list[AuditRow] = []
    if args.audit_file:
        data = json.loads(args.audit_file.read_text(encoding="utf-8"))
        for row in data:
            audit_rows.append(
                AuditRow(
                    criterion_id=row["criterion_id"],
                    result=row["result"],
                    evidence_ids=row.get("evidence_ids", []),
                    notes=row.get("notes", ""),
                )
            )
    elif args.criterion_id and args.result:
        audit_rows.append(
            AuditRow(
                criterion_id=args.criterion_id,
                result=args.result,
                evidence_ids=args.evidence or [],
                notes=args.notes or "",
            )
        )
    elif not audit_rows:
        print(
            "complete requires --audit-file (JSON list) or --criterion-id + --result",
            file=sys.stderr,
        )
        return 1

    store = GoalStore(db_path=_resolve_db(args.db))
    current = store.get_current_goal(session_id)
    if current is None:
        print(f"no current goal for session: {session_id}", file=sys.stderr)
        return 1
    try:
        updated = store.update_status(
            session_id=session_id,
            goal_id=current.goal_id,
            expected_goal_id=current.goal_id,
            status=GoalStatus.COMPLETE,
            audit=audit_rows,
            recap=args.recap,
        )
    except ValueError as e:
        print(f"✗ {e}", file=sys.stderr)
        return 1
    print(f"✓ Goal completed: {updated.goal_id}")
    print(f"  status:    {updated.status.value}")
    print(f"  completed: {updated.completed_at}")
    print(f"  recap:     {updated.recap}")
    return 0


def cmd_goal_list(args: argparse.Namespace) -> int:
    """List goals for a session."""
    session_id = normalize_required_text(args.session_id, "session_id")
    store = GoalStore(db_path=_resolve_db(args.db))
    rows = store._conn.execute(
        "SELECT goal_id, status, objective, updated_at FROM goals "
        "WHERE session_id = ? ORDER BY updated_at DESC",
        (session_id,),
    ).fetchall()
    if not rows:
        print(f"no goals for session: {session_id}")
        return 0
    print(f"=== Goals for session {session_id} (n={len(rows)}) ===")
    for row in rows:
        print(f"  {row['goal_id']}  [{row['status']:>20s}]  {row['objective'][:60]}")
    return 0


def cmd_goal_cancel(args: argparse.Namespace) -> int:
    """Cancel the current goal (status -> cancelled)."""
    session_id = normalize_required_text(args.session_id, "session_id")
    store = GoalStore(db_path=_resolve_db(args.db))
    current = store.get_current_goal(session_id)
    if current is None:
        print(f"no current goal for session: {session_id}", file=sys.stderr)
        return 1
    updated = store.update_status(
        session_id=session_id,
        goal_id=current.goal_id,
        expected_goal_id=current.goal_id,
        status=GoalStatus.CANCELLED,
    )
    print(f"✓ Goal cancelled: {updated.goal_id}")
    return 0


# ─── Helpers ─────────────────────────────────────────────────────────────


def _resolve_db(path: str | None) -> Any:
    """Resolve CLI --db argument to a Path. None means use default."""
    from pathlib import Path
    return Path(path).expanduser() if path else None


# ─── Argparse wiring (used by cli.py) ────────────────────────────────────


def add_goal_subparsers(subparsers: Any) -> None:
    """Attach the goal subcommands to a parent parser.

    Args:
        subparsers: An argparse subparsers container (the value returned by
            ``ArgumentParser.add_subparsers()``).
    """
    goal_parser = subparsers.add_parser("goal", help="研究目标管理")
    goal_sub = goal_parser.add_subparsers(dest="goal_command", help="goal 子命令")

    # goal start
    p_start = goal_sub.add_parser("start", help="创建新目标")
    p_start.add_argument("--session-id", required=True, help="session id")
    p_start.add_argument("--objective", required=True, help="研究目标")
    p_start.add_argument("--criterion", action="append", help="criterion text (可多次)")
    p_start.add_argument("--summary", help="UI summary")
    p_start.add_argument("--source", default="cli", help="来源 (默认 cli)")
    p_start.add_argument("--protocol", default="thesis_review", help="协议名")
    p_start.add_argument(
        "--risk-tier",
        default="research_general",
        choices=[t.value for t in RiskTier],
        help="风险等级",
    )
    p_start.add_argument("--token-budget", type=int, help="token 上限")
    p_start.add_argument("--turn-budget", type=int, help="turn 上限")
    p_start.add_argument("--time-budget", type=int, help="时间上限（秒）")
    p_start.add_argument("--db", help="自定义 DB 路径")

    # goal status
    p_status = goal_sub.add_parser("status", help="查看目标状态")
    p_status.add_argument("--session-id", help="session id")
    p_status.add_argument("--goal-id", help="goal id (与 --session-id 二选一)")
    p_status.add_argument("--db", help="自定义 DB 路径")

    # goal evidence
    p_ev = goal_sub.add_parser("evidence", help="追加证据")
    p_ev.add_argument("--session-id", required=True, help="session id")
    p_ev.add_argument("--text", required=True, help="证据文本")
    p_ev.add_argument("--criterion-id", help="关联 criterion id")
    p_ev.add_argument("--type", default="evidence", help="证据类型")
    p_ev.add_argument("--artifact", help="本地 artifact 路径")
    p_ev.add_argument("--artifact-hash", help="artifact SHA-256 (sha256:xxx)")
    p_ev.add_argument("--data-as-of", help="数据截止日期")
    p_ev.add_argument("--confidence", help="置信度")
    p_ev.add_argument("--caveat", help="注意事项")
    p_ev.add_argument("--symbol", action="append", help="标的 universe")
    p_ev.add_argument("--benchmark", action="append", help="benchmark")
    p_ev.add_argument("--timeframe", help="时间范围")
    p_ev.add_argument("--method", help="方法说明")
    p_ev.add_argument("--db", help="自定义 DB 路径")

    # goal audit
    p_audit = goal_sub.add_parser("audit", help="写完成审计")
    p_audit.add_argument("--session-id", required=True, help="session id")
    p_audit.add_argument("--criterion-id", required=True, help="criterion id")
    p_audit.add_argument(
        "--result",
        required=True,
        choices=["satisfied", "satisfied_with_caveat", "not_applicable_user_accepted"],
        help="审计结果",
    )
    p_audit.add_argument("--evidence", action="append", help="evidence id (可多次)")
    p_audit.add_argument("--notes", help="备注")
    p_audit.add_argument("--db", help="自定义 DB 路径")

    # goal complete
    p_complete = goal_sub.add_parser("complete", help="完成目标")
    p_complete.add_argument("--session-id", required=True, help="session id")
    p_complete.add_argument("--audit-file", type=argparse.FileType("r"), help="审计 JSON 文件")
    p_complete.add_argument("--criterion-id", help="单 criterion id (与 --audit-file 二选一)")
    p_complete.add_argument("--result", choices=["satisfied", "satisfied_with_caveat", "not_applicable_user_accepted"])
    p_complete.add_argument("--evidence", action="append", help="evidence id (可多次)")
    p_complete.add_argument("--notes", help="备注")
    p_complete.add_argument("--recap", help="完成总结")
    p_complete.add_argument("--db", help="自定义 DB 路径")

    # goal list
    p_list = goal_sub.add_parser("list", help="列出会话的目标")
    p_list.add_argument("--session-id", required=True, help="session id")
    p_list.add_argument("--db", help="自定义 DB 路径")

    # goal cancel
    p_cancel = goal_sub.add_parser("cancel", help="取消当前目标")
    p_cancel.add_argument("--session-id", required=True, help="session id")
    p_cancel.add_argument("--db", help="自定义 DB 路径")
