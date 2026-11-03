"""SessionService: unified orchestration for chat (API + TUI).

Borrowed from vibe_trading ``src/session/service.py``. Adapted to:
- Reuse strategy-research's SQLite SessionStore instead of filesystem
- Preserve auto_title_session (strategy-research advantage)
- Preserve double-ID fix (assistant message gets attempt.message_id,
  user message gets auto UUID — prevents SSE event from clobbering
  the user message after loadMessages re-keys the store by DB id)
- Accept model parameter for multi-model routing
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from strategy_research.kernel.agent.compact import CompactConfig, compact_messages
from strategy_research.kernel.agent.event_store import EventStore
from strategy_research.foundation.llm import LLMConfig

from .models import Attempt, AttemptId, AttemptStatus, Message, MessageId, SessionId
from .store import SessionStore

logger = logging.getLogger(__name__)

# Character budget for history (~3000 tokens) — borrowed verbatim from vibe_trading.
MAX_HISTORY_CHARS = 12000

# Hard limit for queued messages per session. Exceeding returns 429 to caller.
_QUEUE_LIMIT = 10

# C1: wallclock timeout for a single attempt. A running AgentLoop that
# exceeds this duration is killed (CancelledError → mark_failed). This
# prevents a single hung tool call / LLM stream from permanently
# blocking the session's queue. Set to 0 to disable.
_CHAT_ATTEMPT_TIMEOUT = int(os.environ.get("SR_CHAT_ATTEMPT_TIMEOUT", "600"))


from .loop_events import (  # noqa: E402,F401
    _bootstrap_workspace,
    _build_attempt_metrics,
    _emit_goal_event,
    _LoopEventForwarder,
    _plan_mode_allowed_tools,
    _resolve_loop_prompt,
    _thinking_instructions,
)


class SessionService:
    """Unified chat service used by both API and TUI paths.

    Attributes:
        store: SQLite-backed SessionStore.
        event_bus: EventStore for event_log + SSE + projector flush.
    """

    def __init__(
        self,
        store: SessionStore,
        event_bus: EventStore,
    ) -> None:
        self.store = store
        self.event_bus = event_bus
        self._active_loops: dict[str, "asyncio.Task"] = {}
        # Per-session FIFO message queue (see docs/chat-message-queue-design.md)
        self._session_queues: dict[str, asyncio.Queue] = {}
        self._processing_sessions: set[str] = set()
        self._paused_sessions: dict[str, asyncio.Event] = {}
        self._queue_consumers: dict[str, "asyncio.Task"] = {}
        # PERF-1: event-driven attempt completion (replaces 250ms polling)
        self._attempt_events: dict[str, asyncio.Event] = {}
        self._attempt_results: dict[str, dict[str, str]] = {}

    # ── Session lifecycle ──────────────────────────────────────────────

    def create_session(
        self, session_id: str, title: str = "", user_id: str = "anonymous"
    ) -> dict[str, Any]:
        """Create a new session row if it doesn't exist.

        ``user_id`` records the owning user (default "anonymous" keeps
        TUI/CLI callers working). Returns the session metadata dict.
        """
        import time

        from ..routers.web_session import _get_db

        now = time.time()
        with _get_db() as conn:
            existing = conn.execute(
                "SELECT id FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
            if not existing:
                conn.execute(
                    "INSERT INTO sessions (id, user_id, title, created_at, updated_at, "
                    "starred, tags_json, message_count, archived) "
                    "VALUES (?, ?, ?, ?, ?, 0, '[]', 0, 0)",
                    (session_id, user_id, title or "新会话", now, now),
                )
                conn.commit()

        self.event_bus.emit(
            session_id,
            "session.created",
            {"session_id": session_id, "title": title},
        )
        return {"id": session_id, "title": title or "新会话"}

    # ── Public entry point (shared by API and TUI) ─────────────────────

    async def send_message(
        self,
        session_id: str,
        content: str,
        *,
        model: str | None = None,
        max_iterations: int = 50,
        system_prompt: Optional[str] = None,
        allow_shell_tools: bool = False,
        persona: str | None = None,
        mode: str | None = None,
        thinking: str | None = None,
    ) -> dict[str, Any]:
        """Send a user message and enqueue background AgentLoop execution.

        Messages are processed FIFO per session via ``_process_session_queue``.
        If a session already has an in-flight attempt, new messages are queued
        behind it. A hard limit of 10 queued items is enforced; exceeding it
        returns ``{"error": "queue_full", "limit": 10}``.

        Args:
            session_id: Target session.
            content: User message text.
            model: Optional LLM model override (multi-model routing).
            max_iterations: AgentLoop iterations (default 50 for chat; the
                caller may pass a larger value for agent/goal workflows).
            system_prompt: Optional custom system prompt (TUI goal mode uses
                a different prompt than API chat mode).
            allow_shell_tools: Whether the registry may include shell tools.
            persona: Optional role/persona name (e.g. ``researcher``,
                ``strategist``). When provided and valid, its system prompt
                replaces the default chat prompt. Unknown personas fall back
                to the default chat persona.

        Returns:
            ``{"message_id": ..., "attempt_id": ..., "queue_position": N}``
            for the user message and the spawned Attempt. On queue-full,
            returns ``{"error": "queue_full", "limit": 10, "current_size": N}``.
        """
        logger.info("[MSG] send_message session=%s content_len=%d", session_id, len(content))

        # ── 1. Generate user message ID + timestamp ────────────────────
        # B4: user message is persisted via EventBusV2 → projector.flush(),
        # not via direct store.append_message(). The message_received event
        # carries the same data that the old direct write used.
        import time
        _ts = time.time()
        user_msg_id = str(uuid.uuid4())
        logger.info("[MSG] user_msg_id=%s", user_msg_id)

        # ── 2. Auto-title on first user message ───────────────────────
        try:
            from ..routers.web_session import _get_db, auto_title_session

            new_title = auto_title_session(session_id, content)
            if new_title:
                conn = _get_db()
                row = conn.execute(
                    "SELECT message_count, starred, tags_json, archived "
                    "FROM sessions WHERE id = ?",
                    (session_id,),
                ).fetchone()
                meta_update = {
                    "session_id": session_id,
                    "title": new_title,
                    "message_count": row["message_count"] if row else 0,
                    "starred": bool(row["starred"]) if row else False,
                    "tags": json.loads(row["tags_json"]) if row and row["tags_json"] else [],
                    "archived": bool(row["archived"]) if row else False,
                }
                self.event_bus.emit(session_id, "session_meta_updated", meta_update)
        except Exception as exc:
            logger.warning("auto_title_session failed: %s", exc)

        # ── 3. Queue length hard limit ────────────────────────────────
        queue = self._session_queues.get(session_id)
        current_size = queue.qsize() if queue is not None else 0
        if current_size >= _QUEUE_LIMIT:
            return {
                "error": "queue_full",
                "limit": _QUEUE_LIMIT,
                "current_size": current_size,
                "message_id": user_msg_id,
            }

        # ── 4. Create Attempt ─────────────────────────────────────────
        attempt = Attempt(
            session_id=SessionId(session_id),
            prompt=content,
            message_id=MessageId(str(uuid.uuid4())),
            status=AttemptStatus.PENDING,
            created_at=_utc_now_iso(),
            persona=persona,
            mode=mode or "build",
            model_override=model,
            thinking=thinking or "auto",
        )
        self.store.create_attempt(attempt)
        self.event_bus.emit(
            session_id,
            "attempt.created",
            {"attempt_id": attempt.attempt_id, "prompt": content},
        )

        # ── 5. Compute queue position + status ────────────────────────
        queue_position = current_size + 1  # 1-based
        is_first = (
            session_id not in self._processing_sessions
            and queue_position == 1
        )
        status = "processing" if is_first else "queued"

        # ── 6. Emit message_received with queue metadata ──────────────
        self.event_bus.emit(
            session_id,
            "message_received",
            {
                "message_id": user_msg_id,
                "user_message_id": user_msg_id,
                "assistant_message_id": attempt.message_id,
                "role": "user",
                "content": content,
                "attempt_id": attempt.attempt_id,
                "status": status,
                "queue_position": queue_position,
                "queue_length": queue_position,  # grows as items are added; later items will see actual length
                "created_at": _ts,
            },
        )

        # ── 7. Enqueue + start consumer if not running ────────────────
        if queue is None:
            queue = asyncio.Queue()
            self._session_queues[session_id] = queue
        await queue.put(
            {
                "attempt_id": attempt.attempt_id,
                "model": model,
                "max_iterations": max_iterations,
                "system_prompt": system_prompt,
                "allow_shell_tools": allow_shell_tools,
                "mode": mode or "build",
                "thinking": thinking or "auto",
            }
        )
        # Recompute queue_length now that we've enqueued (best-effort snapshot
        # for frontend display; full count will be consistent within tolerance).
        self.event_bus.emit(
            session_id,
            "queue_state",
            {
                "session_id": session_id,
                "queue_length": queue.qsize(),
            },
        )

        if session_id not in self._processing_sessions:
            self._processing_sessions.add(session_id)
            consumer = asyncio.create_task(self._process_session_queue(session_id))
            self._queue_consumers[session_id] = consumer
            consumer.add_done_callback(
                lambda t: self._queue_consumers.pop(session_id, None)
            )

        return {
            "message_id": user_msg_id,
            "user_message_id": user_msg_id,
            "assistant_message_id": attempt.message_id,
            "attempt_id": attempt.attempt_id,
            "queue_position": queue_position,
            "status": status,
        }

    def cancel(self, attempt_id: AttemptId) -> bool:
        """Cancel an in-flight Attempt by its id.

        Looks up the per-attempt task created inside ``_process_session_queue``
        and cancels it. The consumer loop catches ``CancelledError`` and pauses
        the queue until ``resume_queue()`` is called.
        """
        task = self._active_loops.get(attempt_id)
        if task is None:
            return False
        task.cancel()
        return True

    def cancel_session(self, session_id: SessionId) -> bool:
        """Cancel the in-flight attempt for a session (if any).

        Used when the caller knows the session but not the attempt id
        (e.g. the frontend's Cancel button). Returns True if cancelled.

        Also cancels the in-flight attempt task directly to prevent orphan
        tasks when the consumer is cancelled while an attempt is running.
        """
        # First, cancel the in-flight attempt task (if any) to prevent orphans
        for aid, task in list(self._active_loops.items()):
            # Find the attempt task belonging to this session
            # (check via store lookup since _active_loops keys by attempt_id)
            attempt = self.store.get_attempt(session_id, aid)
            if attempt is not None and not task.done():
                task.cancel()
                break

        consumer = self._queue_consumers.get(session_id)
        if consumer is None:
            return False
        consumer.cancel()
        return True

    def resume_queue(self, session_id: str) -> bool:
        """Resume a paused queue after an explicit cancel.

        Returns True if a paused session was found and resumed.
        """
        event = self._paused_sessions.get(session_id)
        if event is None:
            return False
        event.set()
        return True

    def is_session_processing(self, session_id: str) -> bool:
        """Return True while a chat attempt is in flight for the session.

        Used by the goal workflow (``core/goal/workflow.py``) to avoid
        running a goal workflow in the same session an AgentLoop is
        already driving. The previous v1 study scheduler also consulted
        this, but the v2 refactor decoupled chat/study sessions — see
        ``core/study/_archived/chat_mutex_legacy.py``.
        """
        return session_id in self._processing_sessions

    def get_available_actions(self, session_id: str) -> list[dict[str, str]]:
        """Actions the current session state permits (C3: drives the UI).

        Returns a list of ``{name, label, destructive}`` dicts.
        """
        actions: list[dict[str, str]] = []
        if session_id in self._processing_sessions:
            # Running attempt: can cancel
            actions.append({"name": "cancel", "label": "取消", "destructive": "false"})
        if session_id in self._paused_sessions:
            # Paused: can resume
            actions.append({"name": "resume", "label": "恢复", "destructive": "false"})
        if session_id not in self._processing_sessions and session_id not in self._paused_sessions:
            # Idle: can send
            actions.append({"name": "send", "label": "发送", "destructive": "false"})
        return actions

    def mark_session_processing(
        self, session_id: str, *, processing: bool
    ) -> None:
        """Cooperatively add/remove the session from the processing set.

        Goal workflow uses this to claim the session's processing slot
        and block concurrent chat attempts. The v1 study scheduler did
        the same, but the v2 refactor decoupled chat/study sessions —
        see ``core/study/_archived/chat_mutex_legacy.py``.
        """
        if processing:
            self._processing_sessions.add(session_id)
        else:
            self._processing_sessions.discard(session_id)

    def list_active_attempts(self, session_id: str) -> list[dict[str, str]]:
        """Non-terminal attempts for a session, oldest first (reload recovery).

        Powers ``GET /api/chat/attempts``: after a page reload the
        frontend rebuilds its streaming/queued state from this list
        (see docs/streaming-reload-recovery.md).

        Guards (in-memory state is authoritative; the attempts table is
        only a snapshot and survives restarts):
        - ``running`` is reported only when the attempt's task is in
          ``self._active_loops`` — a stale ``running`` row left behind
          by a server restart is skipped.
        - ``pending`` is reported only while the session still has a
          live consumer queue (``self._session_queues``). The queue is
          dropped once drained (``_process_session_queue``), so stale
          pending rows from before a restart are skipped too.

        C1: also returns the 5 most recent FAILED attempts (with error)
        so the frontend can display failure reasons.

        Returns one dict per live attempt:
        ``{"attempt_id", "message_id", "status", "prompt", "created_at",
          "error"}``
        with status normalized to ``running`` / ``queued`` / ``failed``.
        """
        attempts = self.store.list_attempts_by_status(
            session_id,
            [AttemptStatus.PENDING.value, AttemptStatus.RUNNING.value,
             AttemptStatus.FAILED.value],
        )
        queue_alive = session_id in self._session_queues
        out: list[dict[str, str]] = []
        failed_count = 0
        for attempt in attempts:
            if attempt.status == AttemptStatus.RUNNING:
                if attempt.attempt_id not in self._active_loops:
                    continue
                status = "running"
            elif attempt.status == AttemptStatus.FAILED:
                # C1: include up to 5 recent failures with error info
                failed_count += 1
                if failed_count > 5:
                    continue
                status = "failed"
            else:
                if not queue_alive:
                    continue
                status = "queued"
            out.append({
                "attempt_id": attempt.attempt_id or "",
                "message_id": attempt.message_id or "",
                "status": status,
                "prompt": attempt.prompt[:200] if attempt.prompt else "",
                "created_at": attempt.created_at,
                "error": attempt.error or "",
            })
        return out

    async def wait_for_attempt(
        self,
        session_id: str,
        attempt_id: str,
        timeout: float = 600.0,
    ) -> Optional[dict[str, str]]:
        """Wait for a background attempt to finish; return its outcome.

        Uses asyncio.Event (PERF-1) for zero-latency wakeup on
        completion instead of polling. The event is set by
        ``_signal_attempt_done`` when the attempt reaches a terminal state.

        Returns ``{"status", "summary", "error"}`` on completion, or
        None when the timeout elapses while the attempt is still
        pending/running (e.g. a long queue ahead of it).
        """
        # Fast path: already completed
        result = self._attempt_results.get(attempt_id)
        if result is not None:
            return result

        # Create event for this attempt
        event = asyncio.Event()
        self._attempt_events[attempt_id] = event
        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            return None
        finally:
            self._attempt_events.pop(attempt_id, None)
        return self._attempt_results.pop(attempt_id, None)

    async def _process_session_queue(self, session_id: str) -> None:
        """Consumer coroutine for a session's message queue.

        Drains ``self._session_queues[session_id]`` one item at a time,
        running each as ``_run_attempt``. After an explicit cancel
        (``CancelledError`` raised by ``cancel(attempt_id)``), pauses
        the queue and emits ``queue_paused`` until ``resume_queue``
        sets the resume event.
        """
        queue = self._session_queues[session_id]
        try:
            while True:
                item = await queue.get()

                # If a previous attempt was explicitly cancelled, the queue is
                # paused. Wait for resume before processing the next item.
                pause_event = self._paused_sessions.get(session_id)
                if pause_event is not None:
                    await pause_event.wait()
                    self._paused_sessions.pop(session_id, None)

                # Resolve the attempt row from DB
                attempt = self.store.get_attempt(session_id, item["attempt_id"])
                if attempt is None:
                    logger.warning(
                        "Attempt %s vanished from DB before queue consumer "
                        "could run it",
                        item["attempt_id"],
                    )
                    queue.task_done()
                    continue

                # Run the attempt as a tracked task so cancel(attempt_id) works
                attempt_task = asyncio.create_task(
                    self._run_attempt(
                        session_id=session_id,
                        attempt=attempt,
                        model=item.get("model"),
                        max_iterations=item.get("max_iterations", 1),
                        system_prompt=item.get("system_prompt"),
                        allow_shell_tools=item.get("allow_shell_tools", False),
                        mode=item.get("mode", "build"),
                        thinking=item.get("thinking", "auto"),
                    )
                )
                self._active_loops[attempt.attempt_id] = attempt_task

                try:
                    await attempt_task
                except asyncio.CancelledError:
                    # User explicit cancel — pause the queue and emit SSE so
                    # the frontend can render the "队列已暂停" banner.
                    self.event_bus.emit(
                        session_id,
                        "queue_paused",
                        {
                            "session_id": session_id,
                            "next_attempt_id": attempt.attempt_id,
                        },
                    )
                    pause_evt = asyncio.Event()
                    self._paused_sessions[session_id] = pause_evt
                    # Wait for resume before continuing the loop
                    await pause_evt.wait()
                    self._paused_sessions.pop(session_id, None)
                    queue.task_done()
                    continue
                except Exception as exc:
                    logger.exception(
                        "Queue consumer: attempt %s failed: %s",
                        attempt.attempt_id,
                        exc,
                    )
                finally:
                    self._active_loops.pop(attempt.attempt_id, None)

                queue.task_done()

                if queue.empty():
                    break
        finally:
            self._processing_sessions.discard(session_id)
            # Exit-vs-enqueue handoff: a producer that fetched this queue
            # before we decided to exit may have put an item into it (now
            # or after any future await appears between its fetch and
            # put). Popping a non-empty queue would strand that item
            # forever, so instead of trusting the empty() sample taken
            # before the finally, re-check here: restart the consumer
            # when items remain, drop the queue only when truly drained.
            q = self._session_queues.get(session_id)
            if q is not None and not q.empty():
                self._processing_sessions.add(session_id)
                consumer = asyncio.create_task(
                    self._process_session_queue(session_id)
                )
                self._queue_consumers[session_id] = consumer
                consumer.add_done_callback(
                    lambda t: self._queue_consumers.pop(session_id, None)
                )
            elif q is not None and q.empty():
                self._session_queues.pop(session_id, None)

    # ── Attempt execution ──────────────────────────────────────────────

    async def _run_attempt(
        self,
        *,
        session_id: str,
        attempt: Attempt,
        model: Optional[str],
        max_iterations: int,
        system_prompt: Optional[str],
        allow_shell_tools: bool,
        mode: str = "build",
        thinking: str = "auto",
    ) -> None:
        """Execute an Attempt: load history -> run AgentLoop -> persist result.

        Runs in its own asyncio task (``asyncio.create_task``), so the
        trace context set here is task-scoped and auto-discarded on
        completion -- no manual reset needed.
        """
        from strategy_research.foundation.observability.trace import _session_id, _trace_id
        _trace_id.set(attempt.attempt_id or uuid.uuid4().hex[:12])
        _session_id.set(session_id)
        logger.info("[EXEC] start attempt=%s session=%s", attempt.attempt_id, session_id)
        attempt.mark_running()

        # Build LLM config early — needed for compaction filter setting
        from ..routers.chat import _build_llm_config
        cfg = _build_llm_config()
        logger.debug("[DIAG] cfg.model=%s cfg.provider=%s cfg.max_tokens=%s",
                     cfg.model if cfg else "N/A",
                     cfg.provider if cfg else "N/A",
                     cfg.max_tokens if cfg else "N/A")
        if cfg is None:
            attempt.mark_failed(error="LLM config unavailable")
            self.store.update_attempt(attempt)
            self.event_bus.emit(
                session_id, "attempt.failed",
                {"attempt_id": attempt.attempt_id, "error": "LLM config unavailable"},
            )
            return
        self.store.update_attempt(attempt)
        # attempt.started carries message_id so the frontend can switch its
        # streamingMessageId from queued placeholder to actual stream.
        self.event_bus.emit(
            session_id,
            "attempt.started",
            {
                "attempt_id": attempt.attempt_id,
                "message_id": attempt.message_id,
            },
        )

        accumulated_parts: list[dict[str, Any]] = []

        try:
            # 1. Load full message history from DB and convert to OpenAI-format
            messages = self.store.get_messages(session_id, limit=100)
            logger.info("[EXEC] loaded %d messages from DB", len(messages))

            # Get keep_all_compactions setting (default False if cfg unavailable)
            keep_all = bool(
                cfg and cfg.compact_config
                and cfg.compact_config.keep_all_compactions_in_history
            )
            history = self._convert_messages_to_history(
                messages, keep_all_compactions=keep_all
            )
            logger.info("[EXEC] converted to %d history entries", len(history))

            # Log compaction messages in history
            compaction_count = sum(1 for h in history
                                  if h.get("role") == "user"
                                  and "<conversation-checkpoint>" in h.get("content", ""))
            logger.info("[EXEC] compaction_messages_in_history=%d", compaction_count)

            # 2. Run AgentLoop with history context
            logger.info("[EXEC] running agent_loop model=%s max_iter=%d", model, max_iterations)
            # C1: wallclock timeout — a hung AgentLoop (stuck tool/LLM
            # stream) must not block the session queue forever.
            if _CHAT_ATTEMPT_TIMEOUT > 0:
                try:
                    result_dict = await asyncio.wait_for(
                        self._run_with_agent(
                            attempt=attempt,
                            history=history,
                            model=model,
                            max_iterations=max_iterations,
                            system_prompt=system_prompt,
                            allow_shell_tools=allow_shell_tools,
                            persona=getattr(attempt, "persona", None),
                            mode=mode,
                            thinking=thinking,
                            cfg=cfg,
                            accumulated_parts=accumulated_parts,
                        ),
                        timeout=_CHAT_ATTEMPT_TIMEOUT,
                    )
                except asyncio.TimeoutError:
                    logger.warning(
                        "[EXEC] attempt %s timed out after %ds",
                        attempt.attempt_id, _CHAT_ATTEMPT_TIMEOUT,
                    )
                    attempt.mark_failed(
                        error=f"attempt timed out after {_CHAT_ATTEMPT_TIMEOUT}s"
                    )
                    self.store.update_attempt(attempt)
                    self.event_bus.emit(
                        session_id, "attempt.failed",
                        {"attempt_id": attempt.attempt_id, "status": "error",
                         "error": attempt.error},
                    )
                    self.event_bus.emit(
                        session_id, "agent_done",
                        {"message_id": attempt.message_id, "status": "error"},
                    )
                    # C1.3: hanging events for observability
                    try:
                        from strategy_research.foundation.observability.hanging_events import record_event
                        record_event(
                            "chat_attempt_stall",
                            session_id=session_id,
                            detail=f"attempt={attempt.attempt_id} timeout={_CHAT_ATTEMPT_TIMEOUT}s",
                        )
                    except Exception:  # noqa: BLE001 — best-effort
                        pass
                    self._signal_attempt_done(attempt.attempt_id, {
                        "status": "error", "summary": "",
                        "error": f"attempt timed out after {_CHAT_ATTEMPT_TIMEOUT}s",
                    })
                    return
            else:
                result_dict = await self._run_with_agent(
                    attempt=attempt,
                    history=history,
                    model=model,
                    max_iterations=max_iterations,
                    system_prompt=system_prompt,
                    allow_shell_tools=allow_shell_tools,
                    persona=getattr(attempt, "persona", None),
                    mode=mode,
                    thinking=thinking,
                    cfg=cfg,  # Pass cfg from _run_attempt to avoid NameError
                    accumulated_parts=accumulated_parts,
                )
            logger.info("[EXEC] agent_result status=%s content_len=%d",
                       result_dict.get("status"), len(result_dict.get("content", "")))

            # 3. Update Attempt and persist assistant message
            answer = result_dict.get("content") or ""
            status = result_dict.get("status", "empty")
            finished_reason = result_dict.get("finished_reason", "")
            agent_error = result_dict.get("error")
            is_error = finished_reason == "error" or status == "failed"

            if status == "success":
                attempt.mark_completed(summary=answer)
            elif is_error:
                error_msg = agent_error or result_dict.get("reason", "unknown error")
                attempt.mark_failed(error=error_msg)
            else:
                # "empty" — agent ran but produced no output
                attempt.mark_completed(summary="")
            attempt.run_dir = result_dict.get("run_dir")
            attempt.metrics = result_dict.get("metrics")
            attempt.message_id = result_dict.get("message_id") or attempt.message_id
            self.store.update_attempt(attempt)
            # Persist message with the Attempt's message_id
            # (this is the SAME id SSE events carry, so they can find it).
            assistant_content = answer or "".join(
                p.get("text", "") for p in accumulated_parts if p.get("type") == "text"
            )

            if is_error:
                # Error message: friendly text + detail in metadata
                friendly = _friendly_error_text(agent_error or result_dict.get("reason", ""))
                detail = agent_error or result_dict.get("reason", "unknown error")
                # B4: persisted via EventBusV2 → projector.flush()
                self.event_bus.emit(
                    session_id,
                    "assistant_message",
                    {
                        "message_id": attempt.message_id,
                        "content": friendly,
                        "message_type": "error",
                        "metadata": {
                            "status": "error",
                            "details": detail,
                        },
                    },
                )
            else:
                # B4: success path — emit assistant_message with final content
                # so the projector can finalize the message. The parts
                # (text, tool_call, thinking) were already emitted via
                # event_callback during streaming.
                self.event_bus.emit(
                    session_id,
                    "assistant_message",
                    {
                        "message_id": attempt.message_id,
                        "content": assistant_content,
                        "message_type": "assistant",
                        "metadata": {
                            "run_id": str(Path(attempt.run_dir).name) if attempt.run_dir else None,
                            "status": attempt.status.value,
                            "metrics": attempt.metrics,
                            "claim_validation": (
                                (attempt.metrics or {}).get("claim_validation")
                                if attempt.metrics else None
                            ),
                        },
                    },
                )

            # 4. Emit attempt.completed / attempt.failed
            self.event_bus.emit(
                session_id,
                "attempt.completed" if attempt.status == AttemptStatus.COMPLETED else "attempt.failed",
                {
                    "attempt_id": attempt.attempt_id,
                    "status": attempt.status.value,
                    "summary": attempt.summary,
                    "error": attempt.error,
                    "run_dir": attempt.run_dir,
                    "message_id": attempt.message_id,
                    "asked_user": bool(result_dict.get("asked_user")),
                },
            )

            # 5. Signal agent_done so the frontend clears streaming state
            self.event_bus.emit(
                session_id,
                "agent_done",
                {
                    "message_id": attempt.message_id,
                    "status": attempt.status.value,
                    "asked_user": bool(result_dict.get("asked_user")),
                },
            )
            # PERF-1: signal event-driven wait_for_attempt
            self._signal_attempt_done(attempt.attempt_id, {
                "status": attempt.status.value,
                "summary": attempt.summary or "",
                "error": attempt.error or "",
            })
        except asyncio.CancelledError:
            attempt.mark_failed(error="cancelled")
            self.store.update_attempt(attempt)
            self.event_bus.emit(
                session_id,
                "attempt.failed",
                {"attempt_id": attempt.attempt_id, "status": "cancelled"},
            )
            self.event_bus.emit(
                session_id,
                "agent_done",
                {"message_id": attempt.message_id, "status": "cancelled"},
            )
            self._signal_attempt_done(attempt.attempt_id, {
                "status": "cancelled", "summary": "", "error": "cancelled",
            })
            raise
        except Exception as exc:
            logger.exception("AgentLoop failed for attempt %s", attempt.attempt_id)
            attempt.mark_failed(error=str(exc))
            self.store.update_attempt(attempt)
            self.event_bus.emit(
                session_id,
                "attempt.failed",
                {"attempt_id": attempt.attempt_id, "error": str(exc)},
            )
            self.event_bus.emit(
                session_id,
                "agent_done",
                {"message_id": attempt.message_id, "status": "error", "error": str(exc)},
            )
            self._signal_attempt_done(attempt.attempt_id, {
                "status": "error", "summary": "", "error": str(exc),
            })

    def _signal_attempt_done(self, attempt_id: str, result: dict[str, str]) -> None:
        """PERF-1: signal an event-driven wait_for_attempt that the attempt is done."""
        self._attempt_results[attempt_id] = result
        event = self._attempt_events.get(attempt_id)
        if event is not None:
            event.set()

    async def _run_with_agent(
        self,
        *,
        attempt: Attempt,
        history: list[dict[str, Any]],
        model: Optional[str],
        max_iterations: int,
        system_prompt: Optional[str],
        allow_shell_tools: bool,
        accumulated_parts: list[dict[str, Any]],
        cfg: LLMConfig,
        persona: Optional[str] = None,
        mode: str = "build",
        thinking: str = "auto",
    ) -> dict[str, Any]:
        """Build AgentLoop and run it. Returns ``{content, status, ...}``.

        Args:
            cfg: Pre-built LLM config from caller (_run_attempt). Required
                to avoid the cfg-undefined NameError regression that
                happened when Phase 1 compaction filter changes were made.
        """
        # Test mode: when STRATEGY_RESEARCH_TEST_CHAT=1, emit a scripted
        # SSE stream and return immediately — no LLM needed. Restores the
        # pre-refactor behavior (chat.py:_run_agent_loop_background) that
        # send_async used to short-circuit to in test mode; the unified
        # SessionService path lost that hook when send_async was migrated.
        if os.environ.get("STRATEGY_RESEARCH_TEST_CHAT") == "1":
            return await self._run_test_script(
                attempt=attempt,
                accumulated_parts=accumulated_parts,
            )

        from strategy_research.kernel.agent.chat_loop import build_chat_agent_loop

        # cfg is passed in by caller; just apply model override
        if cfg and model:
            cfg.model = model

        # Event callback: forward AgentLoop events → EventBus. Each event
        # carries message_id so SSE clients can correlate; llm_usage tokens
        # accumulate into attempt-local metrics (see _LoopEventForwarder).
        forwarder = _LoopEventForwarder(
            self,
            attempt=attempt,
            accumulated_parts=accumulated_parts,
            event_bus=self.event_bus,
            cfg=cfg,
        )

        # Build AgentLoop (Phase 6: via shared factory)
        workspace_path = Path(os.environ.get("SR_WORKSPACE_PATH", str(Path.cwd())))

        # Bootstrap workspace if incomplete
        _bootstrap_workspace(workspace_path)

        # ── Plan mode: restrict to read-only tools ────────────────────
        allowed_tools = _plan_mode_allowed_tools(mode)

        # ── Thinking parameter injection ──────────────────────────────
        thinking_instructions = _thinking_instructions(thinking)

        # Persona override: when a valid persona is requested, render its
        # system prompt and use it in place of the default chat prompt.
        final_prompt, loop_role = _resolve_loop_prompt(system_prompt, persona)

        # Append thinking instructions to system prompt
        if thinking_instructions and final_prompt is not None:
            final_prompt = final_prompt + thinking_instructions

        agent = build_chat_agent_loop(
            config=cfg,
            session_id=attempt.session_id,
            role=loop_role,
            workspace=workspace_path,  # P3: real workspace path for {workspace}
            on_event=forwarder,
            event_bus=self.event_bus,
            max_iterations=max_iterations,
            system_prompt_override=final_prompt,  # caller-provided wins
            allow_shell_tools=allow_shell_tools,
            allowed_tools=allowed_tools,  # Plan mode: read-only tools
            enable_goal_injection=True,  # long-horizon: continue until goal criteria met
        )

        # Run synchronously inside the asyncio loop (AgentLoop.arun is async).
        # DAG-orchestrator continuation guard: when the LLM ends a turn with
        # a question instead of driving the DAG loop (AgentLoop breaks on any
        # turn without tool calls), inject a "keep going" turn and rerun —
        # bounded by SR_ORCHESTRATOR_MAX_CONTINUES (default 10) so a
        # pathological loop always terminates normally.
        try:
            loop_result, asked_user, retries = await self._run_with_guard(agent, attempt, history)
        except RuntimeError as exc:
            return {"status": "failed", "reason": str(exc), "content": ""}

        return {
            "status": "success" if loop_result.answer else "empty",
            "content": loop_result.answer or "",
            "run_dir": None,
            "iterations": loop_result.iterations,
            "tool_calls_made": loop_result.tool_calls_made,
            "finished_reason": loop_result.finished_reason,
            "asked_user": asked_user,
            "continuations": retries,
            "error": loop_result.error,
            "metrics": _build_attempt_metrics(forwarder.usage_state, loop_result),
        }

    async def _run_with_guard(self, agent: Any, attempt: Attempt, history: list[dict[str, Any]]):
        """Run ``agent.arun`` with the DAG-orchestrator continuation guard.

        When the LLM ends a turn with a question instead of driving the DAG
        loop (AgentLoop breaks on any turn without tool calls), inject a
        "keep going" turn and rerun — bounded by
        ``SR_ORCHESTRATOR_MAX_CONTINUES`` (default 10) so a pathological loop
        always terminates normally.

        Returns ``(loop_result, asked_user, retries)`` on success; raises
        ``RuntimeError`` on AgentLoop failure (caller converts to a failed
        result dict).
        """
        from strategy_research.kernel.workflow.orchestrate_guard import (
            continue_instruction,
            looks_like_question,
            max_continues,
        )

        is_dag_session = attempt.session_id.startswith("dag:")
        retries = 0
        guard_cap = max_continues() if is_dag_session else 0
        while True:
            try:
                loop_result = await agent.arun(attempt.prompt, history=history)
            except Exception as exc:
                logger.exception("AgentLoop.arun failed")
                raise RuntimeError(str(exc)) from exc

            final_answer = loop_result.answer or ""
            asked_user = (
                is_dag_session
                and loop_result.tool_calls_made == 0
                and looks_like_question(final_answer)
            )
            if not asked_user or retries >= guard_cap:
                logger.debug(
                    "[DIAG] AgentLoop result: answer=%.200r finished_reason=%s error=%s iterations=%d tool_calls=%d",
                    loop_result.answer[:200] if loop_result.answer else "",
                    loop_result.finished_reason,
                    loop_result.error,
                    loop_result.iterations,
                    loop_result.tool_calls_made,
                )
                return loop_result, asked_user, retries
            logger.info(
                "[DAG-GUARD] orchestrator answered with a question (retry %d/%d), continuing",
                retries + 1,
                guard_cap,
            )
            # Feed the LLM its own answer plus the continuation instruction
            # as history so the next attempt sees the full conversation.
            history = list(history) + [
                {"role": "assistant", "content": final_answer},
                {"role": "user", "content": continue_instruction()},
            ]
            retries += 1

    async def _run_test_script(
        self,
        *,
        attempt: Attempt,
        accumulated_parts: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Emit a scripted SSE stream for E2E tests (no LLM needed).

        Mirrors the real AgentLoop event sequence (iter_start → text_delta
        → iter_end) so SSE tests are deterministic and offline. The caller
        (_run_attempt) persists the assistant message and emits
        attempt.completed / agent_done afterwards, matching production.
        """
        session_id = attempt.session_id
        message_id = attempt.message_id
        attempt_id = attempt.attempt_id
        text_id = f"txt_{uuid.uuid4().hex[:12]}"

        reply_parts = [
            f"这是对「{attempt.prompt[:40]}」的脚本化回复。",
            " 主要演示 SSE 流式事件分发链路。",
            "\n\n**测试要点**：",
            "\n- 事件 message_id 关联",
            "\n- streamingText 累积",
            "\n- agent_done 清空 streamingMessageId",
        ]
        full_text = "".join(reply_parts)

        def emit(event_type: str, data: dict[str, Any]) -> None:
            """Publish an SSE event for this session (bus adapter)."""
            data = dict(data)
            data.setdefault("message_id", message_id)
            data.setdefault("attempt_id", attempt_id)
            self.event_bus.emit(session_id, event_type, data)

        emit("iter_start", {"iteration": 1, "max_iterations": 9999999999})
        emit("text.started", {"text_id": text_id})

        emit("thinking_start", {})
        think_chunks = ["分析用户问题", " → 检索相关策略", " → 准备回复"]
        for chunk in think_chunks:
            await asyncio.sleep(0.03)
            emit("thinking_delta", {"delta": chunk})
        emit("thinking_done", {})

        for part in reply_parts:
            await asyncio.sleep(0.03)
            emit("text_delta", {"text_id": text_id, "text": part})
        emit("thinking_end", {})
        emit("text.ended", {"text_id": text_id, "text": full_text})
        emit("iter_end", {"iteration": 1, "finish_reason": "stop", "tool_calls_made": 0})

        accumulated_parts.append({"type": "text", "text": full_text})

        return {
            "status": "success",
            "content": full_text,
            "run_dir": None,
            "iterations": 1,
            "tool_calls_made": 0,
            "finished_reason": "stop",
            "error": None,
            "metrics": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
        }

    # ── Compact ──────────────────────────────────────────────────────

    @staticmethod
    def _extract_summary(messages: list[dict[str, Any]]) -> str:
        """Extract summary content from compressed messages.

        Supports three storage formats:
        1. New: message_type='compaction' with parts_json containing summary
        2. Legacy: content field starts with [context summary] prefix
        3. Legacy: content starts with ## Anchored Summary or ## Objective

        The legacy format checks are for backward compatibility with old data.
        """
        parts = []
        for m in messages:
            msg_type = m.get("message_type") or m.get("role")
            content = m.get("content", "") or ""
            # New format: message_type='compaction' (parts_json has the summary)
            if msg_type == "compaction":
                try:
                    parts_data = m.get("parts_json") or "[]"
                    if isinstance(parts_data, str):
                        import json as _json
                        parts_data = _json.loads(parts_data)
                    comp_part = next(
                        (p for p in parts_data
                         if isinstance(p, dict) and p.get("type") == "compaction"),
                        None,
                    )
                    if comp_part:
                        parts.append(comp_part.get("summary", content))
                        continue
                except Exception:
                    pass
                parts.append(content)
            # Legacy format: content starts with [context summary]
            elif content.startswith("[context summary]"):
                parts.append(content[len("[context summary]"):].strip())
            # Legacy format: content starts with ## Anchored Summary or ## Objective
            elif content.startswith("## Anchored Summary") or content.startswith("## Objective"):
                parts.append(content)
        return "\n\n".join(parts)

    @staticmethod
    def _build_fallback_summary(
        compressed: list[dict[str, Any]],
        original: list[dict[str, Any]],
    ) -> str:
        """Build a basic structured summary when LLM summarization didn't run."""
        # Collect assistant text snippets from original (last 5 turns)
        assistant_snippets: list[str] = []
        user_msgs: list[str] = []
        for m in original:
            role = m.get("role", "")
            content = (m.get("content") or "").strip()
            # Skip compaction messages (new format: message_type='compaction')
            if m.get("message_type") == "compaction":
                continue
            # Skip legacy compaction messages (content starts with [context summary])
            if not content or content.startswith("[context summary]"):
                continue
            if role == "assistant" and len(content) > 20:
                # Keep first 200 chars of each assistant message
                assistant_snippets.append(content[:200])
            elif role == "user":
                user_msgs.append(content[:100])

        # Last user message = likely current objective
        objective = user_msgs[-1] if user_msgs else "(none)"

        # Build structured summary
        lines = [
            "## Objective",
            f"- {objective}",
            "",
            "## Important Details",
        ]
        if len(compressed) < len(original):
            removed = len(original) - len(compressed)
            lines.append(f"- {removed} messages removed during compaction")
        else:
            lines.append("- (none)")
        lines += [
            "",
            "## Work State",
            "### Completed",
        ]
        # Last 2 assistant snippets as completed work
        for snippet in assistant_snippets[-2:]:
            lines.append(f"- {snippet[:150]}")
        if not assistant_snippets:
            lines.append("- (none)")
        lines += [
            "",
            "### Active",
            "- (none)",
            "",
            "### Blocked",
            "- (none)",
            "",
            "## Next Move",
            "1. Continue from where the conversation left off",
            "",
            "## Relevant Files",
            "- (none)",
        ]
        return "\n".join(lines)

    async def compact_history(
        self,
        session_id: str,
        config: CompactConfig | None = None,
    ) -> dict[str, Any]:
        """Compress session history in-place using compact_messages.

        Writes compressed results back to the database.
        Returns dict with keys: layers, before_tokens, after_tokens, summary.

        Acquires per-session compact lock to prevent concurrent compaction
        (auto from agent loop + manual /compact) on the same session.
        """
        from strategy_research.kernel.agent.compact import _compact_locks
        from ..routers.chat import _build_llm_config

        async with await _compact_locks.get(session_id):
            messages = self.store.get_messages(session_id, limit=10000)
            # keep_all_compactions from the caller-supplied CompactConfig
            keep_all = bool(
                config
                and config.keep_all_compactions_in_history
            )
            history = self._convert_messages_to_history(
                messages, keep_all_compactions=keep_all
            )

            if not history:
                return {"layers": [], "before_tokens": 0, "after_tokens": 0, "summary": ""}

            cfg = _build_llm_config()
            llm_client = None
            model_context_tokens = None
            if cfg:
                model_context_tokens = cfg.model_context_tokens
                try:
                    from strategy_research.foundation.llm import OpenAICompatClient
                    llm_client = OpenAICompatClient(cfg)
                except Exception:
                    pass

            compressed, layers, _l4_summary, _l4_recent = compact_messages(
                history,
                config=config,
                threshold_tokens=0,  # force all layers for manual /compact
                model_context_tokens=model_context_tokens,
                llm_client=llm_client,
            )

            before_tokens = sum(len(m.get("content", "")) for m in history)
            after_tokens = sum(len(m.get("content", "")) for m in compressed)

            # Build summary: prefer LLM-generated [context summary], fallback to extraction
            summary = self._extract_summary(compressed)
            if not summary and layers:
                summary = self._build_fallback_summary(compressed, history)

            # Persist compressed history via event-sourcing (B4/B5)
            # Emit compact.ended event with the compressed message set.
            # The projector keeps ALL original messages (chat record) and
            # adds a compaction marker carrying compacted_until_seq.
            # EventBusV2.flush_to_messages=True ensures messages table is updated.
            if layers:
                try:
                    # C4.2: emit compact.count with message counts for observability
                    msg_count_before = len(messages)
                    msg_count_after = len(compressed) if compressed else msg_count_before
                    self.event_bus.emit(
                        session_id,
                        "compact.count",
                        {
                            "messages_before": msg_count_before,
                            "messages_after": msg_count_after,
                            "tokens_before": before_tokens,
                            "tokens_after": after_tokens,
                            "layers": layers,
                        },
                    )

                    # opencode-aligned: emit compact.ended with the
                    # compressed set. The projector KEEPS all original
                    # messages in the projection (chat record preserved)
                    # and records compacted_until_seq on the marker so the
                    # LLM context builder can hide the covered messages.
                    # The boundary is computed from the compressed recent
                    # messages' seqs (attached during history conversion).
                    compacted_until_seq = None
                    recent_seqs = [
                        m.get("seq") for m in (compressed or [])
                        if m.get("role") != "system" and isinstance(m.get("seq"), int)
                    ]
                    if recent_seqs:
                        compacted_until_seq = min(recent_seqs) - 1
                    self.event_bus.emit(
                        session_id,
                        "compact.ended",
                        {
                            "summary": summary or f"Compressed {before_tokens} → {after_tokens} tokens",
                            "before_tokens": before_tokens,
                            "after_tokens": after_tokens,
                            "layers": layers,
                            "messages": compressed,
                            "compacted_until_seq": compacted_until_seq,
                        },
                    )
                except Exception:
                    logger.exception("failed to emit compact event")

            return {
                "layers": layers,
                "before_tokens": before_tokens,
                "after_tokens": after_tokens,
                "summary": summary,
                "compressed": compressed,
            }

    # ── History conversion ────────────────────────────────────────────

    @staticmethod
    def _convert_messages_to_history(
        messages: list[Message],
        *,
        keep_all_compactions: bool = False,
    ) -> list[dict[str, Any]]:
        """Convert Session messages to OpenAI-format history.

        Handles three storage formats:
        1. New: role=tool messages with tool_call_id (1:1 with OpenAI format)
        2. Legacy: tool_calls embedded in assistant message parts (reconstructed from parts)
        3. Compaction: message_type='compaction' → user role with checkpoint wrap

        **Assistant-Tool ordering (opencode-aligned, Level 0)**:
        For each assistant message with tool_calls, all corresponding tool
        result messages are emitted IMMEDIATELY AFTER the assistant (not
        in raw `created_at` order). This is required by the OpenAI tool
        protocol: `tool` messages must follow the `assistant(tool_calls)`
        that generated them. Violating this order causes provider errors
        like MiniMax 400 "chat content is empty" (2013).

        See opencode `to-llm-message.ts:assistant()` for the reference
        implementation: tools are physically part of the assistant message
        there, making the correct order a structural invariant. We achieve
        the same effect by reordering at conversion time.

        Trim semantics: character budget is enforced from newest → oldest,
        with assistant-tool groups kept intact (either the whole group
        fits or the whole group is dropped).

        Excludes the current turn (last message).

        Args:
            keep_all_compactions: When True, include all compaction messages
                in LLM history (legacy behavior). When False (default), only
                the MOST RECENT compaction is included; older compactions
                are hidden from LLM but kept in DB for audit/UI display.
                See docs/compaction-history-filter.md.
        """
        from strategy_research.kernel.agent.compact import _compaction_metrics

        logger.debug("[HIST] converting %d messages", len(messages))
        _compaction_metrics["filter_calls"] += 1

        # ── First pass: compaction + tool index bookkeeping ──
        compaction_indices = _find_compaction_indices(messages)
        keep_compaction_indices = _decide_kept_compactions(
            compaction_indices, keep_all_compactions
        )
        hidden_until_seq = _hidden_until_seq(messages, keep_compaction_indices)
        tool_to_assistant_idx, tc_to_tool_idx = _build_tool_indexes(messages)

        # ── Second pass: convert with filter + reorder ──
        # Each item: (entry_dict, group_id)
        # group_id is non-None for assistant+tools that must stay together
        # (preserved by trim).
        history_with_groups: list[tuple[dict[str, Any], int | None]] = []
        emitted_assistant_idxs: set[int] = set()
        emitted_tool_msg_idxs: set[int] = set()

        ctx = _HistoryConvertContext(
            messages=messages,
            hidden_until_seq=hidden_until_seq,
            keep_compaction_indices=keep_compaction_indices,
            tool_to_assistant_idx=tool_to_assistant_idx,
            tc_to_tool_idx=tc_to_tool_idx,
        )

        for i, msg in enumerate(messages[:-1]):
            entries = _convert_one_history_message(
                i, msg, ctx, emitted_assistant_idxs, emitted_tool_msg_idxs
            )
            if entries:
                history_with_groups.extend(entries)

        # ── Trim by character budget from newest → oldest, preserving
        #    assistant-tool group integrity. ──
        return _trim_history_groups(history_with_groups)


# ── W-split re-exports (history conversion + part accumulation) ──
from .history_convert import (  # noqa: E402,F401
    _build_tool_indexes,
    _convert_one_history_message,
    _decide_kept_compactions,
    _defer_or_drop_tool_message,
    _extract_tool_calls,
    _find_compaction_indices,
    _hidden_until_seq,
    _HistoryConvertContext,
    _trim_history_groups,
)
from .part_accumulate import (  # noqa: E402,F401
    _accumulate_part,
    _accumulate_text_delta,
    _accumulate_text_ended,
    _accumulate_text_started,
    _accumulate_thinking_delta,
    _accumulate_tool_result,
    _build_tool_call_part,
)


def _utc_now_iso() -> str:
    return datetime.utcnow().isoformat()


def _friendly_error_text(error_detail: str) -> str:
    """Map raw error detail to a user-friendly short message."""
    detail = (error_detail or "").lower()
    # MiniMax-specific: empty chat content (2013) usually means the
    # conversation history was over-compressed and the next LLM call
    # has no user content. Suggest creating a new session.
    if "2013" in detail or "chat content is empty" in detail:
        return "⚠️ 会话内容已压缩为空，请发送新消息或新建会话"
    # 400 + invalid params (e.g. malformed tool call or empty content)
    if "invalid params" in detail and "400" in detail:
        return "⚠️ 请求参数无效，请稍后重试或新建会话"
    if "rate" in detail or "429" in detail or "too many" in detail:
        return "⚠️ 模型请求频率过高，请稍后再试"
    if "timeout" in detail or "timed out" in detail:
        return "⚠️ 模型请求超时，请稍后再试"
    if "auth" in detail or "401" in detail or "403" in detail:
        return "⚠️ 模型鉴权失败，请检查 API Key 配置"
    if "quota" in detail or "balance" in detail:
        return "⚠️ 模型配额不足，请检查账户余额"
    if "server" in detail or "500" in detail or "502" in detail or "503" in detail:
        return "⚠️ 模型服务暂时不可用，请稍后再试"
    return "⚠️ 模型请求失败，请稍后再试"


__all__ = ["SessionService", "MAX_HISTORY_CHARS"]
