"""SQLite-backed store for finance research goals."""

from __future__ import annotations

import hashlib
import logging
import sqlite3
import threading
from pathlib import Path
from typing import Any, cast

from ..storage.sqlite import (
    connect,
    json_dumps,
    json_loads,
    new_id,
    now_iso,
    set_user_version,
    synchronized,
    table_columns,
    user_version,
    write_transaction,
)
from .models import (
    AuditRow,
    EvidenceInput,
    EvidenceRecord,
    GoalClaim,
    GoalCriterion,
    GoalRecord,
    GoalStatus,
    JournalEntry,
    RiskTier,
    StaleGoalError,
)
from .policy import normalize_required_text, reject_live_execution_objective

logger = logging.getLogger(__name__)

_DB_PATH_ENV = "QUANTNODES_RESEARCH_GOAL_DB_PATH"

_CURRENT_STATUSES = {
    GoalStatus.ACTIVE,
    GoalStatus.PAUSED,
    GoalStatus.WAITING_USER,
    GoalStatus.NEEDS_REFRESH,
    GoalStatus.INSUFFICIENT_EVIDENCE,
    GoalStatus.COMPLIANCE_BLOCKED,
    GoalStatus.BUDGET_LIMITED,
}

_COMPLETION_RESULTS = {
    "satisfied",
    "satisfied_with_caveat",
    "not_applicable_user_accepted",
}


from .store_utils import (  # noqa: E402,F401
    _default_db_path,
    _label_similarity,
    _safe_artifact_path,
    _safe_run_id,
    _to_json_dict,
)


