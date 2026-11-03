"""Structured tool results with LLM-facing views (A3).

Tools may return ``str`` (legacy JSON), ``dict``, or ``ToolResult``.
``render_for_llm`` produces a budget-aware text view that preserves
table headers / stats / head+tail instead of a blind mid-cut.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

# ── Model ────────────────────────────────────────────────────────────


@dataclass
class ToolResult:
    """One tool call outcome."""

    status: str = "ok"  # ok | error
    data: Any = None
    text: str = ""
    table: dict[str, Any] | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status == "ok"


# ── Builders ─────────────────────────────────────────────────────────


def ok(
    data: Any = None,
    text: str = "",
    table: dict[str, Any] | None = None,
    **meta: Any,
) -> ToolResult:
    return ToolResult(status="ok", data=data, text=text, table=table, meta=meta)


def error(message: str, **meta: Any) -> ToolResult:
    return ToolResult(status="error", data={"error": message}, text=message, meta=meta)


def table_result(
    rows: Sequence[Sequence[Any]] | Sequence[Mapping[str, Any]],
    columns: Sequence[str] | None = None,
    title: str | None = None,
    text: str = "",
    **meta: Any,
) -> ToolResult:
    """Build a table-shaped result.

    ``rows`` may be list-of-lists (with ``columns``) or list-of-dicts.
    """
    cols, normalized = _normalize_table(rows, columns)
    table = {"columns": cols, "rows": normalized}
    summary = f"{title or 'table'}: {len(normalized)} rows x {len(cols)} cols"
    return ToolResult(
        status="ok",
        data={"table": table},
        text=text or summary,
        table=table,
        meta={"row_count": len(normalized), **meta},
    )


def _normalize_table(
    rows: Sequence[Any], columns: Sequence[str] | None,
) -> tuple[list[str], list[list[Any]]]:
    if not rows:
        return list(columns or []), []
    first = rows[0]
    if isinstance(first, Mapping):
        cols = list(columns or list(first.keys()))
        normalized = [[r.get(c) for c in cols] for r in rows]  # type: ignore[union-attr]
        return cols, normalized
    cols = list(columns or [f"c{i}" for i in range(len(first))])  # type: ignore[arg-type]
    normalized = [list(r) for r in rows]  # type: ignore[arg-type]
    return cols, normalized


# ── Coercion ─────────────────────────────────────────────────────────


def from_any(value: Any) -> ToolResult:
    """Normalise tool output into a ToolResult (never raises)."""
    if isinstance(value, ToolResult):
        return value
    if value is None:
        return ok(data=None, text="")
    if isinstance(value, dict):
        status = str(value.get("status") or "ok").lower()
        if status not in ("ok", "error", "success"):
            status = "ok"
        if status == "success":
            status = "ok"
        data = value.get("result", value.get("data", value))
        text = value.get("text") or value.get("message") or ""
        if not text and status == "error":
            text = str(value.get("error") or "tool error")
        table = value.get("table") if isinstance(value.get("table"), dict) else None
        meta = {k: v for k, v in value.items() if k not in ("status", "result", "data", "text", "table", "message", "error")}
        return ToolResult(status=status, data=data, text=str(text), table=table, meta=meta)
    if isinstance(value, str):
        s = value.strip()
        if s.startswith("{") or s.startswith("["):
            try:
                parsed = json.loads(s)
            except Exception:  # noqa: BLE001
                return ok(data=value, text=value)
            return from_any(parsed)
        return ok(data=value, text=value)
    return ok(data=value, text=str(value))


def to_wire(result: ToolResult) -> dict[str, Any]:
    """Full envelope for SSE / DB / UI (structured data + views)."""
    out: dict[str, Any] = {
        "status": result.status,
        "data": result.data,
        "views": {
            "text": result.text,
            "table": result.table,
        },
    }
    if result.meta:
        out["meta"] = dict(result.meta)
    return out


def to_json(result: ToolResult) -> str:
    return json.dumps(to_wire(result), ensure_ascii=False, default=str)


# ── LLM rendering ────────────────────────────────────────────────────


def render_for_llm(result: ToolResult | Any, max_chars: int = 30_000) -> str:
    """Budget-aware text view for the model.

    - error → short error line
    - table → header + sample rows + row-count footer
    - long text → head + tail with a count marker
    """
    r = result if isinstance(result, ToolResult) else from_any(result)
    if max_chars <= 0:
        return _body(r)

    body = _body(r)
    if len(body) <= max_chars:
        return body

    if r.table:
        return _render_table(r, max_chars)
    return _render_long_text(body, max_chars)


def _body(r: ToolResult) -> str:
    if r.status != "ok":
        return f"[Tool error]: {r.text or (r.data or {}).get('error', 'unknown')}"
    parts = []
    if r.text:
        parts.append(r.text)
    if r.table:
        parts.append(_table_md(r.table, max_rows=50))
    if not parts:
        parts.append(_json_preview(r.data))
    return "\n".join(parts)


def _json_preview(data: Any, max_chars: int = 4000) -> str:
    try:
        s = json.dumps(data, ensure_ascii=False, default=str, indent=2)
    except Exception:  # noqa: BLE001
        s = str(data)
    if len(s) <= max_chars:
        return s
    return s[: max_chars // 2] + f"\n… [json truncated {len(s) - max_chars} chars] …\n" + s[-max_chars // 2 :]


def _table_md(table: Mapping[str, Any], max_rows: int = 50) -> str:
    cols = list(table.get("columns") or [])
    rows = list(table.get("rows") or [])
    if not cols and not rows:
        return "(empty table)"
    header = "| " + " | ".join(str(c) for c in cols) + " |"
    sep = "| " + " | ".join("---" for _ in cols) + " |"
    lines = [header, sep]
    shown = rows[:max_rows]
    for row in shown:
        cells = ["" if v is None else str(v) for v in row]
        lines.append("| " + " | ".join(cells) + " |")
    if len(rows) > max_rows:
        lines.append(f"\n… {len(rows) - max_rows} more rows (total {len(rows)})")
    return "\n".join(lines)


def _render_table(r: ToolResult, max_chars: int) -> str:
    table = r.table or {}
    cols = list(table.get("columns") or [])
    rows = list(table.get("rows") or [])
    intro = r.text or f"table: {len(rows)} rows"
    # Estimate row width to fit budget.
    header = "| " + " | ".join(str(c) for c in cols) + " |"
    sep = "| " + " | ".join("---" for _ in cols) + " |"
    footer = f"\n… total {len(rows)} rows (showing {{n}})"
    budget = max_chars - len(intro) - len(header) - len(sep) - len(footer) - 1
    body_lines: list[str] = []
    used = 0
    for row in rows:
        line = "| " + " | ".join("" if v is None else str(v) for v in row) + " |"
        if used + len(line) + 1 > budget and body_lines:
            break
        body_lines.append(line)
        used += len(line) + 1
    return "\n".join([intro, header, sep, *body_lines, footer.format(n=len(body_lines))])


def _render_long_text(body: str, max_chars: int) -> str:
    marker = f"\n… [truncated {len(body) - max_chars} chars] …\n"
    half = max(1, (max_chars - len(marker)) // 2)
    return body[:half] + marker + body[-half:]


__all__ = [
    "ToolResult",
    "error",
    "from_any",
    "ok",
    "render_for_llm",
    "table_result",
    "to_json",
    "to_wire",
]
