"""Banner widget — renders the QuantNodes-Research ASCII logo at the top
of the transcript view.

Wraps :func:`strategy_research.interfaces.cli.ui.banner.print_banner`. The widget
itself is a :class:`textual.widgets.Static` so it can hold a Rich
``Text`` (returned by the existing renderer) and re-emit on demand.

Mounted inside the TranscriptView at its first row, *not* in the top
Header (the 8-line gradient logo is too tall for the 1-row header).
"""
from __future__ import annotations

from typing import Optional

from rich.console import RenderableType
from textual.widgets import Static

from strategy_research.interfaces.cli.ui.banner import render_banner

# Re-export so we have a single import path into the legacy renderer.
__all__ = ["Banner", "render_banner"]


class Banner(Static):
    """Static widget that holds the latest banner Renderable.

    Methods:
        update_model(model, version, mode): rebuild and refresh contents.
    """

    DEFAULT_CSS = """
    Banner {
        height: auto;
        padding: 0 1;
    }
    """

    def __init__(self, *, model: str = "unknown", version: str = "0.4.2", mode: str = "tui", **kwargs: object) -> None:
        super().__init__(**kwargs)
        self._model = model
        self._version = version
        self._mode = mode
        self._refresh()

    def update_model(
        self,
        *,
        model: Optional[str] = None,
        version: Optional[str] = None,
        mode: Optional[str] = None,
    ) -> None:
        if model is not None:
            self._model = model
        if version is not None:
            self._version = version
        if mode is not None:
            self._mode = mode
        self._refresh()

    def _refresh(self) -> None:
        content: RenderableType = render_banner(model=self._model, version=self._version, mode=self._mode)
        self.update(content)
