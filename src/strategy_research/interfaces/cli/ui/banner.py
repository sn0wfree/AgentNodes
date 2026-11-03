"""Startup banner with ASCII logo and version line.

Mirrors ``vibe-trading/cli/ui/banner.py`` (8-line logo with per-character
gradient from `#258BFF` to `#A5CFFF`). Output is centred on the terminal.
"""

from __future__ import annotations

import shutil
from typing import Optional

from rich.console import Console
from rich.text import Text

# ASCII art for "QuantNodes-Research".
# Minimalist 4-line design with version/mode/model on line 4.
# Designed to fit an 80-col terminal; truncation logic in ``print_banner``
# keeps it usable on narrower consoles.
_LOGO_LINES = [
    "QuantNodes-Research",
    "===================",
    "策略研究 · Quant · Nodes",
    "",
]

def _gradient_color(step: float) -> str:
    """Lerp between #258BFF (0.0) and #A5CFFF (1.0)."""
    def _hex(comp: int) -> str:
        return f"{comp:02x}"

    def _lerp(a: int, b: int) -> int:
        return round(a + (b - a) * step)

    r = _lerp(0x25, 0xA5)
    g = _lerp(0x8B, 0xCF)
    b = _lerp(0xFF, 0xFF)
    return f"#{_hex(r)}{_hex(g)}{_hex(b)}"


def _build_logo(width: int) -> Text:
    """Build the gradient logo, optionally truncated to fit the terminal."""
    usable = max(0, width - 2)  # leave 1 column padding each side
    text = Text()
    for line in _LOGO_LINES:
        truncated = line[:usable] if len(line) > usable else line
        for idx, ch in enumerate(truncated):
            if ch.strip():
                step = idx / max(1, len(truncated) - 1) if truncated else 0
                color = _gradient_color(step)
                text.append(ch, style=color)
            else:
                text.append(ch)
        text.append("\n")
    return text


def render_banner(
    *,
    model: str = "unknown",
    version: str = "0.4.2",
    mode: str = "cli",
    width: Optional[int] = None,
    theme: Optional[object] = None,
) -> Text:
    """Build the centered logo + version/header line as a Rich ``Text``.

    Pure renderer — does not touch a ``Console``. Use :func:`print_banner`
    for the legacy REPL path, or feed the result directly to a Textual
    ``Static`` / ``RichLog`` for the TUI path.

    ``theme`` is an optional ``Theme`` instance (or any object exposing
    ``primary``, ``muted``, ``primary_dim`` attrs). When ``None`` (default),
    plain hex colors are used so the result is portable across Rich
    consoles *and* Textual's internal Rich console (which does NOT have
    our brand theme registered).
    """
    if width is None:
        try:
            width = max(40, shutil.get_terminal_size().columns)
        except (OSError, ValueError):
            width = 80

    body = _build_logo(width)

    if theme is None:
        primary_hex = "#d97706"
        primary_dim_hex = "#fa9842"
        muted_hex = "#888888"
        primary_style = f"bold {primary_hex}"
        primary_dim_style = primary_dim_hex
        muted_style = muted_hex
    else:
        # Legacy path uses Rich theme names (when console has our theme).
        primary_style = getattr(theme, "primary", "bold #d97706")
        primary_dim_style = getattr(theme, "primary_dim", "#fa9842")
        muted_style = getattr(theme, "muted", "#888888")

    header = Text()
    header.append("QuantNodes-Research", style=primary_style)
    header.append(f"  v{version}  ·  {mode}  ·  ", style=muted_style)
    header.append(model, style=primary_dim_style)
    body.append_text(header)
    return body


def print_banner(
    console: Console,
    *,
    model: str = "unknown",
    version: str = "0.4.2",
    mode: str = "cli",
    width: Optional[int] = None,
) -> None:
    """Print the centered logo + version/header line."""
    # Pass our theme so the legacy console gets the brand styling it knows.
    from strategy_research.interfaces.cli.theme import Theme
    console.print(
        render_banner(
            model=model, version=version, mode=mode, width=width, theme=Theme
        ),
        justify="center",
    )


__all__ = ["render_banner", "print_banner"]


