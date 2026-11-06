"""REPL shell — dispatch one CLI turn.

Mirrors ``vibe-trading/cli/main.py``'s role: takes the user's raw input,
detects whether it is a slash command, and routes to the right handler.

This module provides a *testable* dispatch table — :func:`process_turn`
— that consumes a parsed line and returns a structured response. The
interactive REPL loop (active ``input()`` prompt) lives in a sibling
script; the test harness drives ``process_turn`` directly.

Public API:

* :func:`process_turn(raw_input, ctx)` — single-turn driver.
* :func:`dispatch_slash(input_text, ctx)` — returns (handler, args) or raises.
* :func:`main(argv)` — top-level CLI entry (parses argv, runs one turn).
* :class:`InteractiveContext` — dataclass for per-session state (history,
  debug, pending_prompt).
"""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass, field
from typing import Any, Optional

from agentnodes.interfaces.cli.commands.help import render_help_table
from agentnodes.interfaces.cli.commands.show import cmd_pine, cmd_show, cmd_skill
from agentnodes.interfaces.cli.commands.slash_chat import (
    cmd_clear,
    cmd_debug,
    cmd_journal,
    cmd_model,
    cmd_quit,
    cmd_shadow,
)
from agentnodes.interfaces.cli.commands.slash_halt import (
    cmd_halt,
    cmd_resume,
    is_halt_command,
    is_resume_command,
)
from agentnodes.interfaces.cli.commands.slash_memory import (
    cmd_forget,
    cmd_list,
    cmd_search,
)
from agentnodes.interfaces.cli.commands.slash_memory import (
    cmd_show as memory_show,
)
from agentnodes.interfaces.cli.commands.slash_router import (
    _parse_token,
)
from agentnodes.interfaces.cli.commands.slash_session import (
    cmd_export,
    cmd_history,
)
from agentnodes.interfaces.cli.commands.slash_session import (
    cmd_search as session_search,
)
from agentnodes.interfaces.cli.halt import clear_halt, trip_halt
from agentnodes.interfaces.cli.mandate import capture_pick
from agentnodes.interfaces.cli.ui.banner import print_banner


@dataclass
class InteractiveContext:
    """Per-session interactive state."""

    session_id: str = "cli"
    history: list = field(default_factory=list)
    debug: bool = False
    pending_prompt: str = ""
    pending_proposal: Optional[dict] = None
    last_recap_history_len: int = 0
    interactive_mode: str = "chat"   # "chat" | "goal"


# ─── Slash dispatch table ────────────────────────────────────────────────


def _memory_dispatch(ctx: Any, *args: str) -> int:
    if not args:
        return cmd_list()
    sub = args[0]
    rest = args[1:]
    if sub == "search" and rest:
        return cmd_search(" ".join(rest))
    if sub == "forget" and rest:
        return cmd_forget(rest[0], yes=False)
    return memory_show(sub)


def _goal_dispatch(ctx: Any, *args: str) -> int:
    """``/goal`` — delegate to :mod:`cli.commands.slash_goal`.

    R2 (批次 3) 恢复了 goal 台账，dispatch 从「已移除」提示改回真实调用。
    ``session_id`` 取 ``ctx.session_id``（TUI/REPL 每会话一个），缺省
    回落到 slash_goal 自己的 ``"cli"`` 默认值。
    """
    from agentnodes.interfaces.cli.commands import slash_goal

    session_id = getattr(ctx, "session_id", None) or "cli"
    if not args:
        return slash_goal.cmd_status(session_id=session_id)
    sub = args[0]
    rest = list(args[1:])
    if sub == "status":
        return slash_goal.cmd_status(session_id=session_id)
    if sub == "help":
        return slash_goal.cmd_help()
    if sub == "templates":
        return slash_goal.cmd_templates()
    if sub == "workflows":
        return slash_goal.cmd_workflows(*rest)
    if sub == "checkpoint":
        return slash_goal.cmd_checkpoint(*rest, session_id=session_id)
    if sub == "start":
        template_key = ""
        workflow_name = ""
        objective_parts: list[str] = []
        i = 0
        while i < len(rest):
            if rest[i] == "--template" and i + 1 < len(rest):
                template_key = rest[i + 1]
                i += 2
            elif rest[i] == "--workflow" and i + 1 < len(rest):
                workflow_name = rest[i + 1]
                i += 2
            else:
                objective_parts.append(rest[i])
                i += 1
        return slash_goal.cmd_start(
            " ".join(objective_parts),
            session_id=session_id,
            template_key=template_key,
            workflow_name=workflow_name,
        )
    if sub == "evidence":
        if not rest:
            return slash_goal.cmd_help()
        return slash_goal.cmd_evidence(
            rest[0], " ".join(rest[1:]), session_id=session_id,
        )
    if sub == "complete":
        lite = "--lite" in rest
        rest = [r for r in rest if r != "--lite"]
        return slash_goal.cmd_complete(
            " ".join(rest), session_id=session_id, lite=lite,
        )
    if sub == "cancel":
        return slash_goal.cmd_cancel(" ".join(rest), session_id=session_id)
    return slash_goal.cmd_help()


def _search_dispatch(ctx: Any, *args: str) -> int:
    if not args:
        return cmd_export()
    return session_search(" ".join(args))


