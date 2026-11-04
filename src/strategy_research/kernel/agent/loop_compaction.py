"""W-split: context compaction (from AgentLoop)."""
from __future__ import annotations

from typing import Any

import asyncio
import logging
from typing import TYPE_CHECKING, Any, Callable

logger = logging.getLogger(__name__)

from strategy_research.foundation.utils.token_utils import estimate_tokens
from . import compact as _compact_mod

if TYPE_CHECKING:
    pass

class LoopCompactionMixin:
# ── Host surface (provided by AgentLoop / AutoresearchRunner) ──
    _event_bus: Any
    _previous_summary: Any
    cc: Any
    client: Any
    config: Any
    session_id: Any
    threshold_tokens: Any

    def _maybe_compact(self, messages: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
        """Apply context compression if over threshold (sync path).

        Single source: the shared sync core is ``_maybe_compact_impl``;
        the async twin (``_amaybe_compact``) runs the same core in a
        worker thread so the event loop is never blocked.

        opencode-aligned trigger formula:
            trigger = threshold_tokens (default derived from model
            context: context - max(model_max_output, buffer))

        If L4 fails (e.g. DB error during persistence), the
        compaction is rolled back and the original messages are
        kept. The LLM doesn't lose context.
        """
        return self._maybe_compact_impl(
            messages, run_compact=self._run_compact_messages
        )

    def _run_compact_messages(self, messages: list[dict[str, Any]]):
        """Invoke the compact_messages engine with the loop's config.

        Resolved through the module (not a from-import) so tests that
        patch ``compact.compact_messages`` actually intercept the call.
        """
        return _compact_mod.compact_messages(
            messages,
            config=self.cc,
            threshold_tokens=self.threshold_tokens,
            model_context_tokens=self.config.model_context_tokens,
            model_max_output_tokens=self.config.model_max_output_tokens,
            llm_client=self.client,
            previous_summary=self._previous_summary,
            session_id=self.session_id,
        )

    def _maybe_compact_impl(
        self,
        messages: list[dict[str, Any]],
        *,
        run_compact: Callable[[list[dict[str, Any]]], tuple],
    ) -> tuple[list[dict[str, Any]], list[str]]:
        """Shared sync compaction core (single source for both modes).

        ``run_compact`` is the only divergent point: the sync path calls
        it directly on the calling thread, the async path runs this whole
        core in a worker thread (``asyncio.to_thread``) so the loop is
        never blocked by the sync LLM summary call.
        """
        # Overflow detection: log only (no force compact in this path)
        if self.config.model_context_tokens:
            usable = self.config.model_context_tokens - 4096
            tokens = estimate_tokens(messages)
            if tokens >= usable * self.cc.overflow_ratio:
                logger.debug(
                    "Overflow detected: %d tokens >= %d usable * %.2f",
                    tokens, usable, self.cc.overflow_ratio,
                )

        # Save original for rollback. compact_messages reassigns `messages`
        # in the loop, so we need a reference to the pre-compaction list.
        original_messages = list(messages)

        # Delegate to compact_messages engine
        # opencode-aligned: returns 4-tuple (messages, applied, summary, recent_text).
        # The summary text + recent text are pre-computed in compact and
        # passed through to the persistence step. No more NoneType risk
        # in the loop (the recent_count // 100 bug is gone).
        try:
            messages, applied, l4_summary_text, l4_recent_text = run_compact(messages)
        except Exception:
            # Critical: L4 failed. Roll back to keep full history.
            # The LLM is more useful with full history than with
            # partial compaction.
            logger.exception("L4 compaction failed; keeping full history")
            return original_messages, []

        if l4_summary_text and any(layer.startswith("llm_summarize") for layer in applied):
            self._previous_summary = l4_summary_text
            try:
                self._persist_compaction_event(
                    l4_summary_text, l4_recent_text or "",
                    compressed_messages=messages,
                )
            except Exception:
                # Persistence failed AFTER L4 generated summary.
                # Roll back to original messages so the LLM keeps
                # full history on the next turn.
                logger.exception(
                    "compaction persistence failed; rolling back to original messages",
                )
                return original_messages, []

        return messages, applied

    def _persist_compaction_event(
        self,
        summary_text: str,
        recent_text: str,
        compressed_messages: list[dict] | None = None,
    ) -> None:
        """Persist a CompactionMessage event for the L4 layer.

        B6: Event-sourced path. Emits a compact.ended event via the
        injected EventBusV2. The projector handles materialization
        to messages + message_parts tables.

        Falls back to direct persist_message if no event_bus is
        injected (for legacy code paths that haven't migrated yet).

        Args:
            summary_text: LLM-generated summary (non-empty).
            recent_text: Pre-serialized recent messages from compact.
            compressed_messages: Post-L4 messages list (system + recent).
                When provided AND the ``SR_L4_INCLUDE_MESSAGES`` env
                var is truthy, this list is included in the
                ``compact.ended`` event so the projector can replace
                the previous messages table contents — keeping the DB
                consistent with the in-memory state already updated
                by ``compact_messages``.  When the flag is off (the
                default), the previous behaviour is preserved: only a
                marker is emitted, original messages stay in DB.

        Raises:
            Exception: propagates critical errors (not silent fail).
        """
        session_id = self.session_id
        if not session_id:
            logger.debug("L4 ran but no session_id; skipping compaction persistence")
            return

        if not summary_text or not summary_text.strip():
            logger.warning("L4 returned empty summary; skipping persistence")
            return

        try:
            from .compaction_message import new_compaction_message

            comp = new_compaction_message(
                session_id=session_id,
                summary=summary_text,
                recent=recent_text or "",
                reason="auto",
            )

            if self._event_bus is not None:
                # B6: Event-sourced path. Emit compact.ended event.
                #
                # Two modes (controlled by SR_L4_INCLUDE_MESSAGES):
                # 1. Default: emit only a marker. The DB keeps the
                #    original messages; the marker records that
                #    compaction happened.  In-memory state has
                #    already been compressed by compact_messages, so
                #    the LLM sees the compressed view, but a fresh
                #    reload from the DB would see the originals.
                # 2. Flag enabled: include the post-L4 messages list
                #    so the projector replaces the DB contents with
                #    the compressed view.  This makes the DB
                #    consistent with the in-memory state, matching
                #    what manual /compact already does.
                import os

                include_msgs = (
                    compressed_messages is not None
                    and os.environ.get("SR_L4_INCLUDE_MESSAGES", "").lower()
                    in ("1", "true", "yes")
                )
                payload: dict[str, Any] = {
                    "summary": comp.summary,
                    "reason": "auto",
                    "compaction_id": comp.id,
                    "metadata": comp.metadata,
                }
                if include_msgs:
                    payload["messages"] = compressed_messages
                    # opencode-aligned: the projector infers the
                    # compaction boundary (compacted_until_seq) from
                    # the compressed message list + projection order.
                self._event_bus.emit(
                    session_id,
                    "compact.ended",
                    payload,
                )
                logger.info(
                    "compaction event emitted: %s (summary=%d chars, "
                    "recent=%d chars, include_messages=%s)",
                    comp.id, len(comp.summary), len(comp.recent),
                    include_msgs,
                )
            else:
                # Legacy fallback: direct DB write via the registered
                # persister (registered by api/ and TUI entry points).
                #
                # Fail-fast: if no persister is registered, raise.
                # Previously this path silently dropped the compaction
                # event, which made misconfigured deployments invisible
                # to operators. Now the misconfiguration is surfaced
                # immediately at runtime.
                # Read the persister through the module (NOT a from-import):
                # a from-import binds the value at module load time, so a
                # later register_compaction_persister() call would never be
                # visible here — the compaction path would always raise.
                #
                # W10-1 (2026-11-04): read from ``compact`` instead of
                # ``loop``. The registration lives there now, which is what
                # breaks the loop ↔ loop_compaction import cycle.
                persister = _compact_mod.get_compaction_persister()
                if persister is None:
                    raise RuntimeError(
                        f"compaction_persister not registered; cannot "
                        f"persist compaction event {comp.id}. Register "
                        f"via register_compaction_persister() in the "
                        f"api/cli entry point."
                    )
                persister(
                    session_id=comp.session_id,
                    role="assistant",  # DB compat
                    content=comp.summary,
                    parts=comp.to_parts(),
                    message_id=comp.id,
                    message_type="compaction",
                )
                logger.info(
                    "compaction event persisted (legacy): %s (summary=%d chars, recent=%d chars)",
                    comp.id, len(comp.summary), len(comp.recent),
                )
        except Exception:
            # Critical: propagate with full traceback. The caller
            # (compact_messages path) will see the error and roll
            # back messages to keep full history intact.
            logger.exception("compaction persistence failed")
            raise

    async def _amaybe_compact(
        self, messages: list[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]], list[str]]:
        """Async version of _maybe_compact — runs the shared sync core
        (``_maybe_compact_impl``) in a worker thread so the event loop is
        never blocked.

        Acquires per-session compact lock to prevent concurrent compaction
        (auto from agent loop + manual /compact) on the same session.
        The sync LLM client (``self.client.chat``) is invoked directly
        from the worker thread, with no nested event loop.
        """
        if not self.session_id:
            return await asyncio.to_thread(
                self._maybe_compact_impl, messages, run_compact=self._run_compact_messages
            )
        from .compact import _compact_locks
        lock = await _compact_locks.get(self.session_id)
        async with lock:
            return await asyncio.to_thread(
                self._maybe_compact_impl, messages, run_compact=self._run_compact_messages
            )

    # ── Trace helpers ──────────────────────────────
