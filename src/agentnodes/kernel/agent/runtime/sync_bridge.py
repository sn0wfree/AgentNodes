"""Sync/async bridge helpers shared by AgentLoop mixins.

W11 (2026-11-04): this module was one of **two** independent copies of the
sync↔async bridge — ``loop.py`` carried its own ``_run_coro_in_sync`` and
``_get_hook_loop``, with their own ``_HOOK_LOOP`` global and lock. The two
copies had drifted (different docstrings, different ``noqa`` comments), and
because each owns a private event loop, a process could end up with **two**
daemon hook threads while hook callbacks were scheduled onto whichever copy
the caller happened to import.

This module is now the single authority; ``loop.py`` imports from here.
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any

_HOOK_LOOP: asyncio.AbstractEventLoop | None = None
_HOOK_LOOP_LOCK = threading.Lock()


def _get_hook_loop() -> asyncio.AbstractEventLoop:
    """Return the process-wide daemon event loop used for sync→async hooks.

    Creating and tearing down an event loop per hook call dominated the
    adapter cost when hooks fired per iteration/event, so one daemon loop is
    kept for the lifetime of the process.
    """
    global _HOOK_LOOP
    with _HOOK_LOOP_LOCK:
        if _HOOK_LOOP is None or _HOOK_LOOP.is_closed():
            _HOOK_LOOP = asyncio.new_event_loop()
            threading.Thread(
                target=_HOOK_LOOP.run_forever,
                daemon=True,
                name="agent-hooks-loop",
            ).start()
        return _HOOK_LOOP


def _run_coro_in_sync(coro: Any) -> Any:
    """Run a coroutine to completion from synchronous code.

    Works both outside AND inside a running event loop (``AgentLoop.run``
    is invoked from async contexts by ``role_factory``): when a loop is
    already running, the coroutine executes on a background thread with
    its own loop and the caller blocks until it finishes.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    result: dict[str, Any] = {}

    def _runner() -> None:
        try:
            result["value"] = asyncio.run(coro)
        except BaseException as exc:  # noqa: BLE001 — re-raised below
            result["error"] = exc

    thread = threading.Thread(target=_runner, daemon=True)
    thread.start()
    thread.join()
    if "error" in result:
        raise result["error"]
    return result["value"]


__all__ = ["_get_hook_loop", "_run_coro_in_sync"]
