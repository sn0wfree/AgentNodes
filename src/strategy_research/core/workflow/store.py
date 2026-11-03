"""WorkflowStore — isolated SQLite persistence for workflow runs.

Stored at ``<workspace>/workflows.db`` — deliberately separate from
the chat session DB (see docs/workflow-module-design.md §8).

Tables:
    runs          — run lifecycle + params snapshot
    run_segments  — per-segment status
    node_outputs  — unified output envelope per node
    approvals     — approval gate records
    run_events    — SSE event history

Mirrors the SQLiteStore pattern (api/session/memory_manager.py):
_ensure_conn / _init_schema / threading_lock / health_check /
auto_repair.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

logger = logging.getLogger(__name__)

_DEFAULT_DB_NAME = "workflows.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id          TEXT PRIMARY KEY,
    definition_name TEXT NOT NULL,
    session_id      TEXT NOT NULL,
    objective       TEXT NOT NULL DEFAULT '',
    status          TEXT NOT NULL DEFAULT 'pending',
    segment_idx     INTEGER NOT NULL DEFAULT 0,
    params_snapshot TEXT NOT NULL DEFAULT '{}',
    findings        TEXT NOT NULL DEFAULT '[]',
    failures        TEXT NOT NULL DEFAULT '[]',
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS run_segments (
    run_id      TEXT NOT NULL,
    segment_idx INTEGER NOT NULL,
    nodes       TEXT NOT NULL DEFAULT '[]',
    status      TEXT NOT NULL DEFAULT 'pending',
    elapsed_s   REAL NOT NULL DEFAULT 0,
    error       TEXT,
    PRIMARY KEY (run_id, segment_idx)
);
CREATE TABLE IF NOT EXISTS node_outputs (
    run_id       TEXT NOT NULL,
    segment_idx  INTEGER NOT NULL,
    node_id      TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'pending',
    summary      TEXT NOT NULL DEFAULT '',
    output       TEXT NOT NULL DEFAULT '',
    artifacts    TEXT NOT NULL DEFAULT '{}',
    metrics      TEXT NOT NULL DEFAULT '{}',
    error        TEXT,
    elapsed_s    REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (run_id, node_id)
);
CREATE TABLE IF NOT EXISTS approvals (
    run_id       TEXT NOT NULL,
    node_id      TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'awaiting',
    edits        TEXT,
    created_at   TEXT NOT NULL,
    responded_at TEXT,
    PRIMARY KEY (run_id, node_id)
);
CREATE TABLE IF NOT EXISTS run_events (
    run_id     TEXT NOT NULL,
    seq        INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    data       TEXT NOT NULL DEFAULT '{}',
    time       TEXT NOT NULL,
    PRIMARY KEY (run_id, seq)
);
"""


