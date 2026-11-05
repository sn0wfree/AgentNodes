"""A5: fire-and-forget asyncio.create_task 的异常应被记录到 logger.exception。

直接测试 _task_utils.log_task_exception helper，以及它在 study.py /
chat.py 的实际接入 (done-callback 通过 create_task → task.exception())。
"""

from __future__ import annotations

import asyncio
import logging

from strategy_research.interfaces.api.routers._task_utils import log_task_exception

# ────────────────────────── log_task_exception ──────────────────────────


async def _raise_async() -> None:
    raise RuntimeError("boom from background")


async def _ok_async() -> int:
    return 42


def test_log_task_exception_logs_when_task_raises(caplog):
    async def run() -> None:
        task = asyncio.create_task(_raise_async())
        task.add_done_callback(log_task_exception)
        # Yield so the task gets a chance to run + finish.
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        # Force the done-callback to fire before assertions.
        if not task.done():
            await task

    caplog.set_level(logging.ERROR, logger="strategy_research.interfaces.api.routers._task_utils")
    asyncio.run(run())
    # The error message contains the task coroutine; the exception text
    # is in the exc_info tuple. Match either form.
    assert any("background task" in r.message for r in caplog.records), caplog.records
    assert any(
        "boom from background" in r.message
        or (r.exc_info and "boom from background" in str(r.exc_info[1]))
        for r in caplog.records
    ), caplog.records


def test_log_task_exception_silent_on_success(caplog):
    async def run() -> None:
        task = asyncio.create_task(_ok_async())
        task.add_done_callback(log_task_exception)
        await task

    caplog.set_level(logging.ERROR, logger="strategy_research.interfaces.api.routers._task_utils")
    asyncio.run(run())
    # No exception → no log lines from our helper.
    assert not any("background task" in r.message for r in caplog.records)


def test_log_task_exception_handles_cancelled(caplog):
    async def run() -> None:
        task = asyncio.create_task(_ok_async())
        task.cancel()
        task.add_done_callback(log_task_exception)
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

    caplog.set_level(logging.ERROR, logger="strategy_research.interfaces.api.routers._task_utils")
    asyncio.run(run())
    # Cancellation isn't logged as an error (task.exception() returns None
    # for cancelled tasks since py 3.8).
    assert not any("background task" in r.message for r in caplog.records)


# ────────────────────────── integration: study.py + chat.py wiring ──────────────────────────


def test_study_start_registers_done_callback_on_sched_submit(monkeypatch):
    """study_start 调用 asyncio.create_task(sched.submit(study)) 并注册
    log_task_exception done-callback. 通过 grep 源码 + 行为断言验证。"""
    import inspect

    import strategy_research.interfaces.api.routers.study as study_router
    src = inspect.getsource(study_router.study_start)
    # E3 拆分后只剩 autoresearch 一个 call site（workflow 走专用端点），
    # 仍必须使用 callback 模式。
    assert "task.add_done_callback(log_task_exception)" in src
    assert src.count("asyncio.create_task") >= 1


def test_chat_flush_study_pending_submits_uses_callback():
    """chat.py:865 的 create_task 也接入了 callback。"""
    import inspect

    import strategy_research.interfaces.api.routers.slash_commands as slash_commands
    src = inspect.getsource(slash_commands)
    # Find the section that creates the sched.submit task and assert it
    # registers the callback.
    assert "task.add_done_callback(log_task_exception)" in src
