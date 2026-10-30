"""StudyScheduler — per-session serial execution of studies.

Phase 1 scope (see docs/study-longhorizon-plan.md):
    - submit a study → enqueue on its session's queue
    - per-session consumer loop → run one study at a time via
      AutoresearchExecutor
    - cooperative mutex with chat attempts: ask SessionService whether
      the session is currently processing before launching the executor
      and claim the processing slot for the study's lifetime
    - pause / resume / cancel via per-study ControlToken
    - recover_on_startup: re-enqueue studies left running/queued from a
      previous process
    - emit study_* events on the session's event_bus so the WebUI SSE
      channel already wired for chat also carries study progress
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Any

from .models import StudyRecord, StudyStatus
from .runner import AutoresearchRunner, ControlToken, NullEmitter
from .store import StudyStore

logger = logging.getLogger(__name__)

# v2: global concurrency cap across ALL studies (per-process semaphore).
# Study directories are autonomous (study/<id>/), so no per-strategy locks.
SR_STUDY_MAX_CONCURRENT = int(os.environ.get("SR_STUDY_MAX_CONCURRENT", "3"))
# G1: per-user concurrency ceiling (keyed by owner_session_id) so one
# user's studies cannot starve another user's queue when the global
# semaphore is saturated. 0 disables the per-user cap.
SR_STUDY_MAX_PER_USER = int(os.environ.get("SR_STUDY_MAX_PER_USER", "2"))

# Watchdog: a RUNNING study whose heartbeat is older than this is force-
# interrupted (heartbeat is bumped once per round; real-LLM rounds can run
# long, hence the conservative default of 1h).
SR_STUDY_HEARTBEAT_TIMEOUT = int(os.environ.get("SR_STUDY_HEARTBEAT_TIMEOUT", "3600"))
SR_STUDY_WATCHDOG_INTERVAL = int(os.environ.get("SR_STUDY_WATCHDOG_INTERVAL", "60"))

# Statuses that must never be (re-)executed: submitting one is rejected.
# MONITORING / INTERRUPTED / PAUSED remain resumable (recover path).
_TERMINAL_STATUSES = frozenset({
    StudyStatus.COMPLETE,
    StudyStatus.CANCELLED,
    StudyStatus.ERROR,
    StudyStatus.BUDGET_LIMITED,
    StudyStatus.EARLY_STOPPED,
    StudyStatus.NEEDS_REFRESH,
    StudyStatus.ARCHIVED,  # unarchive first, then resume_interrupted
})


def _dlog(module: str, msg: str, *args) -> None:
    """Dual-output log: logger + stderr so both file and terminal see it."""
    msg_fmt = msg % args if args else msg
    logger.info("[STUDY:%s] %s", module, msg_fmt)
    print(f"[STUDY:{module}] {msg_fmt}", flush=True)  # noqa: T201


class StudyScheduler:
    """Per-session serial scheduler for study executors.

    Holds one asyncio task per study, plus one consumer per session that
    drains the session's study queue. Note: chat/study mutex was removed
    in v2 (single-identity ``session_id == study_id``); see
    ``_archived/chat_mutex_legacy.py`` for the removed v1 code.
    """

    def __init__(
        self,
        store: StudyStore,
        *,
        session_service: Any | None = None,
        emitter_factory: Any | None = None,
    ) -> None:
        self.store = store
        self.session_service = session_service
        # emitter_factory(session_id) -> an object with .emit(session_id, event, data)
        # If None we use a NullEmitter per study.
        self._emitter_factory = emitter_factory
        # Active state
        self._active_executors: dict[str, AutoresearchRunner] = {}
        self._control_tokens: dict[str, ControlToken] = {}
        self._active_tasks: dict[str, asyncio.Task] = {}
        # 阶段 4：旧 asyncio.Queue 通道已删除；准入唯一走 mailbox。
        # 3c: per-session mailbox consumers (AUTOMATION kind only)
        self._mailbox_consumers: dict[str, asyncio.Task] = {}
        # v2: fire-and-forget dispatch tasks (session_loop → _run_one_study)
        self._dispatch_tasks: dict[str, asyncio.Task] = {}
        # v2: global concurrency semaphore (SR_STUDY_MAX_CONCURRENT)
        self._semaphore = asyncio.Semaphore(max(1, SR_STUDY_MAX_CONCURRENT))
        # G1: per-user concurrency caps (owner_session_id → Semaphore).
        # LRU-friendly: only users with queued studies get an entry.
        self._user_semaphores: dict[str, asyncio.Semaphore] = {}
        self._per_user_limit = max(1, SR_STUDY_MAX_PER_USER) if SR_STUDY_MAX_PER_USER > 0 else 0
        # Dedupe guard: study_ids sitting in a session queue (submitted but
        # not yet picked up). Prevents double-enqueue of the same study.
        self._queued_study_ids: set[str] = set()
        # Watchdog: done-task cleanup + heartbeat-stale interruption.
        self._watchdog_task: asyncio.Task | None = None
        self._watchdog_interval = max(10, SR_STUDY_WATCHDOG_INTERVAL)
        self._heartbeat_timeout = max(60, SR_STUDY_HEARTBEAT_TIMEOUT)
        self._shutdown = False

    # ── public: submit ─────────────────────────────────────────────

    async def submit(self, study: StudyRecord) -> bool:
        """Enqueue a study for execution on its session.

        Returns ``False`` (and does nothing) when the study is already
        queued / active / held by the scheduler or has reached a terminal
        status — a duplicate submit must never run a study twice.
        """
        sid = study.study_id
        # 1. Already active (running / dispatching / paused via token)?
        if sid in self._active_executors or sid in self._active_tasks \
                or sid in self._dispatch_tasks or sid in self._control_tokens:
            _dlog("sched", "submit rejected: study already active %s", sid)
            return False
        # 2. Already sitting in its session queue?
        if sid in self._queued_study_ids:
            _dlog("sched", "submit rejected: study already queued %s", sid)
            return False
        # 3. Store is authoritative for lifecycle: a terminal study must
        #    never be re-executed (e.g. duplicate start of a finished one).
        current = self.store.get_study(sid)
        if current is not None and current.execution_status in _TERMINAL_STATUSES:
            _dlog("sched", "submit rejected: study terminal %s (%s)",
                  sid, current.execution_status.value)
            return False

        _dlog("sched", "submit study=%s session=%s status=%s",
              sid, study.session_id, study.execution_status.value)
        # Mark queued in store (defensive; create_study already sets it,
        # but if submit() is called on a recovered RUNNING study, we do
        # not downgrade it here — only fresh submits pass through).
        # INTERRUPTED studies are reset to QUEUED so the runner picks them up.
        # MONITORING studies stay MONITORING (v2 §15.2 recover → monitor task).
        if study.execution_status not in (
            StudyStatus.RUNNING, StudyStatus.INTERRUPTED, StudyStatus.MONITORING,
        ):
            self.store.update_execution_status(
                study.study_id, StudyStatus.QUEUED,
            )
        elif study.execution_status == StudyStatus.INTERRUPTED:
            self.store.update_execution_status(
                study.study_id, StudyStatus.QUEUED,
                last_error=None,
            )
        self._emit_event(study.session_id, "study_queued", {
            "study_id": study.study_id, "session_id": study.session_id,
            "objective": study.objective,
        })

        # 阶段 4：旧 asyncio.Queue 路径已删除——准入通道唯一。
        # 工单进 automation:{session} mailbox，由 mailbox 消费者取走执行。
        from strategy_research.core.gateway import scheduler_request, submit_via_entry

        gw = _get_gateway_singleton()
        receipt = submit_via_entry(
            gw.entry,
            scheduler_request(
                study.session_id,
                {
                    "study_id": study.study_id,
                    "objective": study.objective,
                    "session_id": study.session_id,
                },
            ),
        )
        if not receipt.accepted:
            _dlog("sched", "gateway rejected study=%s reason=%s",
                  sid, receipt.reason)
            return False
        self._queued_study_ids.add(sid)
        self._ensure_mailbox_consumer(study.session_id)
        self._ensure_watchdog()
        _dlog("sched", "study=%s queued via Gateway", sid)
        return True

    # ── public: control ────────────────────────────────────────────

    def pause(self, study_id: str) -> bool:
        tok = self._control_tokens.get(study_id)
        if tok is None:
            return False
        tok.paused = True
        return True

    def resume(self, study_id: str) -> bool:
        """Unpause an active runner (deprecated: use continue_study instead)."""
        tok = self._control_tokens.get(study_id)
        if tok is None:
            return False
        tok.paused = False
        return True

    async def resume_interrupted(self, study_id: str) -> bool:
        """Resume an INTERRUPTED study (deprecated: use continue_study instead)."""
        study = self.store.get_study(study_id)
        if study is None:
            return False
        if study.execution_status != StudyStatus.INTERRUPTED:
            return False
        await self.submit(study)
        return True

    async def continue_study(
        self,
        study_id: str,
        *,
        from_round: int | None = None,
        mode: str = "append",
    ) -> bool:
        """Unified continue action (replaces RESUME_INTERRUPTED + RETRY).

        Behavior depends on the current study status:

        - INTERRUPTED / PAUSED: re-queue at current round (no reset).
        - ERROR / BUDGET_LIMITED / EARLY_STOPPED / NEEDS_REFRESH:
          reset round counter and restart (``mode="append"`` keeps
          history; ``mode="restart"`` clears it). Default ``from_round=1``.
        - CANCELLED: restart from round 1 (same as restart mode).
        - COMPLETE: run a fresh round from current state (no reset).

        Returns True on success, False if the status is not continueable.
        """
        study = self.store.get_study(study_id)
        if study is None:
            return False
        from .models import StudyStatus

        status = study.execution_status

        # ── Resume-like: INTERRUPTED / PAUSED ──────────────────
        if status in (StudyStatus.INTERRUPTED, StudyStatus.PAUSED):
            if status == StudyStatus.PAUSED:
                tok = self._control_tokens.get(study_id)
                if tok is not None:
                    tok.paused = False
                    return True
            await self.submit(study)
            return True

        # ── Restart-like: ERROR / BUDGET_LIMITED / EARLY_STOPPED / NEEDS_REFRESH / CANCELLED ──
        restartable = {
            StudyStatus.ERROR,
            StudyStatus.BUDGET_LIMITED,
            StudyStatus.EARLY_STOPPED,
            StudyStatus.NEEDS_REFRESH,
            StudyStatus.CANCELLED,
        }
        if status in restartable:
            effective_mode = mode or "restart"
            start_round = from_round if from_round and from_round > 1 else 1
            self.store.reset_round_counter(
                study_id, mode=effective_mode, start_round=start_round,
            )
            self.store.update_execution_status(
                study_id,
                StudyStatus.INTERRUPTED,
                last_error=None,
                last_traceback=None,
            )
            self._emit_event(study.session_id, "study_retry_queued", {
                "study_id": study_id,
                "from_round": start_round,
                "mode": effective_mode,
                "previous_status": status.value,
            })
            await self.submit(study)
            return True

        # ── Continue-like: COMPLETE ────────────────────────────
        if status == StudyStatus.COMPLETE:
            self.store.update_execution_status(
                study_id,
                StudyStatus.INTERRUPTED,
                last_error=None,
                last_traceback=None,
            )
            self._emit_event(study.session_id, "study_retry_queued", {
                "study_id": study_id,
                "from_round": study.current_round,
                "mode": "continue",
                "previous_status": status.value,
            })
            await self.submit(study)
            return True

        return False

    def cancel(self, study_id: str, reason: str | None = None) -> bool:
        tok = self._control_tokens.get(study_id)
        if tok is not None:
            tok.cancelled = True
            if reason:
                try:
                    self.store.update_execution_status(
                        study_id, StudyStatus.CANCELLED,
                        last_error=f"cancelled: {reason}",
                    )
                except Exception:  # noqa: BLE001 — best-effort audit trail
                    pass
            return True
        # No control token yet — the study may still be sitting in its
        # session queue (ACTION_MATRIX explicitly allows queued→cancel)
        # or waiting for a manual resume after a crash (INTERRUPTED, M11).
        # Mark it CANCELLED in the store; _run_one_study_locked re-reads
        # the row at dispatch time and silently drops terminal entries.
        study = self.store.get_study(study_id)
        if study is not None and study.execution_status in (
            StudyStatus.QUEUED,
            StudyStatus.INTERRUPTED,
        ):
            self._queued_study_ids.discard(study_id)
            self.store.update_execution_status(
                study_id, StudyStatus.CANCELLED,
                last_error=f"cancelled while {study.execution_status.value}{': ' + reason if reason else ''}",
            )
            self._emit_event(study.session_id, "study_cancelled", {
                "study_id": study_id, "session_id": study.session_id,
                "reason": f"cancelled while {study.execution_status.value}",
            })
            return True
        return False

    def archive(
        self,
        study_id: str,
        *,
        archived_by: str | None = None,
        reason: str | None = None,
    ) -> bool:
        """Soft-archive a study.

        1. If a runner is active, request cancel first so the executor
           doesn't write a row while the status flips to ARCHIVED.
        2. Persist ARCHIVED + archived_at / archived_by via the store.
        3. Emit ``study_archived`` so the UI can refresh.
        Returns True when the DB row was successfully flipped.
        """

        study = self.store.get_study(study_id)
        if study is None:
            return False
        if study.execution_status == StudyStatus.ARCHIVED:
            return False  # already archived — caller should treat as 409

        # If a runner is active, signal cancel before the status flip.
        tok = self._control_tokens.get(study_id)
        if tok is not None:
            tok.cancelled = True

        updated = self.store.archive_study(study_id, archived_by=archived_by)
        if updated is None:
            return False

        self._emit_event(study.session_id, "study_archived", {
            "study_id": study_id,
            "archived_by": archived_by,
            "reason": reason,
            "previous_status": study.execution_status.value,
        })
        return True

    def unarchive(self, study_id: str) -> bool:
        """Revert ARCHIVED -> INTERRUPTED + emit study_unarchived.

        INTERRUPTED is the natural landing state — the user must then
        ``RESUME_INTERRUPTED`` to actually re-queue the executor.
        """

        study = self.store.get_study(study_id)
        if study is None:
            return False
        if study.execution_status != StudyStatus.ARCHIVED:
            return False

        updated = self.store.unarchive_study(study_id)
        if updated is None:
            return False

        self._emit_event(study.session_id, "study_unarchived", {
            "study_id": study_id,
        })
        return True

    def retry(
        self,
        study_id: str,
        *,
        from_round: int | None = None,
        mode: str = "append",
    ) -> bool:
        """Retry a failed/limited study.

        ``mode="append"`` (default): keep all round history. The runner
        will simply increment ``current_round`` and keep appending new
        rounds. The journal will show a contiguous sequence
        (1, 2, 3, 4, ...) instead of repeating (1, 2, 3, 1, 2, 3).

        ``mode="restart"``: throw away all round history and reset
        ``current_round`` to 1 (or ``from_round`` if > 1).

        After the counter adjustment, the study is set to INTERRUPTED
        and ``resume_interrupted`` re-queues it for execution. Returns
        ``True`` on success.
        """

        study = self.store.get_study(study_id)
        if study is None:
            return False
        # Only retryable terminal states
        from .models import StudyStatus
        retryable = {
            StudyStatus.ERROR,
            StudyStatus.EARLY_STOPPED,
            StudyStatus.BUDGET_LIMITED,
            StudyStatus.NEEDS_REFRESH,
        }
        if study.execution_status not in retryable:
            return False

        # Normalize mode + from_round
        if mode not in ("append", "restart"):
            mode = "append"
        start_round = 1 if (from_round is None or from_round <= 1) else from_round

        # Adjust round counter (clear history only on restart)
        self.store.reset_round_counter(
            study_id, mode=mode, start_round=start_round,
        )
        self.store.update_execution_status(
            study_id,
            StudyStatus.INTERRUPTED,
            last_error=None,
            last_traceback=None,
        )

        self._emit_event(study.session_id, "study_retry_queued", {
            "study_id": study_id,
            "from_round": start_round,
            "mode": mode,
            "previous_status": study.execution_status.value,
        })

        # Re-queue via resume_interrupted
        # Must be called via asyncio (it's async), so schedule it
        import asyncio
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self.resume_interrupted(study_id))
        except RuntimeError:
            # No event loop — sync context (tests)
            pass
        return True

    def approve_agent_loop(self, study_id: str, decision: str) -> bool:
        """Forward an agent-loop approval to the active runner.

        Called when the frontend POSTs to
        ``/api/study/{id}/agents/approve`` after seeing
        ``agent_approval_requested`` on the SSE stream. ``decision`` is
        ``"approved"`` or ``"reject"``. Returns ``True`` when an active
        runner picked up the signal.

        The runner forwards it to ``AgentLoop.approve_loop()`` which
        unblocks ``_check_no_progress``. If no runner is waiting on
        approval, the call is a no-op and returns ``False``.
        """
        runner = self._active_executors.get(study_id)
        if runner is None:
            return False
        loop = getattr(runner, "agent_loop", None)
        if loop is None:
            return False
        loop.approve_loop(decision)
        return True

    def replace_objective(
        self,
        study_id: str,
        *,
        new_objective: str,
        expected_goal_id: str,
        replaced_by: str | None = None,
        reason: str | None = None,
    ) -> dict | None:
        """Queue a new objective; takes effect on the runner's next round.

        Steps (all-or-nothing transaction):
          1. ``StudyStore.queue_objective_replace`` — INSERT history row
             (applied_round=NULL), UPDATE studies.objective.
          2. If the study has a goal, sync ``goals.objective`` + the
             active ``goal_claims.thesis`` so the ledger matches.
          3. Emit ``study_objective_replaced`` for SSE listeners.

        Returns ``{"history_id": int, "expected_goal_id": str}`` on
        success; ``None`` when the study is missing.
        """

        study = self.store.get_study(study_id)
        if study is None:
            return None

        new_objective = new_objective.strip()
        if not new_objective:
            return None

        # Reject live-trading / execution wording (reuse goal policy).
        try:
            from ..goal.policy import reject_live_execution_objective
            reject_live_execution_objective(new_objective)
        except ValueError:
            raise

        # 1. study row + audit row
        entry = self.store.queue_objective_replace(
            study_id,
            new_objective=new_objective,
            expected_goal_id=expected_goal_id,
            replaced_by=replaced_by,
            reason=reason,
        )

        # 2. best-effort: sync goal ledger so they don't drift.
        if study.goal_id:
            try:
                from ..goal import GoalStore, StaleGoalError
                with GoalStore() as gs:
                    try:
                        gs.update_goal(
                            session_id=study.session_id,
                            goal_id=study.goal_id,
                            expected_goal_id=expected_goal_id,
                            objective=new_objective,
                        )
                    except StaleGoalError:
                        # Goal was superseded meanwhile; not fatal — the
                        # study row already carries the new objective.
                        pass
            except Exception as exc:  # noqa: BLE001
                logger.debug(
                    "replace_objective: goal sync skipped study=%s: %s",
                    study_id, exc,
                )

        # 3. event
        self._emit_event(study.session_id, "study_objective_replaced", {
            "study_id": study_id,
            "history_id": entry.id,
            "new_objective": new_objective,
            "replaced_by": replaced_by,
            "reason": reason,
            "applied_round": None,
        })
        return {
            "history_id": entry.id,
            "expected_goal_id": expected_goal_id,
        }

    async def redo(
        self,
        study_id: str,
        round_num: int,
        *,
        workspace_path: str,
    ) -> bool:
        """Discard round ``round_num`` (DB row + state + round dir) and
        re-queue the study to start again from round ``round_num - 1``.

        Phase 5 redo: the runner's round loop starts from
        ``state.last_completed_round``, so rewinding that counter (plus
        removing the round's artifacts) makes the next execution
        re-produce the round. The study must not be currently running.
        """
        from pathlib import Path

        from . import state_store as ss

        study = self.store.get_study(study_id)
        if study is None:
            return False
        if study.execution_status in _TERMINAL_STATUSES:
            return False
        if study.execution_status == StudyStatus.RUNNING:
            return False  # must pause/cancel first

        # 1. DB: drop the round row + rewind current_round
        self.store.delete_round(study_id, round_num)
        self.store.update_round_heartbeat(study_id, max(0, round_num - 1))
        self.store.update_execution_status(
            study_id, StudyStatus.INTERRUPTED,
            last_error=None,
        )

        # 2. State: rewind last_completed_round so the runner re-does
        #    round_num on next execution.
        state = ss.load(Path(workspace_path), study_id)
        state.last_completed_round = max(0, round_num - 1)
        if state.last_keep_run_dir:
            # If the discarded round was the last keep, fall back so the
            # next round does not inherit from a deleted run dir.
            state.last_keep_run_dir = None
        ss.save(Path(workspace_path), study_id, state)

        # 3. Remove the round's artifacts dir (round dir tree).
        from . import round_manifest as rm
        rd = rm.round_dir(Path(workspace_path), study_id, round_num)
        if rd.exists():
            import shutil
            shutil.rmtree(rd, ignore_errors=True)

        # 4. Re-queue (INTERRUPTED → QUEUED via submit).
        refreshed = self.store.get_study(study_id)
        if refreshed is None:
            return False
        await self.submit(refreshed)
        return True

    # ── public: introspection ─────────────────────────────────────

    def is_running(self, study_id: str) -> bool:
        return study_id in self._active_executors

    def active_studies(self) -> list[str]:
        return list(self._active_executors.keys())

    # ── startup recovery ──────────────────────────────────────────

    async def recover_on_startup(self) -> list[StudyRecord]:
        """Recover studies from a previous run.

        Policy:
        - RUNNING → INTERRUPTED (manual resume required; prevents auto-restart
          loops when uvicorn reload kills the study due to file changes)
        - PAUSED → stays PAUSED (user pause is respected)
        - QUEUED → stays QUEUED but is re-submitted to the scheduler
        - workspace_path not exists → mark as ERROR (stale test data cleanup)
        """
        from pathlib import Path

        # Memory guard: process-local state is empty at startup so there
        # is no false-positive "ghost" to filter (unlike chat attempts we
        # need no inter-process coordination).
        recoverable: list[StudyRecord] = []
        for s in self.store.list_active_studies():
            # Defense: skip studies with non-existent workspace paths
            # (e.g. pytest temp dirs that were cleaned up)
            ws_path = Path(s.workspace_path)
            if not ws_path.exists():
                logger.warning(
                    "study %s workspace not found: %s, marking as error",
                    s.study_id, s.workspace_path,
                )
                self.store.update_execution_status(
                    s.study_id, StudyStatus.ERROR,
                    last_error=f"workspace not found: {s.workspace_path}",
                )
                continue

            if s.execution_status == StudyStatus.PAUSED:
                continue  # respect user pause
            if s.execution_status == StudyStatus.RUNNING:
                # Running study was killed by restart; mark as INTERRUPTED
                # so user can manually resume. Do NOT auto-resubmit.
                self.store.update_execution_status(
                    s.study_id, StudyStatus.INTERRUPTED,
                    last_error="interrupted by server restart",
                )
                self._emit_event(s.session_id, "study_interrupted", {
                    "study_id": s.study_id,
                    "session_id": s.session_id,
                    "round": s.current_round,
                    "reason": "interrupted by server restart",
                })
                recoverable.append(s)
                continue
            if s.execution_status == StudyStatus.MONITORING:
                # v2 §15.2 recover: rebuild the monitor task, no research rounds.
                recoverable.append(s)
                await self.submit(s)
                continue
            # QUEUED: re-enqueue so the consumer picks it up
            recoverable.append(s)
            await self.submit(s)
        if recoverable:
            logger.info(
                "study scheduler recovered %d studies", len(recoverable),
            )
        return recoverable

    # ── public: shutdown ───────────────────────────────────────────

    async def shutdown(self) -> None:
        """Cancel all active executors + consumers (best-effort)."""
        self._shutdown = True
        for sid, tok in list(self._control_tokens.items()):
            tok.cancelled = True
        for _sid, task in list(self._active_tasks.items()):
            task.cancel()
        for _sid, task in list(self._dispatch_tasks.items()):
            task.cancel()
        for _sid, task in list(self._mailbox_consumers.items()):
            task.cancel()
        if self._watchdog_task is not None:
            self._watchdog_task.cancel()
            self._watchdog_task = None
        # give tasks a moment to unwind
        await asyncio.sleep(0)
        self._active_tasks.clear()
        self._dispatch_tasks.clear()
        self._mailbox_consumers.clear()
        self._queued_study_ids.clear()

    # ── internals ──────────────────────────────────────────────────

    # ── 阶段 3c：AUTOMATION mailbox 消费者 ─────────────────────────

    def _ensure_mailbox_consumer(self, session_id: str) -> None:
        """懒启动 per-session mailbox 消费者（只领 AUTOMATION 工单）。

        与 chat 的 ``gw.run_once()`` 分道：kind 过滤保证研究工单不会被聊天
        消费路径当成聊天轮次执行（stage-3c 设计 §1）。
        """
        if session_id in self._mailbox_consumers:
            return
        task = asyncio.create_task(self._mailbox_loop(session_id))
        self._mailbox_consumers[session_id] = task
        task.add_done_callback(
            lambda t: self._mailbox_consumers.pop(session_id, None)
        )

    async def _mailbox_loop(self, session_id: str) -> None:
        """从 automation:{session} mailbox 取 AUTOMATION 工单并派发执行。

        mailbox 空即退出（懒启动、不常驻空转）；下次 submit 再起。
        执行体仍是 scheduler 的 ``_run_one_study``（见设计 §2）。

        R3.2：同 session 串行域——chat turn（fence 域=物理 session id）
        在跑时不取单，工单留队；0.5s 退避等待，与 chat 消费者的跳过
        检查构成两侧互斥。
        """
        from strategy_research.core.orchestration import WorkKind

        gw = _get_gateway_singleton()
        mailbox = gw.entry.mailbox_for(f"automation:{session_id}")
        while not self._shutdown and mailbox is not None:
            fence = getattr(gw, "fence", None)
            if fence is not None and fence.active_run(session_id) is not None:
                await asyncio.sleep(0.5)
                continue
            request = mailbox.poll_due({WorkKind.AUTOMATION})
            if request is None:
                break
            study_id = request.payload.get("study_id")
            if not study_id:
                _dlog("sched", "mailbox consumer: payload missing study_id")
                continue
            self._queued_study_ids.discard(study_id)
            _dlog("sched", "mailbox consumer got study=%s", study_id)
            task = asyncio.create_task(self._run_one_study(study_id))
            self._dispatch_tasks[study_id] = task
            task.add_done_callback(
                lambda t, sid=study_id: self._dispatch_tasks.pop(sid, None)
            )
        logger.info("study mailbox consumer exit session=%s", session_id)

    def _resolve_session_user_id(self, session_id: str) -> str:
        """Resolve a session's owner user_id (fallback to the session id).

        Used to key the per-user concurrency ceiling by the real user.
        """
        try:
            from ...api.routers.web_session import _get_db
            conn = _get_db()
            row = conn.execute(
                "SELECT user_id FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
            if row and row["user_id"]:
                return row["user_id"]
        except Exception:
            pass
        return session_id

    async def _run_one_study(self, study_id: str) -> None:
        _dlog("sched", "_run_one_study start study=%s", study_id)
        # R3.2：同 session 串行域——study 执行期间占栏（fence 域=物理
        # session id，与 chat turn 同域），chat 消费者据此跳过排队；
        # 结束/崩溃即释放。同 session 的另一个 study 先到先占，本任务
        # 等待（home_session 语义：同 session 的事按序来）。
        gw = _get_gateway_singleton()
        fence = getattr(gw, "fence", None)
        study0 = self.store.get_study(study_id)
        fence_token = None
        if fence is not None and study0 is not None and study0.session_id:
            while fence.active_run(study0.session_id) is not None and not self._shutdown:
                await asyncio.sleep(0.5)
            fence_token = fence.claim(
                study0.session_id, f"study:{study_id}:r0",
            )
        # G1: per-user cap first, then the global cap. Acquiring the
        # user slot before the global one means a user at their own
        # ceiling never consumes global capacity they cannot use anyway.
        #
        # The user slot is acquired INSIDE the try so a cancellation or
        # error between the two acquires still releases it — exactly one
        # acquire per study, exactly one release in the nested finally.
        user_sem: asyncio.Semaphore | None = None
        try:
            if self._per_user_limit > 0:
                study = self.store.get_study(study_id)
                owner = study.owner_session_id if study else None
                if owner:
                    # Resolve owner session → user id so the per-user ceiling
                    # applies per real user, not per session (a user owning
                    # many sessions could otherwise bypass the cap).
                    uid = self._resolve_session_user_id(owner)
                    user_sem = self._user_semaphores.setdefault(
                        uid, asyncio.Semaphore(self._per_user_limit)
                    )
                    await user_sem.acquire()
            # Global concurrency cap: holds the slot for the whole study
            # lifetime (rounds + cooldown + review), per design §5.2.
            await self._semaphore.acquire()
            try:
                await self._run_one_study_locked(study_id)
            finally:
                self._semaphore.release()
        finally:
            if user_sem is not None:
                user_sem.release()
            if fence_token is not None:
                fence.release(fence_token)  # R3.2：崩溃/收尾都释放，不挡 chat

    async def _run_one_study_locked(self, study_id: str) -> None:
        _dlog("sched", "_run_one_study_locked study=%s", study_id)
        study = self.store.get_study(study_id)
        if study is None:
            _dlog("sched", "_run_one_study: study not found %s", study_id)
            return
        # Defense in depth: the store is authoritative. A study that went
        # terminal between enqueue and dispatch (e.g. cancelled elsewhere)
        # must not execute — a stale queue entry is silently dropped.
        if study.execution_status in _TERMINAL_STATUSES:
            _dlog("sched", "_run_one_study: drop terminal %s (%s)",
                  study_id, study.execution_status.value)
            return

        control = ControlToken()
        self._control_tokens[study_id] = control
        emitter = self._make_emitter(study)
        runner_kwargs: dict[str, Any] = {
            "control": control, "emitter": emitter,
        }
        # R4：P-3 通道。鸭子类型 runner（测试替身）可能不接受该参数——
        # 有则传，无则退回旧签名（与 _execute_tool 的兼容写法同型）。
        _artifacts = self._artifact_channel()
        if _artifacts is not None:
            try:
                import inspect

                if "artifacts" in inspect.signature(
                    AutoresearchRunner.__init__
                ).parameters:
                    runner_kwargs["artifacts"] = _artifacts
            except (TypeError, ValueError):
                pass
        executor = AutoresearchRunner(study, self.store, **runner_kwargs)
        self._active_executors[study_id] = executor
        _dlog("sched", "marking RUNNING study=%s", study_id)
        # v2 §15.2: a recovered MONITORING study keeps its status; the runner
        # skips research rounds and enters the monitor phase directly.
        if study.execution_status != StudyStatus.MONITORING:
            self.store.update_execution_status(
                study_id, StudyStatus.RUNNING,
            )
        self._emit_event(study.session_id, "study_started", {
            "study_id": study_id, "round": study.current_round,
        })

        task = asyncio.create_task(executor.run())
        self._active_tasks[study_id] = task
        _dlog("sched", "executor task created study=%s", study_id)
        try:
            await task
            _dlog("sched", "executor task finished study=%s", study_id)
        except asyncio.CancelledError:
            control.cancelled = True
        finally:
            self._active_executors.pop(study_id, None)
            self._control_tokens.pop(study_id, None)
            self._active_tasks.pop(study_id, None)

    # ── watchdog: task health + heartbeat staleness ────────────────

    def _ensure_watchdog(self) -> None:
        """Lazily start the background watchdog (one per scheduler)."""
        if self._watchdog_task is not None:
            return
        self._watchdog_task = asyncio.create_task(self._watchdog_loop())

    async def _watchdog_loop(self) -> None:
        """Periodic sweep for dead/stale studies.

        The loop body is wrapped in a broad try/except: any tick error
        (transient SQLite lock, unexpected row shape, ...) must be logged
        and swallowed so the watchdog itself never dies silently — after
        which every subsequent hang would go undetected forever.
        """
        while not self._shutdown:
            await asyncio.sleep(self._watchdog_interval)
            try:
                await self._watchdog_tick()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 — watchdog must survive ticks
                logger.exception("watchdog tick failed (continuing)")

    async def _watchdog_tick(self) -> None:
        """Sweep active tasks: cleanup dead ones, interrupt stale ones."""
        # 1. Tasks that finished without the normal cleanup path → mark
        #    INTERRUPTED so the row is not left RUNNING forever.
        for sid in list(self._active_tasks):
            task = self._active_tasks.get(sid)
            if task is None or not task.done():
                continue
            self._active_tasks.pop(sid, None)
            self._dispatch_tasks.pop(sid, None)
            self._active_executors.pop(sid, None)
            tok = self._control_tokens.pop(sid, None)
            record = self.store.get_study(sid)
            if record is not None and \
                    record.execution_status not in _TERMINAL_STATUSES:
                self.store.update_execution_status(
                    sid, StudyStatus.INTERRUPTED,
                    last_error="watchdog: task ended without cleanup",
                )
                self._emit_event(record.session_id, "study_interrupted", {
                    "study_id": sid,
                    "session_id": record.session_id,
                    "round": record.current_round,
                    "reason": "watchdog: task ended without cleanup",
                })
            elif tok is not None:
                # Terminal study with a lingering token — just drop it.
                self._control_tokens.pop(sid, None)

        # 2. Heartbeat stale → force-cancel the executor task + INTERRUPTED.
        now = datetime.now(timezone.utc)
        for sid, task in list(self._active_tasks.items()):
            record = self.store.get_study(sid)
            if record is None or not record.heartbeat:
                continue
            try:
                hb = datetime.fromisoformat(record.heartbeat)
            except ValueError:
                continue
            age = (now - hb).total_seconds()
            if age <= self._heartbeat_timeout:
                continue
            _dlog("sched", "watchdog: heartbeat stale study=%s age=%.0fs",
                  sid, age)
            from .hanging_events import record_event
            record_event(
                "watchdog_interrupt",
                study_id=sid,
                session_id=record.session_id,
                detail=f"heartbeat stale ({age:.0f}s)",
            )
            tok = self._control_tokens.get(sid)
            if tok is not None:
                tok.cancelled = True
            task.cancel()
            self.store.update_execution_status(
                sid, StudyStatus.INTERRUPTED,
                last_error=f"watchdog: heartbeat stale ({age:.0f}s)",
            )
            self._emit_event(record.session_id, "study_interrupted", {
                "study_id": sid,
                "session_id": record.session_id,
                "round": record.current_round,
                "reason": "heartbeat stale",
            })

        # 3. Background-task registry: any live task whose log stopped
        #    advancing is stuck → kill + deregister (log-progress liveness).
        from ..utils.bg_proc import (
            active_tasks,
            is_stalled,
            kill_bg,
            log_tail,
            unregister_task,
        )
        for handle in active_tasks():
            if not is_stalled(handle.log_path, self._heartbeat_timeout):
                continue
            _dlog("sched", "watchdog: bg task stalled task=%s log=%s",
                  handle.task_id, handle.log_path)
            from .hanging_events import record_event
            record_event(
                "log_stall",
                study_id=handle.owner,
                session_id=handle.owner,
                detail=f"bg task {handle.task_id} log stalled",
            )
            if handle.proc is not None:
                kill_bg(handle.proc)
            unregister_task(handle.task_id)
            self._emit_event(
                handle.owner or "system", "study_interrupted", {
                    "study_id": handle.owner or "",
                    "session_id": handle.owner or "",
                    "reason": f"bg task stalled ({handle.task_id})",
                    "tail": log_tail(handle.log_path, n=3),
                },
            )

    def _make_emitter(self, study: StudyRecord):
        """Construct an emitter bound to the study's session event_bus.

        Resolution order:
          1. explicit ``emitter_factory`` (tests / custom sinks)
          2. the session's EventBus (real service) — forwards every
             study_* event from the executor into the SSE stream so the
             WebUI Study panel updates instantly
          3. NullEmitter (no service wiring — e.g. unit tests)
        """
        if self._emitter_factory is not None:
            return self._emitter_factory(study.session_id)
        if self.session_service is not None and \
                self.session_service.event_bus is not None:
            return make_event_bus_emitter(study.session_id,
                                          self.session_service.event_bus)
        return NullEmitter()

    def _artifact_channel(self) -> Any | None:
        """R4：P-3 通道（与 chat 侧同一 Gateway 实例上的组装产物）。

        None 时 runner 保持现行为（metrics 内联进事件）。取不到 Gateway
        不报错——study 不因数据面不可用而失败。
        """
        try:
            gw = _get_gateway_singleton()
            return getattr(gw, "artifacts", None)
        except Exception:  # noqa: BLE001
            return None

    def _emit_event(self, session_id: str, event: str, data: dict) -> None:
        if self.session_service is None:
            return
        try:
            self.session_service.event_bus.emit(session_id, event, data)
        except Exception as exc:
            logger.debug("study scheduler emit %s failed: %s", event, exc)

    # ── ops: in-process status dump (A.observability) ────────────────

    def dump_concurrency(self) -> dict[str, Any]:
        """Snapshot scheduler-level concurrency state.

        Cheap to call: no DB, no event_bus access. Used by the
        ``/api/study/_internal/dump`` ops endpoint and tests.
        """
        return {
            "semaphore_limit": SR_STUDY_MAX_CONCURRENT,
            "per_user_limit": self._per_user_limit,
            "per_user_active": {
                owner: self._per_user_limit - (sem._value if sem._value is not None else 0)  # noqa: SLF001 — introspection
                for owner, sem in sorted(self._user_semaphores.items())
            },
            "queued_study_ids": sorted(self._queued_study_ids),
            "active_executor_ids": sorted(self._active_executors.keys()),
            "active_task_ids": sorted(self._active_tasks.keys()),
            "queued_count": len(self._queued_study_ids),
            "active_count": len(self._active_executors),
        }

    def dump_watchdog(self) -> dict[str, Any]:
        """Watchdog liveness + configured thresholds."""
        return {
            "alive": (self._watchdog_task is not None
                      and not self._watchdog_task.done()),
            "interval_s": self._watchdog_interval,
            "heartbeat_timeout_s": self._heartbeat_timeout,
        }

    def dump_session_queues(self) -> dict[str, dict[str, Any]]:
        """Per-session mailbox depth + consumer health.

        阶段 4：旧 asyncio.Queue 通道删除后，这里改报 mailbox 状态
        （键 = automation:{session}）。仍用于 ``/api/study/_internal/dump``。
        """
        out: dict[str, dict[str, Any]] = {}
        gw = _get_gateway_singleton()
        for session_key, mailbox in gw.entry.iter_mailboxes():
            if not session_key.startswith("automation:"):
                continue
            session_id = session_key.split(":", 1)[1]
            consumer = self._mailbox_consumers.get(session_id)
            out[session_id] = {
                "queued_depth": mailbox.peek_depth(),
                "consumer_alive": (consumer is not None
                                   and not consumer.done()),
            }
        return out


# ── emitter factory: bridge EventBusV2 ──────────────────────────────


def make_event_bus_emitter(session_id: str, event_bus: Any) -> "_EventBusEmitter":
    """Factory helper: bind EventBusV2 emission to a session id."""
    return _EventBusEmitter(session_id, event_bus)


class _EventBusEmitter:
    """Wraps EventBusV2.emit() so it matches the EventEmitter protocol."""

    def __init__(self, session_id: str, event_bus: Any) -> None:
        self.session_id = session_id
        self.event_bus = event_bus

    def emit(self, session_id: str, event: str, data: dict) -> None:
        # Use the session the listener subscribed to, not the executor's
        # session: they match in practice for Phase 1.
        self.event_bus.emit(session_id, event, data)



# ── 阶段 3a：Gateway 访问（共享唯一实例，保证 session 串行域） ──────


def _get_gateway_singleton() -> Any:
    """进程内唯一 Gateway（与 API/CLI 共享 mailbox）。"""
    from strategy_research.core.gateway import get_gateway

    return get_gateway()
