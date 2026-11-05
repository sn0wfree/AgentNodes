"""StreamingText - pure-logic streaming text accumulator + fold renderer.

Provides the text accumulation and fold/expand rendering logic used by
:class:`TranscriptView`. This is NOT a widget - it has no visual
representation. The host widget (:class:`TranscriptView`) calls
``render()`` and writes the returned string to its own RichLog surface.

Implements principles 2 (fold, not discard) and 3 (in-place replacement)
from the TUI display philosophy.

Render layout (folded, when text exceeds ``_HEAD_CHARS + _TAIL_CHARS``):

    ...head content (first 80 chars)...
    [muted]… +N chars (middle)  (ctrl+e to expand)[/muted]
    ...tail content (last 120 chars)...

When expanded:

    [muted]↓ N chars (ctrl+e to fold)[/muted]
    ...full text...

Lifecycle:
    * ``render()`` returns a truncated view with head + middle + tail.
    * ``toggle_expand()`` / ``expand()`` / ``collapse()`` control state.
"""
from __future__ import annotations

_HEAD_CHARS = 80
_TAIL_CHARS = 120


class StreamingText:
    """Text accumulator with fold/expand rendering.

    ``full_text`` is always the complete accumulated content; the
    ``render()`` method controls how much is *displayed*.
    """

    def __init__(self) -> None:
        self.full_text: str = ""
        self._expanded: bool = False

    def start(self) -> None:
        self.full_text = ""
        self._expanded = False

    def update_streaming(self, text: str) -> None:
        self.full_text = text

    def append_delta(self, delta: str) -> None:
        self.full_text += delta

    def stop(self) -> None:
        self.full_text = ""
        self._expanded = False

    def toggle_expand(self) -> None:
        self._expanded = not self._expanded

    def expand(self) -> None:
        self._expanded = True

    def collapse(self) -> None:
        self._expanded = False

    @property
    def expanded(self) -> bool:
        return self._expanded

    def render(self) -> str:
        """Return the display string (folded or expanded)."""
        text = self.full_text
        if not text:
            return ""
        threshold = _HEAD_CHARS + _TAIL_CHARS
        if len(text) <= threshold:
            return text
        if self._expanded:
            return (
                f"[muted]\u2193 {len(text)} chars "
                f"(ctrl+e to fold)[/muted]\n"
                f"{text}"
            )
        hidden = len(text) - _HEAD_CHARS - _TAIL_CHARS
        return (
            f"{text[:_HEAD_CHARS]}\n"
            f"[muted]\u2026 +{hidden} chars (middle)  "
            f"(ctrl+e to expand)[/muted]\n"
            f"{text[-_TAIL_CHARS:]}"
        )


__all__ = ["StreamingText"]
