"""State management utilities.

Extracted from runner.py to reduce file size and improve testability.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)


def mark_terminal(
    study_store: Any,
    study_id: str,
    status: Any,
    *,
    last_metrics: dict | None = None,
    last_error: str | None = None,
    reason: str | None = None,
) -> None:
    """Persist a terminal status to the DB (best-effort)."""
    err = last_error if reason is None else f"{reason}:{last_error or ''}"
    try:
        study_store.update_execution_status(
            study_id, status,
            last_error=err, last_metrics=last_metrics,
        )
    except Exception as exc:
        logger.warning(
            "mark_terminal failed to persist %s for study %s: %s",
            status.value, study_id, exc,
        )


async def wait_until_resumed(control: Any) -> None:
    """Wait until the control token is unpaused."""
    while control.paused and not control.cancelled:
        await asyncio.sleep(0.5)


def emit_with_trace(
    emitter: Any,
    session_id: str,
    event: str,
    data: dict,
) -> None:
    """Emit SSE event with trace context decoration."""
    try:
        from strategy_research.foundation.observability import get_trace_context
        ctx = get_trace_context()
        data = {
            "trace_id": ctx.get("trace_id"),
            "study_id": ctx.get("study_id") or data.get("study_id"),
            "round_num": ctx.get("round_num") or data.get("round"),
            **data,
        }
    except Exception:
        pass
    try:
        emitter.emit(session_id, event, data)
    except Exception as exc:
        logger.debug("runner emit %s failed: %s", event, exc)


def open_goal_store() -> Any:
    """Create a new GoalStore instance."""
    from strategy_research.apps.goal import GoalStore
    return GoalStore()


def format_directives(directives: list) -> str:
    """Format directives for injection into agent prompts."""
    lines = ["<user-directives>", "Honour them in this round's research plan:"]
    for d in directives:
        lines.append(f"- [{d.created_at}] {d.content.replace(chr(10), ' ').strip()}")
    lines.append("</user-directives>")
    return "\n".join(lines)
