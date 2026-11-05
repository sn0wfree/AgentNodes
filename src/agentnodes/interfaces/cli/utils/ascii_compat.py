"""Unicode ↔ ASCII fallback utilities.

A handful of CLI components emit Unicode glyphs (``●``, ``×``, ``…``,
``·``, ``→``) that look correct on modern terminals but render as
mojibake on plain ASCII-only TTYs (vt100, dumb terminals, serial
consoles, ``LANG=C``, ``LC_ALL=C``).

This module exposes:

* :func:`is_ascii_mode` — runtime probe (env var or terminal encoding).
* :func:`ascii_fallback` — replace unsafe non-ASCII with ASCII lookalikes.
* :data:`STATUS_MARKERS_ASCII` / ``_UNICODE`` — paired symbol maps so
  components can pick the right glyph per mode.
* :func:`register_ascii_mode` — thread-local override (used by tests).

The intent is *visual* parity — we keep the same column width with
substitutes like ``*``, ``-``, ``...``, ``->`` rather than dropping
characters. This is the same approach :mod:`urwid` / :mod:`rich`
take when ``terminal.is_term256`` reports false.
"""
from __future__ import annotations

import os
import sys
import threading
from typing import Mapping

# Per-thread override (used by tests; falls back to env detection
# when no override is set).
_ASCII_OVERRIDE = threading.local()


# Paired marker symbols so callers can pick the right glyph per mode.
STATUS_MARKERS_UNICODE: Mapping[str, str] = {
    "running": "●",
    "ok": "●",
    "error": "×",
    "info": "○",
}

STATUS_MARKERS_ASCII: Mapping[str, str] = {
    "running": "*",
    "ok": "*",
    "error": "x",
    "info": "o",
}

ELLIPSIS_UNICODE = "…"
ELLIPSIS_ASCII = "..."

MIDDOT_UNICODE = "·"
MIDDOT_ASCII = "-"

ARROW_UNICODE = "→"
ARROW_ASCII = "->"


# ──────────────────────────────────────────────────────────────────────
# Mode probe
# ──────────────────────────────────────────────────────────────────────


def is_ascii_mode() -> bool:
    """Return True iff the current invocation should emit ASCII-only output.

    Resolution order:
    1. Per-thread override set by :func:`register_ascii_mode` (used by
       tests and explicit opt-in).
    2. ``STRATEGY_ASCII_MODE=1`` env override (any value truthy).
    3. ``LANG`` / ``LC_ALL`` / ``LANGUAGE`` env starting with ``C`` or
       ``POSIX`` (no UTF-8 by default on those locales).
    4. Probe ``sys.stdout.encoding`` — if it's ``ascii`` or starts with
       ``ANSI_X3.4`` / ``646`` we infer ASCII mode.
    5. Default: ``False`` (Unicode is assumed available).
    """
    override = getattr(_ASCII_OVERRIDE, "value", _NOT_SET)
    if override is not _NOT_SET:
        return bool(override)

    env = os.environ
    if env.get("STRATEGY_ASCII_MODE", "").strip().lower() in {
        "1", "true", "yes", "ascii", "on",
    }:
        return True
    if env.get("STRATEGY_ASCII_MODE", "").strip().lower() in {
        "0", "false", "no", "off", "unicode",
    }:
        return False

    for var in ("LANG", "LC_ALL", "LANGUAGE"):
        val = env.get(var, "")
        if val and not val.lower().startswith(("utf", "utf-", "utf8", "utf_8")):
            # ``C``, ``POSIX``, ``C.UTF-8`` is unicode; ``C`` alone is ASCII.
            base = val.split(".", 1)[0].upper()
            if base in {"C", "POSIX"} and "UTF" not in val.upper():
                return True

    try:
        encoding = (sys.stdout.encoding or "").upper()
    except Exception:  # noqa: BLE001
        encoding = ""
    if encoding in {"ASCII", "ANSI_X3.4", "ANSI_X3.4-1968", "646"}:
        return True
    if encoding.startswith("UTF"):
        return False
    return False


_NOT_SET = object()


def register_ascii_mode(value: bool | None) -> None:
    """Override :func:`is_ascii_mode` for the current thread.

    Pass True to force ASCII, False to force Unicode, None to clear
    the override and fall back to env / encoding detection.
    """
    if value is None:
        if hasattr(_ASCII_OVERRIDE, "value"):
            delattr(_ASCII_OVERRIDE, "value")
    else:
        _ASCII_OVERRIDE.value = bool(value)


# ──────────────────────────────────────────────────────────────────────
# Substitution helpers
# ──────────────────────────────────────────────────────────────────────


_UNICODE_REPLACEMENTS: Mapping[str, str] = {
    ELLIPSIS_UNICODE: ELLIPSIS_ASCII,
    MIDDOT_UNICODE: MIDDOT_ASCII,
    ARROW_UNICODE: ARROW_ASCII,
    # Status markers — these may leak through raw ``str`` paths in
    # user-supplied content (e.g. log lines containing ``●``).
    "●": "*",
    "×": "x",
    "○": "o",
}


def status_marker(status: str) -> str:
    """Pick the running/ok/error marker appropriate for the current mode.

    Unknown statuses fall back to ``?`` in either mode.
    """
    table = STATUS_MARKERS_ASCII if is_ascii_mode() else STATUS_MARKERS_UNICODE
    return table.get(status, "?")


def ellipsis() -> str:
    """Return the mode-appropriate ellipsis glyph."""
    return ELLIPSIS_ASCII if is_ascii_mode() else ELLIPSIS_UNICODE


def middot() -> str:
    """Return the mode-appropriate middle-dot separator."""
    return MIDDOT_ASCII if is_ascii_mode() else MIDDOT_UNICODE


def arrow() -> str:
    """Return the mode-appropriate right-arrow."""
    return ARROW_ASCII if is_ascii_mode() else ARROW_UNICODE


def ascii_fallback(text: str) -> str:
    """Replace any unicode glyph in ``text`` with its ASCII equivalent.

    For characters we don't have an ASCII mapping for, fall back to
    ``?``. The function preserves string width intent — every output
    character is one of: ASCII letter, ASCII punctuation, whitespace,
    or ``?`` (for unknown glyphs).
    """
    if not is_ascii_mode():
        return text
    out: list[str] = []
    for ch in text:
        if ord(ch) < 128:
            out.append(ch)
            continue
        replacement = _UNICODE_REPLACEMENTS.get(ch)
        if replacement is not None:
            out.append(replacement)
        else:
            out.append("?")
    return "".join(out)


__all__ = [
    "STATUS_MARKERS_UNICODE",
    "STATUS_MARKERS_ASCII",
    "ELLIPSIS_UNICODE",
    "ELLIPSIS_ASCII",
    "MIDDOT_UNICODE",
    "MIDDOT_ASCII",
    "ARROW_UNICODE",
    "ARROW_ASCII",
    "is_ascii_mode",
    "register_ascii_mode",
    "ascii_fallback",
    "status_marker",
    "ellipsis",
    "middot",
    "arrow",
]