class GoalStore:
    """SQLite-backed store for finance research goals."""

    def __init__(self, db_path: Path | None = None) -> None:
        """Initialize the goal store.

        Args:
            db_path: SQLite database path. When omitted,
                ``QUANTNODES_RESEARCH_GOAL_DB_PATH`` can override the default.
        """
        self.db_path = Path(db_path) if db_path is not None else _default_db_path()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = connect(self.db_path)
        self._closed = False
        self._lock = threading.RLock()
        self._init_db()

    def close(self) -> None:
        """Close the underlying SQLite connection (idempotent).

        GoalStore owns one connection for its lifetime; always close
        instances created per-request (or use the context manager).
        Module-level long-lived stores (e.g. the agent loop's cached
        snapshot store) stay open for the process lifetime.
        """
        with self._lock:
            if not self._closed:
                try:
                    self._conn.close()
                except sqlite3.Error:
                    pass
                self._closed = True

    def __enter__(self) -> "GoalStore":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def _init_db(self) -> None:
        """Create goal tables and indexes if they do not exist."""
        with self._lock:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS goals (
                    goal_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    objective TEXT NOT NULL,
                    ui_summary TEXT NOT NULL,
                    source TEXT NOT NULL,
                    protocol TEXT NOT NULL,
                    risk_tier TEXT NOT NULL,
                    token_budget INTEGER,
                    tokens_used INTEGER NOT NULL DEFAULT 0,
                    turn_budget INTEGER,
                    turns_used INTEGER NOT NULL DEFAULT 0,
                    time_budget_seconds INTEGER,
                    time_used_seconds INTEGER NOT NULL DEFAULT 0,
                    budget_wrapup_sent INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    completed_at TEXT,
                    recap TEXT,
                    progress_percent REAL NOT NULL DEFAULT 0.0,
                    parent_goal_id TEXT,
                    workflow_id TEXT
                );

                CREATE UNIQUE INDEX IF NOT EXISTS idx_goals_one_current_per_session
                    ON goals(session_id)
                    WHERE status IN (
                        'active',
                        'paused',
                        'waiting_user',
                        'needs_refresh',
                        'insufficient_evidence',
                        'compliance_blocked',
                        'budget_limited'
                    );

                CREATE TABLE IF NOT EXISTS goal_claims (
                    claim_id TEXT PRIMARY KEY,
                    goal_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    claim_type TEXT NOT NULL,
                    text TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(goal_id) REFERENCES goals(goal_id)
                );

                CREATE INDEX IF NOT EXISTS idx_goal_claims_goal
                    ON goal_claims(goal_id, status);

                CREATE TABLE IF NOT EXISTS goal_criteria (
                    criterion_id TEXT PRIMARY KEY,
                    goal_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    text TEXT NOT NULL,
                    required INTEGER NOT NULL DEFAULT 1,
                    status TEXT NOT NULL DEFAULT 'pending',
                    freshness_requirement TEXT,
                    protocol_step TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(goal_id) REFERENCES goals(goal_id)
                );

                CREATE INDEX IF NOT EXISTS idx_goal_criteria_goal
                    ON goal_criteria(goal_id, status);

                CREATE TABLE IF NOT EXISTS goal_evidence (
                    evidence_id TEXT PRIMARY KEY,
                    goal_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    criterion_id TEXT,
                    claim_id TEXT,
                    evidence_type TEXT NOT NULL,
                    text TEXT NOT NULL,
                    tool_call_id TEXT,
                    run_id TEXT,
                    source_provider TEXT,
                    source_type TEXT,
                    source_uri TEXT,
                    symbol_universe_json TEXT NOT NULL DEFAULT '[]',
                    benchmark_json TEXT NOT NULL DEFAULT '[]',
                    timeframe TEXT,
                    method TEXT,
                    assumptions_json TEXT NOT NULL DEFAULT '{}',
                    artifact_path TEXT,
                    artifact_hash TEXT,
                    retrieved_at TEXT NOT NULL,
                    data_as_of TEXT,
                    freshness_status TEXT NOT NULL DEFAULT 'unknown',
                    verification_status TEXT NOT NULL DEFAULT 'unverified',
                    confidence TEXT,
                    caveat TEXT,
                    contradicts_claim_ids_json TEXT NOT NULL DEFAULT '[]',
                    hypothesis_id TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(goal_id) REFERENCES goals(goal_id)
                );

                CREATE INDEX IF NOT EXISTS idx_goal_evidence_goal
                    ON goal_evidence(goal_id, created_at);

                CREATE TABLE IF NOT EXISTS goal_audits (
                    audit_id TEXT PRIMARY KEY,
                    goal_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    audit_type TEXT NOT NULL,
                    result TEXT NOT NULL,
                    rows_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(goal_id) REFERENCES goals(goal_id)
                );

                CREATE TABLE IF NOT EXISTS goal_journal (
                    entry_id                TEXT PRIMARY KEY,
                    goal_id                 TEXT NOT NULL,
                    session_id              TEXT NOT NULL,
                    round_num               INTEGER NOT NULL,
                    hypothesis_id           TEXT NOT NULL,
                    label                   TEXT NOT NULL,
                    levers_json             TEXT NOT NULL DEFAULT '[]',
                    predicted_affected_json TEXT NOT NULL DEFAULT '[]',
                    gating_outcome          TEXT NOT NULL DEFAULT 'pending',
                    gating_attribution_json TEXT NOT NULL DEFAULT '{}',
                    changeset_json          TEXT,
                    retry_rationale         TEXT,
                    archived_reason         TEXT,
                    created_at              TEXT NOT NULL,
                    updated_at              TEXT NOT NULL,
                    FOREIGN KEY(goal_id) REFERENCES goals(goal_id)
                );
                """
            )
            if user_version(self._conn) < 1:
                set_user_version(self._conn, 1)
            self._conn.commit()

        # P3-B migration: add progress_percent and parent_goal_id columns if missing
        self._migrate_p3b()

        # Isolation migration: add user_id column and backfill ownership
        # from the unified session DB (D1).
        self._migrate_user_id()

        # Claims tracking: predictions + outcome columns on goal_journal.
        self._migrate_claims()

    def _migrate_claims(self) -> None:
        """Add predictions_json / prediction_outcome_json to goal_journal."""
        cols = table_columns(self._conn, "goal_journal")
        if "predictions_json" not in cols:
            self._conn.execute(
                "ALTER TABLE goal_journal ADD COLUMN predictions_json TEXT NOT NULL DEFAULT '{}'"
            )
        if "prediction_outcome_json" not in cols:
            self._conn.execute(
                "ALTER TABLE goal_journal ADD COLUMN prediction_outcome_json TEXT NOT NULL DEFAULT '{}'"
            )
        self._conn.commit()

    def _migrate_p3b(self) -> None:
        """Add progress_percent and parent_goal_id columns to existing goals table."""
        cols = table_columns(self._conn, "goals")
        if "progress_percent" not in cols:
            self._conn.execute(
                "ALTER TABLE goals ADD COLUMN progress_percent REAL NOT NULL DEFAULT 0.0"
            )
        if "parent_goal_id" not in cols:
            self._conn.execute(
                "ALTER TABLE goals ADD COLUMN parent_goal_id TEXT"
            )
        if "workflow_id" not in cols:
            self._conn.execute(
                "ALTER TABLE goals ADD COLUMN workflow_id TEXT"
            )

        # P3-C: hypothesis_id on goal_evidence
        ev_cols = table_columns(self._conn, "goal_evidence")
        if "hypothesis_id" not in ev_cols:
            self._conn.execute(
                "ALTER TABLE goal_evidence ADD COLUMN hypothesis_id TEXT"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_goal_evidence_hypothesis "
                "ON goal_evidence(hypothesis_id) WHERE hypothesis_id IS NOT NULL"
            )
        self._conn.commit()

    def _migrate_user_id(self) -> None:
        """Add user_id column to goals and backfill ownership (isolation D1).

        Goals are owned through their session: resolve each goal's
        ``session_id`` to the owning ``user_id`` via the unified session
        DB. Resolve failures default to ``anonymous`` so legacy rows never
        block listing.
        """
        cols = table_columns(self._conn, "goals")
        if "user_id" not in cols:
            self._conn.execute(
                "ALTER TABLE goals ADD COLUMN user_id TEXT NOT NULL DEFAULT 'anonymous'"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_goals_user "
                "ON goals(user_id, created_at)"
            )
            rows = self._conn.execute(
                "SELECT goal_id, session_id FROM goals"
            ).fetchall()
            if rows:
                session_to_user = self._resolve_session_owners()
                for goal_id, session_id in rows:
                    user_id = session_to_user.get(session_id, "anonymous")
                    self._conn.execute(
                        "UPDATE goals SET user_id = ? WHERE goal_id = ?",
                        (user_id, goal_id),
                    )
        self._conn.commit()

    @staticmethod
    def _resolve_session_owners() -> dict[str, str]:
        """Map session_id -> user_id across the unified session DB.

        Best-effort: reads the same session DB resolved by the web_session
        projector (``SR_SESSIONS_DB`` / workspace / home fallback). Returns
        an empty dict if the DB is unreadable so callers default to
        "anonymous".
        """
        try:
            from ..agent.memory_manager import resolve_session_db_path

            db_path = resolve_session_db_path()
            conn = sqlite3.connect(str(db_path))
            try:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    "SELECT id, user_id FROM sessions"
                ).fetchall()
                return {r["id"]: (r["user_id"] or "anonymous") for r in rows}
            finally:
                conn.close()
        except Exception:
            return {}

    @synchronized
    def replace_goal(
        self,
        *,
        session_id: str,
        objective: str,
        criteria: list[str],
        supersede: bool = True,
        ui_summary: str = "",
        source: str = "api",
        protocol: str = "thesis_review",
        risk_tier: RiskTier = RiskTier.RESEARCH_GENERAL,
        token_budget: int | None = None,
        turn_budget: int | None = None,
        time_budget_seconds: int | None = None,
        parent_goal_id: str | None = None,
        workflow_id: str | None = None,
        user_id: str = "anonymous",
    ) -> GoalRecord:
        """Supersede the current goal and create a new active goal.

        Args:
            session_id: Owning session id — the isolation domain. chat
                sessions keep one active goal (supersede=True, default);
                a study passes its study_id with supersede=False so
                parallel studies never invalidate each other's goals.
            objective: Research objective.
            criteria: Required criteria generated by the finance protocol.
            supersede: When True (default), the session's existing active
                goals are marked SUPERSEDED (chat 1:1 semantics). When
                False the new goal coexists with any existing active goal
                in the same session domain (study parallel semantics).
            ui_summary: Optional compact summary.
            source: Source of goal creation.
            protocol: Finance research protocol name.
            risk_tier: Risk classification.
            token_budget: Optional token budget.
            turn_budget: Optional turn budget.
            time_budget_seconds: Optional wall-clock budget.
            parent_goal_id: Optional parent goal for sub-goal decomposition.
            workflow_id: Optional workflow config name绑定到 goal.

        Returns:
            The newly active goal.

        Raises:
            ValueError: If objective or criteria are empty or live-execution.
        """
        session_id = normalize_required_text(session_id, "session_id")
        objective = normalize_required_text(objective, "goal objective")
        reject_live_execution_objective(objective)
        if risk_tier is RiskTier.LIVE_TRADING_OR_EXECUTION:
            raise ValueError("live trading or execution goals are not supported")
        cleaned_criteria = [item.strip() for item in criteria if item.strip()]
        if not cleaned_criteria:
            raise ValueError("at least one goal criterion is required")
        for criterion in cleaned_criteria:
            reject_live_execution_objective(criterion)
        budgets = {
            "token_budget": token_budget,
            "turn_budget": turn_budget,
            "time_budget_seconds": time_budget_seconds,
        }
        for name, value in budgets.items():
            if value is not None and value <= 0:
                raise ValueError(f"{name} must be positive")

        now = now_iso()
        goal_id = new_id("goal")
        summary = ui_summary.strip() or objective[:80]
        current_values = [status.value for status in _CURRENT_STATUSES]
        placeholders = ",".join("?" for _ in current_values)

        with write_transaction(self._conn):
            if supersede:
                self._conn.execute(
                    f"""
                    UPDATE goals
                    SET status = ?, updated_at = ?, completed_at = COALESCE(completed_at, ?)
                    WHERE session_id = ? AND status IN ({placeholders})
                    """,
                    [GoalStatus.SUPERSEDED.value, now, now, session_id, *current_values],
                )
            self._conn.execute(
                """
                INSERT INTO goals (
                    goal_id, session_id, status, objective, ui_summary, source,
                    protocol, risk_tier, token_budget, turn_budget,
                    time_budget_seconds, created_at, updated_at, progress_percent,
                    parent_goal_id, workflow_id, user_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    goal_id,
                    session_id,
                    GoalStatus.ACTIVE.value,
                    objective,
                    summary,
                    source,
                    protocol,
                    risk_tier.value,
                    token_budget,
                    turn_budget,
                    time_budget_seconds,
                    now,
                    now,
                    0.0,
                    parent_goal_id,
                    workflow_id,
                    user_id,
                ),
            )
            self._conn.execute(
                """
                INSERT INTO goal_claims (
                    claim_id, goal_id, session_id, claim_type, text,
                    status, created_at, updated_at
                )
                VALUES (?, ?, ?, 'thesis', ?, 'active', ?, ?)
                """,
                (new_id("claim"), goal_id, session_id, objective, now, now),
            )
            for index, text in enumerate(cleaned_criteria):
                self._conn.execute(
                    """
                    INSERT INTO goal_criteria (
                        criterion_id, goal_id, session_id, text, required,
                        status, protocol_step, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, 1, 'pending', ?, ?, ?)
                    """,
                    (
                        new_id("crit"),
                        goal_id,
                        session_id,
                        text,
                        f"step_{index + 1}",
                        now,
                        now,
                    ),
                )

        goal = self.get_goal(goal_id)
        if goal is None:
            raise RuntimeError("created goal could not be reloaded")
        return goal

    @synchronized
    def update_goal(
        self,
        *,
        session_id: str,
        goal_id: str,
        expected_goal_id: str,
        objective: str | None = None,
        ui_summary: str | None = None,
    ) -> GoalRecord:
        """Edit mutable goal metadata without superseding the active goal.

        Args:
            session_id: Owning session id.
            goal_id: Goal being mutated.
            expected_goal_id: Stale-write guard captured by the caller.
            objective: Optional replacement research objective.
            ui_summary: Optional compact display summary.

        Returns:
            Updated goal record.

        Raises:
            StaleGoalError: If the goal is stale or not current.
            ValueError: If the new objective is empty or unsafe.
        """
        with write_transaction(self._conn):
            goal = self._require_mutable_goal(session_id, goal_id, expected_goal_id)
            session_id = goal.session_id
            goal_id = goal.goal_id
            next_objective = goal.objective
            if objective is not None:
                next_objective = normalize_required_text(objective, "goal objective")
                reject_live_execution_objective(next_objective)
            next_summary = goal.ui_summary
            if ui_summary is not None:
                next_summary = ui_summary.strip() or next_objective[:80]
            elif objective is not None and goal.ui_summary == goal.objective[:80]:
                next_summary = next_objective[:80]
            now = now_iso()
            self._conn.execute(
                """
                UPDATE goals
                SET objective = ?, ui_summary = ?, updated_at = ?
                WHERE goal_id = ? AND session_id = ?
                """,
                (next_objective, next_summary, now, goal_id, session_id),
            )
            if objective is not None:
                self._conn.execute(
                    """
                    UPDATE goal_claims
                    SET text = ?, updated_at = ?
                    WHERE goal_id = ? AND session_id = ?
                        AND claim_type = 'thesis'
                        AND status = 'active'
                    """,
                    (next_objective, now, goal_id, session_id),
                )

        updated = self.get_goal(goal_id)
        if updated is None:
            raise RuntimeError("updated goal could not be reloaded")
        return updated

    @synchronized
    def get_goal(self, goal_id: str) -> GoalRecord | None:
        """Return a goal by id."""
        row = self._conn.execute(
            "SELECT * FROM goals WHERE goal_id = ?",
            (normalize_required_text(goal_id, "goal_id"),),
        ).fetchone()
        return self._goal_from_row(row) if row else None

    @synchronized
    def get_current_goal(self, session_id: str) -> GoalRecord | None:
        """Return the current goal for a session."""
        current_values = [status.value for status in _CURRENT_STATUSES]
        session_id = normalize_required_text(session_id, "session_id")
        placeholders = ",".join("?" for _ in current_values)
        row = self._conn.execute(
            f"""
            SELECT * FROM goals
            WHERE session_id = ? AND status IN ({placeholders})
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            [session_id, *current_values],
        ).fetchone()
        return self._goal_from_row(row) if row else None

    @synchronized
    def list_criteria(self, goal_id: str) -> list[GoalCriterion]:
        """Return criteria for a goal."""
        rows = self._conn.execute(
            """
            SELECT * FROM goal_criteria
            WHERE goal_id = ?
            ORDER BY
                CASE
                    WHEN protocol_step GLOB 'step_[0-9]*' THEN CAST(substr(protocol_step, 6) AS INTEGER)
                    ELSE 2147483647
                END,
                created_at,
                criterion_id
            """,
            (normalize_required_text(goal_id, "goal_id"),),
        ).fetchall()
        return [self._criterion_from_row(row) for row in rows]

    @synchronized
    def list_claims(self, goal_id: str) -> list[GoalClaim]:
        """Return claims for a goal."""
        rows = self._conn.execute(
            """
            SELECT * FROM goal_claims
            WHERE goal_id = ?
            ORDER BY created_at, claim_id
            """,
            (normalize_required_text(goal_id, "goal_id"),),
        ).fetchall()
        return [self._claim_from_row(row) for row in rows]

    @synchronized
    def list_evidence(self, goal_id: str, limit: int | None = None) -> list[EvidenceRecord]:
        """Return evidence rows for a goal."""
        goal_id = normalize_required_text(goal_id, "goal_id")
        if limit is not None and limit <= 0:
            raise ValueError("evidence limit must be positive")
        if limit is not None:
            rows = self._conn.execute(
                """
                SELECT * FROM (
                    SELECT * FROM goal_evidence
                    WHERE goal_id = ?
                    ORDER BY created_at DESC, evidence_id DESC
                    LIMIT ?
                )
                ORDER BY created_at, evidence_id
                """,
                (goal_id, limit),
            ).fetchall()
            return [self._evidence_from_row(row) for row in rows]
        rows = self._conn.execute(
            """
            SELECT * FROM goal_evidence
            WHERE goal_id = ?
            ORDER BY created_at, evidence_id
            """,
            (goal_id,),
        ).fetchall()
        return [self._evidence_from_row(row) for row in rows]

    @synchronized
    def count_evidence(self, goal_id: str) -> int:
        """Return the total evidence row count for a goal."""
        row = self._conn.execute(
            "SELECT COUNT(*) FROM goal_evidence WHERE goal_id = ?",
            (normalize_required_text(goal_id, "goal_id"),),
        ).fetchone()
        return int(row[0]) if row else 0

    @synchronized
    def list_goals(
        self,
        session_id: str | None = None,
        status: GoalStatus | None = None,
        limit: int = 100,
        user_id: str | None = None,
    ) -> list[GoalRecord]:
        """List goals, optionally filtered by session_id and/or status.

        Args:
            session_id: If provided, filter to this session only.
            status: If provided, filter to this status only.
            limit: Maximum number of goals to return (default 100).
            user_id: If provided and ``session_id`` is None, filter to this
                owner's goals only (isolation listing).

        Returns:
            List of GoalRecord objects, ordered by created_at DESC.
        """
        query = "SELECT * FROM goals WHERE 1=1"
        params: list = []
        if session_id:
            query += " AND session_id = ?"
            params.append(session_id)
        if not session_id and user_id:
            query += " AND user_id = ?"
            params.append(user_id)
        if status:
            query += " AND status = ?"
            params.append(status.value)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        with self._lock:
            rows = self._conn.execute(query, params).fetchall()
        return [self._goal_from_row(row) for row in rows]

    def delete_session_goals(self, session_id: str) -> int:
        """Delete all goal ledger rows for a session.

        Args:
            session_id: Session whose goal ledger should be removed.

        Returns:
            Number of goal rows deleted.
        """
        session_id = normalize_required_text(session_id, "session_id")
        with write_transaction(self._conn):
            row = self._conn.execute(
                "SELECT COUNT(*) FROM goals WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            count = int(row[0]) if row else 0
            for table in (
                "goal_audits",
                "goal_evidence",
                "goal_criteria",
                "goal_claims",
                "goals",
            ):
                self._conn.execute(f"DELETE FROM {table} WHERE session_id = ?", (session_id,))
        return count

    @synchronized
    def get_current_snapshot(self, session_id: str) -> dict | None:
        """Return the current goal plus ledger rows for a session."""
        goal = self.get_current_goal(session_id)
        if goal is None:
            return None
        return self.get_goal_snapshot(goal.goal_id)

    @synchronized
    def get_goal_snapshot(self, goal_id: str, evidence_limit: int | None = 50) -> dict | None:
        """Return a JSON-safe goal snapshot."""
        goal = self.get_goal(goal_id)
        if goal is None:
            return None
        evidence = self.list_evidence(goal.goal_id, limit=evidence_limit)
        return {
            "goal": _to_json_dict(goal),
            "claims": [_to_json_dict(item) for item in self.list_claims(goal.goal_id)],
            "criteria": [_to_json_dict(item) for item in self.list_criteria(goal.goal_id)],
            "evidence": [_to_json_dict(item) for item in evidence],
            "evidence_count": self.count_evidence(goal.goal_id),
        }

    @synchronized
    def append_evidence(
        self,
        *,
        session_id: str,
        goal_id: str,
        expected_goal_id: str,
        evidence: EvidenceInput,
    ) -> EvidenceRecord:
        """Append traceable evidence after stale-goal validation.

        Args:
            session_id: Owning session id.
            goal_id: Goal being mutated.
            expected_goal_id: Goal id captured at the start of the agent turn.
            evidence: Evidence payload.

        Returns:
            Persisted evidence record.

        Raises:
            StaleGoalError: If the expected goal id does not match or goal is not current.
            ValueError: If evidence text is empty or references an unknown criterion.
        """
        evidence_id = new_id("ev")
        with write_transaction(self._conn):
            goal = self._require_mutable_goal(session_id, goal_id, expected_goal_id)
            session_id = goal.session_id
            goal_id = goal.goal_id
            text = evidence.text.strip()
            if not text:
                raise ValueError("evidence text cannot be empty")
            if evidence.criterion_id is not None:
                self._require_criterion(goal.goal_id, evidence.criterion_id)
            if evidence.claim_id is not None:
                self._require_claim(goal.goal_id, evidence.claim_id)

            now = now_iso()
            freshness_status = "fresh" if evidence.data_as_of else "unknown"
            verification_status = self._verification_status(evidence)
            self._conn.execute(
                """
                INSERT INTO goal_evidence (
                    evidence_id, goal_id, session_id, criterion_id, claim_id,
                    evidence_type, text, tool_call_id, run_id, source_provider,
                    source_type, source_uri, symbol_universe_json, benchmark_json,
                    timeframe, method, assumptions_json, artifact_path,
                    artifact_hash, retrieved_at, data_as_of, freshness_status,
                    verification_status, confidence, caveat,
                    contradicts_claim_ids_json, hypothesis_id, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    evidence_id,
                    goal_id,
                    session_id,
                    evidence.criterion_id,
                    evidence.claim_id,
                    evidence.evidence_type,
                    text,
                    evidence.tool_call_id,
                    evidence.run_id,
                    evidence.source_provider,
                    evidence.source_type,
                    evidence.source_uri,
                    json_dumps(evidence.symbol_universe),
                    json_dumps(evidence.benchmark),
                    evidence.timeframe,
                    evidence.method,
                    json_dumps(evidence.assumptions),
                    evidence.artifact_path,
                    evidence.artifact_hash,
                    now,
                    evidence.data_as_of,
                    freshness_status,
                    verification_status,
                    evidence.confidence,
                    evidence.caveat,
                    json_dumps(evidence.contradicts_claim_ids),
                    evidence.hypothesis_id,
                    now,
                ),
            )
            if evidence.criterion_id is not None:
                self._conn.execute(
                    """
                    UPDATE goal_criteria
                    SET status = 'covered', updated_at = ?
                    WHERE goal_id = ? AND session_id = ? AND criterion_id = ?
                        AND status IN ('pending', 'open', 'unsatisfied')
                    """,
                    (now, goal_id, session_id, evidence.criterion_id),
                )

            # P3-B: Recompute progress_percent after evidence addition
            self._update_progress(goal_id)

        record = self._get_evidence(evidence_id)
        if record is None:
            raise RuntimeError("created evidence could not be reloaded")
        return record

    @synchronized
    def update_status(
        self,
        *,
        session_id: str,
        goal_id: str,
        expected_goal_id: str,
        status: GoalStatus,
        audit: list[AuditRow] | None = None,
        recap: str | None = None,
    ) -> GoalRecord:
        """Update a goal status with stale-goal and completion validation."""
        with write_transaction(self._conn):
            goal = self._require_mutable_goal(session_id, goal_id, expected_goal_id)
            session_id = goal.session_id
            goal_id = goal.goal_id
            if status is GoalStatus.COMPLETE:
                self._validate_completion_audit(goal, audit or [])

            now = now_iso()
            completed_at = now if status in {
                GoalStatus.COMPLETE,
                GoalStatus.BLOCKED,
                GoalStatus.CANCELLED,
                GoalStatus.SUPERSEDED,
                GoalStatus.USAGE_LIMITED,
            } else None
            self._conn.execute(
                """
                UPDATE goals
                SET status = ?, updated_at = ?, completed_at = COALESCE(?, completed_at),
                    recap = COALESCE(?, recap)
                WHERE goal_id = ? AND session_id = ?
                """,
                (status.value, now, completed_at, recap, goal_id, session_id),
            )
            if audit:
                self._conn.execute(
                    """
                    INSERT INTO goal_audits (
                        audit_id, goal_id, session_id, audit_type, result,
                        rows_json, created_at
                    )
                    VALUES (?, ?, ?, 'completion', ?, ?, ?)
                    """,
                    (
                        new_id("audit"),
                        goal_id,
                        session_id,
                        status.value,
                        json_dumps([row.__dict__ for row in audit]),
                        now,
                    ),
                )
            if audit and status is GoalStatus.COMPLETE:
                for row in audit:
                    self._conn.execute(
                        """
                        UPDATE goal_criteria
                        SET status = ?, updated_at = ?
                        WHERE goal_id = ? AND session_id = ? AND criterion_id = ?
                        """,
                        (row.result, now, goal_id, session_id, row.criterion_id),
                    )

            # P3-B: Recompute progress_percent after status update
            self._update_progress(goal_id)

            # P3-D2: On COMPLETE, trigger goal completion hooks (hypothesis monitoring)
            if status is GoalStatus.COMPLETE:
                self._on_goal_complete(session_id, goal_id)

        updated = self.get_goal(goal_id)
        if updated is None:
            raise RuntimeError("updated goal could not be reloaded")
        return updated

    def complete_lite(
        self,
        *,
        session_id: str,
        goal_id: str,
        expected_goal_id: str,
        recap: str | None = None,
    ) -> GoalRecord:
        """Complete a goal in lite mode — evidence coverage only, no audit.

        Lite mode verifies that every required criterion has at least one
        linked evidence record, but does NOT require audit rows or verified
        status.  Suitable for quick research and exploratory analysis.

        Args:
            session_id: Owning session id.
            goal_id: Goal to complete.
            expected_goal_id: Stale-write guard (must match goal_id).
            recap: Optional completion summary.

        Returns:
            Updated GoalRecord.

        Raises:
            StaleGoalError: If goal is not current or id mismatch.
            ValueError: If required criteria lack evidence.
        """
        with write_transaction(self._conn):
            goal = self._require_mutable_goal(session_id, goal_id, expected_goal_id)
            session_id = goal.session_id
            goal_id = goal.goal_id

            # Lite validation: every required criterion must have evidence
            criteria = self.list_criteria(goal_id)
            evidence = self.list_evidence(goal_id)
            evidence_by_criterion: dict[str, list] = {}
            for ev in evidence:
                cid = ev.criterion_id
                if cid:
                    evidence_by_criterion.setdefault(cid, []).append(ev)

            for criterion in criteria:
                if not criterion.required:
                    continue
                if not evidence_by_criterion.get(criterion.criterion_id):
                    raise ValueError(
                        f"Criterion {criterion.criterion_id} ({criterion.text}) "
                        "lacks evidence — cannot complete in lite mode"
                    )

            now = now_iso()
            self._conn.execute(
                """
                UPDATE goals
                SET status = ?, updated_at = ?, completed_at = COALESCE(?, completed_at),
                    recap = COALESCE(?, recap)
                WHERE goal_id = ? AND session_id = ?
                """,
                (GoalStatus.COMPLETE.value, now, now, recap, goal_id, session_id),
            )

            # Write a synthetic audit row for record-keeping
            self._conn.execute(
                """
                INSERT INTO goal_audits (
                    audit_id, goal_id, session_id, audit_type, result,
                    rows_json, created_at
                )
                VALUES (?, ?, ?, 'completion_lite', ?, ?, ?)
                """,
                (
                    new_id("audit"),
                    goal_id,
                    session_id,
                    GoalStatus.COMPLETE.value,
                    json_dumps([{
                        "criterion_id": c.criterion_id,
                        "result": "satisfied",
                        "evidence_ids": [
                            ev.evidence_id for ev in evidence
                            if ev.criterion_id == c.criterion_id
                        ],
                        "notes": "(lite completion)",
                    } for c in criteria if c.required]),
                    now,
                ),
            )

            self._update_progress(goal_id)
            self._on_goal_complete(session_id, goal_id)

        updated = self.get_goal(goal_id)
        if updated is None:
            raise RuntimeError("updated goal could not be reloaded")
        return updated

    def _on_goal_complete(self, session_id: str, goal_id: str) -> None:
        """Hook fired when a goal reaches COMPLETE status.

        P3-D2: Auto-transition linked hypotheses to monitoring.
        Failures are swallowed (logged at most) to avoid breaking the loop.
        """
        try:
            from strategy_research.apps.hypothesis import HypothesisRegistry
            registry = HypothesisRegistry()
            linked = registry.list_by_goal(goal_id)
            for hyp in linked:
                # Only transition if in a continuable state
                if hyp.status in ("validated", "testing"):
                    try:
                        registry.update(
                            hyp.hypothesis_id,
                            status="monitoring",
                            invalidation_notes=(
                                f"{hyp.invalidation_notes}\nGoal {goal_id} completed"
                                if hyp.invalidation_notes
                                else f"Goal {goal_id} completed"
                            ),
                        )
                        logger.info(
                            "Goal-complete hook: hypothesis %s -> monitoring",
                            hyp.hypothesis_id,
                        )
                    except ValueError as exc:
                        logger.debug(
                            "Hypothesis %s transition skipped: %s",
                            hyp.hypothesis_id, exc,
                        )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Goal-complete hook failed: %s", exc)

    @synchronized
    def account_usage(
        self,
        *,
        session_id: str,
        goal_id: str,
        expected_goal_id: str,
        token_delta: int = 0,
        time_delta_seconds: int = 0,
        turn_delta: int = 0,
    ) -> GoalRecord:
        """Account usage and move the goal to budget_limited if needed."""
        if min(token_delta, time_delta_seconds, turn_delta) < 0:
            raise ValueError("usage deltas must be non-negative")

        with write_transaction(self._conn):
            goal = self._require_mutable_goal(session_id, goal_id, expected_goal_id)
            session_id = goal.session_id
            goal_id = goal.goal_id
            tokens_used = goal.tokens_used + token_delta
            time_used_seconds = goal.time_used_seconds + time_delta_seconds
            turns_used = goal.turns_used + turn_delta
            crosses_budget = (
                (goal.token_budget is not None and tokens_used >= goal.token_budget)
                or (
                    goal.time_budget_seconds is not None
                    and time_used_seconds >= goal.time_budget_seconds
                )
                or (goal.turn_budget is not None and turns_used >= goal.turn_budget)
            )
            next_status = GoalStatus.BUDGET_LIMITED if crosses_budget else goal.status
            now = now_iso()
            self._conn.execute(
                """
                UPDATE goals
                SET tokens_used = ?, time_used_seconds = ?, turns_used = ?,
                    status = ?, updated_at = ?
                WHERE goal_id = ? AND session_id = ?
                """,
                (
                    tokens_used,
                    time_used_seconds,
                    turns_used,
                    next_status.value,
                    now,
                    goal_id,
                    session_id,
                ),
            )

        updated = self.get_goal(goal_id)
        if updated is None:
            raise RuntimeError("usage-updated goal could not be reloaded")
        return updated

    def _require_mutable_goal(
        self,
        session_id: str,
        goal_id: str,
        expected_goal_id: str,
    ) -> GoalRecord:
        """Validate a goal is writable (stale + status guards only).

        v2 (decision D): the session is NOT part of the write guard — any
        executor identity (e.g. a study's micro session "study:{id}") may
        write to a goal it holds the goal_id/expected_goal_id for. Session
        remains a record field (query convenience); IDOR protection lives
        at the API layer (_fetch_session_owned).
        """
        if expected_goal_id != goal_id:
            raise StaleGoalError("expected_goal_id does not match target goal")
        session_id = normalize_required_text(session_id, "session_id")
        goal_id = normalize_required_text(goal_id, "goal_id")
        goal = self.get_goal(goal_id)
        if goal is None:
            raise StaleGoalError("goal not found")
        if goal.status not in _CURRENT_STATUSES:
            raise StaleGoalError(f"goal status {goal.status.value!r} is not mutable")
        return goal

    @staticmethod
    def _verification_status(evidence: EvidenceInput) -> str:
        """Return whether evidence has a traceable local artifact/run source."""
        if evidence.artifact_path:
            artifact = _safe_artifact_path(evidence.artifact_path)
            if artifact and artifact.is_file():
                if GoalStore._artifact_hash_matches(artifact, evidence.artifact_hash):
                    return "verified"
        if evidence.run_id:
            run_dir = _safe_run_id(evidence.run_id)
            if run_dir and run_dir.is_dir():
                return "verified"
        return "unverified"

    @staticmethod
    def _artifact_hash_matches(path: Path, expected_hash: str | None) -> bool:
        if not expected_hash:
            return False
        try:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            return False
        return digest == expected_hash.lower().removeprefix("sha256:")

    def _require_criterion(self, goal_id: str, criterion_id: str) -> GoalCriterion:
        # Normalize criterion_id: remove leading colon if present
        criterion_id = criterion_id.lstrip(':')
        row = self._conn.execute(
            """
            SELECT * FROM goal_criteria
            WHERE goal_id = ? AND criterion_id = ?
            """,
            (goal_id, criterion_id),
        ).fetchone()
        if row is None:
            raise ValueError(f"unknown criterion_id: {criterion_id}")
        return self._criterion_from_row(row)

    def _require_claim(self, goal_id: str, claim_id: str) -> GoalClaim:
        row = self._conn.execute(
            """
            SELECT * FROM goal_claims
            WHERE goal_id = ? AND claim_id = ?
            """,
            (goal_id, claim_id),
        ).fetchone()
        if row is None:
            raise ValueError(f"unknown claim_id: {claim_id}")
        return self._claim_from_row(row)

    def _validate_completion_audit(
        self,
        goal: GoalRecord,
        audit: list[AuditRow],
    ) -> None:
        criteria = self.list_criteria(goal.goal_id)
        rows_by_criterion = {row.criterion_id: row for row in audit}
        for criterion in criteria:
            if not criterion.required:
                continue
            row = rows_by_criterion.get(criterion.criterion_id)
            if row is None:
                raise ValueError(f"missing audit row for criterion {criterion.criterion_id}")
            if row.result not in _COMPLETION_RESULTS:
                raise ValueError(f"criterion {criterion.criterion_id} is not satisfied")
            if row.result in {"satisfied", "satisfied_with_caveat"} and not row.evidence_ids:
                raise ValueError("complete goals require verified evidence")
            if row.result == "not_applicable_user_accepted" and not row.notes.strip():
                raise ValueError("not-applicable criteria require acceptance notes")
            has_verified_evidence = False
            for evidence_id in row.evidence_ids:
                evidence = self._get_evidence(evidence_id)
                if evidence is None or evidence.goal_id != goal.goal_id:
                    raise ValueError(f"unknown evidence_id: {evidence_id}")
                if evidence.criterion_id != criterion.criterion_id:
                    raise ValueError(
                        f"evidence {evidence_id} does not match criterion {criterion.criterion_id}"
                    )
                if evidence.verification_status == "verified":
                    has_verified_evidence = True
            if row.result in {"satisfied", "satisfied_with_caveat"} and not has_verified_evidence:
                raise ValueError("complete goals require verified evidence")

    def _get_evidence(self, evidence_id: str) -> EvidenceRecord | None:
        row = self._conn.execute(
            "SELECT * FROM goal_evidence WHERE evidence_id = ?",
            (evidence_id,),
        ).fetchone()
        return self._evidence_from_row(row) if row else None

    # ── Progress computation (P3-B) ─────────────────────────

    def _compute_progress(self, goal_id: str) -> float:
        """Compute progress_percent for a goal based on covered required criteria.

        Returns:
            Float in [0.0, 100.0]. Goals with no required criteria return 100.0.
        """

        # Read-only queries — use direct connection access (no transaction needed)
        goal_row = self._conn.execute(
            "SELECT * FROM goals WHERE goal_id = ?", (goal_id,)
        ).fetchone()
        if goal_row is None:
            return 0.0

        criteria_rows = self._conn.execute(
            "SELECT * FROM goal_criteria WHERE goal_id = ? ORDER BY created_at",
            (goal_id,),
        ).fetchall()
        evidence_rows = self._conn.execute(
            "SELECT * FROM goal_evidence WHERE goal_id = ? ORDER BY created_at",
            (goal_id,),
        ).fetchall()

        covered = 0
        required_total = 0
        for crit_row in criteria_rows:
            if not crit_row["required"]:
                continue
            required_total += 1
            criterion_id = crit_row["criterion_id"]
            if crit_row["status"] not in {"", "pending", "open", "unsatisfied", "missing", "stale", "too_weak"}:
                covered += 1
                continue
            for ev_row in evidence_rows:
                if ev_row["criterion_id"] == criterion_id:
                    covered += 1
                    break

        if required_total == 0:
            return 100.0
        return round(min(100.0, max(0.0, covered / required_total * 100.0)), 2)

    def _update_progress(self, goal_id: str) -> None:
        """Recompute and persist progress_percent for a goal.

        Must be called within an open write transaction (BEGIN IMMEDIATE).
        """
        pct = self._compute_progress(goal_id)
        self._conn.execute(
            "UPDATE goals SET progress_percent = ?, updated_at = ? WHERE goal_id = ?",
            (pct, now_iso(), goal_id),
        )

    def get_current_snapshot_by_id(self, goal_id: str) -> dict[str, Any] | None:
        """Return a snapshot dict for any goal by id (not just current)."""
        goal_row = self._conn.execute(
            "SELECT * FROM goals WHERE goal_id = ?", (goal_id,)
        ).fetchone()
        if goal_row is None:
            return None
        goal = self._goal_from_row(goal_row)
        criteria_rows = self._conn.execute(
            "SELECT * FROM goal_criteria WHERE goal_id = ? ORDER BY created_at",
            (goal_id,),
        ).fetchall()
        evidence_rows = self._conn.execute(
            "SELECT * FROM goal_evidence WHERE goal_id = ? ORDER BY created_at",
            (goal_id,),
        ).fetchall()
        return {
            "goal": _to_json_dict(goal),
            "criteria": [dict(r) for r in criteria_rows],
            "evidence": [dict(r) for r in evidence_rows],
            "evidence_count": len(evidence_rows),
        }

    # ── Sub-goal decomposition (P3-B) ────────────────────────

    @synchronized
    def decompose_goal(
        self,
        *,
        parent_goal_id: str,
        sub_objectives: list[str],
        default_criteria: list[str] | None = None,
    ) -> list[GoalRecord]:
        """Decompose a parent goal into multiple sub-goals.

        Each sub-objective becomes a new active goal with the parent_goal_id set.
        Sub-goals share the parent's session_id.

        Args:
            parent_goal_id: The goal to decompose.
            sub_objectives: List of sub-goal objectives (must be non-empty).
            default_criteria: Criteria applied to each sub-goal. Defaults to
                ``default_goal_criteria()``.

        Returns:
            List of newly created sub-goals.
        """
        parent_row = self._conn.execute(
            "SELECT * FROM goals WHERE goal_id = ?", (parent_goal_id,)
        ).fetchone()
        if parent_row is None:
            raise ValueError(f"unknown parent_goal_id: {parent_goal_id}")
        parent = self._goal_from_row(parent_row)

        if not sub_objectives:
            raise ValueError("sub_objectives cannot be empty")

        from .context import default_goal_criteria
        criteria = default_criteria or default_goal_criteria()

        sub_goals: list[GoalRecord] = []
        for obj in sub_objectives:
            sub = self.replace_goal(
                session_id=parent.session_id,
                objective=obj,
                criteria=criteria,
                ui_summary=f"[sub of {parent_goal_id}] {obj[:60]}",
                source="decomposition",
                protocol=parent.protocol,
                risk_tier=parent.risk_tier,
                token_budget=parent.token_budget,
                turn_budget=parent.turn_budget,
                time_budget_seconds=parent.time_budget_seconds,
                parent_goal_id=parent_goal_id,
            )
            sub_goals.append(sub)
        return sub_goals

    @synchronized
    def list_sub_goals(self, parent_goal_id: str) -> list[GoalRecord]:
        """Return all sub-goals of a parent goal (any status)."""
        rows = self._conn.execute(
            "SELECT * FROM goals WHERE parent_goal_id = ? ORDER BY created_at",
            (parent_goal_id,),
        ).fetchall()
        return [self._goal_from_row(r) for r in rows]

    def list_parent_goals(self, child_goal_id: str) -> list[GoalRecord]:
        """Return the chain of ancestors for a sub-goal (parent, grandparent, ...)."""
        chain: list[GoalRecord] = []
        current_id: str | None = child_goal_id
        seen: set[str] = set()
        while current_id and current_id not in seen:
            seen.add(current_id)
            row = self._conn.execute(
                "SELECT * FROM goals WHERE goal_id = ?", (current_id,)
            ).fetchone()
            if row is None:
                break
            goal = self._goal_from_row(row)
            if goal.parent_goal_id is None:
                break
            parent_row = self._conn.execute(
                "SELECT * FROM goals WHERE goal_id = ?", (goal.parent_goal_id,)
            ).fetchone()
            if parent_row is None:
                break
            parent = self._goal_from_row(parent_row)
            chain.append(parent)
            current_id = parent.goal_id
        return chain

    # ── AEGIS: goal_journal CRUD ──────────────────────────────────────

    @synchronized
    def append_journal_entry(
        self,
        goal_id: str,
        session_id: str,
        round_num: int,
        hypothesis_id: str,
        label: str,
        levers: list[str] | None = None,
        predicted_affected: list[str] | None = None,
        changeset: dict | None = None,
        retry_rationale: str | None = None,
        predictions: dict | None = None,
    ) -> "JournalEntry":
        """Append a journal entry for a round's hypothesis.

        ``predictions`` carries the falsifiable claims captured BEFORE the
        backtest runs (at the novelty gate). Missing/empty predictions are
        stored as ``{}`` — claims tracking is optional by design.
        """
        from .models import JournalEntry
        now = now_iso()
        entry_id = new_id("journal")
        with write_transaction(self._conn):
            self._conn.execute(
                """
                INSERT INTO goal_journal (
                    entry_id, goal_id, session_id, round_num,
                    hypothesis_id, label, levers_json,
                    predicted_affected_json, gating_outcome,
                    gating_attribution_json, changeset_json,
                    retry_rationale, archived_reason,
                    predictions_json, prediction_outcome_json,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', '{}', ?, ?, NULL, ?, '{}', ?, ?)
                """,
                (
                    entry_id, goal_id, session_id, round_num,
                    hypothesis_id, label, json_dumps(levers or []),
                    json_dumps(predicted_affected or []),
                    json_dumps(changeset) if changeset else None,
                    retry_rationale,
                    json_dumps(predictions or {}),
                    now, now,
                ),
            )
        return JournalEntry(
            entry_id=entry_id, goal_id=goal_id, session_id=session_id,
            round_num=round_num, hypothesis_id=hypothesis_id, label=label,
            levers=levers or [], predicted_affected=predicted_affected or [],
            predictions=dict(predictions or {}),
            gating_outcome="pending", changeset=changeset,
            retry_rationale=retry_rationale, created_at=now, updated_at=now,
        )

    @synchronized
    def fill_journal_attribution(
        self,
        goal_id: str,
        session_id: str,
        round_num: int,
        outcome: str,
        attribution: dict,
        levers: list[str] | None = None,
        changeset: dict | None = None,
    ) -> bool:
        """Update the latest journal entry for a round with attribution result.

        ``levers`` / ``changeset`` optionally backfill fields left empty by
        the pre-backtest claims capture (two-phase write pattern).
        """
        now = now_iso()
        with write_transaction(self._conn):
            cur = self._conn.execute(
                """
                UPDATE goal_journal
                SET gating_outcome = ?, gating_attribution_json = ?,
                    levers_json = COALESCE(?, levers_json),
                    changeset_json = COALESCE(?, changeset_json),
                    updated_at = ?
                WHERE goal_id = ? AND session_id = ? AND round_num = ?
                  AND gating_outcome = 'pending'
                """,
                (outcome, json_dumps(attribution),
                 json_dumps(levers) if levers is not None else None,
                 json_dumps(changeset) if changeset else None,
                 now, goal_id, session_id, round_num),
            )
        return cur.rowcount > 0

    @synchronized
    def fill_journal_prediction_outcome(
        self,
        goal_id: str,
        session_id: str,
        round_num: int,
        outcome: dict,
    ) -> bool:
        """Backfill the prediction outcome on a round's journal entry.

        Called post-backtest with ``validate_predictions`` output, e.g.
        ``{"sharpe": {"actual": 0.55, "abs_error": 0.25,
        "direction_correct": true, "within_tolerance": true}}``.
        Returns False when the round has no pending journal entry.
        """
        now = now_iso()
        with write_transaction(self._conn):
            cur = self._conn.execute(
                """
                UPDATE goal_journal
                SET prediction_outcome_json = ?, updated_at = ?
                WHERE goal_id = ? AND session_id = ? AND round_num = ?
                  AND prediction_outcome_json = '{}'
                """,
                (json_dumps(outcome), now, goal_id, session_id, round_num),
            )
        return cur.rowcount > 0

    @synchronized
    def list_journal_entries(
        self, goal_id: str, limit: int = 50
    ) -> list["JournalEntry"]:
        """Return journal entries for a goal, newest first."""
        rows = self._conn.execute(
            "SELECT * FROM goal_journal WHERE goal_id = ? "
            "ORDER BY round_num DESC, created_at DESC LIMIT ?",
            (goal_id, limit),
        ).fetchall()
        return [self._journal_from_row(r) for r in rows]

    @synchronized
    def get_latest_journal_entry(
        self, goal_id: str
    ) -> "JournalEntry | None":
        """Return the most recent journal entry for a goal."""
        row = self._conn.execute(
            "SELECT * FROM goal_journal WHERE goal_id = ? "
            "ORDER BY round_num DESC, created_at DESC LIMIT 1",
            (goal_id,),
        ).fetchone()
        return self._journal_from_row(row) if row else None

    @synchronized
    def check_novelty(
        self,
        goal_id: str,
        hypothesis_id: str,
        levers: list[str],
        predicted_affected: list[str],
    ) -> tuple[bool, str | None]:
        """Check if a hypothesis is novel enough to proceed.

        Returns (is_novel, reason). Blocks if:
        1. Exact hypothesis_id duplicate
        2. Signature duplicate (same levers + predicted_affected, needs retry_rationale)
        3. Label similarity > 0.85
        """
        entries = self.list_journal_entries(goal_id, limit=50)
        for entry in entries:
            if entry.hypothesis_id == hypothesis_id:
                return False, f"duplicate hypothesis_id: {hypothesis_id}"
            if (set(entry.levers) == set(levers)
                    and set(entry.predicted_affected) == set(predicted_affected)):
                if entry.gating_outcome == "reverted":
                    return False, (
                        f"signature duplicate (reverted): "
                        f"levers={levers}, tasks={predicted_affected}"
                    )
            # Label similarity > 0.85
            if entry.label and hypothesis_id:
                if _label_similarity(entry.label, hypothesis_id) > 0.85:
                    return False, f"similar label: {entry.label[:40]}"
        return True, None

    @synchronized
    def check_regression(
        self,
        goal_id: str,
        attribution: dict[str, str],
    ) -> tuple[bool, list[str]]:
        """Check if attribution reveals any regression of previously-solved tasks.

        Returns (passes, regressed_tasks). If any predicted task regressed,
        the round is soft-flagged (not hard-rejected per user decision).
        """
        regressed = [
            tid for tid, outcome in attribution.items()
            if outcome == "regressed"
        ]
        return len(regressed) == 0, regressed

    @synchronized
    def archive_rejected_edit(
        self,
        goal_id: str,
        round_num: int,
        hypothesis_id: str,
        reason: str,
        detail: str,
    ) -> bool:
        """Archive a rejected edit by setting archived_reason on the latest entry."""
        now = now_iso()
        with write_transaction(self._conn):
            cur = self._conn.execute(
                """
                UPDATE goal_journal
                SET archived_reason = ?, updated_at = ?
                WHERE goal_id = ? AND round_num = ? AND hypothesis_id = ?
                  AND archived_reason IS NULL
                """,
                (f"{reason}: {detail}", now, goal_id, round_num, hypothesis_id),
            )
        return cur.rowcount > 0

    @synchronized
    def build_journal_context(
        self,
        goal_id: str,
        current_round: int,
        recent_window: int = 5,
    ) -> str:
        """Build a Markdown context string from recent journal entries.

        Injected into the researcher prompt so the LLM sees what has
        been tried and what worked/failed.
        """
        entries = self.list_journal_entries(goal_id, limit=recent_window)
        if not entries:
            return ""
        lines = ["<journal-history>", "跨轮次进化记忆（最近实验）："]
        for e in entries:
            outcome_tag = f"[{e.gating_outcome}]" if e.gating_outcome != "pending" else ""
            regressed = [
                tid for tid, out in (e.gating_attribution or {}).items()
                if out == "regressed"
            ]
            regressed_tag = f" ⚠回归: {','.join(regressed)}" if regressed else ""
            lines.append(
                f"  R{e.round_num} {outcome_tag} "
                f"hypothesis: {e.label[:60]}; "
                f"levers: {','.join(e.levers)}; "
                f"tasks: {','.join(e.predicted_affected)}"
                f"{regressed_tag}"
            )
        # Claims calibration (F: falsifiable-prediction self-calibration)
        from ..study.method.claims import summarize_prediction_accuracy
        stats = summarize_prediction_accuracy(entries)
        if stats["n_predictions"]:
            hit = stats["direction_hit_rate"]
            err = stats["mean_abs_error"]
            lines.append(
                f"  <claims-calibration n={stats['n_predictions']}"
                f" validated={stats['n_validated']}"
                f" direction_hit_rate={hit}"
                f" mean_abs_error={err}>"
                "你过去的预测命中率如上——据此校准本轮 predictions 的方向与幅度。"
            )
        lines.append("</journal-history>")
        return "\n".join(lines)

    def _journal_from_row(self, row: sqlite3.Row) -> "JournalEntry":
        from .models import JournalEntry
        return JournalEntry(
            entry_id=row["entry_id"],
            goal_id=row["goal_id"],
            session_id=row["session_id"],
            round_num=row["round_num"],
            hypothesis_id=row["hypothesis_id"],
            label=row["label"],
            levers=cast(list, json_loads(row["levers_json"], [])),
            predicted_affected=cast(list, json_loads(row["predicted_affected_json"], [])),
            predictions=cast(dict, json_loads(row["predictions_json"], {})),
            prediction_outcome=cast(dict, json_loads(row["prediction_outcome_json"], {})),
            gating_outcome=row["gating_outcome"],
            gating_attribution=cast(dict, json_loads(row["gating_attribution_json"], {})),
            changeset=json_loads(row["changeset_json"], None),
            retry_rationale=row["retry_rationale"],
            archived_reason=row["archived_reason"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _goal_from_row(row: sqlite3.Row) -> GoalRecord:
        return GoalRecord(
            goal_id=row["goal_id"],
            session_id=row["session_id"],
            status=GoalStatus(row["status"]),
            objective=row["objective"],
            ui_summary=row["ui_summary"],
            source=row["source"],
            protocol=row["protocol"],
            risk_tier=RiskTier(row["risk_tier"]),
            token_budget=row["token_budget"],
            tokens_used=row["tokens_used"],
            turn_budget=row["turn_budget"],
            turns_used=row["turns_used"],
            time_budget_seconds=row["time_budget_seconds"],
            time_used_seconds=row["time_used_seconds"],
            budget_wrapup_sent=bool(row["budget_wrapup_sent"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            completed_at=row["completed_at"],
            recap=row["recap"],
            progress_percent=float(row["progress_percent"]) if row["progress_percent"] is not None else 0.0,
            parent_goal_id=row["parent_goal_id"] if "parent_goal_id" in row.keys() else None,
            workflow_id=row["workflow_id"] if "workflow_id" in row.keys() else None,
            user_id=row["user_id"] if "user_id" in row.keys() else None,
        )

    @staticmethod
    def _criterion_from_row(row: sqlite3.Row) -> GoalCriterion:
        return GoalCriterion(
            criterion_id=row["criterion_id"],
            goal_id=row["goal_id"],
            session_id=row["session_id"],
            text=row["text"],
            required=bool(row["required"]),
            status=row["status"],
            freshness_requirement=row["freshness_requirement"],
            protocol_step=row["protocol_step"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _claim_from_row(row: sqlite3.Row) -> GoalClaim:
        return GoalClaim(
            claim_id=row["claim_id"],
            goal_id=row["goal_id"],
            session_id=row["session_id"],
            claim_type=row["claim_type"],
            text=row["text"],
            status=row["status"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _evidence_from_row(row: sqlite3.Row) -> EvidenceRecord:
        return EvidenceRecord(
            evidence_id=row["evidence_id"],
            goal_id=row["goal_id"],
            session_id=row["session_id"],
            text=row["text"],
            criterion_id=row["criterion_id"],
            claim_id=row["claim_id"],
            evidence_type=row["evidence_type"],
            tool_call_id=row["tool_call_id"],
            run_id=row["run_id"],
            source_provider=row["source_provider"],
            source_type=row["source_type"],
            source_uri=row["source_uri"],
            symbol_universe=cast(list, json_loads(row["symbol_universe_json"], [])),
            benchmark=cast(list, json_loads(row["benchmark_json"], [])),
            timeframe=row["timeframe"],
            method=row["method"],
            assumptions=cast(dict, json_loads(row["assumptions_json"], {})),
            artifact_path=row["artifact_path"],
            artifact_hash=row["artifact_hash"],
            retrieved_at=row["retrieved_at"],
            data_as_of=row["data_as_of"],
            freshness_status=row["freshness_status"],
            verification_status=row["verification_status"],
            confidence=row["confidence"],
            caveat=row["caveat"],
            contradicts_claim_ids=cast(list, json_loads(row["contradicts_claim_ids_json"], [])),
            hypothesis_id=row["hypothesis_id"] if "hypothesis_id" in row.keys() else None,
            created_at=row["created_at"],
        )