def _show_dispatch(ctx: Any, *args: str) -> int:
    if not args:
        return cmd_skill()
    return cmd_show(args[0])


def _pine_dispatch(ctx: Any, *args: str) -> int:
    if not args:
        return cmd_skill()
    return cmd_pine(args[0])


def _swarm_placeholder(ctx: Any, *args: str) -> int:
    from rich.console import Console
    from rich.panel import Panel
    Console().print(Panel("/swarm is delegated via the legacy `swarm` subcommand.", title="/swarm"))
    return 0


# Per-name dispatch table (router). Each entry maps to a callable taking
# ``(ctx, *args)`` and returning an int rc.
_DISPATCH: dict[str, Any] = {
    "help": lambda ctx, *a: render_help_table(),
    "model": lambda ctx, *a: cmd_model(),
    "memory": _memory_dispatch,
    "history": lambda ctx, *a: cmd_history(),
    "goal": _goal_dispatch,
    "search": _search_dispatch,
    "swarm": _swarm_placeholder,
    "skill": lambda ctx, *a: cmd_skill(),
    "show": _show_dispatch,
    "clear": lambda ctx, *a: cmd_clear(ctx),
    "pine": _pine_dispatch,
    "journal": lambda ctx, *a: cmd_journal(ctx, *a),
    "shadow": lambda ctx, *a: cmd_shadow(ctx, *a),
    "export": lambda ctx, *a: cmd_export(),
    "debug": lambda ctx, *a: cmd_debug(ctx),
    "quit": lambda ctx, *a: cmd_quit(),
    "halt": lambda ctx, *a: cmd_halt(" ".join(a)),
    "resume": lambda ctx, *a: cmd_resume(),
}


# ─── Public entry points ────────────────────────────────────────────────


def dispatch_slash(input_text: str, ctx: Any) -> tuple[Any, tuple]:
    """Return ``(handler, args)`` for a slash command.

    Raises ``ValueError`` if ``input_text`` is not a slash command or does
    not match any registered name.
    """
    name = _parse_token(input_text)
    handler = _DISPATCH.get(name)
    if handler is None:
        raise ValueError(f"unknown slash command: {name!r}")
    args = input_text.lstrip()[1:].split(None, 1)
    args = args[1].split() if len(args) > 1 else []
    return handler, tuple(args)


def process_turn(input_text: str, ctx: Optional[InteractiveContext] = None) -> int:
    """Single-turn dispatcher.

    Returns ``cmd_quit``'s sentinel (``2``) for ``/quit``; otherwise the
    handler's rc value. Non-slash input is appended to ``ctx.history``
    (no LLM call here — that lives in the real REPL driver).

    Bare-word kill switch intercept: ``停``/``stop``/``kill``/``halt``/
    ``停手`` → trip HALT. ``resume``/``continue``/``go`` → clear HALT.
    These never reach the LLM.

    Proposal intercept: a bare integer when ``ctx.pending_proposal`` is
    set is consumed as a numbered pick — never reaches the LLM.
    """
    ctx = ctx or InteractiveContext()
    if not input_text or not input_text.strip():
        return 0
    if is_halt_command(input_text):
        trip_halt(reason="user typed halt keyword")
        return 0
    if is_resume_command(input_text):
        clear_halt()
        return 0
    # Proposal intercept: bare integer → numbered pick.
    proposal = getattr(ctx, "pending_proposal", None)
    if proposal is not None and input_text.strip().isdigit():
        pick = capture_pick(input_text, proposal)
        if pick is not None:
            ctx.pending_proposal = None
            return 0
    if input_text.lstrip().startswith("/"):
        name = _parse_token(input_text)
        from agentnodes.interfaces.cli.commands.slash_router import _ALIASES
        target = _ALIASES.get(name, name)
        handler = _DISPATCH.get(target)
        if handler is None:
            return render_help_table()
        rest = input_text.lstrip()[1:].split(None, 1)
        args = rest[1].split() if len(rest) > 1 else []
        return handler(ctx, *args)
    ctx.history.append({"role": "user", "content": input_text})
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    """Top-level CLI entry.

    For now ``main(argv)`` only handles the ``--banner`` flag and runs a
    single dry-run ``process_turn`` on the first positional argument. The
    full interactive REPL (prompt_toolkit-driven) lives in the companion
    script and is exercised manually / by an integration test.
    """
    parser = argparse.ArgumentParser(
        prog="agentnodes-interactive",
        description="Interactive REPL driver (slim testable wrapper).",
    )
    parser.add_argument("--banner", action="store_true", help="Print the startup banner and exit")
    parser.add_argument("--model", default=os.environ.get("LANGCHAIN_MODEL_NAME", "unknown"))
    parser.add_argument("--version", default="0.4.2")
    parser.add_argument("input", nargs="?", help="Optional single-line input to dispatch")

    args = parser.parse_args(argv)

    if args.banner:
        from agentnodes.interfaces.cli.theme import get_console
        print_banner(get_console(), model=args.model, version=args.version, mode="chat")
        return 0

    ctx = InteractiveContext()
    if args.input:
        return process_turn(args.input, ctx)
    parser.print_help()
    return 0


__all__ = [
    "InteractiveContext",
    "process_turn",
    "dispatch_slash",
    "main",
]
