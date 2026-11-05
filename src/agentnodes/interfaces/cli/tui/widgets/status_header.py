"""StatusHeader - single-row header showing model, tokens, ctx, and session id.

Replaces the plain Textual Header with a status bar that displays:
- Connection status (● live / idle / error)
- Model name
- Message count + tool count
- Token usage + context window progress bar
- Session id (where the user can find it)

Layout:
    ● live  minimax-M3  │ 5 msg  3 tool │ 1.2k/128k [====] │ sid:cli
"""
from __future__ import annotations

from typing import Any, Optional

from textual.widgets import Static


def _progress_bar(used: int, total: int, width: int = 10) -> str:
    """Render a progress bar like [====-------]."""
    if total <= 0:
        return "[" + "-" * width + "]"
    ratio = min(used / total, 1.0)
    filled = int(ratio * width)
    return "[" + "=" * filled + "-" * (width - filled) + "]"


def _format_token_count(n: int) -> str:
    """Format token count: 1234 -> '1.2k', 500 -> '500'."""
    if n >= 1000:
        return f"{n / 1000:.1f}k"
    return str(n)


def _status_dot(status: str) -> str:
    """Return colored status dot."""
    if status == "live":
        return "[green]\u25cf[/green]"
    elif status == "error":
        return "[red]\u25cf[/red]"
    return "[dim]\u25cf[/dim]"


def _short_sid(sid: str | None) -> str:
    """Shorten a session id for the header (keep last 8 chars if long)."""
    if not sid:
        return "cli"
    if len(sid) <= 12:
        return sid
    return "\u2026" + sid[-8:]


class StatusHeader(Static):
    """Single-row status header with model, tokens, ctx, and session id."""

    DEFAULT_CSS = """
    StatusHeader {
        height: 1;
        dock: top;
        background: $primary 25%;
        color: $text;
        padding: 0 1;
    }
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._connection_status: str = "idle"
        self._model: str = "unknown"
        self._message_count: int = 0
        self._tool_count: int = 0
        self._token_used: int = 0
        self._token_total: int = 128000
        self._session_id: str = "cli"
        self._iter_count: int = 0
        self._iter_max: int = 0

    def update_status(
        self,
        *,
        connection_status: Optional[str] = None,
        model: Optional[str] = None,
        message_count: Optional[int] = None,
        tool_count: Optional[int] = None,
        token_used: Optional[int] = None,
        token_total: Optional[int] = None,
        session_id: Optional[str] = None,
        iter_count: Optional[int] = None,
        iter_max: Optional[int] = None,
    ) -> None:
        """Update header fields and re-render."""
        if connection_status is not None:
            self._connection_status = connection_status
        if model is not None:
            self._model = model
        if message_count is not None:
            self._message_count = message_count
        if tool_count is not None:
            self._tool_count = tool_count
        if token_used is not None:
            self._token_used = token_used
        if token_total is not None:
            self._token_total = token_total
        if session_id is not None:
            self._session_id = session_id
        if iter_count is not None:
            self._iter_count = iter_count
        if iter_max is not None:
            self._iter_max = iter_max

        self._refresh()

    def _refresh(self) -> None:
        """Re-render the header content."""
        status_dot = _status_dot(self._connection_status)
        model = self._model
        msgs = f"{self._message_count} msg"
        tools = f"{self._tool_count} tool"
        token_str = f"{_format_token_count(self._token_used)}/{_format_token_count(self._token_total)}"
        bar = _progress_bar(self._token_used, self._token_total, width=8)
        sid = _short_sid(self._session_id)

        parts = [
            f"{status_dot} {model}",
            f"  [dim]\u2502[/dim]  {msgs}  {tools}",
            f"  [dim]\u2502[/dim]  {token_str} {bar}",
        ]
        if self._iter_max > 0:
            parts.append(f"  [dim]\u2502[/dim]  iter {self._iter_count}/{self._iter_max}")
        parts.append(f"  [dim]\u2502[/dim]  sid:{sid}")

        content = "".join(parts)
        self.update(content)


__all__ = ["StatusHeader"]
