"""Scheduled research API router — ``/api/schedule/*``.

CRUD + run for ``ScheduledResearchJob`` rows. Jobs dispatch to the
in-process study system (``target='study'``): at trigger time a study is
created via the shared bootstrap and submitted to ``StudyScheduler``.

Ownership (IDOR): every job carries ``owner_session_id`` (the creating
chat session); mutations verify the caller owns the session.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from ._task_utils import log_task_exception

router = APIRouter()
logger = logging.getLogger(__name__)


class ScheduleCreateRequest(BaseModel):
    session_id: str
    objective: str = Field(..., description="研究目标（映射 study objective）")
    workspace_path: str
    strategy_name: str
    cron: str = ""
    interval_seconds: int = 0
    max_rounds: int = 1
    metric_targets: Optional[list[dict]] = None
    budget_token: Optional[int] = None
    budget_turn: Optional[int] = None
    budget_time_seconds: Optional[int] = None
    monitor_interval_seconds: Optional[int] = None
    guidance_md: Optional[str] = None
    behavior: Optional[str] = None


def _require_owner(request: Request, session_id: str) -> None:
    """Enforce that the caller owns the chat session (IDOR)."""
    from .web_session import _fetch_session_owned, _get_db

    user_id = getattr(request.state, "user_id", None) or "anonymous"
    _fetch_session_owned(_get_db(), session_id, user_id)


def _load_job_owned(request: Request, job_id: str, session_id: str):
    """Load a job and verify ownership; returns the job or raises 404/403."""
    from strategy_research.kernel.scheduled_research.store import ScheduledResearchStore

    with ScheduledResearchStore() as store:
        job = store.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail=f"job not found: {job_id}")
        # CLI-created jobs have no owner → not mutable via the API
        if job.owner_session_id != session_id:
            raise HTTPException(status_code=403, detail="job belongs to another session")
        return job


def _shape(job) -> dict:
    return {
        "job_id": job.id,
        "workspace": job.workspace,
        "strategy_name": job.strategy_name,
        "prompt": job.prompt,
        "target": job.target,
        "cron": job.cron,
        "interval_ms": job.interval_ms,
        "next_run_at": job.next_run_at,
        "last_run_at": job.last_run_at,
        "last_run_id": job.last_run_id,
        "status": job.status.value,
        "config": job.config,
        "max_rounds": job.max_rounds,
        "owner_session_id": job.owner_session_id,
        "created_at": job.created_at,
    }


# ── CRUD ────────────────────────────────────────────────────────────


@router.post("/create")
async def schedule_create(req: ScheduleCreateRequest, request: Request):
    """Create a scheduled job (dispatches to the study system at trigger)."""
    from strategy_research.kernel.scheduled_research.cron_parser import (
        next_cron_trigger,
        validate_cron,
    )
    from strategy_research.kernel.scheduled_research.models import ScheduledResearchJob
    from strategy_research.kernel.scheduled_research.store import ScheduledResearchStore

    _require_owner(request, req.session_id)

    from pathlib import Path
    if not Path(req.workspace_path).exists():
        raise HTTPException(
            status_code=400,
            detail=f"workspace_path does not exist: {req.workspace_path}",
        )

    if not req.cron and not req.interval_seconds:
        raise HTTPException(status_code=400, detail="must set cron or interval_seconds")
    if req.cron and not validate_cron(req.cron):
        raise HTTPException(status_code=400, detail=f"invalid cron: {req.cron}")

    if req.cron:
        next_run = next_cron_trigger(req.cron)
    else:
        import time
        next_run = time.time() + req.interval_seconds

    config = {
        "metric_targets": req.metric_targets,
        "budget_token": req.budget_token,
        "budget_turn": req.budget_turn,
        "budget_time_seconds": req.budget_time_seconds,
        "monitor_interval_seconds": req.monitor_interval_seconds,
        "guidance_md": req.guidance_md,
        "behavior": req.behavior,
    }
    config = {k: v for k, v in config.items() if v is not None}

    job = ScheduledResearchJob(
        workspace=req.workspace_path,
        strategy_name=req.strategy_name,
        prompt=req.objective,
        cron=req.cron,
        interval_ms=req.interval_seconds * 1000 if req.interval_seconds else 0,
        next_run_at=next_run,
        max_rounds=req.max_rounds or 1,
        target="study",
        owner_session_id=req.session_id,
        config=config,
    )
    with ScheduledResearchStore() as store:
        store.add(job)
    logger.info("scheduled job created id=%s owner=%s", job.id, req.session_id)
    return {"status": "ok", "job_id": job.id, "next_run_at": job.next_run_at}


@router.get("/list")
async def schedule_list(request: Request, session_id: str):
    """List the caller's scheduled jobs."""
    from strategy_research.kernel.scheduled_research.store import ScheduledResearchStore

    _require_owner(request, session_id)
    with ScheduledResearchStore() as store:
        jobs = store.list_jobs(owner_session_id=session_id)
    return {"status": "ok", "jobs": [_shape(j) for j in jobs]}


@router.get("/show/{job_id}")
async def schedule_show(request: Request, job_id: str, session_id: str):
    """Show a single job (ownership-checked)."""
    job = _load_job_owned(request, job_id, session_id)
    return {"status": "ok", "job": _shape(job)}


@router.post("/cancel/{job_id}")
async def schedule_cancel(request: Request, job_id: str, session_id: str):
    """Cancel a scheduled job (stops future triggers)."""
    from strategy_research.kernel.scheduled_research.models import JobStatus
    from strategy_research.kernel.scheduled_research.store import ScheduledResearchStore

    job = _load_job_owned(request, job_id, session_id)
    job.status = JobStatus.CANCELLED
    with ScheduledResearchStore() as store:
        store.update(job)
    return {"status": "ok", "job_id": job.id}


@router.post("/delete/{job_id}")
async def schedule_delete(request: Request, job_id: str, session_id: str):
    """Delete a scheduled job."""
    from strategy_research.kernel.scheduled_research.store import ScheduledResearchStore

    job = _load_job_owned(request, job_id, session_id)
    with ScheduledResearchStore() as store:
        store.delete(job.id)
    return {"status": "ok", "job_id": job.id}


@router.post("/run/{job_id}")
async def schedule_run(request: Request, job_id: str, session_id: str):
    """Immediately dispatch a job once (background; study runs async)."""
    from strategy_research.kernel.scheduled_research.executor import ScheduledResearchExecutor
    from strategy_research.kernel.scheduled_research.store import ScheduledResearchStore

    job = _load_job_owned(request, job_id, session_id)

    from .study import _get_study_scheduler

    sched = _get_study_scheduler()
    with ScheduledResearchStore() as store:
        executor = ScheduledResearchExecutor(store, scheduler=sched)
        task = asyncio.create_task(executor.run_once_async(job.id))
        task.add_done_callback(log_task_exception)
    return {"status": "ok", "job_id": job.id, "dispatched": True}
