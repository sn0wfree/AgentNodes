"""``/memory`` slash-command shims.

Wraps the existing :class:`PersistentMemory` (in ``core.memory.persistent``)
to provide a CRUD interface through the slash router.

Sub-commands:

* ``/memory`` (no args) — list all entries.
* ``/memory <name>`` — show one entry.
* ``/memory search <q>`` — search.
* ``/memory forget <name>`` — delete an entry.
"""

from __future__ import annotations

import os
from typing import Any, Optional

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from strategy_research.cli.theme import get_console


def _resolve_console(console: Any = None) -> Console:
    return console or get_console()


def _memory_dir() -> Optional[str]:
    """Return the memory directory from env, or ``None``."""
    return os.environ.get("STRATEGY_RESEARCH_MEMORY_DIR") or None


def _store():
    from strategy_research.kernel.memory.persistent import PersistentMemory

    md = _memory_dir()
    if md:
        from pathlib import Path
        return PersistentMemory(memory_dir=Path(md))
    return PersistentMemory()


def cmd_list(*, console: Any = None, limit: int = 50) -> int:
    """``/memory`` (no args) — list memory entries."""
    console = _resolve_console(console)
    try:
        store = _store()
        entries = store.list_entries()[:limit]
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]/memory list failed:[/red] {exc}")
        return 1

    if not entries:
        console.print("[yellow]No memory entries.[/yellow]")
        return 0

    table = Table(show_header=True, header_style="bold")
    table.add_column("Name")
    table.add_column("Type")
    table.add_column("Updated")
    for entry in entries:
        name = getattr(entry, "title", "")
        mtype = getattr(entry, "memory_type", "")
        modified = getattr(entry, "modified_at", 0)
        updated = ""
        if isinstance(modified, (int, float)) and modified > 0:
            import datetime
            try:
                updated = datetime.datetime.fromtimestamp(modified).isoformat(timespec="seconds")
            except (OSError, ValueError, OverflowError):
                updated = str(modified)
        table.add_row(name[:40], mtype, updated[:19])
    console.print(table)
    return 0


def cmd_show(name: str, *, console: Any = None) -> int:
    """``/memory <name>`` — show one entry."""
    console = _resolve_console(console)
    if not name:
        console.print("[red]Usage:[/red] /memory <name>")
        return 1
    try:
        store = _store()
        entry = store.find(name)
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]/memory show failed:[/red] {exc}")
        return 1

    if entry is None:
        console.print(f"[yellow]No memory entry named '{name}'.[/yellow]")
        return 0

    title = str(getattr(entry, "title", name))
    body = str(getattr(entry, "body", ""))
    console.print(Panel(body, title=title, border_style="blue"))
    return 0


def cmd_search(query: str, *, console: Any = None) -> int:
    """``/memory search <q>`` — find relevant entries."""
    console = _resolve_console(console)
    if not query.strip():
        console.print("[red]Usage:[/red] /memory search <q>")
        return 1
    try:
        store = _store()
        results = store.find_relevant(query)
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]/memory search failed:[/red] {exc}")
        return 1

    if not results:
        console.print(f"[yellow]No matches for '{query}'.[/yellow]")
        return 0

    for entry in results:
        console.print(f"• [bold]{getattr(entry, 'title', '')}[/bold]")
        snippet = str(getattr(entry, "body", ""))[:160]
        console.print(f"  {snippet}")
    return 0


def cmd_forget(name: str, *, console: Any = None, yes: bool = False) -> int:
    """``/memory forget <name>`` — delete an entry."""
    console = _resolve_console(console)
    if not name:
        console.print("[red]Usage:[/red] /memory forget <name>")
        return 1
    if not yes:
        console.print(f"[yellow]Pass yes=True to confirm forgetting '{name}'.[/yellow]")
        return 1
    try:
        store = _store()
        ok = store.remove(name)
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]/memory forget failed:[/red] {exc}")
        return 1

    if ok:
        console.print(f"[green]Forgot '{name}'.[/green]")
        return 0
    console.print(f"[yellow]No memory entry named '{name}'.[/yellow]")
    return 0


# Slash router entrypoint — dispatches subcommands by arg shape.
def run(ctx: Any = None, *args: str) -> int:
    """Slash-router dispatcher for ``/memory``.

    * No args → :func:`cmd_list`.
    * ``search <q>`` → :func:`cmd_search`.
    * ``forget <name>`` → :func:`cmd_forget`.
    * Otherwise → :func:`cmd_show`.
    """
    if not args:
        return cmd_list()
    first = args[0]
    rest = list(args[1:])
    if first == "search" and rest:
        return cmd_search(" ".join(rest))
    if first == "forget" and rest:
        return cmd_forget(rest[0])
    return cmd_show(first)


__all__ = [
    "cmd_list",
    "cmd_show",
    "cmd_search",
    "cmd_forget",
    "run",
]
