"""Slash completer for prompt_toolkit.

Mirrors ``vibe-trading/cli/completer.py``. Activates only when the line
starts with ``/`` AND the cursor sits on the command token (no trailing
space yet). Once the user types ``/help `` the completer gets out of the way.

Public API:

* :class:`SlashCompleter` — prompt_toolkit ``Completer`` subclass.
"""

from __future__ import annotations

from typing import Iterable

from prompt_toolkit.completion import Completer, Completion

from agentnodes.interfaces.cli.commands.slash_router import match_commands


class SlashCompleter(Completer):
    """prompt_toolkit completer for slash commands."""

    def __init__(self, *, max_suggestions: int = 8) -> None:
        self._max = max_suggestions

    def get_completions(self, document, complete_event) -> Iterable[Completion]:  # noqa: ARG002
        """Yield tab-completions for slash commands.

        Completions are offered only when the line starts with ``/``
        and contains no space yet (single-token commands). Returns
        nothing for regular text.
        """
        text = document.text_before_cursor
        if not text.startswith("/"):
            return
        if " " in text.lstrip():
            return

        matches = match_commands(text, limit=self._max)
        slash_idx = text.rfind("/")
        for cmd in matches:
            yield Completion(
                cmd.name,
                start_position=-(len(text) - slash_idx - 1),
                display=cmd.name,
                display_meta=cmd.description,
            )


__all__ = ["SlashCompleter"]
