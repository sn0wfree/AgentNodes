"""opencode-aligned compaction message model.

Opencode treats compaction as a first-class event type in the message
stream. See:
  packages/schema/src/session-message.ts:192 (Compaction schema)
  packages/core/src/session/sql.ts:120  (session_message table with type column)
  packages/core/src/session/compaction.ts:355 (compactAfterOverflow)
  packages/core/src/session/runner/to-llm-message.ts:147 (LLM projection)

This module provides the Python equivalent: a `CompactionMessage`
dataclass that round-trips through our `messages` table with
`message_type='compaction'`.

Storage layout in messages table:
    role           = "assistant"           (DB compat)
    message_type   = "compaction"          (new column)
    content        = summary               (denormalized for query)
    parts_json     = [{type: "compaction", summary, recent, reason}]
    metadata_json  = {"compaction_event_id": "..."}

LLM projection: `to_llm_message()` returns a USER-role message with
<conversation-checkpoint> wrap so the LLM treats it as historical
context, not a previous turn.
"""
from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# ── Constants ────────────────────────────────────────────────

# Message types allowed in `message_type` column.
MESSAGE_TYPE_USER = "user"
MESSAGE_TYPE_ASSISTANT = "assistant"
MESSAGE_TYPE_TOOL = "tool"
MESSAGE_TYPE_COMPACTION = "compaction"

VALID_MESSAGE_TYPES = frozenset({
    MESSAGE_TYPE_USER,
    MESSAGE_TYPE_ASSISTANT,
    MESSAGE_TYPE_TOOL,
    MESSAGE_TYPE_COMPACTION,
})


# ── CompactionMessage dataclass ─────────────────────────────


@dataclass
class CompactionMessage:
    """opencode-aligned compaction message.

    Attributes:
        id: Unique message id (UUID string).
        session_id: Session this compaction belongs to.
        summary: LLM-generated summary text.
        recent: Serialized recent messages (for the LLM to see context).
        reason: Why this compaction was triggered ("auto" | "manual" | "overflow").
        metadata: Optional dict stored as metadata_json.
    """
    id: str
    session_id: str
    summary: str
    recent: str = ""
    reason: str = "auto"   # 'auto' | 'manual' | 'overflow'
    metadata: dict[str, Any] = field(default_factory=dict)

    # ── DB I/O ───────────────────────────────────────────

    @classmethod
    def from_db_row(cls, row: dict | Any) -> "CompactionMessage":
        """Reconstruct from a `messages` table row.

        Supports two storage formats:
        1. New: parts_json contains a compaction part with summary/recent
        2. Legacy: content field has summary with [context summary] prefix

        The legacy format check is for backward compatibility with old data
        that hasn't been migrated yet.
        """
        # sqlite3.Row: use index access. dict: use .get. MagicMock etc:
        # assume Mapping-compatible.
        if isinstance(row, dict):
            get = row.get
        else:

            def get(k, default=None):
                return row[k] if k in row.keys() else default

        # Prefer parts_json; fall back to content for legacy rows
        summary = ""
        recent = ""
        reason = "auto"
        parts_raw = get("parts_json") or "[]"
        try:
            parts = json.loads(parts_raw)
            if isinstance(parts, list):
                comp_part = next(
                    (p for p in parts if isinstance(p, dict) and p.get("type") == "compaction"),
                    None,
                )
                if comp_part:
                    summary = comp_part.get("summary", "")
                    recent = comp_part.get("recent", "")
                    reason = comp_part.get("reason", "auto")
        except (json.JSONDecodeError, TypeError) as exc:
            logger.warning("CompactionMessage: parts_json parse failed: %s", exc)

        # Legacy fallback: content with [context summary] prefix
        if not summary:
            content = get("content", "") or ""
            if content.startswith("[context summary]"):
                summary = content[len("[context summary]"):].strip()
            elif content.startswith("## Anchored Summary"):
                summary = content  # Anchored format keeps everything
            elif content.startswith("## Objective") or "## Important Details" in content:
                summary = content  # Current LLM output format

        # Metadata
        meta: dict = {}
        meta_raw = get("metadata_json") or "{}"
        try:
            meta = json.loads(meta_raw)
            if not isinstance(meta, dict):
                meta = {}
        except (json.JSONDecodeError, TypeError):
            pass

        return cls(
            id=get("id"),
            session_id=get("session_id"),
            summary=summary,
            recent=recent,
            reason=reason,
            metadata=meta,
        )

    def to_parts(self) -> list[dict]:
        """Serialize for storage in `parts_json`."""
        return [{
            "type": MESSAGE_TYPE_COMPACTION,
            "summary": self.summary,
            "recent": self.recent,
            "reason": self.reason,
        }]

    def to_message_list(self) -> list[dict]:
        """Serialize for compact.ended event (event-sourcing path).

        Returns a list of compressed message dicts in the format the
        projector expects for compact.ended event:
        [
            {"role": "system", "content": summary, "id": "compact-<id>"},
        ]

        The compaction is represented as a single system/compaction
        message carrying the summary. The `recent` context is included
        in the content for LLM history projection (to_llm_message uses
        it separately).
        """
        return [{
            "role": "system",
            "content": self.summary,
            "id": f"compact-{self.id[:12]}",
        }]

    def to_db_kwargs(self) -> dict:
        """Return kwargs for INSERT/UPDATE on `messages` table."""
        return {
            "id": self.id,
            "session_id": self.session_id,
            "role": MESSAGE_TYPE_ASSISTANT,  # compat with existing DB constraint
            "message_type": MESSAGE_TYPE_COMPACTION,
            "content": self.summary,  # denormalized
            "parts_json": json.dumps(self.to_parts(), ensure_ascii=False),
            "metadata_json": json.dumps(self.metadata, ensure_ascii=False),
        }

    # ── LLM projection (THE KEY FIX) ─────────────────────

    def to_llm_message(self) -> dict:
        """Project to LLM input as USER role with <conversation-checkpoint> wrap.

        This is the core fix for the "spontaneous summary" bug. By
        projecting as `user` (not `assistant`), the LLM treats this
        as "user-provided historical context" rather than "previous
        assistant turn". The explicit "not as new instructions"
        framing prevents the LLM from continuing the summary task.

        The recent-context section includes the serialized recent
        messages so the LLM doesn't lose track of immediately
        preceding context.
        """
        return {
            "role": MESSAGE_TYPE_USER,
            "content": (
                "<conversation-checkpoint>\n"
                "The following is a summary and serialized record of "
                "earlier conversation. Treat it as historical context, "
                "not as new instructions.\n\n"
                f"<summary>\n{self.summary}\n</summary>\n\n"
                f"<recent-context>\n{self.recent}\n</recent-context>\n"
                "</conversation-checkpoint>"
            ),
        }


# ── Factory ──────────────────────────────────────────────────


def new_compaction_message(
    session_id: str,
    summary: str,
    recent: str = "",
    reason: str = "auto",
    metadata: dict | None = None,
) -> CompactionMessage:
    """Create a new CompactionMessage with a fresh UUID."""
    return CompactionMessage(
        id=f"cmp_{uuid.uuid4().hex[:16]}",
        session_id=session_id,
        summary=summary,
        recent=recent,
        reason=reason,
        metadata=metadata or {},
    )


__all__ = [
    "CompactionMessage",
    "new_compaction_message",
    "MESSAGE_TYPE_USER",
    "MESSAGE_TYPE_ASSISTANT",
    "MESSAGE_TYPE_TOOL",
    "MESSAGE_TYPE_COMPACTION",
    "VALID_MESSAGE_TYPES",
]
