"""Process-local monotonic sequence number generator (Level 1).

Assigns a per-session integer that increments by 1 each time a message
is appended. Used as the authoritative ordering key for LLM history
projection, replacing `created_at` (which is vulnerable to clock skew).

Lifecycle:
- The generator lives for the lifetime of the process (one per FastAPI
  worker).
- Counters are kept in memory only; not persisted.
- On process restart, counters reset to 0. This is fine because:
  1. The seq column in the DB is set externally (caller responsibility
     or backfill script). The generator is just a safety net for new
     messages.
  2. The `backfill_seq.py` script is run once to assign seq from
     `created_at` for legacy data, so DB state is consistent.
  3. The UNIQUE INDEX (session_id, seq) ensures DB integrity even if
     counters reset.

Thread safety: all mutations are guarded by a single lock. The counter
is per-session, so concurrent writes to DIFFERENT sessions still benefit
from parallelism (only the lock acquisition serializes them briefly).

This module intentionally has no external dependencies beyond the
standard library.
"""
from __future__ import annotations

import threading
from typing import Dict


class SeqGenerator:
    """Per-session monotonic sequence number generator.

    Example:
        gen = SeqGenerator()
        gen.next("sess-1")  # → 1
        gen.next("sess-1")  # → 2
        gen.next("sess-2")  # → 1
        gen.next("sess-1")  # → 3
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: Dict[str, int] = {}

    def next(self, session_id: str) -> int:
        """Return the next sequence number for the given session.

        Each call increments the per-session counter by 1, starting
        from 0 (first call returns 1, second returns 2, etc.).
        """
        if not session_id:
            raise ValueError("session_id must be non-empty")
        with self._lock:
            current = self._counters.get(session_id, 0)
            new = current + 1
            self._counters[session_id] = new
            return new

    def peek(self, session_id: str) -> int:
        """Return the current counter value without incrementing.

        Returns 0 if the session has not yet been assigned a seq.
        """
        with self._lock:
            return self._counters.get(session_id, 0)

    def reset(self, session_id: str | None = None) -> None:
        """Reset one or all counters (mainly for tests)."""
        with self._lock:
            if session_id is None:
                self._counters.clear()
            else:
                self._counters.pop(session_id, None)


# Module-level singleton (one per process). The webui/server imports
# this directly; tests can instantiate their own.
_default_generator: SeqGenerator | None = None
_default_lock = threading.Lock()


def get_default_generator() -> SeqGenerator:
    """Return the process-wide SeqGenerator singleton."""
    global _default_generator
    with _default_lock:
        if _default_generator is None:
            _default_generator = SeqGenerator()
        return _default_generator


__all__ = ["SeqGenerator", "get_default_generator"]
