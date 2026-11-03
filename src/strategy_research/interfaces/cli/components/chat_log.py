"""Chat-log replay renderer.

Mirrors ``vibe-trading/cli/components/chat_log.py``. Iterates over a list of
turn dicts (``{role, content, ...}``) and prints a stylised header + body
for each. Plain-text only — markdown is left to the web bubble.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from rich.text import Text

_VALID_ROLES = {"user", "assistant", "system", "tool"}


def _looks_like_mapping(value: Any) -> bool:
    return isinstance(value, Mapping)


def render_turn(turn: Mapping[str, Any]) -> Text:
    """Render a single turn as a Rich ``Text``.

    Header line: ``Vibe`` (assistant) / ``you`` (user) / ``system`` / ``tool``.
    Body: plain text from ``turn["content"]``.
    """
    text = Text()
    role = str(turn.get("role", "user")).lower()
    if role not in _VALID_ROLES:
        role = "user"

    if role == "user":
        text.append("you", style="bold")
    elif role == "assistant":
        text.append("Vibe", style="primary")
    elif role == "tool":
        text.append("tool", style="muted")
    else:  # system
        text.append("system", style="info")

    text.append("\n")

    content = turn.get("content", "")
    if content:
        text.append(str(content), style=None)
        text.append("\n")

    return text


def render_history(history: Iterable[Mapping[str, Any]]) -> Text:
    """Render a sequence of turns as one concatenated Rich ``Text``."""
    out = Text()
    for turn in history:
        if not _looks_like_mapping(turn):
            continue
        out.append_text(render_turn(turn))
        out.append("\n")
    return out


__all__ = ["render_history", "render_turn"]
