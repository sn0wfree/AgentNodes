"""``/help`` — render a table of slash commands and keyboard shortcuts."""

from __future__ import annotations

from typing import Any

from rich.console import Console
from rich.table import Table

from agentnodes.interfaces.cli.commands.slash_router import SLASH_COMMANDS
from agentnodes.interfaces.cli.theme import get_console

_SHORTCUTS: tuple[tuple[str, str], ...] = (
    ("⏎ Send", "Enter"),
    ("Shift+⏎ Newline", "Multi-line input"),
    ("Tab", "Accept completion"),
    ("↑/↓", "Browse history"),
    ("Ctrl+C", "Clear input (empty = exit)"),
    ("Ctrl+D", "Exit"),
    ("/", "Open typeahead"),
)


def _resolve_console(console: Any = None) -> Console:
    return console or get_console()


def render_help_table(*, console: Any = None) -> int:
    """Render the help tables. Returns 0 on success."""
    console = _resolve_console(console)

    table = Table.grid(padding=(0, 2))
    table.add_column(style="bold", no_wrap=True)
    table.add_column(style="")

    for cmd in SLASH_COMMANDS:
        table.add_row(f"/{cmd.name}", cmd.description)

    console.print(table)

    console.print()
    console.print("[bold]Shortcuts[/bold]")
    shortcuts = Table.grid(padding=(0, 2))
    shortcuts.add_column(style="bold", no_wrap=True)
    shortcuts.add_column(style="")
    for label, hint in _SHORTCUTS:
        shortcuts.add_row(label, hint)
    console.print(shortcuts)

    return 0


def run(ctx: Any = None, *args: str) -> int:
    """Single-entrypoint wrapper for the slash router."""
    return render_help_table()


__all__ = ["render_help_table", "run"]
