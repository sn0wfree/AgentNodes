"""ModeBar — single-row mode indicator between StatusHeader and main content.

Shows the current interactive mode (chat / goal) with a color-coded
background and a Ctrl+M toggle hint.  Always visible — the user never
has to guess which prompt is active.

Layout:
    [CHAT] 普通聊天 — 自然语言回复  Ctrl+M 切换     (green bg)
    [GOAL] 策略研究 — 结构化 JSON 输出  Ctrl+M 切换  (amber bg)
"""
from __future__ import annotations

from textual.widgets import Static


class ModeBar(Static):
    """Single-row mode indicator docked below StatusHeader."""

    DEFAULT_CSS = """
    ModeBar {
        height: 1;
        dock: top;
        padding: 0 1;
    }
    """

    _MODE_LABELS = {
        "chat": (
            "[bold white on #2d7a27] CHAT [/] "
            "[bold]普通聊天[/] — 自然语言回复  "
            "[dim]Ctrl+M 切换[/dim]"
        ),
        "goal": (
            "[bold white on #b85c00] GOAL [/] "
            "[bold]策略研究[/] — 结构化 JSON 输出  "
            "[dim]Ctrl+M 切换[/dim]"
        ),
    }

    def __init__(self, *, mode: str = "chat", **kwargs) -> None:
        super().__init__(**kwargs)
        self._mode = mode
        self._render_mode()

    def update_mode(self, mode: str) -> None:
        """Switch displayed mode and re-render."""
        self._mode = mode
        self._render_mode()

    def _render_mode(self) -> None:
        label = self._MODE_LABELS.get(self._mode, self._MODE_LABELS["chat"])
        self.update(label)


__all__ = ["ModeBar"]
