"""ThinkingSpinner - inline spinner shown in TranscriptView while waiting for LLM.

Displays a brand-colored spinning indicator with elapsed time until the first
token arrives. Auto-hides when thinking_end event fires.

Verb pool: cycles through a set of action verbs every 2s to give the
user a sense of progress.
"""
from __future__ import annotations

import time
from typing import Any, List

from textual.widgets import Static

from agentnodes.interfaces.cli.tui.theme import brand_tokens

_VERB_POOL: List[str] = [
    "thinking",
    "analyzing",
    "searching",
    "reasoning",
    "writing",
]

_VERB_INTERVAL_S: float = 2.0


class ThinkingSpinner(Static):
    """Inline spinner widget for thinking state."""

    DEFAULT_CSS = """
    ThinkingSpinner {
        height: 1;
        visibility: hidden;
        color: $text-muted;
    }
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._active = False
        self._start_time: float = 0.0
        self._timer: Any = None
        self._verb_timer: Any = None
        self._verb: str = "thinking"
        self._verb_idx: int = 0

    def start(self, verb: str = "") -> None:
        """Start the spinner."""
        self._active = True
        if verb:
            self._verb = verb
        else:
            self._verb_idx = 0
            self._verb = _VERB_POOL[0]
        self._start_time = time.time()
        self.visible = True
        self._tick()
        if self._timer is None:
            self._timer = self.set_interval(0.1, self._tick)
        if self._verb_timer is None:
            self._verb_timer = self.set_interval(_VERB_INTERVAL_S, self._rotate_verb)

    def stop(self) -> None:
        """Stop the spinner."""
        self._active = False
        self.visible = False
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        if self._verb_timer is not None:
            self._verb_timer.cancel()
            self._verb_timer = None

    def _rotate_verb(self) -> None:
        """Cycle to the next verb in the pool."""
        self._verb_idx = (self._verb_idx + 1) % len(_VERB_POOL)
        self._verb = _VERB_POOL[self._verb_idx]

    def _tick(self) -> None:
        """Update spinner display."""
        if not self._active:
            return
        elapsed = time.time() - self._start_time
        tokens = brand_tokens()
        self.update(
            f"[{tokens.primary}]{tokens.primary}[/] "
            f"[{tokens.muted}]{self._verb}... {elapsed:.1f}s[/{tokens.muted}]"
        )


__all__ = ["ThinkingSpinner"]
