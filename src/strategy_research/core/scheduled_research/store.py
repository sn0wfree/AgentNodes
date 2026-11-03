"""Scheduled Research store — SQLite-backed persistence.

``scheduled_jobs`` table lives in the same DB as the study/goal ledger
(``goals.db``, env ``QUANTNODES_RESEARCH_GOAL_DB_PATH``) so scheduled
jobs sit next to the studies they spawn.

Legacy JSON storage (``~/.quantnodes-research/scheduled_jobs.json``) is
migrated once via ``migrate_from_json`` (file renamed to ``.migrated``).
"""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import List

from strategy_research.foundation.storage.sqlite import connect, resolve_db_path, synchronized
from .models import JobStatus, ScheduledResearchJob

logger = logging.getLogger(__name__)

# Reuse the study/goal ledger DB: scheduled jobs live next to the studies
# they spawn (same resolution as ``StudyStore._default_db_path``).
_DB_PATH_ENV = "QUANTNODES_RESEARCH_GOAL_DB_PATH"
LEGACY_JSON_PATH = Path.home() / ".quantnodes-research" / "scheduled_jobs.json"


def _default_db_path() -> Path:
    """Return the configured scheduled-jobs DB path.

    Resolution order mirrors ``StudyStore``:
        1. ``QUANTNODES_RESEARCH_GOAL_DB_PATH`` environment variable
        2. ``~/.quantnodes-research/goals.db``
    """
    return resolve_db_path("goals.db", _DB_PATH_ENV)


