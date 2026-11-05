"""``/halt`` and ``/resume`` — kill-switch control for long-running loops.

Mirrors ``vibe-trading/cli/commands/chat.py``'s halt/resume slash commands.

* :func:`cmd_halt(reason)` — trip the global HALT sentinel.
* :func:`cmd_resume()` — clear it.
* :func:`is_halt_command(input_text)` — bare-word intercept: ``"停"``,
  ``"stop"``, ``"kill"``, ``"halt"``, ``"停手"`` → matches.
"""

from __future__ import annotations

from typing import Any


from agentnodes.interfaces.cli.halt import (
    clear_halt,
    trip_halt,
)
from agentnodes.interfaces.cli.theme import get_console

_HALT_TRIGGERS: tuple[str, ...] = (
    "停",
    "停手",
    "stop",
    "kill",
    "halt",
    "halt!",
)


def is_halt_command(input_text: str) -> bool:
    """True iff ``input_text`` is a bare-word halt trigger."""
    text = input_text.strip().lower()
    return text in {t.lower() for t in _HALT_TRIGGERS}


def is_resume_command(input_text: str) -> bool:
    """True iff ``input_text`` is a bare-word resume trigger."""
    text = input_text.strip().lower()
    return text in {"resume", "continue", "go"}


def cmd_halt(reason: str = "", *, console: Any = None) -> int:
    """``/halt`` — trip the kill switch."""
    trip_halt(reason=reason)
    get_console().print("[bold red]⚠ HALT tripped[/bold red] — long-running loops will exit.")
    return 0


def cmd_resume(*, console: Any = None) -> int:
    """``/resume`` — clear the kill switch."""
    clear_halt()
    get_console().print("[bold green]✓ HALT cleared[/bold green] — operations resumed.")
    return 0


# Slash router entrypoints
def run_halt(ctx: Any = None, *args: str) -> int:
    """Halt the running agent loop."""
    return cmd_halt(reason=" ".join(args))


def run_resume(ctx: Any = None, *args: str) -> int:
    """Resume a halted agent loop."""
    return cmd_resume()


__all__ = [
    "is_halt_command",
    "is_resume_command",
    "cmd_halt",
    "cmd_resume",
    "run_halt",
    "run_resume",
]
