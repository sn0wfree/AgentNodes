"""Pure formatters for tokens, durations, and abbreviated numbers.

Mirrors ``vibe-trading/cli/utils/format.py`` and the
``frontend/src/lib/format.ts`` source — pure functions, no I/O.

* :func:`format_duration` — ms / s → human ``"230ms" | "1.4s" | "4m 12s" | "1h 02m"``.
* :func:`format_tokens` — int → ``"452 tokens" | "1.2k tokens" | ...``.
* :func:`abbreviate_num` — number (or currency) → abbreviated form with
  currency-aware precision.
"""

from __future__ import annotations

from typing import Optional

# ─── format_duration ─────────────────────────────────────────────────────


def format_duration(value, *, unit: str = "ms") -> str:
    """Format a duration with units chosen by magnitude.

    Args:
        value: Numeric ms or seconds; ``None``/negative ⇒ ``"—"``.
        unit: ``"ms"`` (default) or ``"s"``.
    """
    if value is None:
        return "—"
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "—"
    if v < 0 or v != v:  # negative or NaN
        return "—"

    if unit == "s":
        if v < 60:
            return f"{v:.1f}s"
        if v < 3600:
            m = int(v // 60)
            s = int(v % 60)
            return f"{m}m {s:02d}s"
        h = int(v // 3600)
        m = int((v % 3600) // 60)
        return f"{h}h {m:02d}m"

    # ms (default)
    if v < 1000:
        return f"{int(v)}ms"
    if v < 60_000:
        return f"{v / 1000:.1f}s"
    if v < 3_600_000:
        total_s = v / 1000
        m = int(total_s // 60)
        s = int(total_s % 60)
        return f"{m}m {s:02d}s"
    total_s = v / 1000
    h = int(total_s // 3600)
    m = int((total_s % 3600) // 60)
    return f"{h}h {m:02d}m"


# ─── format_tokens ───────────────────────────────────────────────────────


def format_tokens(count) -> str:
    """Format a token count with a thousands/k/M/B suffix."""
    if count is None:
        return "—"
    try:
        n = int(count)
    except (TypeError, ValueError):
        return "—"
    if n < 0:
        return "—"
    if n < 1000:
        return f"{n} tokens"
    if n < 1_000_000:
        return f"{_round1(n / 1000)}k tokens"
    if n < 1_000_000_000:
        return f"{_round1(n / 1_000_000)}M tokens"
    return f"{_round1(n / 1_000_000_000)}B tokens"


def _round1(value: float) -> str:
    """Render with 1 decimal, trimming trailing '.0'."""
    s = f"{value:.1f}"
    if s.endswith(".0"):
        s = s[:-2]
    return s


# ─── abbreviate_num ──────────────────────────────────────────────────────


def abbreviate_num(value, *, currency: Optional[str] = None) -> str:
    """Abbreviate a numeric value with optional currency prefix.

    Rules:
        * 0 ≤ |n| < 1000 ⇒ ``"452"`` (or ``"$452"`` if currency set).
        * Up to 1M ⇒ ``"12.4k"`` / ``"$12.4k"``.
        * Up to 1B ⇒ ``"3.2M"``.
        * Else ⇒ ``"1.2B"`` etc.
        * Fractional values < 1 ⇒ 3-decimal precision
          (``"0.003"`` / ``"$0.003"``).
    """
    if value is None:
        return "—"
    try:
        n = float(value)
    except (TypeError, ValueError):
        return "—"
    prefix = f"{currency}" if currency else ""
    abs_n = abs(n)

    if abs_n == 0:
        return f"{prefix}0"
    if abs_n < 1:
        return f"{prefix}{n:.3f}"
    if abs_n < 1000:
        return f"{prefix}{int(n) if n == int(n) else n:.0f}"
    if abs_n < 1_000_000:
        return f"{prefix}{_round1(n / 1000)}k"
    if abs_n < 1_000_000_000:
        return f"{prefix}{_round1(n / 1_000_000)}M"
    return f"{prefix}{_round1(n / 1_000_000_000)}B"


__all__ = ["format_duration", "format_tokens", "abbreviate_num"]