class ScheduledResearchStore:
    """SQLite-backed store for scheduled research jobs.

    Owns one connection for its lifetime; per-request callers should use
    the context manager (``with ScheduledResearchStore() as store:``).
    """

    def __init__(self, path: Path | str | None = None) -> None:
        self.db_path = Path(path) if path is not None else _default_db_path()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = connect(self.db_path)
        self._lock = threading.Lock()
        self._init_db()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def __enter__(self) -> "ScheduledResearchStore":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def _init_db(self) -> None:
        with self._lock:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS scheduled_jobs (
                    job_id           TEXT PRIMARY KEY,
                    workspace        TEXT NOT NULL,
                    strategy_name    TEXT NOT NULL,
                    prompt           TEXT NOT NULL DEFAULT '',
                    cron             TEXT NOT NULL DEFAULT '',
                    interval_ms      INTEGER NOT NULL DEFAULT 0,
                    next_run_at      REAL NOT NULL,
                    created_at       REAL NOT NULL,
                    last_run_at      REAL,
                    last_run_id      TEXT,
                    status           TEXT NOT NULL DEFAULT 'pending',
                    config           TEXT NOT NULL DEFAULT '{}',
                    max_rounds       INTEGER NOT NULL DEFAULT 1,
                    target           TEXT NOT NULL DEFAULT 'study',
                    owner_session_id TEXT
                )
                """
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_scheduled_jobs_status "
                "ON scheduled_jobs(status)"
            )
            self._conn.commit()

    # ── reads ─────────────────────────────────────────────────────

    def load(self) -> List[ScheduledResearchJob]:
        """Load all jobs from the table (no filter)."""
        rows = self._conn.execute(
            "SELECT * FROM scheduled_jobs ORDER BY created_at"
        ).fetchall()
        return [self._job_from_row(r) for r in rows]

    def get(self, job_id: str) -> ScheduledResearchJob | None:
        """Get a job by ID."""
        row = self._conn.execute(
            "SELECT * FROM scheduled_jobs WHERE job_id = ?", (job_id,)
        ).fetchone()
        return self._job_from_row(row) if row is not None else None

    def list_jobs(
        self,
        workspace: str | None = None,
        status: JobStatus | None = None,
        owner_session_id: str | None = None,
    ) -> List[ScheduledResearchJob]:
        """List jobs with optional filters."""
        clauses: list[str] = []
        params: list[object] = []
        if workspace:
            clauses.append("workspace = ?")
            params.append(workspace)
        if status is not None:
            clauses.append("status = ?")
            params.append(status.value)
        if owner_session_id:
            clauses.append("owner_session_id = ?")
            params.append(owner_session_id)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._conn.execute(
            f"SELECT * FROM scheduled_jobs{where} ORDER BY created_at", params
        ).fetchall()
        return [self._job_from_row(r) for r in rows]

    # ── writes ────────────────────────────────────────────────────

    @synchronized
    def add(self, job: ScheduledResearchJob) -> None:
        """Add or replace a job (by ID)."""
        self._conn.execute(
            """
            INSERT INTO scheduled_jobs (
                job_id, workspace, strategy_name, prompt, cron, interval_ms,
                next_run_at, created_at, last_run_at, last_run_id, status,
                config, max_rounds, target, owner_session_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job.id, job.workspace, job.strategy_name, job.prompt, job.cron,
                job.interval_ms, job.next_run_at, job.created_at, job.last_run_at,
                job.last_run_id, job.status.value, json.dumps(job.config),
                job.max_rounds, job.target, job.owner_session_id,
            ),
        )
        self._conn.commit()

    @synchronized
    def update(self, job: ScheduledResearchJob) -> None:
        """Update a job (by ID)."""
        cur = self._conn.execute(
            """
            UPDATE scheduled_jobs SET
                workspace = ?, strategy_name = ?, prompt = ?, cron = ?,
                interval_ms = ?, next_run_at = ?, created_at = ?,
                last_run_at = ?, last_run_id = ?, status = ?, config = ?,
                max_rounds = ?, target = ?, owner_session_id = ?
            WHERE job_id = ?
            """,
            (
                job.workspace, job.strategy_name, job.prompt, job.cron,
                job.interval_ms, job.next_run_at, job.created_at, job.last_run_at,
                job.last_run_id, job.status.value, json.dumps(job.config),
                job.max_rounds, job.target, job.owner_session_id, job.id,
            ),
        )
        if cur.rowcount == 0:
            raise KeyError(f"Job not found: {job.id}")
        self._conn.commit()

    @synchronized
    def delete(self, job_id: str) -> bool:
        """Delete a job by ID. Returns True if deleted."""
        cur = self._conn.execute(
            "DELETE FROM scheduled_jobs WHERE job_id = ?", (job_id,)
        )
        self._conn.commit()
        return cur.rowcount > 0

    @synchronized
    def recover_stale_running(self) -> int:
        """Reset any RUNNING jobs back to PENDING (for crash recovery).

        Returns number of jobs recovered.
        """
        cur = self._conn.execute(
            "UPDATE scheduled_jobs SET status = 'pending' WHERE status = 'running'"
        )
        self._conn.commit()
        return cur.rowcount

    # ── legacy JSON migration ─────────────────────────────────────

    def migrate_from_json(self, json_path: Path | str | None = None) -> int:
        """Migrate legacy ``scheduled_jobs.json`` rows into SQLite.

        The JSON file is renamed to ``<name>.migrated`` after a successful
        import (idempotent: a missing/renamed file yields 0). Legacy jobs
        are unified to ``target='study'`` per design decision. A corrupt
        file is renamed to ``<name>.corrupt-<ts>`` (old behaviour).

        Returns the number of jobs imported.
        """
        src = Path(json_path) if json_path is not None else LEGACY_JSON_PATH
        if not src.exists():
            return 0
        try:
            raw = json.loads(src.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            src.rename(src.with_suffix(f"{src.suffix}.corrupt-{int(time.time())}"))
            logger.warning("Corrupt legacy store renamed to %s", src)
            return 0
        jobs_data = raw.get("jobs", []) if isinstance(raw, dict) else []
        count = 0
        for data in jobs_data:
            job = ScheduledResearchJob.from_dict(data)
            if job.target == "autoresearch":
                job.target = "study"
            job.owner_session_id = job.owner_session_id or None
            self.add(job)
            count += 1
        src.rename(Path(f"{src}.migrated"))
        logger.info("Migrated %d legacy scheduled jobs → SQLite (%s)", count, src)
        return count

    # ── helpers ───────────────────────────────────────────────────

    @staticmethod
    def _job_from_row(row) -> ScheduledResearchJob:
        try:
            config = json.loads(row["config"]) if row["config"] else {}
        except json.JSONDecodeError:
            config = {}
        return ScheduledResearchJob(
            id=row["job_id"],
            workspace=row["workspace"],
            strategy_name=row["strategy_name"],
            prompt=row["prompt"],
            cron=row["cron"],
            interval_ms=row["interval_ms"],
            next_run_at=row["next_run_at"],
            created_at=row["created_at"],
            last_run_at=row["last_run_at"],
            last_run_id=row["last_run_id"],
            status=JobStatus(row["status"]),
            config=config,
            max_rounds=row["max_rounds"],
            target=row["target"],
            owner_session_id=row["owner_session_id"],
        )
