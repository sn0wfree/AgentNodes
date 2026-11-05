"""SQLite-backed hypothesis storage (P3-E).

Replaces the JSON-file storage in registry.py with a SQLite database that
supports FTS5 full-text search, indexed queries, and concurrent access.

Schema:
  hypotheses       — main records
  hypothesis_relations  — graph edges (parent, related, contradicts)
  hypothesis_goals      — M:N relationship to research goals

Migration (v2 design §14.2): the legacy JSON file is intentionally NOT
imported; it is left in place but never read.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from pathlib import Path
from typing import Any

from strategy_research.foundation.storage.sqlite import (
    connect,
    resolve_db_path,
    write_transaction,
)
from .registry import (
    VALID_TRANSITIONS,
    Hypothesis,
    _coerce_str_list,
    _tokenize,
    _utc_now,
    _validate_status,
)

logger = logging.getLogger(__name__)


_ENV_PATH = "QUANTNODES_RESEARCH_HYPOTHESES_DB_PATH"


def _apply_scalar_fields(hyp: Any, fields: dict) -> None:
    """Set stripped scalar fields that are not None (by attribute name)."""
    for attr, value in fields.items():
        if value is not None:
            setattr(hyp, attr, value.strip())


def _apply_status(hyp: Any, status: str) -> None:
    """Validate and apply a status change on a hypothesis."""
    new_status = _validate_status(status)
    if new_status != hyp.status:
        allowed = VALID_TRANSITIONS.get(hyp.status, set())
        if new_status not in allowed:
            raise ValueError(
                f"invalid hypothesis transition: {hyp.status} -> {new_status}. "
                f"Allowed: {sorted(allowed) or '(terminal)'}"
            )
    hyp.status = new_status


def default_db_path() -> Path:
    """Return the configured hypothesis database path.

    Order:
        1. QUANTNODES_RESEARCH_HYPOTHESES_DB_PATH env var
        2. ~/.quantnodes-research/hypotheses.db (default)
    """
    return resolve_db_path("hypotheses.db", _ENV_PATH)


class HypothesisStore:
    """SQLite-backed hypothesis storage with FTS5 search.

    Thread-safe via RLock. All writes go through explicit write transactions.
    """

    def __init__(self, db_path: Path | None = None) -> None:
        """Initialize the store.

        Args:
            db_path: Optional database path. Defaults to env override or
                ``~/.quantnodes-research/hypotheses.db``.
        """
        self.db_path = db_path or default_db_path()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = connect(self.db_path)
        self._init_db()

    # ── Schema & migration ─────────────────────────────────────

    def _init_db(self) -> None:
        """Create tables and FTS5 index if they do not exist."""
        with self._lock:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS hypotheses (
                    hypothesis_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    thesis TEXT NOT NULL,
                    status TEXT NOT NULL,
                    universe TEXT NOT NULL DEFAULT '',
                    signal_definition TEXT NOT NULL DEFAULT '',
                    invalidation_notes TEXT NOT NULL DEFAULT '',
                    parent_hypothesis_id TEXT,
                    related_ids_json TEXT NOT NULL DEFAULT '[]',
                    contradicts_ids_json TEXT NOT NULL DEFAULT '[]',
                    goal_id TEXT,
                    data_sources_json TEXT NOT NULL DEFAULT '[]',
                    skills_json TEXT NOT NULL DEFAULT '[]',
                    run_cards_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_hypotheses_status
                    ON hypotheses(status);
                CREATE INDEX IF NOT EXISTS idx_hypotheses_goal
                    ON hypotheses(goal_id)
                    WHERE goal_id IS NOT NULL;
                CREATE INDEX IF NOT EXISTS idx_hypotheses_parent
                    ON hypotheses(parent_hypothesis_id)
                    WHERE parent_hypothesis_id IS NOT NULL;
                CREATE INDEX IF NOT EXISTS idx_hypotheses_updated
                    ON hypotheses(updated_at DESC);

                CREATE VIRTUAL TABLE IF NOT EXISTS hypotheses_fts USING fts5(
                    hypothesis_id UNINDEXED,
                    title,
                    thesis,
                    universe,
                    signal_definition,
                    invalidation_notes,
                    tokenize = 'unicode61 remove_diacritics 2'
                );
                """
            )
            self._conn.commit()

    def _insert_raw(self, hyp: Hypothesis) -> None:
        """Insert a hypothesis without validation. Used by migration."""
        with self._lock:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO hypotheses (
                    hypothesis_id, title, thesis, status, universe,
                    signal_definition, invalidation_notes,
                    parent_hypothesis_id, related_ids_json,
                    contradicts_ids_json, goal_id, data_sources_json,
                    skills_json, run_cards_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    hyp.hypothesis_id, hyp.title, hyp.thesis, hyp.status,
                    hyp.universe, hyp.signal_definition, hyp.invalidation_notes,
                    hyp.parent_hypothesis_id,
                    json.dumps(hyp.related_ids, ensure_ascii=False),
                    json.dumps(hyp.contradicts_ids, ensure_ascii=False),
                    hyp.goal_id,
                    json.dumps(hyp.data_sources, ensure_ascii=False),
                    json.dumps(hyp.skills, ensure_ascii=False),
                    json.dumps(hyp.run_cards, ensure_ascii=False),
                    hyp.created_at, hyp.updated_at,
                ),
            )
            self._conn.execute(
                "INSERT OR REPLACE INTO hypotheses_fts (hypothesis_id, title, thesis, universe, signal_definition, invalidation_notes) VALUES (?, ?, ?, ?, ?, ?)",
                (hyp.hypothesis_id, hyp.title, hyp.thesis, hyp.universe,
                 hyp.signal_definition, hyp.invalidation_notes),
            )
            self._conn.commit()

    # ── Write transactions ─────────────────────────────────────

    def _row_to_hyp(self, row: sqlite3.Row) -> Hypothesis:
        return Hypothesis(
            hypothesis_id=row["hypothesis_id"],
            title=row["title"],
            thesis=row["thesis"],
            status=row["status"],
            universe=row["universe"] or "",
            signal_definition=row["signal_definition"] or "",
            invalidation_notes=row["invalidation_notes"] or "",
            parent_hypothesis_id=row["parent_hypothesis_id"],
            related_ids=list(json.loads(row["related_ids_json"] or "[]")),
            contradicts_ids=list(json.loads(row["contradicts_ids_json"] or "[]")),
            goal_id=row["goal_id"],
            data_sources=list(json.loads(row["data_sources_json"] or "[]")),
            skills=list(json.loads(row["skills_json"] or "[]")),
            run_cards=list(json.loads(row["run_cards_json"] or "[]")),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    # ── CRUD operations ─────────────────────────────────────────

    def list(
        self,
        *,
        status: str | None = None,
        goal_id: str | None = None,
        parent_id: str | None = None,
        limit: int = 1000,
    ) -> list[Hypothesis]:
        """List hypotheses, optionally filtered."""
        query = "SELECT * FROM hypotheses WHERE 1=1"
        params: list[Any] = []
        if status:
            query += " AND status = ?"
            params.append(_validate_status(status))
        if goal_id:
            query += " AND goal_id = ?"
            params.append(goal_id)
        if parent_id:
            query += " AND parent_hypothesis_id = ?"
            params.append(parent_id)
        query += " ORDER BY updated_at DESC LIMIT ?"
        params.append(limit)
        with self._lock:
            rows = self._conn.execute(query, params).fetchall()
        return [self._row_to_hyp(r) for r in rows]

    def get(self, hypothesis_id: str) -> Hypothesis | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM hypotheses WHERE hypothesis_id = ?",
                (hypothesis_id,),
            ).fetchone()
        return self._row_to_hyp(row) if row else None

    def create(
        self,
        *,
        title: str,
        thesis: str,
        status: str = "exploring",
        universe: str = "",
        signal_definition: str = "",
        data_sources: list[str] | None = None,
        skills: list[str] | None = None,
        invalidation_notes: str = "",
        parent_hypothesis_id: str | None = None,
        related_ids: list[str] | None = None,
        contradicts_ids: list[str] | None = None,
        goal_id: str | None = None,
    ) -> Hypothesis:
        """Create and persist a new hypothesis."""
        title = title.strip()
        thesis = thesis.strip()
        if not title:
            raise ValueError("title is required")
        if not thesis:
            raise ValueError("thesis is required")

        with self._lock:
            existing_ids = {row["hypothesis_id"] for row in
                            self._conn.execute("SELECT hypothesis_id FROM hypotheses").fetchall()}
            from .registry import _new_hypothesis_id
            now = _utc_now()
            hyp = Hypothesis(
                hypothesis_id=_new_hypothesis_id(title, now, existing_ids),
                title=title,
                thesis=thesis,
                status=_validate_status(status),
                universe=universe.strip(),
                signal_definition=signal_definition.strip(),
                data_sources=_coerce_str_list(data_sources),
                skills=_coerce_str_list(skills),
                invalidation_notes=invalidation_notes.strip(),
                parent_hypothesis_id=parent_hypothesis_id,
                related_ids=_coerce_str_list(related_ids),
                contradicts_ids=_coerce_str_list(contradicts_ids),
                goal_id=goal_id,
                created_at=now,
                updated_at=now,
            )
            with write_transaction(self._conn):
                self._insert_raw(hyp)
        return hyp

    def update(
        self,
        hypothesis_id: str,
        *,
        title: str | None = None,
        thesis: str | None = None,
        status: str | None = None,
        universe: str | None = None,
        signal_definition: str | None = None,
        data_sources: list[str] | None = None,
        skills: list[str] | None = None,
        invalidation_notes: str | None = None,
        parent_hypothesis_id: str | None = None,
        related_ids: list[str] | None = None,
        contradicts_ids: list[str] | None = None,
        goal_id: str | None = None,
    ) -> Hypothesis | None:
        """Update an existing hypothesis.

        Status changes are validated against VALID_TRANSITIONS.
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM hypotheses WHERE hypothesis_id = ?",
                (hypothesis_id,),
            ).fetchone()
            if row is None:
                return None
            hyp = self._row_to_hyp(row)

            _apply_scalar_fields(hyp, {
                "title": title,
                "thesis": thesis,
                "universe": universe,
                "signal_definition": signal_definition,
                "invalidation_notes": invalidation_notes,
            })
            if status is not None:
                _apply_status(hyp, status)
            if data_sources is not None:
                hyp.data_sources = _coerce_str_list(data_sources)
            if skills is not None:
                hyp.skills = _coerce_str_list(skills)
            if parent_hypothesis_id is not None:
                hyp.parent_hypothesis_id = parent_hypothesis_id or None
            if related_ids is not None:
                hyp.related_ids = _coerce_str_list(related_ids)
            if contradicts_ids is not None:
                hyp.contradicts_ids = _coerce_str_list(contradicts_ids)
            if goal_id is not None:
                hyp.goal_id = goal_id or None
            hyp.updated_at = _utc_now()

            with write_transaction(self._conn):
                self._insert_raw(hyp)
        return hyp

    def link_backtest(
        self,
        hypothesis_id: str,
        *,
        run_card_path: str = "",
        backtest_run_dir: str = "",
        metrics: dict[str, Any] | None = None,
        notes: str = "",
    ) -> Hypothesis | None:
        """Link a run card or backtest artifact to a hypothesis."""
        if not run_card_path and not backtest_run_dir:
            raise ValueError("run_card_path or backtest_run_dir is required")
        hyp = self.get(hypothesis_id)
        if hyp is None:
            return None
        hyp.run_cards.append({
            "run_card_path": run_card_path,
            "backtest_run_dir": backtest_run_dir,
            "metrics": metrics or {},
            "notes": notes,
            "linked_at": _utc_now(),
        })
        hyp.updated_at = _utc_now()
        with write_transaction(self._conn):
            self._insert_raw(hyp)
        return hyp

    # ── FTS5 search ─────────────────────────────────────────────

    def search(
        self,
        *,
        query: str = "",
        status: str | None = None,
        limit: int = 10,
    ) -> list[Hypothesis]:
        """FTS5 search across title, thesis, universe, signal, notes."""
        status_filter = _validate_status(status) if status else None

        tokens = _tokenize(query) if query.strip() else set()
        if tokens:
            # FTS5 MATCH query
            fts_query = " ".join(f'"{tok}"' for tok in tokens)
            sql = (
                "SELECT h.* FROM hypotheses h "
                "JOIN hypotheses_fts f ON h.hypothesis_id = f.hypothesis_id "
                "WHERE hypotheses_fts MATCH ?"
            )
            params: list[Any] = [fts_query]
        else:
            sql = "SELECT h.* FROM hypotheses h WHERE 1=1"
            params = []

        if status_filter:
            sql += " AND h.status = ?"
            params.append(status_filter)
        sql += " ORDER BY h.updated_at DESC LIMIT ?"
        params.append(max(1, min(int(limit), 100)))

        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [self._row_to_hyp(r) for r in rows]

    def list_by_goal(self, goal_id: str) -> list[Hypothesis]:
        return self.list(goal_id=goal_id, limit=10000)

    def list_children(self, parent_id: str) -> list[Hypothesis]:
        return self.list(parent_id=parent_id, limit=10000)

    def list_contradictions(self, hyp_id: str) -> list[Hypothesis]:
        hyp = self.get(hyp_id)
        if hyp is None:
            return []
        result = []
        for cid in hyp.contradicts_ids:
            h = self.get(cid)
            if h is not None:
                result.append(h)
        return result

    # ── Relationship operations ────────────────────────────────

    def derive(
        self,
        *,
        parent_id: str,
        title: str,
        thesis: str,
        signal_definition: str = "",
    ) -> Hypothesis:
        """Create a child hypothesis derived from a parent."""
        parent = self.get(parent_id)
        if parent is None:
            raise KeyError(f"parent hypothesis not found: {parent_id}")
        return self.create(
            title=title,
            thesis=thesis,
            status="exploring",
            universe=parent.universe,
            signal_definition=signal_definition or parent.signal_definition,
            data_sources=list(parent.data_sources),
            skills=list(parent.skills),
            parent_hypothesis_id=parent_id,
        )

    def link(self, hyp_id: str, related_id: str) -> Hypothesis | None:
        """Mark two hypotheses as related (bidirectional)."""
        hyp_a = self.get(hyp_id)
        hyp_b = self.get(related_id)
        if hyp_a is None or hyp_b is None:
            return None
        if related_id not in hyp_a.related_ids:
            hyp_a.related_ids.append(related_id)
            hyp_a.updated_at = _utc_now()
            with write_transaction(self._conn):
                self._insert_raw(hyp_a)
        if hyp_id not in hyp_b.related_ids:
            hyp_b.related_ids.append(hyp_id)
            hyp_b.updated_at = _utc_now()
            with write_transaction(self._conn):
                self._insert_raw(hyp_b)
        return hyp_a

    def unlink(self, hyp_id: str, related_id: str) -> Hypothesis | None:
        """Remove bidirectional related link."""
        hyp_a = self.get(hyp_id)
        hyp_b = self.get(related_id)
        if hyp_a is None:
            return None
        hyp_a.related_ids = [x for x in hyp_a.related_ids if x != related_id]
        hyp_a.updated_at = _utc_now()
        with write_transaction(self._conn):
            self._insert_raw(hyp_a)
        if hyp_b is not None:
            hyp_b.related_ids = [x for x in hyp_b.related_ids if x != hyp_id]
            hyp_b.updated_at = _utc_now()
            with write_transaction(self._conn):
                self._insert_raw(hyp_b)
        return hyp_a

    def contradicts(self, hyp_id: str, other_id: str, notes: str = "") -> Hypothesis | None:
        """Mark hyp_id as contradicting other_id."""
        hyp_a = self.get(hyp_id)
        if hyp_a is None:
            return None
        if other_id not in hyp_a.contradicts_ids:
            hyp_a.contradicts_ids.append(other_id)
        hyp_a.invalidation_notes = (
            f"{hyp_a.invalidation_notes}\nContradicts {other_id}: {notes}"
            if hyp_a.invalidation_notes
            else f"Contradicts {other_id}: {notes}"
        ).strip()
        hyp_a.updated_at = _utc_now()
        with write_transaction(self._conn):
            self._insert_raw(hyp_a)
        return hyp_a

    def link_goal(self, hyp_id: str, goal_id: str) -> Hypothesis | None:
        """Associate a hypothesis with a research goal."""
        return self.update(hyp_id, goal_id=goal_id)

    def close(self) -> None:
        """Close the underlying SQLite connection."""
        with self._lock:
            try:
                self._conn.close()
            except Exception:  # noqa: BLE001
                pass


__all__ = ["HypothesisStore", "default_db_path"]
