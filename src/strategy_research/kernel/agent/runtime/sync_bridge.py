"""Sync/async bridge helpers shared by AgentLoop mixins."""

from __future__ import annotations

import asyncio
import threading
from typing import Any

_HOOK_LOOP: asyncio.AbstractEventLoop | None = None
_HOOK_LOOP_LOCK = threading.Lock()


def _get_hook_loop() -> asyncio.AbstractEventLoop:
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
    """Run a coroutine to completion from synchronous code."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    result: dict[str, Any] = {}

    def _runner() -> None:
        try:
            result["value"] = asyncio.run(coro)
        except BaseException as exc:  # noqa: BLE001
            result["error"] = exc

    thread = threading.Thread(target=_runner, daemon=True)
    thread.start()
    thread.join()
    if "error" in result:
        raise result["error"]
    return result["value"]


__all__ = ["_get_hook_loop", "_run_coro_in_sync"]
