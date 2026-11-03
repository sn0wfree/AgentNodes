"""Brand bridge — maps QuantNodes-Research theme tokens to Textual primitives.

Textual widgets need CSS-friendly color tokens (hex / ANSI names) rather
than the Rich-style tokens stored on :class:`strategy_research.interfaces.cli.theme.Theme`.
This module reads :data:`Theme.brand_hex` (and friends) and exposes them as
plain hex strings that ``styles.tcss`` and direct widget ``style`` props
can consume.

Why a separate module instead of importing in CSS:
* Textual ``CSS`` strings are static; they cannot read Python attributes.
* We want ``styles.tcss`` to use ``$primary`` (the textual CSS variable
  pattern) so widgets get the active brand color without a CSS rewrite
  when the user toggles ``STRATEGY_RESEARCH_THEME=dark``.
"""
from __future__ import annotations

from dataclasses import dataclass

from strategy_research.interfaces.cli.theme import Theme, is_dark


@dataclass(frozen=True)
class _BrandTokens:
    """Plain hex strings for Textual CSS / widget style props."""

    primary: str       # brand orange (or hex prefix only)
    primary_dim: str
    success: str
    danger: str
    warning: str
    info: str
    muted: str
    surface: str       # background-friendly hex (very dim primary for dark)


def _strip_rich_modifier(style: str) -> str:
    """Strip the ``bold`` / ``reverse`` prefix from a Rich style string.

    Textual accepts only hex names like ``#d97706`` or ANSI names like
    ``red``. Anything else needs sanitizing.
    """
    style = (style or "").strip()
    for prefix in ("bold ", "italic ", "underline ", "blink "):
        if style.lower().startswith(prefix):
            style = style[len(prefix):]
            break
    return style


def brand_tokens() -> _BrandTokens:
    """Read the active theme and return plain hex tokens."""
    Theme.refresh()
    primary = _strip_rich_modifier(Theme.primary)
    primary_dim = _strip_rich_modifier(Theme.primary_dim)
    success = _strip_rich_modifier(Theme.success)
    danger = _strip_rich_modifier(Theme.danger)
    warning = _strip_rich_modifier(Theme.warning)
    info = _strip_rich_modifier(Theme.info)
    muted = _strip_rich_modifier(Theme.muted)

    dark = is_dark()
    surface = "#0f1115" if dark else "#f7f7f5"

    return _BrandTokens(
        primary=primary,
        primary_dim=primary_dim,
        success=success,
        danger=danger,
        warning=warning,
        info=info,
        muted=muted,
        surface=surface,
    )


# Module-level singleton (re-read on each call to honor env override)
def active_primary() -> str:
    """Current brand primary hex."""
    return brand_tokens().primary


def active_surface() -> str:
    """Current surface hex."""
    return brand_tokens().surface


__all__ = ["brand_tokens", "active_primary", "active_surface"]
