"""Tool-event renderer — pretty-print a single tool call row.

Mirrors ``vibe-trading/cli/components/tool_event.py``. Used by the activity
rail to render per-tool lines. The output is a Rich ``Text`` with a status
marker (``●``, ``○``, ``×``), pretty tool name, dim args summary, and a
duration + result summary suffix.

Public API:

* :func:`beautify_tool_name` — ``get_financials`` → ``Get Financials``.
* :func:`summarize_args` — short summary of an args dict.
* :func:`render_tool_event` — assemble a single event line.
* :func:`render_tool_events` — batch renderer.
"""

from __future__ import annotations

import re
from typing import Any, Mapping, Optional

from rich.text import Text

# Mode-aware ellipsis (Unicode "…" or ASCII "...") so non-UTF-8
# terminals don't render mojibake.
from agentnodes.interfaces.cli.utils.ascii_compat import (
    ELLIPSIS_ASCII as _ELL_ASCII,
)
from agentnodes.interfaces.cli.utils.ascii_compat import (
    ELLIPSIS_UNICODE as _ELL_UNI,
)
from agentnodes.interfaces.cli.utils.ascii_compat import (
    is_ascii_mode,
    status_marker,
)


def _ellipsis_glyph() -> str:
    return _ELL_ASCII if is_ascii_mode() else _ELL_UNI

_PREFIX_RE = re.compile(r"^(get_|run_|do_|fetch_|load_|build_|compute_|calc_|find_|list_)+")

# Tokens that should stay UPPER in the pretty name.
_ACRONYMS = frozenset({"API", "URL", "CSV", "JSON", "YAML", "ID", "HTML", "QPS"})

_STATUS_STYLE: dict[str, str] = {
    "running": "warning",
    "ok": "success",
    "error": "danger",
}

# Status markers are Unicode ●/× by default. The :func:`status_marker`
# helper picks the right glyph per current mode (Unicode vs ASCII).

_STATUS_MARKER: dict[str, str] = {
    "running": status_marker("running"),
    "ok": status_marker("ok"),
    "error": status_marker("error"),
}


def _live_status_marker(status: str) -> str:
    """Resolve the status marker glyph at call time.

    Module-level ``_STATUS_MARKER`` is computed at import time, so once
    bound it ignores later env changes. This wrapper re-resolves via
    :func:`status_marker` so callsites pick up ``STRATEGY_ASCII_MODE=1``
    toggled after import (the common tests scenario).
    """
    return status_marker(status)


# Tokens that should stay UPPER in the pretty name.

_PREFERRED_ARG_KEYS = (
    "query",
    "prompt",
    "url",
    "symbol",
    "ticker",
    "code",
    "name",
    "path",
    "file",
)


def beautify_tool_name(raw: str) -> str:
    """Convert a snake_case tool name into a Title-Cased display name.

    Strips leading ``get_`` / ``run_`` / ``fetch_`` / etc. prefixes
    (potentially multiple). Acronyms (≤4 chars, all upper) are preserved.
    """
    if not raw:
        return ""
    stripped = _PREFIX_RE.sub("", raw)
    parts = stripped.split("_")
    pretty_parts: list[str] = []
    for part in parts:
        if not part:
            continue
        if len(part) <= 4 and part.isupper():
            pretty_parts.append(part)  # acronym preserved
        else:
            pretty_parts.append(part.capitalize())
    return " ".join(pretty_parts) if pretty_parts else raw


def summarize_args(
    args: Optional[Mapping[str, Any]],
    *,
    max_len: int = 60,
    prefer_keys: tuple[str, ...] = _PREFERRED_ARG_KEYS,
) -> str:
    """Build a short human-readable summary of an args dict.

    Prefers well-known ``query/prompt/url/symbol`` etc. keys; falls back to
    the first few non-empty ``k=v`` pairs; truncates the result at
    ``max_len``.
    """
    if not args:
        return ""

    def _is_usable(v: Any) -> bool:
        return v not in (None, "")

    def _render(key: str, value: Any) -> str:
        return f"{key}={str(value)!r}"

    # Preferred keys first.
    parts: list[str] = []
    for key in prefer_keys:
        if len(parts) >= 2:
            break
        if key in args and _is_usable(args[key]):
            parts.append(_render(key, args[key]))

    # Fallback: first 2 usable items not already rendered.
    if not parts:
        seen = {parts[0].split("=", 1)[0] for p in parts} if parts else set()
        for k, v in args.items():
            if k in seen:
                continue
            if not _is_usable(v):
                continue
            parts.append(_render(k, v))
            if len(parts) >= 2:
                break

    joined = " ".join(parts) if parts else ""
    if len(joined) > max_len:
        joined = joined[: max_len - 1] + _ellipsis_glyph()
    return joined


def render_tool_event(
    name: str,
    args: Optional[Mapping[str, Any]] = None,
    *,
    status: str = "ok",
    duration_ms: Optional[float] = None,
    result_summary: Optional[str] = None,
) -> Text:
    """Build a single tool-event ``Text`` line.

    Args:
        name: The tool name (e.g. ``get_financials``).
        args: Optional args mapping.
        status: ``"running"`` / ``"ok"`` / ``"error"``.
        duration_ms: Optional elapsed duration in milliseconds.
        result_summary: Optional short text describing the result.
    """
    text = Text()
    marker = _live_status_marker(status)
    style = _STATUS_STYLE.get(status, "muted")
    text.append(f"{marker} ", style=style)
    text.append(beautify_tool_name(name), style="bold")

    args_text = summarize_args(args or {})
    if args_text:
        text.append(f" ({args_text})", style="muted")

    if duration_ms is not None:
        from agentnodes.interfaces.cli.utils.format import format_duration
        text.append(f"  {format_duration(duration_ms)}", style="muted")

    if result_summary:
        text.append(f"  · {result_summary}", style="muted")

    return text


def render_tool_events(events: list[Mapping[str, Any]]) -> list[Text]:
    """Batch version of :func:`render_tool_event`.

    Each ``events[i]`` may contain ``name``, ``args``, ``status``,
    ``duration_ms``, ``result_summary``.
    """
    return [render_tool_event(**dict(ev)) for ev in events]


__all__ = [
    "beautify_tool_name",
    "summarize_args",
    "render_tool_event",
    "render_tool_events",
]
