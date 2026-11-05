"""Projected state models (Level 3 read-side view).

Extracted from projector.py in the Phase 2 split — pure data
representation of the event-projected session (docs/phase2-web-
session-projector-design.md). No IO lives here.
"""
from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# ── Projected state ────────────────────────────────────────────────


@dataclass
class ProjectedPart:
    """A part within a ProjectedMessage.

    Mirrors the message_parts.data_json shape (opencode Part model).
    """
    id: str
    type: str
    data: Dict[str, Any]
    seq: int = 0
    time_created: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "data": self.data,
            "seq": self.seq,
            "time_created": self.time_created,
        }


@dataclass
class ProjectedMessage:
    """A message within a ProjectedSession.

    Mirrors the messages row shape (Level 2: content is the user
    text or assistant text summary; parts hold the rich content).
    """
    id: str
    session_id: str
    role: str
    content: str
    message_type: str = "assistant"
    created_at: float = 0.0
    seq: int = 0
    parts: Dict[str, ProjectedPart] = field(default_factory=dict)
    # Arbitrary metadata persisted to messages.metadata_json (e.g.
    # compaction markers carry compacted_until_seq).
    metadata: Dict[str, Any] = field(default_factory=dict)
    # attempt_id is for SSE correlation; not stored in messages table
    attempt_id: Optional[str] = None
    # Tracks the currently open thinking block so ``thinking_delta``
    # and ``thinking_end`` events (which arrive without their start's
    # seq) know which part to append to / close. Reset to ``None`` on
    # ``thinking_end``. Not persisted in the messages table — it's a
    # projector-in-memory bookkeeping field only.
    open_thinking_part_id: Optional[str] = None

    def parts_in_order(self) -> List[ProjectedPart]:
        """Return parts sorted by seq."""
        return sorted(self.parts.values(), key=lambda p: p.seq)


@dataclass
class ProjectedSession:
    """In-memory projection of a session's event_log."""
    session_id: str
    messages: Dict[str, ProjectedMessage] = field(default_factory=dict)
    last_seq: int = 0

    def messages_in_order(self) -> List[ProjectedMessage]:
        """Return messages sorted by seq."""
        return sorted(
            self.messages.values(),
            key=lambda m: (m.seq, m.created_at),
        )

    def to_dict(self) -> Dict[str, Any]:
        """P0-1 B2 — serialize to a JSON-safe dict for snapshot persistence.

        Field shape mirrors the dataclass exactly so ``from_dict`` is a
        straight ``ProjectedMessage(**m)`` reconstruction per message.
        """
        return {
            "session_id": self.session_id,
            "last_seq": self.last_seq,
            "messages": {
                mid: dataclasses.asdict(m)
                for mid, m in self.messages.items()
            },
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ProjectedSession":
        """Reconstruct a ProjectedSession from ``to_dict``'s output.

        ``ProjectedMessage.parts`` may be either a dict (legacy) or a
        list (current dataclass); both are accepted so older snapshots
        keep loading.
        """
        state = cls(session_id=d["session_id"], last_seq=d.get("last_seq", 0))
        state.messages = {
            mid: ProjectedMessage(
                **{
                    **m,
                    # dataclasses.asdict() serializes parts values to raw
                    # dicts; rebuild ProjectedPart so parts_in_order()
                    # (which reads .seq) doesn't crash on snapshot
                    # round-trip. Also normalize the open-thinking
                    # bookkeeping field, which newer snapshots may omit.
                    "parts": {
                        pid: ProjectedPart(**p)
                        if isinstance(p, dict) else p
                        for pid, p in (m.get("parts") or {}).items()
                    },
                    "open_thinking_part_id": None,
                }
            )
            for mid, m in d.get("messages", {}).items()
        }
        return state

    def to_message_rows(self) -> List[Dict[str, Any]]:
        """Serialize to the shape of messages table rows.

        Each row is a dict with the columns the messages table
        expects. parts are NOT included (they go in message_parts).
        """
        return [
            {
                "id": m.id,
                "session_id": m.session_id,
                "role": m.role,
                "content": m.content,
                "message_type": m.message_type,
                "created_at": m.created_at,
                "seq": m.seq,
                "metadata_json": json.dumps(m.metadata, ensure_ascii=False)
                if m.metadata else None,
            }
            for m in self.messages_in_order()
        ]

    def to_part_rows(self) -> List[Dict[str, Any]]:
        """Serialize parts to the shape of message_parts rows."""
        rows: List[Dict[str, Any]] = []
        for m in self.messages_in_order():
            for p in m.parts_in_order():
                rows.append({
                    "id": p.id,
                    "message_id": m.id,
                    "session_id": m.session_id,
                    "type": p.type,
                    "data_json": json.dumps(p.data, ensure_ascii=False),
                    "seq": p.seq,
                    "time_created": p.time_created,
                })
        return rows

    def to_messages(self) -> List[Any]:
        """Convert to list[Message] (same shape as SessionStore.get_messages).

        Produces Message objects with parts stored in metadata["_parts"],
        matching the convention used by SessionStore.get_messages().

        created_at is converted from epoch float to ISO string to match
        the Message model convention.
        """
        from datetime import datetime, timezone

        from .models import Message

        out: List[Message] = []
        for m in self.messages_in_order():
            parts_list = [
                {
                    "id": p.id,
                    "type": p.type,
                    **p.data,
                }
                for p in m.parts_in_order()
            ]
            created_iso = datetime.fromtimestamp(
                m.created_at, tz=timezone.utc
            ).isoformat() if m.created_at > 0 else ""
            out.append(Message(
                message_id=m.id,
                session_id=m.session_id,
                role=m.role,
                content=m.content,
                created_at=created_iso,
                message_type=m.message_type,
                seq=m.seq,
                metadata={
                    "_parts": parts_list,
                    **(m.metadata or {}),
                },
            ))
        return out
