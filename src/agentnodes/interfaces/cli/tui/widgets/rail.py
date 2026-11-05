"""ActivityRail — right-side event ticker for tool calls / results.

Wraps :class:`textual.widgets.Log`. Events are posted via the
``WriteRail`` message and dispatched to ``write_event()`` which formats
the row using :mod:`agentnodes.interfaces.cli.components.tool_event`.
"""
from __future__ import annotations

from typing import Any, Optional

from textual.widgets import Log

from agentnodes.interfaces.cli.components.tool_event import (
    beautify_tool_name,
    render_tool_event,
)
from agentnodes.interfaces.cli.tui.messages import WriteRail


class ActivityRail(Log):
    """Right-panel ticker for tool events and progress notifications."""

    DEFAULT_CSS = """
    ActivityRail {
        height: 1fr;
    }
    """

    BORDER_TITLE = "Activity"

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(auto_scroll=True, **kwargs)

    def write_line(self, content: str) -> None:
        """Append a single pre-formatted line."""
        super().write_line(content)

    def write_event(self, event: Any, *, duration_ms: Optional[int] = None) -> None:
        """Format a tool / progress event and append.

        ``event`` may be:
        - a dict with ``tool``, ``args`` (optional), ``status`` (optional),
          ``preview`` (optional), ``phase`` ("call" / "result" / etc.).
        - a plain string (treated as a system note).
        """
        if isinstance(event, str):
            self.write_line(event)
            return

        tool = event.get("tool", "?") if isinstance(event, dict) else "?"
        args = event.get("args") if isinstance(event, dict) else None
        status = event.get("status", "ok") if isinstance(event, dict) else "ok"
        preview = event.get("preview", "") if isinstance(event, dict) else ""

        pretty = beautify_tool_name(tool)
        phase = event.get("phase", "call") if isinstance(event, dict) else "call"

        line = render_tool_event(
            pretty,
            args,
            status=status,
            duration_ms=duration_ms,
            result_summary=preview,
        )
        prefix = {
            "call": "↪",
            "result": "✔" if status == "ok" else "✘",
            "progress": "…",
            "system": "·",
        }.get(phase, "·")
        self.write_line(f"{prefix} {line} [{phase}]")

    def on_write_rail(self, message: WriteRail) -> None:
        """Textual message handler for ``WriteRail`` events."""
        self.write_event(message.event)
