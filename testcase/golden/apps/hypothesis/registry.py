"""Pure-code durable research hypothesis registry (P3-b).

The registry is intentionally small: local JSON storage, deterministic reads,
and no dependency on LLMs or live trading services.

Adapted from vibe-trading-ai 0.1.11 (MIT License, HKUDS).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HYPOTHESIS_STATUSES = (
    "exploring",
    "testing",
    "validated",
    "rejected",
    "monitoring",
)
_STATUS_SET = set(HYPOTHESIS_STATUSES)

# P3-C1: Valid status transitions (state machine)
VALID_TRANSITIONS: dict[str, set[str]] = {
    "exploring": {"testing", "rejected"},
    "testing": {"validated", "rejected", "exploring"},
    "validated": {"monitoring"},
    "monitoring": {"testing", "rejected"},
    "rejected": set(),  # terminal
}

_TOKEN_RE = re.compile(r"[a-zA-Z0-9]{2,}|[\u4e00-\u9fff]")


def _check_transition(from_status: str, to_status: str) -> None:
    """Validate that a status transition is legal.

    Raises:
        ValueError: If the transition is not in VALID_TRANSITIONS.
    """
    allowed = VALID_TRANSITIONS.get(from_status, set())
    if to_status not in allowed:
        raise ValueError(
            f"invalid hypothesis transition: {from_status} -> {to_status}. "
            f"Allowed from {from_status}: {sorted(allowed) or '(terminal)'}"
        )


def _apply_scalar_fields(hyp: Any, fields: dict) -> None:
    """Set stripped scalar fields that are not None (by attribute name)."""
    for attr, value in fields.items():
        if value is not None:
            setattr(hyp, attr, value.strip())


def _apply_status(hyp: Any, status: str) -> None:
    """Validate and apply a status change on a hypothesis."""
    new_status = _validate_status(status)
    if new_status != hyp.status:
        _check_transition(hyp.status, new_status)
    hyp.status = new_status


def default_hypotheses_path() -> Path:
    """Return the configured hypotheses storage path.

    Resolution order:
        1. ``QUANTNODES_RESEARCH_HYPOTHESES_PATH`` env var (legacy alias)
        2. ``QUANTNODES_RESEARCH_HYPOTHESES_DB_PATH`` env var
        3. ``~/.quantnodes-research/hypotheses.db`` (default)

    The storage is SQLite (P2); the legacy JSON file backend was removed
    and any existing ``hypotheses.json`` is left in place but never read.
    """
    for env in ("QUANTNODES_RESEARCH_HYPOTHESES_PATH", "QUANTNODES_RESEARCH_HYPOTHESES_DB_PATH"):
        override = os.environ.get(env, "").strip()
        if override:
            return Path(override).expanduser()
    return Path.home() / ".quantnodes-research" / "hypotheses.db"


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _coerce_str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, list):
        return [str(item) for item in value if str(item)]
    return [str(value)]


def _tokenize(text: str) -> set[str]:
    return set(_TOKEN_RE.findall(text.lower()))


def _new_hypothesis_id(title: str, created_at: str, existing_ids: set[str]) -> str:
    seed = f"{title.strip().lower()}|{created_at}"
    base = "hyp_" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:12]
    if base not in existing_ids:
        return base
    idx = 2
    while f"{base}_{idx}" in existing_ids:
        idx += 1
    return f"{base}_{idx}"


def _validate_status(status: str) -> str:
    normalized = str(status).strip().lower()
    if normalized not in _STATUS_SET:
        allowed = ", ".join(HYPOTHESIS_STATUSES)
        raise ValueError(f"unknown hypothesis status '{status}'. Allowed: {allowed}")
    return normalized


@dataclass
class Hypothesis:
    """A research hypothesis tracked across analysis and backtests.

    Attributes:
        hypothesis_id: Stable registry identifier.
        title: Short human-readable title.
        thesis: Research thesis or rationale.
        status: Lifecycle status.
        universe: Target universe, market, or asset set.
        signal_definition: Signal logic in plain text.
        data_sources: Data sources expected or used.
        skills: Relevant quant-research skills.
        run_cards: Linked backtest/run-card artifacts.
        invalidation_notes: Notes describing rejection or invalidation logic.
        parent_hypothesis_id: Parent in the hypothesis graph (for derived hypotheses).
        related_ids: Manually linked related hypotheses.
        contradicts_ids: Hypotheses this one contradicts.
        goal_id: Associated research goal id (if any).
        created_at: UTC creation timestamp.
        updated_at: UTC last update timestamp.
    """

    hypothesis_id: str
    title: str
    thesis: str
    status: str = "exploring"
    universe: str = ""
    signal_definition: str = ""
    data_sources: list[str] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)
    run_cards: list[dict[str, Any]] = field(default_factory=list)
    invalidation_notes: str = ""
    parent_hypothesis_id: str | None = None
    related_ids: list[str] = field(default_factory=list)
    contradicts_ids: list[str] = field(default_factory=list)
    goal_id: str | None = None
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialize the hypothesis to plain JSON-compatible data."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Hypothesis":
        """Build a hypothesis from persisted JSON data."""
        now = _utc_now()
        return cls(
            hypothesis_id=str(data.get("hypothesis_id", "")),
            title=str(data.get("title", "")),
            thesis=str(data.get("thesis", "")),
            status=_validate_status(str(data.get("status", "exploring"))),
            universe=str(data.get("universe", "")),
            signal_definition=str(data.get("signal_definition", "")),
            data_sources=_coerce_str_list(data.get("data_sources")),
            skills=_coerce_str_list(data.get("skills")),
            run_cards=list(data.get("run_cards") or data.get("backtests") or []),
            invalidation_notes=str(data.get("invalidation_notes", "")),
            parent_hypothesis_id=data.get("parent_hypothesis_id") or None,
            related_ids=_coerce_str_list(data.get("related_ids")),
            contradicts_ids=_coerce_str_list(data.get("contradicts_ids")),
            goal_id=data.get("goal_id") or None,
            created_at=str(data.get("created_at") or now),
            updated_at=str(data.get("updated_at") or data.get("created_at") or now),
        )


def _rank_search(
    hypotheses: list["Hypothesis"],
    *,
    query: str,
    status: str | None,
    limit: int,
) -> list["Hypothesis"]:
    """Filter and score hypotheses by text tokens + status (JSON semantics).

    Shared by both storage backends so registry.search behaves identically
    in JSON and SQLite mode: any token overlap qualifies (OR semantics),
    results are ordered by score then most recently updated.
    """
    status_filter = _validate_status(status) if status else None
    query_tokens = _tokenize(query)
    scored: list[tuple[int, Hypothesis]] = []
    for hyp in hypotheses:
        if status_filter and hyp.status != status_filter:
            continue
        haystack = json.dumps(hyp.to_dict(), ensure_ascii=False, sort_keys=True)
        if not query_tokens:
            score = 1
        else:
            hay_tokens = _tokenize(haystack)
            score = len(query_tokens & hay_tokens)
        if score > 0:
            scored.append((score, hyp))
    scored.sort(key=lambda item: (item[0], item[1].updated_at), reverse=True)
    return [hyp for _, hyp in scored[: max(1, min(int(limit), 100))]]


class HypothesisRegistry:
    """Research hypothesis registry (SQLite-backed).

    P2: the legacy JSON-file backend was removed — all persistence goes
    through ``HypothesisStore`` (FTS5 search, indexed graph queries,
    concurrent access). ``path`` is kept as a backwards-compatible
    constructor alias for the storage file; existing ``hypotheses.json``
    files are left in place but never read (see ``store.py``).
    """

    def __init__(self, path: Path | None = None, db_path: Path | None = None) -> None:
        """Initialize the registry.

        Args:
            path: Optional storage file path (SQLite). Defaults to env
                override or ``~/.quantnodes-research/hypotheses.db``.
            db_path: Optional explicit SQLite path (alias of ``path``).
        """
        from .store import HypothesisStore

        if db_path is None and path is not None:
            db_path = path
        self.path = Path(db_path) if db_path is not None else default_hypotheses_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._store: HypothesisStore = HypothesisStore(db_path=self.path)

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
        return self._store.create(
            title=title, thesis=thesis, status=status, universe=universe,
            signal_definition=signal_definition,
            data_sources=data_sources, skills=skills,
            invalidation_notes=invalidation_notes,
            parent_hypothesis_id=parent_hypothesis_id,
            related_ids=related_ids, contradicts_ids=contradicts_ids,
            goal_id=goal_id,
        )

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
    ) -> Hypothesis:
        """Update an existing hypothesis.

        P3-C: status changes are validated against VALID_TRANSITIONS.
        P3-C: parent_hypothesis_id / related_ids / contradicts_ids / goal_id
              can be set or cleared (None clears).
        """
        updated = self._store.update(
            hypothesis_id, title=title, thesis=thesis, status=status,
            universe=universe, signal_definition=signal_definition,
            data_sources=data_sources, skills=skills,
            invalidation_notes=invalidation_notes,
            parent_hypothesis_id=parent_hypothesis_id,
            related_ids=related_ids, contradicts_ids=contradicts_ids,
            goal_id=goal_id,
        )
        if updated is None:
            raise KeyError(f"hypothesis not found: {hypothesis_id}")
        return updated

    def link_backtest(
        self,
        hypothesis_id: str,
        *,
        run_card_path: str = "",
        backtest_run_dir: str = "",
        metrics: dict[str, Any] | None = None,
        notes: str = "",
    ) -> Hypothesis:
        """Link a run card or backtest artifact to a hypothesis."""
        if not run_card_path and not backtest_run_dir:
            raise ValueError("run_card_path or backtest_run_dir is required")
        updated = self._store.link_backtest(
            hypothesis_id, run_card_path=run_card_path,
            backtest_run_dir=backtest_run_dir, metrics=metrics, notes=notes,
        )
        if updated is None:
            raise KeyError(f"hypothesis not found: {hypothesis_id}")
        return updated

    # ── P3-C: Relationship graph operations ─────────────────────

    def derive(
        self,
        *,
        parent_id: str,
        title: str,
        thesis: str,
        signal_definition: str = "",
    ) -> Hypothesis:
        """Create a child hypothesis derived from a parent.

        Inherits parent's universe, data_sources, and skills. Sets
        parent_hypothesis_id on the new hypothesis.
        """
        return self._store.derive(
            parent_id=parent_id, title=title, thesis=thesis,
            signal_definition=signal_definition,
        )

    def link(self, hyp_id: str, related_id: str) -> Hypothesis:
        """Mark two hypotheses as related (bidirectional)."""
        updated = self._store.link(hyp_id, related_id)
        if updated is None:
            raise KeyError(f"hypothesis not found: {hyp_id or related_id}")
        return updated

    def unlink(self, hyp_id: str, related_id: str) -> Hypothesis:
        """Remove bidirectional related link."""
        updated = self._store.unlink(hyp_id, related_id)
        if updated is None:
            raise KeyError(f"hypothesis not found: {hyp_id}")
        return updated

    def contradicts(self, hyp_id: str, other_id: str, notes: str = "") -> Hypothesis:
        """Mark two hypotheses as contradicting (one-way from hyp_id's perspective)."""
        updated = self._store.contradicts(hyp_id, other_id, notes=notes)
        if updated is None:
            raise KeyError(f"hypothesis not found: {hyp_id or other_id}")
        return updated

    def link_goal(self, hyp_id: str, goal_id: str) -> Hypothesis:
        """Associate a hypothesis with a research goal."""
        updated = self._store.link_goal(hyp_id, goal_id)
        if updated is None:
            raise KeyError(f"hypothesis not found: {hyp_id}")
        return updated

    def list_by_goal(self, goal_id: str) -> list[Hypothesis]:
        """Return all hypotheses linked to a given goal."""
        return self._store.list_by_goal(goal_id)

    def list_children(self, parent_id: str) -> list[Hypothesis]:
        """Return all child hypotheses of a parent."""
        return self._store.list_children(parent_id)

    def list_contradictions(self, hyp_id: str) -> list[Hypothesis]:
        """Return all hypotheses that this one contradicts."""
        if self._store.get(hyp_id) is None:
            raise KeyError(f"hypothesis not found: {hyp_id}")
        return self._store.list_contradictions(hyp_id)

    def search(
        self,
        *,
        query: str = "",
        status: str | None = None,
        limit: int = 10,
    ) -> list[Hypothesis]:
        """Search hypotheses by text and/or status.

        Args:
            query: Text query over title, thesis, universe, signal, sources,
                skills, notes, and links.
            status: Optional status filter.
            limit: Maximum results.

        Returns:
            Matching hypotheses ordered by score then most recently updated.
        """
        return _rank_search(
            self._store.list(limit=10000),
            query=query, status=status, limit=limit,
        )

    def list(self) -> list[Hypothesis]:
        """Load all hypotheses from storage (oldest first)."""
        return sorted(
            self._store.list(limit=10000),
            key=lambda h: h.created_at,
        )

    def get(self, hypothesis_id: str) -> Hypothesis | None:
        """Return a hypothesis by id or None if missing."""
        return self._store.get(hypothesis_id)
