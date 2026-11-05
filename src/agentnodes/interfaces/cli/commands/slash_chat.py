"""``/model``, ``/clear``, ``/quit``, ``/debug``, ``/journal``, ``/shadow`` — chat-flavor slash commands.

These do not talk to an LLM — they are pure utility commands used by the
interactive REPL.

* :func:`cmd_model` — show current LLM provider/model.
* :func:`cmd_clear` — clear the screen + reset history (ctx-bound).
* :func:`cmd_quit` — return 2 as the conventional "user-requested quit" sentinel.
* :func:`cmd_debug` — toggle ``ctx.debug``; prints ON/OFF.
* :func:`cmd_journal` — placeholder until journal CSV analysis is wired.
* :func:`cmd_shadow` — placeholder until shadow account dashboard is wired.
"""

from __future__ import annotations

from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from agentnodes.interfaces.cli.theme import get_console


def _resolve_console(console: Any = None) -> Console:
    return console or get_console()


def _coming_soon(command: str, hint: str, console: Any = None) -> int:
    """Render the placeholder panel for not-yet-wired commands."""
    console = _resolve_console(console)
    body = Text()
    body.append(f"/{command} is not yet wired up to the interactive CLI.\n\n", style="dim")
    body.append("Until then: ", style="dim")
    body.append(hint, style="bold")
    console.print(Panel(body, title=f"/{command}", border_style="dim", padding=(1, 2)))
    return 0


# ─── /model ────────────────────────────────────────────────────────────


def cmd_model(ctx: Any = None, *args: str) -> int:
    """``/model`` — print provider/model + how to switch."""
    console = _resolve_console(None)
    import os

    provider = os.environ.get("LANGCHAIN_PROVIDER") or os.environ.get("MINIMAX_PROVIDER") or "(not set)"
    model = os.environ.get("LANGCHAIN_MODEL_NAME") or os.environ.get("MINIMAX_MODEL") or "(not set)"

    console.print(Text(f"Provider: {provider}", style="bold"))
    console.print(Text(f"Model:    {model}", style="bold"))
    console.print()
    console.print(Text("Run `agentnodes init` to switch provider / model / credentials.", style="dim"))
    return 0


# ─── /clear ────────────────────────────────────────────────────────────


def cmd_clear(ctx: Any = None, *args: str) -> int:
    """``/clear`` — clear the screen and the conversation history (ctx-bound)."""
    console = _resolve_console(None)
    try:
        console.clear()
    except Exception:  # noqa: BLE001
        pass
    if ctx is not None and hasattr(ctx, "history"):
        ctx.history = []
    console.print("[dim](history cleared)[/dim]")
    return 0


# ─── /quit ─────────────────────────────────────────────────────────────


def cmd_quit(ctx: Any = None, *args: str) -> int:
    """``/quit`` — return the conventional 2 sentinel so the REPL exits cleanly."""
    return 2


# ─── /debug ────────────────────────────────────────────────────────────


def cmd_debug(ctx: Any = None, *args: str) -> int:
    """``/debug`` — toggle ``ctx.debug``; prints ON/OFF."""
    if ctx is None:
        _resolve_console(None).print("[dim]/debug toggles a flag on the REPL context — no effect standalone.[/dim]")
        return 0
    new_value = not getattr(ctx, "debug", False)
    ctx.debug = new_value
    state = "ON" if new_value else "OFF"
    info = "(iter · tools · elapsed · ctx≈)" if new_value else "(one-line per turn)"
    _resolve_console(None).print(f"[bold]debug {state}[/bold] {info}")
    return 0


# ─── /journal, /shadow ────────────────────────────────────────────────


def cmd_journal(ctx: Any = None, *args: str) -> int:
    """``/journal [path]`` — place a prompt on ``ctx.pending_prompt`` if a path is given."""
    if ctx is not None and hasattr(ctx, "pending_prompt") and args:
        path = args[0]
        setattr(ctx, "pending_prompt", f"Analyze my trade journal at {path}")
        return 0
    return _coming_soon("journal", "drop a CSV into the workspace and call cmd-journal manually.")


def cmd_shadow(ctx: Any = None, *args: str) -> int:
    """``/shadow [path]`` — queue a shadow-account task."""
    if ctx is not None and hasattr(ctx, "pending_prompt") and args:
        path = args[0]
        setattr(ctx, "pending_prompt", f"Train a shadow account from my trade journal at {path}")
        return 0
    return _coming_soon("shadow", "use the autoresearch loop to study prior runs.")


# Slash router entrypoints
def run_quit(ctx: Any = None, *args: str) -> int:
    """Exit the interactive chat loop."""
    return cmd_quit()


def run_clear(ctx: Any = None, *args: str) -> int:
    """Clear the current conversation history."""
    return cmd_clear(ctx, *args)


def run_model(ctx: Any = None, *args: str) -> int:
    """Show or switch the active LLM model."""
    return cmd_model()


def run_debug(ctx: Any = None, *args: str) -> int:
    """Toggle debug output."""
    return cmd_debug(ctx, *args)


def run_journal(ctx: Any = None, *args: str) -> int:
    """Show the research journal."""
    return cmd_journal(ctx, *args)


def run_shadow(ctx: Any = None, *args: str) -> int:
    """Show shadow-mode status."""
    return cmd_shadow(ctx, *args)


__all__ = [
    "cmd_model",
    "cmd_clear",
    "cmd_quit",
    "cmd_debug",
    "cmd_journal",
    "cmd_shadow",
    "run_quit",
    "run_clear",
    "run_model",
    "run_debug",
    "run_journal",
    "run_shadow",
]
