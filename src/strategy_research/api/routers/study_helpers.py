"""W-split: study router helpers (cache / serialize / ownership)."""
from __future__ import annotations

import hashlib
import logging
from typing import TYPE_CHECKING

from fastapi import HTTPException, Request

if TYPE_CHECKING:
    from strategy_research.apps.study.scheduler import StudyScheduler

logger = logging.getLogger(__name__)

import time
from collections import OrderedDict

_SUMMARY_CACHE: OrderedDict = OrderedDict()  # sid -> (monotonic, data, etag)
_SUMMARY_CACHE_TTL = 3.0
_SUMMARY_CACHE_MAX = 256

def _get_cached_summary(study_id: str) -> tuple[dict, str] | None:
    """Return (data, etag) if TTL is fresh; else None."""
    entry = _SUMMARY_CACHE.get(study_id)
    if entry and (time.monotonic() - entry[0]) < _SUMMARY_CACHE_TTL:
        # Refresh LRU recency on hit.
        _SUMMARY_CACHE.move_to_end(study_id)
        return entry[1], entry[2]
    return None




def _set_cached_summary(study_id: str, data: dict, etag: str) -> None:
    _SUMMARY_CACHE[study_id] = (time.monotonic(), data, etag)
    while len(_SUMMARY_CACHE) > _SUMMARY_CACHE_MAX:
        _SUMMARY_CACHE.popitem(last=False)  # evict least-recently-used




def _invalidate_summary_cache(study_id: str) -> None:
    """Drop cached summary when the study is mutated (pause/resume/etc.)."""
    _SUMMARY_CACHE.pop(study_id, None)




def _compute_summary_etag(study) -> str:
    """Deterministic ETag from fields that change when summary data changes."""
    seed = f"{study.updated_at}:{study.current_round}:{study.goal_id}"
    h = hashlib.sha256(seed.encode()).hexdigest()[:12]
    return f'"{h}"'


# ── scheduler cache (mirrors chat._session_service_cache pattern) ───


_scheduler_cache: dict[str, "StudyScheduler"] = {}




def _get_study_scheduler():
    """Return the process-wide StudyScheduler for the study/goal DB.

    Builds it bound to the cached SessionService so the chat/study mutex
    primitives cooperate. Idempotent per DB path.

    IMPORTANT: the store must use the SAME database as every study
    creation site (``StudyStore()`` — the default goals.db path).
    Using ``_get_db_path()`` here (the session/EventStore DB) made the
    scheduler read from a different SQLite file than the one where
    ``/study start`` wrote the row — the consumer loop then saw no
    study and silently exited.
    """
    from strategy_research.apps.study import StudyScheduler, StudyStore
    from .chat import _get_session_service

    db_path = str(StudyStore().db_path)
    sched = _scheduler_cache.get(db_path)
    if sched is None:
        store = StudyStore(db_path=db_path)
        svc = _get_session_service()
        sched = StudyScheduler(store, session_service=svc)
        _scheduler_cache[db_path] = sched
        logger.info("study scheduler instantiated for db=%s", db_path)
    return sched


# ── ops: in-process status dump (A.observability) ─────────────────




def _serialize_round(r) -> dict:
    """Shared round wire shape (used by summary + rounds list).

    Single source of truth so both endpoints return identical fields
    (round_num, run_name, metrics, verdict, review, error,
    factor_failures, verdict_reason, created_at).
    """
    return {
        "round_num": r.round_num,
        "run_name": r.run_name,
        "metrics": r.metrics,
        "verdict": r.verdict,
        "review": getattr(r, "review", None),
        "error": getattr(r, "error", None),
        "factor_failures": getattr(r, "factor_failures", None) or [],
        "verdict_reason": getattr(r, "verdict_reason", None),
        "created_at": r.created_at,
    }


# ── POST /study/start ───────────────────────────────────────────────




