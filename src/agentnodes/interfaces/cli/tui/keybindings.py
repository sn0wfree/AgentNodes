"""TUI keybindings — central catalogue of ``Binding`` declarations.

Mirrors the convenience-on-F1 / Ctrl+C / Ctrl+D layout that vibe-trading
exposes in its single-line REPL, but adapted to Textual's declarative
``BINDINGS`` attribute on :class:`textual.app.App`.

Pulled out so we can reuse the same tuples across apps (e.g. a future
debug / replay app).
"""
from __future__ import annotations

from typing import Tuple

from textual.binding import Binding

TUI_BINDINGS: Tuple[Binding, ...] = (
    Binding("ctrl+c", "halt", "Halt", show=True),
    Binding("ctrl+d", "quit_app", "Quit", show=True),
    Binding("f1", "show_help", "Help", show=True),
    Binding("tab", "focus_next", "Tab", show=False),
    Binding("shift+tab", "focus_previous", "Back-Tab", show=False),
    Binding("ctrl+l", "clear_transcript", "Clear", show=True),
    Binding("ctrl+1", "toggle_sidebar", "Cmds", show=True),
    Binding("ctrl+2", "toggle_tools_rail", "Tools", show=True),
    Binding("ctrl+e", "toggle_fold", "Fold", show=True),
    Binding("ctrl+m", "toggle_mode", "Mode", show=True),
    Binding("ctrl+g", "toggle_goal_continuation", "Goal", show=True),
)


__all__ = ["TUI_BINDINGS"]