def resolve_workflows_db_path(workspace: Path) -> Path:
    """Resolve the workflows DB path inside a workspace root."""
    return workspace / _DEFAULT_DB_NAME


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class WorkflowStore:
    """SQLite persistence for workflow runs (one DB per workspace)."""

    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = Path(db_path) if db_path else resolve_workflows_db_path(Path.cwd())
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn: sqlite3.Connection | None = None

    # ── Connection ────────────────────────────────────────────

    def _ensure_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            self._init_schema()
        return self._conn

    def _init_schema(self) -> None:
        with self._lock:
            # Python 3.12+ sqlite3 rejects multi-statement strings
            for statement in _SCHEMA.split(";"):
                statement = statement.strip()
                if statement:
                    self._conn.execute(statement)  # type: ignore[union-attr]
            self._migrate()  # type: ignore[union-attr]
            self._conn.commit()  # type: ignore[union-attr]

    def _migrate(self) -> None:
        """Additive column migrations for DBs created before R-B0.

        ``node_outputs.output`` carries the node's full text output so
        ``WorkflowRunner.resume_from_store`` can rebuild ``pre_completed``
        after a restart (rows written earlier have an empty string; those
        runs can resume but downstream nodes see no upstream text).
        """
        cols = {
            row[1] for row in
            self._conn.execute("PRAGMA table_info(node_outputs)")  # type: ignore[union-attr]
        }
        if cols and "output" not in cols:
            self._conn.execute(  # type: ignore[union-attr]
                "ALTER TABLE node_outputs ADD COLUMN output TEXT NOT NULL DEFAULT ''"
            )

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    def health_check(self) -> bool:
        try:
            row = self._ensure_conn().execute("SELECT 1").fetchone()
            return row is not None
        except sqlite3.Error:
            return False

    def auto_repair(self) -> bool:
        """Recreate the DB from scratch if corrupted. Returns True on success."""
        try:
            if self.health_check():
                return True
            self.close()
            self.db_path.unlink(missing_ok=True)
            self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            self._init_schema()
            return True
        except sqlite3.Error as exc:
            logger.error("workflows.db repair failed: %s", exc)
            return False

    # ── Runs ──────────────────────────────────────────────────

    def create_run(
        self,
        run_id: str,
        definition_name: str,
        session_id: str,
        objective: str,
        params_snapshot: dict[str, Any],
    ) -> None:
        conn = self._ensure_conn()
        now = _now()
        with self._lock:
            conn.execute(
                "INSERT INTO runs (run_id, definition_name, session_id, objective, "
                "status, segment_idx, params_snapshot, findings, failures, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, 'pending', 0, ?, '[]', '[]', ?, ?)",
                (run_id, definition_name, session_id, objective,
                 json.dumps(params_snapshot, ensure_ascii=False), now, now),
            )
            conn.commit()

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        row = self._ensure_conn().execute(
            "SELECT * FROM runs WHERE run_id = ?", (run_id,),
        ).fetchone()
        return _row_to_dict(row)

    def list_runs(self, session_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        conn = self._ensure_conn()
        if session_id:
            rows = conn.execute(
                "SELECT * FROM runs WHERE session_id = ? ORDER BY created_at DESC LIMIT ?",
                (session_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM runs ORDER BY created_at DESC LIMIT ?", (limit,),
            ).fetchall()
        return cast(list[dict[str, Any]], [_row_to_dict(r) for r in rows])

    def update_run(
        self,
        run_id: str,
        *,
        status: str | None = None,
        segment_idx: int | None = None,
        findings: list[str] | None = None,
        failures: list[str] | None = None,
    ) -> None:
        conn = self._ensure_conn()
        sets, values = ["updated_at = ?"], [_now()]
        if status is not None:
            sets.append("status = ?")
            values.append(status)
        if segment_idx is not None:
            sets.append("segment_idx = ?")
            values.append(segment_idx)
        if findings is not None:
            sets.append("findings = ?")
            values.append(json.dumps(findings, ensure_ascii=False))
        if failures is not None:
            sets.append("failures = ?")
            values.append(json.dumps(failures, ensure_ascii=False))
        values.append(run_id)
        with self._lock:
            conn.execute(f"UPDATE runs SET {', '.join(sets)} WHERE run_id = ?", values)
            conn.commit()

    def delete_run(self, run_id: str) -> bool:
        conn = self._ensure_conn()
        with self._lock:
            for table in ("runs", "run_segments", "node_outputs", "approvals", "run_events"):
                conn.execute(f"DELETE FROM {table} WHERE run_id = ?", (run_id,))
            conn.commit()
        return True

    # ── Segments ──────────────────────────────────────────────

    def upsert_segment(
        self, run_id: str, segment_idx: int, nodes: list[str],
        status: str = "pending", elapsed_s: float = 0.0, error: str | None = None,
    ) -> None:
        conn = self._ensure_conn()
        with self._lock:
            conn.execute(
                "INSERT OR REPLACE INTO run_segments "
                "(run_id, segment_idx, nodes, status, elapsed_s, error) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (run_id, segment_idx, json.dumps(nodes, ensure_ascii=False),
                 status, elapsed_s, error),
            )
            conn.commit()

    def list_segments(self, run_id: str) -> list[dict[str, Any]]:
        rows = self._ensure_conn().execute(
            "SELECT * FROM run_segments WHERE run_id = ? ORDER BY segment_idx", (run_id,),
        ).fetchall()
        return cast(list[dict[str, Any]], [_row_to_dict(r) for r in rows])

    # ── Node outputs ──────────────────────────────────────────

    def save_node_output(self, run_id: str, segment_idx: int, result: Any) -> None:
        conn = self._ensure_conn()
        with self._lock:
            conn.execute(
                "INSERT OR REPLACE INTO node_outputs "
                "(run_id, segment_idx, node_id, status, summary, output, "
                "artifacts, metrics, error, elapsed_s) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (run_id, segment_idx, result.agent_id,
                 result.status.value if hasattr(result.status, "value") else str(result.status),
                 result.summary or result.output or "",
                 result.output or "",
                 json.dumps(result.artifacts or {}, ensure_ascii=False, default=str),
                 json.dumps(result.metrics or {}, ensure_ascii=False, default=str),
                 result.error, result.elapsed_s or 0.0),
            )
            conn.commit()

    def list_node_outputs(self, run_id: str) -> list[dict[str, Any]]:
        rows = self._ensure_conn().execute(
            "SELECT * FROM node_outputs WHERE run_id = ? ORDER BY segment_idx", (run_id,),
        ).fetchall()
        return cast(list[dict[str, Any]], [_row_to_dict(r) for r in rows])

    def rebuild_agent_results(self, run_id: str) -> dict[str, Any]:
        """Rebuild ``{node_id: AgentResult}`` from persisted node outputs.

        R-B2: used by ``WorkflowRunner.resume_from_store`` to restore
        ``pre_completed`` in a fresh process. Only SUCCESS rows are
        restored — a failed node must re-run on resume (its stored
        envelope carries an error, not a usable downstream output).
        """
        from strategy_research.kernel.swarm.runtime import AgentResult, AgentStatus

        rebuilt: dict[str, Any] = {}
        for row in self.list_node_outputs(run_id):
            if row.get("status") != AgentStatus.SUCCESS.value:
                continue
            try:
                artifacts = json.loads(row.get("artifacts") or "{}")
            except (json.JSONDecodeError, TypeError):
                artifacts = {}
            try:
                metrics = json.loads(row.get("metrics") or "{}")
            except (json.JSONDecodeError, TypeError):
                metrics = {}
            rebuilt[row["node_id"]] = AgentResult(
                agent_id=row["node_id"],
                status=AgentStatus.SUCCESS,
                output=row.get("output") or "",
                summary=row.get("summary") or "",
                artifacts=artifacts,
                metrics=metrics,
                elapsed_s=float(row.get("elapsed_s") or 0.0),
            )
        return rebuilt

    # ── Approvals ─────────────────────────────────────────────

    def create_approval(self, run_id: str, node_id: str) -> None:
        conn = self._ensure_conn()
        with self._lock:
            conn.execute(
                "INSERT OR REPLACE INTO approvals (run_id, node_id, status, created_at) "
                "VALUES (?, ?, 'awaiting', ?)",
                (run_id, node_id, _now()),
            )
            conn.commit()

    def respond_approval(self, run_id: str, node_id: str, approved: bool, edits: dict | None = None) -> bool:
        conn = self._ensure_conn()
        with self._lock:
            cur = conn.execute(
                "UPDATE approvals SET status = ?, edits = ?, responded_at = ? "
                "WHERE run_id = ? AND node_id = ?",
                ("approved" if approved else "rejected",
                 json.dumps(edits or {}, ensure_ascii=False) if edits else None,
                 _now(), run_id, node_id),
            )
            conn.commit()
            return cur.rowcount > 0

    def get_approval(self, run_id: str, node_id: str) -> dict[str, Any] | None:
        row = self._ensure_conn().execute(
            "SELECT * FROM approvals WHERE run_id = ? AND node_id = ?", (run_id, node_id),
        ).fetchone()
        return _row_to_dict(row)

    # ── Events ────────────────────────────────────────────────

    def append_event(self, run_id: str, event_type: str, data: dict[str, Any]) -> int:
        conn = self._ensure_conn()
        with self._lock:
            row = conn.execute(
                "SELECT COALESCE(MAX(seq), 0) + 1 AS next FROM run_events WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            seq = int(row["next"]) if row else 1
            conn.execute(
                "INSERT INTO run_events (run_id, seq, event_type, data, time) VALUES (?, ?, ?, ?, ?)",
                (run_id, seq, event_type, json.dumps(data, ensure_ascii=False, default=str), _now()),
            )
            conn.commit()
            return seq

    def list_events(self, run_id: str, limit: int = 500) -> list[dict[str, Any]]:
        rows = self._ensure_conn().execute(
            "SELECT * FROM run_events WHERE run_id = ? ORDER BY seq DESC LIMIT ?", (run_id, limit),
        ).fetchall()
        return cast(list[dict[str, Any]], [_row_to_dict(r) for r in rows])


def _row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {key: row[key] for key in row.keys()}


__all__ = ["WorkflowStore", "resolve_workflows_db_path"]
