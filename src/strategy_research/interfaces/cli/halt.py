"""Kill-switch / halt sentinel for running operations.

Mirrors ``vibe-trading/src/live/halt.py``. Strategy-research has no live
trading, but the autoresearch / backtest loops are long-running and may
need to be interrupted cleanly. The HALT sentinel is checked by long-
running agent loops.

Public API:

* :data:`HALT` — module-level boolean sentinel.
* :func:`trip_halt` — flip ``HALT`` to ``True``.
* :func:`clear_halt` — flip ``HALT`` to ``False``.
* :func:`is_halted` — return ``HALT``.
* :func:`require_not_halted` — raise :class:`HaltError` if ``HALT``.
* :class:`HaltError` — raised when a protected operation sees a halt.
"""

from __future__ import annotations

import threading

_HALT_LOCK = threading.Lock()
HALT: bool = False


class HaltError(RuntimeError):
    """Raised when a protected operation encounters the HALT sentinel."""


def trip_halt(*, reason: str | None = None) -> None:
    """Trip the HALT sentinel. Idempotent."""
    global HALT
    with _HALT_LOCK:
        HALT = True


def clear_halt() -> None:
    """Clear the HALT sentinel. Idempotent."""
    global HALT
    with _HALT_LOCK:
        HALT = False


def is_halted() -> bool:
    """True iff HALT has been tripped."""
    with _HALT_LOCK:
        return HALT


def require_not_halted(*, operation: str = "operation") -> None:
    """Raise :class:`HaltError` if HALT has been tripped."""
    if is_halted():
        raise HaltError(f"{operation} aborted: kill switch is active")


__all__ = [
    "HALT",
    "HaltError",
    "trip_halt",
    "clear_halt",
    "is_halted",
    "require_not_halted",
]
