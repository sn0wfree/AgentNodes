"""Shared typed IDs — re-exported from api.session.models for convenience.

NewType creates distinct types at compile time with zero runtime cost.
Prevents accidentally passing an attempt_id where session_id is expected.

Usage::

    from agentnodes.foundation.utils.ids import SessionId, AttemptId

    def cancel(session_id: SessionId) -> None: ...
"""

from __future__ import annotations

from typing import NewType

SessionId = NewType("SessionId", str)
MessageId = NewType("MessageId", str)
AttemptId = NewType("AttemptId", str)

__all__ = ["SessionId", "MessageId", "AttemptId"]
