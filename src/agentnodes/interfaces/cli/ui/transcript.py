"""Transcript rendering — markdown-ish answer + recap + status line.

Mirrors ``vibe-trading/cli/ui/transcript.py``. Provides:

* :func:`render_answer` — markdown content with pipe-table upgrade.
* :func:`render_recap` — one-line history recap.
* :func:`render_elapsed_status` — ``✻ Analyzed for Ns/m/h``.
* :func:`render_prompt_footer` — ``─`` rule under the prompt.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Optional

from rich.console import RenderResult
from rich.table import Table
from rich.text import Text

from agentnodes.interfaces.cli.utils.format import format_duration

_BOLD_RE = re.compile(r"\*\*(.+?)\*\*|__(.+?)__")
_ITALIC_RE = re.compile(r"(?<!\*)\*(?!\*)([^*]+)\*(?!\*)|(?<!_)_(?!_)([^_]+)_(?!_)")
_INLINE_CODE_RE = re.compile(r"`([^`]+)`")
_HRULE_RE = re.compile(r"^(?:---|\*\*\*|___)$")

# Pipe-table: | col | col |, sep like |---|---|
_TABLE_ROW_RE = re.compile(r"^\s*\|.*\|\s*$")
_TABLE_SEP_RE = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)+\|?\s*$")


def _strip_inline(value: str) -> str:
    """Reduce inline markdown to plain text — bold, italic, code → no markup."""
    value = _INLINE_CODE_RE.sub(r"\1", value)
    value = _BOLD_RE.sub(r"\1\2", value)
    value = _ITALIC_RE.sub(r"\1\2", value)
    return value


def _detect_pipe_table(lines: list[str]) -> Optional[tuple[int, int]]:
    """Return (start, end_exclusive) of a pipe-table block, or ``None``."""
    n = len(lines)
    for i in range(n - 1):
        row1 = lines[i]
        row2 = lines[i + 1]
        if _TABLE_ROW_RE.match(row1) and _TABLE_SEP_RE.match(row2):
            # Collect data rows
            j = i + 2
            while j < n and _TABLE_ROW_RE.match(lines[j]):
                j += 1
            return (i, j)
    return None


def _parse_row(line: str) -> list[str]:
    """Split a ``| a | b | c |`` row into columns."""
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [_strip_inline(cell.strip()) for cell in s.split("|")]


def _render_table(lines: list[str], start: int, end: int) -> Table:
    header = _parse_row(lines[start])
    rows = [_parse_row(lines[i]) for i in range(start + 2, end)]
    table = Table(show_header=True, show_lines=True, box=None)
    for col in header:
        table.add_column(col, header_style="bold")
    for row in rows:
        # Pad row cells to header length
        while len(row) < len(header):
            row.append("")
        table.add_row(*row[: len(header)])
    return table


def render_answer(content: str) -> RenderResult:
    """Render an answer with pipe-table upgrade and inline-markdown strip."""
    if not content:
        yield Text("")
        return

    lines = content.splitlines()

    i = 0
    while i < len(lines):
        block = _detect_pipe_table(lines[i:])
        if block is not None:
            start, end = block
            yield _render_table(lines, i + start, i + end)
            i += end
            continue

        line = lines[i]
        if _HRULE_RE.match(line):
            i += 1
            continue

        # Blank line → blank spacer
        if not line.strip():
            yield Text("")
            i += 1
            continue

        # Strip inline markdown
        out = _strip_inline(line)
        yield Text(out)
        i += 1


def render_recap(history: Iterable[Any], *, last_request_max: int = 92,
                last_result_max: int = 128) -> Text:
    """Render a one-line recap: ``※ recap: Last request: …; Result: …``."""
    history = list(history)
    last = history[-1] if history else None
    request = ""
    if last is not None:
        content = last.get("content", "") if hasattr(last, "get") else getattr(last, "content", "")
        request = str(content).strip()[:last_request_max]
        if len(str(content)) > last_request_max:
            request = request[: last_request_max - 1] + "…"

    result = ""
    if len(history) >= 2:
        prev = history[-2]
        content = prev.get("content", "") if hasattr(prev, "get") else getattr(prev, "content", "")
        result = str(content).strip()[:last_result_max]
        if len(str(content)) > last_result_max:
            result = result[: last_result_max - 1] + "…"

    text = Text("※ recap: ", style="muted")
    text.append("Last request: ", style="muted")
    text.append(request)
    text.append("; Result: ", style="muted")
    text.append(result)
    return text


def render_elapsed_status(elapsed: float) -> Text:
    """Render ``✻ Analyzed for Ns/m/h``."""
    text = Text()
    text.append("✻ ", style="primary")
    text.append(f"Analyzed for {format_duration(elapsed, unit='s')}", style="muted")
    return text


def render_prompt_footer(*, width: Optional[int] = None) -> Text:
    """Render a horizontal rule under the prompt."""
    import shutil

    if width is None:
        try:
            width = max(40, shutil.get_terminal_size().columns)
        except (OSError, ValueError):
            width = 80
    return Text("─" * width, style="muted")


__all__ = [
    "render_answer",
    "render_recap",
    "render_elapsed_status",
    "render_prompt_footer",
]
