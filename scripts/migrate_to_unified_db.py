#!/usr/bin/env python3
"""One-shot migration: merge legacy session DBs into the unified DB.

Merges rows from the two historical DB files into the new unified
``.quantnodes_agentnodes_session.db`` (located via
``resolve_session_db_path``). Uses ``INSERT OR IGNORE`` so sessions
that already exist in the target are skipped (no duplicate-key errors,
no data loss).

Source DBs (in merge order — earlier sources win on key conflict):
  1. <workspace>/quantnodes_agentnodes_user.db  (current production)
  2. ~/.quantnodes/quantnodes_agentnodes_user.db (older home-dir DB)

Target:
  resolve_session_db_path()  (AN_SESSIONS_DB > AN_WORKSPACE_PATH > cwd)

Tables migrated (when present in source):
  sessions, messages, message_parts, event_log, attempts

Usage:
  python scripts/migrate_to_unified_db.py [--workspace PATH]
  python scripts/migrate_to_unified_db.py --workspace /home/ll/Public/qn-research

Idempotent: safe to re-run (INSERT OR IGNORE skips existing rows).
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path

# Ensure we can import the package when run from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from agentnodes.core.agent.memory_manager import resolve_session_db_path  # noqa: E402

MIGRATE_TABLES = ["sessions", "messages", "message_parts", "event_log", "attempts"]


def _table_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return [r[1] for r in rows]


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    r = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return r is not None


def _row_count(conn: sqlite3.Connection, table: str) -> int:
    return conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


def _ensure_target_schema(target_path: Path) -> None:
    """Run the canonical schema setup on the target DB so all tables exist."""
    from agentnodes.api.routers.web_session import _ensure_schema

    conn = sqlite3.connect(str(target_path))
    try:
        _ensure_schema(conn)
        # attempts table is created by SessionStore — ensure it too.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS attempts (
                attempt_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                status TEXT NOT NULL,
                prompt TEXT,
                summary TEXT,
                error TEXT,
                run_dir TEXT,
                metrics_json TEXT,
                created_at REAL NOT NULL,
                completed_at REAL,
                message_id TEXT,
                FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def migrate_source(
    source_path: Path,
    target_path: Path,
    source_label: str,
) -> dict[str, int]:
    """Copy rows from source to target. Returns per-table inserted counts."""
    if not source_path.exists():
        print(f"[{source_label}] SKIP — {source_path} does not exist")
        return {}

    conn_src = sqlite3.connect(f"file:{source_path}?mode=ro&immutable=1", uri=True)
    conn_dst = sqlite3.connect(str(target_path))
    inserted: dict[str, int] = {}

    try:
        conn_dst.execute("PRAGMA foreign_keys=OFF")  # allow flexible insert order
        for table in MIGRATE_TABLES:
            if not _table_exists(conn_src, table):
                print(f"[{source_label}] {table}: not in source, skip")
                continue
            if not _table_exists(conn_dst, table):
                print(f"[{source_label}] {table}: target table missing, skip")
                continue

            cols = _table_columns(conn_src, table)
            # Only keep columns that exist in BOTH source and target
            dst_cols = set(_table_columns(conn_dst, table))
            shared = [c for c in cols if c in dst_cols]
            if not shared:
                print(f"[{source_label}] {table}: no shared columns, skip")
                continue

            placeholders = ",".join("?" * len(shared))
            col_list = ",".join(shared)
            before = _row_count(conn_dst, table)

            rows = conn_src.execute(f"SELECT {col_list} FROM {table}").fetchall()
            conn_dst.executemany(
                f"INSERT OR IGNORE INTO {table} ({col_list}) VALUES ({placeholders})",
                rows,
            )
            conn_dst.commit()
            after = _row_count(conn_dst, table)
            delta = after - before
            inserted[table] = delta
            print(f"[{source_label}] {table}: +{delta} (source={len(rows)}, target {before}->{after})")
    finally:
        conn_src.close()
        conn_dst.close()

    return inserted


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--workspace",
        default=None,
        help="Workspace dir (sets AN_WORKSPACE_PATH). Defaults to cwd.",
    )
    args = parser.parse_args()

    if args.workspace:
        os.environ["AN_WORKSPACE_PATH"] = args.workspace

    target = resolve_session_db_path()
    print(f"Target unified DB: {target}\n")

    # Ensure target schema exists
    _ensure_target_schema(target)

    # Sources (merge order: workspace first since it's the most current)
    workspace_dir = (
        Path(os.environ.get("AN_WORKSPACE_PATH", str(Path.cwd())))
    )
    sources = [
        ("workspace_old", workspace_dir / "quantnodes_agentnodes_user.db"),
        ("home_old", Path.home() / ".quantnodes" / "quantnodes_agentnodes_user.db"),
    ]

    grand_total: dict[str, int] = {}
    for label, path in sources:
        print(f"\n=== Migrating [{label}]: {path} ===")
        counts = migrate_source(path, target, label)
        for t, c in counts.items():
            grand_total[t] = grand_total.get(t, 0) + c

    # Final report
    print("\n=== Migration complete ===")
    print(f"Target: {target}")
    for t in MIGRATE_TABLES:
        if t in grand_total:
            print(f"  {t}: +{grand_total[t]} rows merged")
    print("\nNote: sources kept intact (read-only). Delete them manually after verifying.")

    # Verify target row counts
    print("\n=== Target DB final counts ===")
    conn = sqlite3.connect(str(target))
    for t in MIGRATE_TABLES:
        if _table_exists(conn, t):
            print(f"  {t}: {_row_count(conn, t)}")
    conn.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
