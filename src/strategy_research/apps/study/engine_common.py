"""Shared utilities for study execution engines.

Extracted from runner.py to eliminate duplication across Phase, DAG,
engines. (P5-2: the langgraph study branch is retired.)
"""
from __future__ import annotations

import json
import logging
import re
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)


# ── JSON utilities ────────────────────────────────────────────────

def safe_json_loads(text: str, fallback: Any = None) -> Any:
    """Parse JSON with markdown fence stripping.

    Robust version that handles ```json fences, bare objects, and arrays.
    Returns ``fallback`` on failure.
    """
    if not isinstance(text, str):
        return text if fallback is None else fallback
    text = text.strip()
    if not text:
        return fallback
    # Fast path: whole-text JSON
    try:
        data = json.loads(text)
        return data
    except (json.JSONDecodeError, TypeError):
        pass
    # Strip markdown fences
    for pattern in (
        r"```json\s*\n?(.*?)\n?\s*```",
        r"```\s*\n?(.*?)\n?\s*```",
        r"(\[.*\])",
        r"(\{.*\})",
    ):
        m = re.search(pattern, text, re.DOTALL)
        if not m:
            continue
        try:
            data = json.loads(m.group(1))
            return data
        except (json.JSONDecodeError, TypeError):
            continue
    return fallback


# ── Agent context builder ─────────────────────────────────────────

def build_agent_ctx(
    strategy: str,
    run_dir: Path,
    session: str,
    runner: Any = None,
) -> dict[str, Any]:
    """Build the standard agent context dict.

    Shared across engine branches (phases is the only live one since P5-2).
    """
    return {
        "strategy_name": strategy,
        "strategy_dir": run_dir,
        "runs_dir": run_dir,
        "results_tsv": run_dir / "results.tsv",
        "session_id": session,
        "session_manager": getattr(runner, "_session_manager", None) if runner else None,
    }


# ── SSE phase emitter ─────────────────────────────────────────────

@contextmanager
def phase_emitter(
    emit_fn: Callable,
    session: str,
    sid: str,
    round_num: int,
    phase: str,
):
    """Context manager that emits study_phase started/done events.

    Usage::

        with phase_emitter(emit, session, sid, round_num, "researcher"):
            result = do_researcher_work(...)
    """
    emit_fn(session, "study_phase", {
        "study_id": sid,
        "round": round_num,
        "phase": phase,
        "status": "started",
    })
    try:
        yield
    finally:
        emit_fn(session, "study_phase", {
            "study_id": sid,
            "round": round_num,
            "phase": phase,
            "status": "done",
        })


# ── Agent output saving ──────────────────────────────────────────

def save_agent_outputs(
    runner: Any,
    run_dir: Path,
    agent_outputs: dict[str, Any],
    round_num: int = 0,
    *,
    agent_histories: dict[str, list] | None = None,
) -> None:
    """Save agent outputs to disk using runner's _save_agent_output.

    Unified saving across all engines.  When *agent_histories* is
    provided,
    the full execution trace is persisted alongside the final answer.
    """
    for agent_id, output in agent_outputs.items():
        # Normalize output to string
        if isinstance(output, (dict, list)):
            output_str = json.dumps(output, ensure_ascii=False)
        elif isinstance(output, str):
            output_str = output
        else:
            output_str = str(output)

        runner._save_agent_output(run_dir, agent_id, {
            "agent": agent_id,
            "output": output_str,
            "status": "success",
            "timestamp": time.time(),
        })

    # Persist full agent execution histories (stage 3)
    if agent_histories:
        agents_dir = run_dir / "agents"
        agents_dir.mkdir(exist_ok=True)
        for agent_id, history in agent_histories.items():
            if history:
                hist_path = agents_dir / f"{agent_id}_history.json"
                with open(hist_path, "w", encoding="utf-8") as f:
                    json.dump(history, f, ensure_ascii=False, indent=2)


# ── Cancel/ARCHIVED guard ────────────────────────────────────────

def check_cancelled(runner: Any, session: str, sid: str) -> str | None:
    """Check if the study should be cancelled. Returns ShutdownReason or None.

    Extracted from _run_loop and _monitor_phase to eliminate duplication.
    """
    from .contracts import ShutdownReason
    from .models import StudyStatus

    if not runner.control.cancelled:
        return None

    live_status = runner._current_db_status()
    if live_status == StudyStatus.ARCHIVED:
        runner._emit(session, "study_cancelled", {
            "study_id": sid,
            "note": f"preserved live status={live_status.value}",
        })
        return ShutdownReason.CANCELLED

    runner._mark_terminal(StudyStatus.CANCELLED, reason=ShutdownReason.CANCELLED)
    runner._emit(session, "study_cancelled", {"study_id": sid})
    return ShutdownReason.CANCELLED


# ── Study chat session (event-sourced agent traces) ───────────────

SESSION_DB_FILENAME = ".quantnodes_strategy_research_session.db"


def get_study_session_db_path(workspace: Path) -> Path:
    """Session DB that lives inside the study workspace (same file the
    backend uses when started with that workspace as cwd)."""
    return workspace / SESSION_DB_FILENAME


def ensure_study_session(
    db_path: Path,
    session_id: str,
    title: str,
) -> None:
    """Create the chat-session row for a study round.

    The row must exist before the projector flush (messages FK) and
    must be owned by the real backend user so the session API's IDOR
    check (``_fetch_session_owned``) passes. Ownership is inherited
    from the most common non-system user among existing sessions.
    """
    import sqlite3

    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT user_id, COUNT(*) AS c FROM sessions "
            "WHERE user_id IS NOT NULL AND user_id != 'system' "
            "GROUP BY user_id ORDER BY c DESC LIMIT 1"
        ).fetchone()
        owner = row["user_id"] if row else "system"
        conn.execute(
            "INSERT OR IGNORE INTO sessions (id, title, user_id, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (session_id, title, owner, time.time(), time.time()),
        )
        conn.commit()
    except Exception:
        logger.warning("ensure_study_session failed for %s", session_id, exc_info=True)
    finally:
        try:
            conn.close()
        except Exception:
            pass
