"""Scheduled Research executor — asyncio scheduler for study dispatch.

Two run modes:
- **server mode** — ``start(loop=main_loop)`` registers an asyncio task
  on the API server's event loop; jobs dispatch to the in-process study
  system (``core/study/bootstrap`` + ``StudyScheduler``).
- **CLI mode** — ``start()`` with no loop spawns a background thread +
  private event loop (legacy behaviour, used by the CLI).

The default dispatch resolves by ``job.target``:
- ``'study'`` (default): create a study via the shared bootstrap and
  submit it to the scheduler; ``job.last_run_id`` = study_id.
- ``'autoresearch'`` (legacy): subprocess ``quantnodes-research
  autoresearch`` (defensive fallback; new jobs are all ``'study'``).

A custom ``dispatch_fn`` may be sync or async (return value, when
truthy, is stored as ``job.last_run_id``).
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import subprocess
import threading
import time
from pathlib import Path
from typing import Awaitable, Callable, Optional

from .cron_parser import next_cron_trigger
from .models import JobStatus, ScheduledResearchJob
from .store import ScheduledResearchStore

logger = logging.getLogger(__name__)

DispatchFn = Callable[[ScheduledResearchJob], Optional[Awaitable[Optional[str]]]]


class ScheduledResearchExecutor:
    """Async scheduler that runs research jobs at specified times.

    Usage (server):
        executor = ScheduledResearchExecutor(store, scheduler=study_scheduler)
        executor.start(loop=asyncio.get_running_loop())
        ...
        executor.stop()

    Usage (CLI, background thread):
        executor = ScheduledResearchExecutor(store)
        executor.start()
        ...
        executor.stop()
    """

    def __init__(
        self,
        store: ScheduledResearchStore,
        tick_interval: float = 60.0,
        dispatch_fn: DispatchFn | None = None,
        scheduler: Optional[object] = None,
    ) -> None:
        self._store = store
        self._tick_interval = tick_interval
        self._dispatch_fn = dispatch_fn or self._dispatch_by_target
        # StudyScheduler used by the default study dispatch (may be None —
        # the study is persisted as QUEUED and submitted by the caller).
        self._scheduler = scheduler
        self._running = False
        self._task: asyncio.Task | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None

    def start(self, loop: asyncio.AbstractEventLoop | None = None) -> None:
        """Start the scheduler.

        ``loop`` given → register an asyncio task on that loop (server
        mode). ``loop`` None → spawn a background thread with its own
        event loop (CLI mode).
        """
        if self._running:
            return
        self._running = True
        if loop is not None:
            self._loop = loop
            self._task = loop.create_task(self._run_loop())
            logger.info("Scheduled research executor started on main loop (tick=%ss)",
                        self._tick_interval)
            return
        self._loop = asyncio.new_event_loop()
        self._task = asyncio.ensure_future(self._run_loop(), loop=self._loop)
        self._thread = threading.Thread(
            target=self._run_loop_sync,
            daemon=True,
            name="scheduled-research-executor",
        )
        self._thread.start()
        logger.info("Scheduled research executor started (tick=%ss)", self._tick_interval)

    def stop(self) -> None:
        """Stop the scheduler (best-effort cancel)."""
        if not self._running:
            return
        self._running = False
        if self._task is not None:
            if self._loop is not None and self._thread is not None:
                self._loop.call_soon_threadsafe(self._task.cancel)
            else:
                self._task.cancel()
        logger.info("Scheduled research executor stopped")

    def run_once(self, job_id: str) -> bool:
        """Immediately run a specific job once (blocking).

        Returns True if the job was found and dispatched.
        """
        job = self._store.get(job_id)
        if job is None:
            return False
        result = self._dispatch(job)
        if inspect.isawaitable(result):
            if self._loop is not None:
                asyncio.run_coroutine_threadsafe(result, self._loop).result()
            else:
                asyncio.run(result)  # noqa: RUF006 — caller blocks anyway
        return True

    async def run_once_async(self, job_id: str) -> bool:
        """Immediately run a specific job once (async, awaits dispatch)."""
        job = self._store.get(job_id)
        if job is None:
            return False
        await self._dispatch_async(job)
        return True

    # ── internals ──────────────────────────────────────────────────

    def _run_loop_sync(self) -> None:
        """Run the event loop in a thread (CLI mode)."""
        asyncio.set_event_loop(self._loop)
        assert self._loop is not None
        self._loop.run_until_complete(self._run_loop())

    async def _run_loop(self) -> None:
        """Main scheduler loop."""
        # Recover stale running jobs
        recovered = self._store.recover_stale_running()
        if recovered:
            logger.info("Recovered %d stale running jobs", recovered)

        while self._running:
            try:
                await self._tick()
            except asyncio.CancelledError:
                break
            except Exception:  # noqa: BLE001
                logger.exception("Scheduler tick failed")
            await asyncio.sleep(self._tick_interval)

    async def _tick(self) -> None:
        """Check for due jobs and dispatch them."""
        now = time.time()
        jobs = self._store.load()

        for job in jobs:
            if job.is_due(now):
                await self._dispatch_async(job)

    async def _dispatch_async(self, job: ScheduledResearchJob) -> None:
        """Dispatch a single job (awaits async dispatch_fn)."""
        # Pre-check: skip jobs with non-existent workspace (test cleanup, stale jobs)
        if job.workspace and not Path(job.workspace).exists():
            logger.warning(
                "Job %s skipped: workspace not found (%s)",
                job.id, job.workspace,
            )
            self._schedule_next(job)
            self._store.update(job)
            return

        self._mark_running(job)
        try:
            result = self._dispatch_fn(job)
            if inspect.isawaitable(result):
                result = await result
            # Success
            if result:
                job.last_run_id = str(result)
            job.status = JobStatus.COMPLETED
            logger.info("Job %s completed successfully", job.id)
        except Exception as exc:  # noqa: BLE001
            job.status = JobStatus.FAILED
            job.config["last_error"] = str(exc)
            logger.error("Job %s failed: %s", job.id, exc)

        self._schedule_next(job)
        self._store.update(job)

    def _dispatch(self, job: ScheduledResearchJob) -> Optional[Awaitable[Optional[str]]]:
        """Sync variant of ``_dispatch_async`` — may return an awaitable."""
        # Pre-check: skip jobs with non-existent workspace
        if job.workspace and not Path(job.workspace).exists():
            logger.warning(
                "Job %s skipped: workspace not found (%s)",
                job.id, job.workspace,
            )
            self._schedule_next(job)
            self._store.update(job)
            return None

        self._mark_running(job)
        try:
            result = self._dispatch_fn(job)
            if not inspect.isawaitable(result):
                if result:
                    job.last_run_id = str(result)
                job.status = JobStatus.COMPLETED
                logger.info("Job %s completed successfully", job.id)
                self._schedule_next(job)
                self._store.update(job)
            return result
        except Exception as exc:  # noqa: BLE001
            job.status = JobStatus.FAILED
            job.config["last_error"] = str(exc)
            logger.error("Job %s failed: %s", job.id, exc)
            self._schedule_next(job)
            self._store.update(job)
            return None

    def _mark_running(self, job: ScheduledResearchJob) -> None:
        job.status = JobStatus.RUNNING
        job.last_run_at = time.time()
        self._store.update(job)

    def _schedule_next(self, job: ScheduledResearchJob) -> None:
        """Update next_run_at for recurring jobs (called before store.update)."""
        if job.status == JobStatus.CANCELLED:
            return
        if not job.is_recurring():
            return
        try:
            if job.cron:
                job.next_run_at = next_cron_trigger(job.cron)
            elif job.interval_ms > 0:
                job.next_run_at = time.time() + job.interval_ms / 1000
            job.status = JobStatus.PENDING  # Reset for next run
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to compute next_run_at for %s: %s", job.id, exc)
            job.status = JobStatus.FAILED
            job.config["last_error"] = str(exc)

    # ── default dispatch: by target ────────────────────────────────

    async def _dispatch_by_target(
        self, job: ScheduledResearchJob
    ) -> Optional[str]:
        if job.target == "autoresearch":
            self._default_dispatch_subprocess(job)
            return None
        return await self._default_dispatch_study(job)

    async def _default_dispatch_study(self, job: ScheduledResearchJob) -> str:
        """Dispatch to the in-process study system.

        Creates the study via ``core/study/bootstrap`` (objective =
        job.prompt) and submits it to the scheduler. Returns the study_id
        so it lands in ``job.last_run_id``.
        """
        from strategy_research.apps.study.bootstrap import create_study_record

        logger.info("Dispatching job %s → study (%s/%s)",
                    job.id, job.workspace, job.strategy_name)
        study = create_study_record(
            # API-created jobs carry the creator session; CLI jobs fall
            # back to a stable cli: placeholder (owner must be non-empty).
            owner_session_id=job.owner_session_id or f"cli:{job.id}",
            objective=job.prompt or f"定时研究 {job.strategy_name}",
            workspace_path=job.workspace,
            strategy_name=job.strategy_name,
            **job.study_params(),
        )
        if self._scheduler is not None:
            await self._scheduler.submit(study)
        else:
            logger.warning("job %s: no scheduler wired — study %s left queued",
                           job.id, study.study_id)
        return study.study_id

    def _default_dispatch_subprocess(self, job: ScheduledResearchJob) -> None:
        """Legacy dispatch: run autoresearch as subprocess."""
        cmd = [
            "quantnodes-research", "autoresearch",
            job.workspace,
            "--strategy", job.strategy_name,
            "--max-rounds", str(job.max_rounds),
        ]
        if job.prompt:
            cmd.extend(["--prompt", job.prompt])

        logger.info("Running: %s", " ".join(cmd))
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=3600,  # 1 hour max
            check=False,
        )

        if result.returncode != 0:
            raise RuntimeError(
                f"autoresearch failed (exit {result.returncode}): {result.stderr[:500]}"
            )

        logger.info("autoresearch completed for job %s", job.id)
