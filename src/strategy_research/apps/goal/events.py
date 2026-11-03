"""Goal SSE event payload builder (single source of truth).

All goal mutation paths (chat tools via service.py, /goal slash
commands via chat.py, REST endpoints via goal.py) emit the SAME
full-snapshot ``goal_updated`` event through this helper, so the
frontend panel and the message-stream projector never drift.

Design (docs/goal-events-panel-link.md):
- One full-snapshot event per mutation (no incremental events).
- The event carries BOTH the full evidence text (UI / audit) and a
  truncated copy for the LLM-facing message content. Truncation is
  configurable via CompactConfig.goal_evidence_truncate_chars and
  happens HERE — the projector is a pure pass-through and never
  reads configuration.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Optional

logger = logging.getLogger(__name__)

# change_type values, also used as the UI badge label source
CHANGE_TYPE_CREATE = "create"
CHANGE_TYPE_EVIDENCE = "evidence"
CHANGE_TYPE_COMPLETE = "complete"


def _truncate(text: str, max_chars: int) -> str:
    if not text:
        return ""
    text = text.strip()
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "…"


def build_goal_updated_payload(
    session_id: str,
    store: Any,
    change_type: str,
    *,
    truncate_chars: int = 100,
    evidence_text: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    """Build the full-snapshot ``goal_updated`` SSE payload.

    Args:
        session_id: The session owning the goal.
        store: GoalStore instance (any object exposing
            ``get_current_snapshot(session_id)``).
        change_type: one of CHANGE_TYPE_CREATE / CHANGE_TYPE_EVIDENCE /
            CHANGE_TYPE_COMPLETE.
        truncate_chars: LLM-facing evidence text budget
            (CompactConfig.goal_evidence_truncate_chars).
        evidence_text: Optional explicit evidence text. When omitted,
            the most recent evidence row in the snapshot is used.

    Returns:
        Payload dict, or None when the session has no goal.
    """
    try:
        snapshot = store.get_current_snapshot(session_id)
    except Exception:  # noqa: BLE001
        logger.warning("goal snapshot read failed for %s", session_id, exc_info=True)
        return None
    if snapshot is None:
        return None

    goal = snapshot.get("goal", {}) or {}
    criteria = snapshot.get("criteria", []) or []
    evidence_list = snapshot.get("evidence", []) or []
    evidence_count = snapshot.get("evidence_count", 0) or 0

    criteria_out = [
        {
            "criterion_id": c.get("criterion_id"),
            "text": c.get("text"),
            "status": c.get("status"),
            "evidence_count": c.get("evidence_count", 0),
        }
        for c in criteria
        if isinstance(c, dict) and c.get("criterion_id")
    ]

    # Latest evidence text: explicit override wins, else snapshot tail.
    full_text = (evidence_text or "").strip()
    if not full_text and evidence_list:
        last = evidence_list[-1]
        if isinstance(last, dict):
            full_text = (last.get("text") or "").strip()

    return {
        "message_id": f"goal-{uuid.uuid4().hex[:8]}",
        "goal_id": goal.get("goal_id"),
        "session_id": session_id,
        "goal_status": goal.get("status"),
        "objective": goal.get("objective"),
        "progress_percent": goal.get("progress_percent", 0),
        "recap": goal.get("recap"),
        "criteria": criteria_out,
        "evidence_count": evidence_count,
        "change_type": change_type,
        "evidence_text": full_text,
        "evidence_text_llm": _truncate(full_text, max(0, truncate_chars)),
    }