def _snapshot(s: dict | None) -> dict | None:
    if not s:
        return None
    g = s.get("goal", {}) or {}
    return {
        "goal_id": g.get("goal_id"),
        "goal_status": g.get("status"),
        "objective": g.get("objective"),
        "progress_percent": g.get("progress_percent", 0),
        "evidence_count": s.get("evidence_count", 0),
        "criteria": [
            {"criterion_id": c.get("criterion_id"), "text": c.get("text"),
             "status": c.get("status"), "required": c.get("required", True)}
            for c in s.get("criteria", []) or []
        ],
    }


# ── GET /study/{study_id}/summary ──────────────────────────────────




def _build_scoreboard(journal_entries: list) -> list[dict]:
    """Build lever scoreboard from journal entries."""
    from collections import defaultdict
    lever_stats: dict[str, dict] = defaultdict(lambda: {"attempts": 0, "accepted": 0, "reverted": 0})

    for entry in journal_entries:
        # Handle both JournalEntry objects and dicts
        levers = getattr(entry, "levers", None) or (entry.get("levers", []) if isinstance(entry, dict) else [])
        outcome = getattr(entry, "gating_outcome", "") or (entry.get("gating_outcome", "") if isinstance(entry, dict) else "")
        for lever in levers:
            lever_stats[lever]["attempts"] += 1
            if outcome == "accepted":
                lever_stats[lever]["accepted"] += 1
            elif outcome == "reverted":
                lever_stats[lever]["reverted"] += 1

    scoreboard = []
    for lever, stats in sorted(lever_stats.items(), key=lambda x: -x[1]["attempts"]):
        attempts = stats["attempts"]
        accepted = stats["accepted"]
        precision = accepted / attempts if attempts > 0 else 0.0
        scoreboard.append({
            "lever": lever,
            "precision_mean": round(precision, 2),
            "attempts": attempts,
            "accepted": accepted,
            "reverted": stats["reverted"],
        })
    return scoreboard


# ── POST /study/{study_id}/pause|resume|cancel ──────────────────────




def _study_session_id(study_id: str) -> str | None:
    """Look up a study's OWNER session (creator chat session) for IDOR.

    v2 single identity: the study's ``session_id`` column equals the
    study_id (no sessions row exists for it) — ownership is verified via
    ``owner_session_id`` (the creator's chat session, which has a row).
    """
    from strategy_research.apps.study import StudyStore
    with StudyStore() as store:
        study = store.get_study(study_id)
    return study.owner_session_id if study else None




def _user_session_ids(user_id: str) -> list[str]:
    """Return the session ids owned by ``user_id`` (for study scoping)."""
    from .web_session import _get_db
    conn = _get_db()
    rows = conn.execute(
        "SELECT id FROM sessions WHERE user_id = ?", (user_id,)
    ).fetchall()
    return [r["id"] for r in rows]




def _owned_study(request: Request, study_id: str):
    """Fetch a study with IDOR enforcement (owner-session based).

    The study's ``owner_session_id`` must belong to the authenticated user
    (a session row owned by ``request.state.user_id``); otherwise 403.
    Enforcement is on by default (``SR_ENFORCE_STUDY_IDOR`` defaults to
    ``"1"``); set ``SR_ENFORCE_STUDY_IDOR=0`` to disable for single-user
    workspaces.
    """
    import os as _os

    from strategy_research.apps.study import StudyStore
    with StudyStore() as store:
        study = store.get_study(study_id)
        if study is None:
            raise HTTPException(status_code=404, detail="study not found")

    enforce = _os.environ.get("SR_ENFORCE_STUDY_IDOR", "1") != "0"
    if enforce:
        user_id = getattr(request.state, "user_id", None) or "anonymous"
        owner_sid = study.owner_session_id
        if owner_sid:
            from .web_session import _fetch_session_owned, _get_db
            try:
                _fetch_session_owned(_get_db(), owner_sid, user_id)
            except HTTPException as exc:
                if exc.status_code == 403:
                    raise HTTPException(
                        status_code=403, detail="Study does not belong to this user",
                    )
                raise
        elif user_id != "anonymous":
            raise HTTPException(
                status_code=403, detail="Study does not belong to this user",
            )
    return study


