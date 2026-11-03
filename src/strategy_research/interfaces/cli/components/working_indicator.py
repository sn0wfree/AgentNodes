"""ThinkingSpinner — a transient Rich ``Live`` indicator.

Mirrors ``vibe-trading/cli/components/working_indicator.py`` minus the live
threading: for test-friendliness we use ``Rich.Live(transient=True)`` so the
output is captured into the test record. The verb is picked from the shared
:func:`pick_thinking_verb` helper.

Used as a context manager::

    with ThinkingSpinner() as spinner:
        spinner.update_verb("Loading skill…")
        do_work()
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator, Optional

from rich.live import Live
from rich.spinner import Spinner
from rich.text import Text

from strategy_research.interfaces.cli.utils.thinking_verbs import pick_thinking_verb


class ThinkingSpinner:
    """Transientspinner with verb + optional extras.

    Use as a context manager. On enter, a new Rich ``Live`` is started; on
    exit it stops cleanly. ``update_verb`` swaps the verb mid-run; ``pause``
    temporarily suspends the spinner so callers can print static output.
    """

    def __init__(
        self,
        *,
        verb: Optional[str] = None,
        spinner_name: str = "dots",
        seed: Optional[int] = None,
    ) -> None:
        self._verb = verb or pick_thinking_verb(seed=seed)
        self._spinner_name = spinner_name
        self._extra: Optional[str] = None
        self._renderable: Text = self._build_renderable()
        self._live: Optional[Live] = None

    def _build_renderable(self) -> Text:
        text = Text()
        text.append(self._verb, style="primary")
        if self._extra:
            text.append("  ")
            text.append(self._extra, style="muted")
        return text

    def update_verb(self, verb: str) -> None:
        """Swap the verb mid-run."""
        self._verb = verb
        self._renderable = self._build_renderable()
        if self._live is not None:
            self._live.update(self._renderable)

    def set_extra(self, extra: Optional[str]) -> None:
        """Add or remove a right-aligned tag (e.g. token count preview)."""
        self._extra = extra
        self._renderable = self._build_renderable()
        if self._live is not None:
            self._live.update(self._renderable)

    def __enter__(self) -> "ThinkingSpinner":
        spinner = Spinner(self._spinner_name, text=self._renderable, style="primary")
        live = Live(spinner, refresh_per_second=12, transient=True)
        self._live = live
        live.__enter__()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._live is not None:
            self._live.__exit__(exc_type, exc, tb)
            self._live = None

    @contextmanager
    def pause(self) -> Iterator[None]:
        """Temporarily suspend the live render so caller can print static lines."""
        if self._live is None:
            yield
            return
        self._live.__exit__(None, None, None)
        try:
            yield
        finally:
            spinner = Spinner(self._spinner_name, text=self._build_renderable(), style="primary")
            live = Live(spinner, refresh_per_second=12, transient=True)
            self._live = live
            live.__enter__()


__all__ = ["ThinkingSpinner"]
