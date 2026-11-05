#!/usr/bin/env python3
"""Backfill graph.json for legacy studies.

Iterates every study in the production DB and writes
``{ws}/study/{study_id}/graph.json`` if it doesn't exist, using
``DEFAULT_STANDARD_GRAPH`` from ``graph_templates``.

Safe to re-run; existing graph.json is never overwritten.

Usage:
    python scripts/migrate_study_graph.py [--db PATH] [--workspace-root PATH]

Defaults:
    --db            $AGENTNODES_GOAL_DB_PATH or /home/ll/.agentnodes/goals.db
    --workspace-root Path stored in ``studies.workspace_path`` (each row's own path)
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path


def _resolve_db_path(cli_arg: str | None) -> Path:
    if cli_arg:
        return Path(cli_arg)
    env = os.environ.get("AGENTNODES_GOAL_DB_PATH")
    if env:
        return Path(env)
    return Path("/home/ll/.agentnodes/goals.db")


def _resolve_workspace_root(cli_arg: str | None) -> Path | None:
    """Optional fallback root if ``studies.workspace_path`` is empty."""
    if cli_arg:
        return Path(cli_arg)
    env = os.environ.get("AGENTNODES_WORKSPACE_ROOT")
    if env:
        return Path(env)
    return None


def _resolve_study_path(
    row: sqlite3.Row,
    workspace_root: Path | None,
) -> Path | None:
    ws = row["workspace_path"]
    if ws:
        return Path(ws)
    if workspace_root is not None:
        return workspace_root
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", help="Path to goals.db (default: production)")
    parser.add_argument(
        "--workspace-root",
        help="Fallback workspace root when studies.workspace_path is empty",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would change without writing files",
    )
    args = parser.parse_args()

    db_path = _resolve_db_path(args.db)
    if not db_path.is_file():
        print(f"ERROR: db not found: {db_path}", file=sys.stderr)
        return 2

    # Lazy import so the script is portable for `--help` outside the venv.
    from agentnodes.core.study.graph_templates import (
        DEFAULT_STANDARD_GRAPH,
    )

    workspace_root = _resolve_workspace_root(args.workspace_root)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT study_id, workspace_path FROM studies"
    ).fetchall()

    migrated = 0
    skipped = 0
    no_ws = 0

    for row in rows:
        sid = row["study_id"]
        ws = _resolve_study_path(row, workspace_root)
        if ws is None:
            print(f"  {sid}: no workspace_path (skipped)")
            no_ws += 1
            continue
        graph_file = ws / "study" / sid / "graph.json"
        if graph_file.exists():
            skipped += 1
            continue
        if args.dry_run:
            print(f"  {sid}: would create {graph_file}")
            migrated += 1
            continue
        DEFAULT_STANDARD_GRAPH.save(ws, sid)
        print(f"  {sid}: wrote {graph_file}")
        migrated += 1

    print(
        f"\nSummary: {migrated} migrated, {skipped} already had graph.json, "
        f"{no_ws} no workspace_path (skipped)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())