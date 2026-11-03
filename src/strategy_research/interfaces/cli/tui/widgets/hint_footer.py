"""HintFooter — bottom row that surfaces the live keybinding hints.

Wraps :class:`textual.widgets.Footer`. The footer automatically shows
every :class:`textual.binding.Binding` declared with ``show=True`` on
the parent app, so the user always sees the live shortcut hints without
us having to maintain a separate string.

We do not override the rendering — the parent ``ResearchApp``'s
``TUI_BINDINGS`` already declares ``Halt / Quit / Help / Clear``, which
the footer surfaces. This widget exists so that the app can render the
footer in a different position (overlay) or with extra message lines if
needed in the future.
"""
from __future__ import annotations

from textual.widgets import Footer

from strategy_research.interfaces.cli.components.hint_bar import render_hint_bar
from strategy_research.interfaces.cli.tui.theme import brand_tokens


class HintFooter(Footer):
    """Standard Textual Footer with brand-aware keys.

    The footer auto-renders the :attr:`BINDINGS` from the parent app.
    """

    DEFAULT_CSS = """
    HintFooter {
        background: $primary 15%;
        color: $text;
    }
    HintFooter > .footer--key {
        color: $primary;
        background: $primary 25%;
    }
    HintFooter > .footer--description {
        color: $text;
    }
    """


def render_hint_strip(width: int = 80) -> str:
    """Return a one-line, Rich-markup hint string for non-TUI callers.

    Mirrors :func:`strategy_research.interfaces.cli.components.hint_bar.render_hint_bar`
    but with the brand-aware Textual tokens for TUI callers that want a
    plain ``Text`` they can paste into a RichLog line.
    """
    tokens = brand_tokens()
    return render_hint_bar(
        left=f"[{tokens.primary}]f1[/] help · [{tokens.primary}]ctrl+c[/] halt · [{tokens.primary}]ctrl+d[/] quit",
        right="[muted]vibe-trading parity · QuantNodes-Research v0.4.2[/muted]",
        width=width,
    )


__all__ = ["HintFooter", "render_hint_strip"]
