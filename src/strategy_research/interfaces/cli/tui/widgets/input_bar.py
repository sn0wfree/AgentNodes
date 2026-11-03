"""ChatInput — bottom prompt widget.

Wraps :class:`textual.widgets.Input` with sensible defaults (single-line
submit on Enter, placeholder text). The submit handler posts a message
the parent ``ResearchApp`` consumes and dispatches via the same
``process_turn`` function the legacy REPL uses.
"""
from __future__ import annotations

from typing import Any

from textual.widgets import Input

from strategy_research.interfaces.cli.tui.messages import SynthesizeInput


class ChatInput(Input):
    """Single-line prompt bar. Submit posts ``SynthesizeInput``."""

    DEFAULT_CSS = """
    ChatInput {
        height: 3;
    }
    """

    PLACEHOLDER = "Type /help, then Enter…"

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(
            placeholder=self.PLACEHOLDER,
            **kwargs,
        )

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        """Re-emit a ``SynthesizeInput`` message; clear the buffer."""
        text = (event.value or "").strip()
        if text:
            self.post_message(SynthesizeInput(text=text))
        self.value = ""
