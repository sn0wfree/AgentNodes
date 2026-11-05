"""Shared helpers for api/routers/* — task exception logging, etc."""

from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)


def log_task_exception(task: asyncio.Task) -> None:
    """done-callback that surfaces background-task failures to the logger.

    Usage::

        task = asyncio.create_task(some_coroutine())
        task.add_done_callback(log_task_exception)

    Without this, exceptions raised in fire-and-forget tasks (e.g.
    scheduler.submit, workflow_runner.start) are silently swallowed
    by asyncio's default exception handler and never reach the
    application log. A5 audit-fix.
    """
    if exc := task.exception():
        logger.exception(
            "background task %r failed", task.get_coro(), exc_info=exc
        )


__all__ = ["log_task_exception"]
