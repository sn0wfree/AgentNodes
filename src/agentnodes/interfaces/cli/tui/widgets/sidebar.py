"""CommandSidebar — left-panel command list (clickable).

Builds a single :class:`textual.widgets.ListView` row per registered
slash command. Click a row to dispatch the command into the input bar
(and submit). Search/filter lives outside the scope of this widget in v1.

Source of truth for the command list:
:data:`agentnodes.interfaces.cli.commands.slash_router.SLASH_COMMANDS`.
"""
from __future__ import annotations

from typing import Any, List, Optional

from textual.widgets import Label, ListItem, ListView

from agentnodes.interfaces.cli.commands.slash_router import (
    SLASH_COMMANDS,
    Command,
)
from agentnodes.interfaces.cli.tui.messages import SynthesizeInput


def _render_cmd_label(cmd: Command) -> Label:
    """Render ``[bold]/<name>[/bold]  [dim]<desc>[/dim]``."""
    return Label(f"[bold]/{cmd.name}[/bold]  [muted]{cmd.description}[/muted]")


class CommandSidebar(ListView):
    """Left-panel list of slash commands. Click to dispatch.

    Click handler posts a ``SynthesizeInput(text=f"/{name}")`` message;
    the parent app forwards it into the ChatInput widget which auto-submits.
    """

    DEFAULT_CSS = """
    CommandSidebar {
        height: 1fr;
    }

    CommandSidebar > ListItem {
        height: 1;
        padding: 0 1;
    }
    """

    BORDER_TITLE = "Commands"

    def __init__(self, commands: Optional[List[Command]] = None, **kwargs: Any) -> None:
        self._cmds = commands if commands is not None else list(SLASH_COMMANDS)
        # Pre-build children so the list is populated at mount time.
        items = [
            ListItem(_render_cmd_label(cmd), id=f"cmd-{cmd.name}", classes="sidebar-item")
            for cmd in self._cmds
        ]
        super().__init__(*items, **kwargs)

    @property
    def commands(self) -> List[Command]:
        return list(self._cmds)

    async def on_list_view_selected(self, event: ListView.Selected) -> None:
        """Translate a click into a synthetic slash command."""
        item = event.item
        item_id = getattr(item, "id", None) or ""
        prefix = "cmd-"
        if not item_id.startswith(prefix):
            return
        name = item_id[len(prefix):]
        # Post a message; the parent app will route to ChatInput.
        self.post_message(SynthesizeInput(text=f"/{name}"))
