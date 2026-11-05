r"""Trace context (ContextVar) + TraceFilter + JsonFormatter.

Design:
- Four ``ContextVar``\ s carry request-scoped ids through the
  asyncio task graph: ``trace_id`` (per HTTP request / agent turn),
  ``session_id``, ``study_id``, ``round_num``.
- ``bind_trace(**kw)`` is a context manager that sets vars and restores
  on exit -- safe for nested calls (study runner inside agent loop
  inside chat request).
- ``TraceFilter`` is a ``logging.Filter`` that copies current ContextVar
  values onto each ``LogRecord`` so formatters can emit them.
- ``JsonFormatter`` renders the record as a single-line JSON object.
- ``setup_trace_logging()`` wires the filter onto the root logger and
  optionally swaps the handler's formatter to JSON when
  ``SR_LOG_JSON=1``.

ContextVars propagate correctly across ``await`` boundaries and
``asyncio.create_task`` (via ``contextvars.copy_context()``), so a
trace_id set at the chat request entry appears in LLM client logs
emitted deep inside ``AgentLoop.arun -> client.astream``.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Iterator

# ── ContextVars ──────────────────────────────────────────────────────

_trace_id: ContextVar[str | None] = ContextVar("trace_id", default=None)
_session_id: ContextVar[str | None] = ContextVar("session_id", default=None)
_study_id: ContextVar[str | None] = ContextVar("study_id", default=None)
_round_num: ContextVar[int | None] = ContextVar("round_num", default=None)

_ALL_VARS = (_trace_id, _session_id, _study_id, _round_num)
_VAR_NAMES = ("trace_id", "session_id", "study_id", "round_num")


def new_trace_id() -> str:
    """Generate a short trace id (12 hex chars, matching attempt_id style)."""
    return uuid.uuid4().hex[:12]


def get_trace_context() -> dict[str, Any]:
    """Snapshot current trace vars as a dict (for record_event etc.)."""
    return {
        "trace_id": _trace_id.get(),
        "session_id": _session_id.get(),
        "study_id": _study_id.get(),
        "round_num": _round_num.get(),
    }


@contextmanager
def bind_trace(
    *,
    trace_id: str | None = None,
    session_id: str | None = None,
    study_id: str | None = None,
    round_num: int | None = None,
) -> Iterator[dict[str, Any]]:
    """Bind trace vars for the duration of the context.

    Only the kwargs explicitly passed are overridden; others keep their
    outer value. On exit, previous values are restored (nested-safe).

    >>> with bind_trace(trace_id="abc", session_id="sess-1"):
    ...     # logs here carry trace_id="abc", session_id="sess-1"
    ...     pass
    """
    tokens: list[Any] = []
    new_vals: dict[str, Any] = {}
    if trace_id is not None:
        tokens.append(_trace_id.set(trace_id))
        new_vals["trace_id"] = trace_id
    if session_id is not None:
        tokens.append(_session_id.set(session_id))
        new_vals["session_id"] = session_id
    if study_id is not None:
        tokens.append(_study_id.set(study_id))
        new_vals["study_id"] = study_id
    if round_num is not None:
        tokens.append(_round_num.set(round_num))
        new_vals["round_num"] = round_num
    try:
        yield get_trace_context()
    finally:
        for tok in reversed(tokens):
            tok.var.reset(tok)


# ── TraceFilter ──────────────────────────────────────────────────────


class TraceFilter(logging.Filter):
    """Inject current ContextVar values onto every ``LogRecord``.

    Attach to log **handlers** (not the root logger — logger filters do
    not see records propagated from child loggers). The filter runs
    *before* the handler formatter, so ``JsonFormatter`` (and the text
    formatter via ``%(trace_id)s``) can read the attrs.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        record.trace_id = _trace_id.get() or "-"
        record.session_id = _session_id.get() or "-"
        record.study_id = _study_id.get() or "-"
        record.round_num = _round_num.get() if _round_num.get() is not None else "-"
        return True


# ── JsonFormatter ────────────────────────────────────────────────────


class JsonFormatter(logging.Formatter):
    """Single-line JSON log formatter.

    Output shape::

        {"ts":"13:45:01","level":"INFO","logger":"agentnodes.foundation.llm.openai_client",
         "trace_id":"a1b2c3d4e5f6","session_id":"sess-1","study_id":"st-9","round_num":3,
         "msg":"stream retryable status 429 (attempt 1/3)"}
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for name in _VAR_NAMES:
            val = getattr(record, name, None)
            if val is not None and val != "-":
                payload[name] = val
        if record.exc_info and record.exc_info[1] is not None:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


# ── setup ────────────────────────────────────────────────────────────


def ensure_trace_filter(root: logging.Logger) -> None:
    """Attach ``TraceFilter`` to every root handler (idempotent).

    挂 **handler** 而不是 root logger：logger 级 filter 只对「该 logger
    自己产生的记录」生效，子 logger 传播上来的记录不经过 root.filters
    （defect #17——旧实现挂在 root 上，trace 字段对所有 surface 的子
    logger 日志都没生效，JsonFormatter 静默省略掩盖了这一点）。Handler
    级 filter 对每条被处理的记录都会运行。
    """
    for h in root.handlers:
        if not any(isinstance(f, TraceFilter) for f in h.filters):
            h.addFilter(TraceFilter())


def setup_trace_logging(
    *,
    json_output: bool | None = None,
    log_level: int = logging.INFO,
) -> None:
    """Wire ``TraceFilter`` (+ optional ``JsonFormatter``) onto root handlers.

    Idempotent: safe to call multiple times (e.g. ``create_app`` in
    tests). The filter is added to each handler once; the formatter is
    re-applied based on ``json_output``.

    Args:
        json_output: If ``None``, reads ``SR_LOG_JSON`` env var.
            ``1``/``true`` enables JSON; anything else keeps text.
        log_level: Root logger level (default INFO, usually from
            ``SR_LOG_LEVEL``).
    """
    if json_output is None:
        json_output = os.environ.get("SR_LOG_JSON", "").lower() in ("1", "true")

    root = logging.getLogger()
    root.setLevel(log_level)
    ensure_trace_filter(root)

    fmt = JsonFormatter(datefmt="%H:%M:%S") if json_output else None
    for h in root.handlers:
        if fmt is not None:
            h.setFormatter(fmt)
