"""Centralized Rich style table for the strategy-research CLI.

Mirrors the brand palette and dark-mode detection logic from
``vibe-trading/cli/theme.py``. Exposes:

* :class:`Theme` — class-attribute stylesheet (colors, bold, etc.).
* :data:`_ThemeStyles` frozen dataclass — full palette bundle.
* :func:`get_console` — singleton ``Console`` shared across the CLI.
* :func:`is_dark` — detect dark terminal automatically.
* :func:`force_dark` / :func:`clear_force_dark` — env-override hooks (used by tests).

Environment variables honored:

* ``STRATEGY_RESEARCH_THEME`` — ``dark`` / ``light`` / ``auto`` (default).
* ``NO_COLOR`` — when set to a non-empty value, disables color output.
"""

from __future__ import annotations

import contextvars
import os
import threading
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from typing import Optional

from rich.console import Console
from rich.theme import Theme as _RichThemeType

# Brand palette — kept in sync with QuantNodes-Research frontend.
_BRAND_HEX = "#d97706"
_BRAND_HEX_DARK = "#fa9842"


# Per-call override for ``get_console()``. The TUI installs a recording
# console via :func:`captured_console` so handler output can be captured
# into the chat transcript without disturbing the singleton lookups in
# the legacy REPL path.
_CURRENT_CONSOLE = contextvars.ContextVar(
    "_strategy_research_captured_console", default=None
)


@dataclass(frozen=True)
class _ThemeStyles:
    """Bundle of all style strings consumed by ``Rich`` renderables."""

    primary: str
    primary_dim: str
    success: str
    danger: str
    warning: str
    info: str
    muted: str
    bold: str
    label: str
    accent_bg: str


def _is_dark_terminal() -> bool:
    """Best-effort dark-mode detection.

    Order (first match wins):

    1. ``STRATEGY_RESEARCH_THEME`` env var (``dark``/``light``/``auto``).
    2. ``COLORFGBG`` env: low ANSI background brightness ⇒ dark.
    3. ``TERM_PROGRAM=Apple_Terminal`` ⇒ dark.
    4. Otherwise: assume dark (most modern terminals).
    """
    override = os.environ.get("STRATEGY_RESEARCH_THEME", "").strip().lower()
    if override == "dark":
        return True
    if override == "light":
        return False

    colorfgbg = os.environ.get("COLORFGBG", "")
    if colorfgbg:
        try:
            bg_str = colorfgbg.rsplit(";", 1)[-1]
            bg = int(bg_str)
            # Standard ANSI: 0–7 dark, 8–15 light
            return bg < 8
        except (ValueError, IndexError):
            pass

    term = os.environ.get("TERM_PROGRAM", "")
    if term == "Apple_Terminal":
        return True

    return True


def _no_color_requested() -> bool:
    """Honor the NO_COLOR convention."""
    return bool(os.environ.get("NO_COLOR"))


def _build_styles(dark: bool, no_color: bool) -> _ThemeStyles:
    """Construct the full style bundle."""
    if no_color:
        # In NO_COLOR mode every style collapses to "none" — Rich leaves text plain.
        return _ThemeStyles(
            primary="", primary_dim="", success="", danger="",
            warning="", info="", muted="", bold="bold", label="",
            accent_bg="reverse",
        )

    brand = _BRAND_HEX_DARK if dark else _BRAND_HEX
    return _ThemeStyles(
        primary=f"bold {brand}",
        primary_dim=brand,
        success="bold green",
        danger="bold red",
        warning="bold yellow",
        info="bold cyan",
        muted="grey50",
        bold="bold",
        label=brand,
        accent_bg="reverse",
    )


_FORCE_DARK_LOCK = threading.Lock()
_FORCE_DARK: Optional[bool] = None


def force_dark(value: bool) -> None:
    """Override dark-mode detection (used by tests)."""
    global _FORCE_DARK
    with _FORCE_DARK_LOCK:
        _FORCE_DARK = bool(value)


def clear_force_dark() -> None:
    """Drop any forced dark/light override."""
    global _FORCE_DARK
    with _FORCE_DARK_LOCK:
        _FORCE_DARK = None


def is_dark() -> bool:
    """Return effective dark mode (forced override wins)."""
    with _FORCE_DARK_LOCK:
        forced = _FORCE_DARK
    if forced is not None:
        return forced
    return _is_dark_terminal()


def _current_styles() -> _ThemeStyles:
    """Build the styles bundle for the current environment."""
    return _build_styles(is_dark(), _no_color_requested())


class Theme:
    """Brand-orange Rich stylesheet.

    Class-level attributes — match ``vibe-trading/cli/theme.py``'s public API.
    """

    styles = _current_styles()
    brand_hex = _BRAND_HEX_DARK if is_dark() else _BRAND_HEX

    # Convenience class-level shortcuts
    primary = styles.primary
    primary_dim = styles.primary_dim
    success = styles.success
    danger = styles.danger
    warning = styles.warning
    info = styles.info
    muted = styles.muted
    bold = styles.bold
    label = styles.label
    accent_bg = styles.accent_bg

    @classmethod
    def refresh(cls) -> None:
        """Re-read env / override and refresh class attributes."""
        s = _current_styles()
        cls.styles = s
        cls.brand_hex = _BRAND_HEX_DARK if is_dark() else _BRAND_HEX
        cls.primary = s.primary
        cls.primary_dim = s.primary_dim
        cls.success = s.success
        cls.danger = s.danger
        cls.warning = s.warning
        cls.info = s.info
        cls.muted = s.muted
        cls.bold = s.bold
        cls.label = s.label
        cls.accent_bg = s.accent_bg


_CONSOLE_LOCK = threading.Lock()
_CONSOLE: Optional[Console] = None
_CONSOLE_FORCED: Optional[Console] = None


def get_console(*, force_terminal: bool = False) -> Console:
    """Return (and cache) the shared CLI ``Console``.

    Two separate singletons are maintained: one for normal TTY output and one
    for tests / snapshots that require ``force_terminal=True``.

    Args:
        force_terminal: Pass True to force ANSI even when stdout is not a TTY
            (useful for snapshots / regression tests).
    """
    global _CONSOLE, _CONSOLE_FORCED
    with _CONSOLE_LOCK:
        if force_terminal:
            if _CONSOLE_FORCED is None:
                _CONSOLE_FORCED = _make_console(force_terminal=True)
            return _CONSOLE_FORCED
        if _CONSOLE is None:
            _CONSOLE = _make_console(force_terminal=False)
    override = _CURRENT_CONSOLE.get()
    if override is not None:
        return override
    return _CONSOLE


def _make_console(*, force_terminal: bool) -> Console:
    """Build a Rich Console from the current theme palette."""
    rich_theme = _RichThemeType(
        {
            "primary": Theme.primary,
            "primary.dim": Theme.primary_dim,
            "success": Theme.success,
            "danger": Theme.danger,
            "warning": Theme.warning,
            "info": Theme.info,
            "muted": Theme.muted,
            "bold": Theme.bold,
            "label": Theme.label,
            "accent.bg": Theme.accent_bg,
        }
    )
    return Console(
        theme=rich_theme,
        no_color=_no_color_requested(),
        soft_wrap=False,
        highlight=False,
        emoji=False,
        markup=True,
        stderr=False,
        force_terminal=force_terminal,
    )


def captured_console(*, width: int = 120) -> AbstractContextManager[Console]:
    """Install a recording Console as the singleton override for one turn.

    Within the ``with`` block, ``get_console()`` returns a fresh Rich
    ``Console(record=True, ...)`` so handlers writing via ``console=``
    or ``_resolve_console(None)`` accumulate their output into the
    returned Console. Use ``console.export_text(...)`` / ``export(...)``
    to extract the captured Renderable.

    Example::

        with captured_console() as rec:
            handler(ctx, *args)
        renderable = Text.from_ansi(rec.export_text(clear=False))

    The Textual TUI calls this from :class:`ChatSession.dispatch` so
    that handler output written via the singleton flows into the
    ``TranscriptView`` rather than the underlying TTY.
    """
    @contextmanager
    def _ctx():
        rec = Console(
            record=True,
            force_terminal=False,
            no_color=_no_color_requested(),
            width=width,
            soft_wrap=False,
            highlight=False,
            emoji=False,
            markup=True,
            theme=_RichThemeType(
                {
                    "primary": Theme.primary,
                    "primary.dim": Theme.primary_dim,
                    "success": Theme.success,
                    "danger": Theme.danger,
                    "warning": Theme.warning,
                    "info": Theme.info,
                    "muted": Theme.muted,
                    "bold": Theme.bold,
                    "label": Theme.label,
                    "accent.bg": Theme.accent_bg,
                }
            ),
        )
        token = _CURRENT_CONSOLE.set(rec)
        try:
            yield rec
        finally:
            _CURRENT_CONSOLE.reset(token)
    return _ctx()


__all__ = [
    "Theme",
    "get_console",
    "is_dark",
    "force_dark",
    "clear_force_dark",
    "captured_console",
]
