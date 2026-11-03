"""Shared utilities for agent tools.

Provides defensive parameter parsing and structured error messages
that help LLMs recover from common shape mistakes.

Common LLM mistakes this module handles:

1. **Wrapping a list in a dict**:
   - LLM sends: ``data[code] = {"item": [...]}``
   - Expected:  ``data[code] = [...]``
   - Handled by: ``unwrap_dict_to_list``

2. **Wrapping a dict in a dict**:
   - LLM sends: ``args = {"data": {...}}``
   - Expected:  ``args = {...}``
   - Handled by: ``unwrap_dict_to_dict``

3. **JSON-stringified values**:
   - LLM sends: ``codes = '["A", "B"]'``
   - Expected:  ``codes = ["A", "B"]``
   - Handled by: ``safe_get_param`` (auto-parses JSON for list/dict types)

4. **Wrong type for known shape**:
   - LLM sends: ``limit = "5"`` (string instead of int)
   - Handled by: ``safe_get_param`` (coerces if possible)

When a parameter cannot be coerced, the error message includes:
- **received**: what the LLM sent (truncated)
- **expected**: what shape we want
- **fix**: a concrete next step (often: a complete tool-call example)

These structured fields are consumed by the LLM in the next turn to
recover without trial-and-error.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


# Common LLM "wrong wrapping" patterns
_LIST_WRAPPER_KEYS = (
    "item", "data", "records", "bars", "rows", "ohlcv", "values",
    "list", "items", "result", "results", "output",
)
_DICT_WRAPPER_KEYS = (
    "item", "data", "value", "obj", "dict", "value", "args",
)


# ── Public API ───────────────────────────────────────────────


def tool_ok(payload: dict[str, Any]) -> str:
    """Standard success envelope for tool results."""
    return json.dumps({"status": "ok", **payload}, ensure_ascii=False)


def tool_err(message: Any, **extra: Any) -> str:
    """Standard error envelope for tool results."""
    return json.dumps(
        {"status": "error", "error": str(message), **extra},
        ensure_ascii=False,
    )


def try_unwrap_list(value: Any) -> Optional[list]:
    """If value is a dict that wraps a single list, return the list.

    Returns:
        The unwrapped list, or None if no list wrapper found.
    """
    if not isinstance(value, dict):
        return None
    return _try_unwrap_list(value)


def try_unwrap_dict(value: Any) -> Optional[dict]:
    """If value is a dict that wraps a single dict, return the inner dict.

    Returns:
        The unwrapped dict, or None if no dict wrapper found.
    """
    if not isinstance(value, dict):
        return None
    return _try_unwrap_dict(value)


def err_actionable(
    message: str,
    *,
    received: Any = None,
    expected: str = "",
    fix: str = "",
    tool: str = "",
    extra: dict | None = None,
) -> str:
    """Structured error message that helps the LLM recover.

    Format::

        {
            "status": "error",
            "error": "<message>",
            "received": <received_value>,   # truncated to 200 chars
            "expected": "<what we want>",
            "fix": "<how to call correctly>",
            "tool": "<tool_name>",
            ...extra
        }

    Args:
        message: Human-readable error.
        received: What the LLM sent (will be truncated).
        expected: What we want (concrete example preferred).
        fix: Concrete next step (call X with Y, then call Z with W).
        tool: Tool name (e.g. "import_data") for self-documentation.
        extra: Additional structured fields.

    Returns:
        JSON string suitable for returning to the LLM.
    """
    payload: dict = {"status": "error", "error": str(message)}
    if received is not None:
        payload["received"] = truncate(received)
    if expected:
        payload["expected"] = expected
    if fix:
        payload["fix"] = fix
    if tool:
        payload["tool"] = tool
    if extra:
        payload.update(extra)
    return json.dumps(payload, ensure_ascii=False, default=str)


def truncate(value: Any, max_len: int = 200) -> Any:
    """Truncate long strings/collections in error payloads.

    Keeps error payloads small so they fit comfortably in the LLM
    context window even when the LLM sends a giant wrong input.
    """
    if isinstance(value, str):
        if len(value) > max_len:
            return value[:max_len] + f"... (truncated, total {len(value)} chars)"
        return value
    if isinstance(value, dict):
        return {k: truncate(v, max_len) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        items = [truncate(v, max_len) for v in value[:5]]
        if len(value) > 5:
            items.append(f"... (total {len(value)} items)")
        return items
    return value


# ── Internal helpers ──────────────────────────────────────────


def _try_unwrap_list(d: dict) -> Optional[list]:
    """Try common wrapper keys to find a list inside ``d``."""
    for key in _LIST_WRAPPER_KEYS:
        if key in d and isinstance(d[key], list):
            logger.debug("unwrap_list: found key %r", key)
            return d[key]
    return None


def _try_unwrap_dict(d: dict) -> Optional[dict]:
    """Try common wrapper keys to find a dict inside ``d``."""
    for key in _DICT_WRAPPER_KEYS:
        if key in d and isinstance(d[key], dict):
            logger.debug("unwrap_dict: found key %r", key)
            return d[key]
    return None


__all__ = [
    "tool_ok",
    "tool_err",
    "try_unwrap_list",
    "try_unwrap_dict",
    "err_actionable",
    "truncate",
    "_LIST_WRAPPER_KEYS",
    "_DICT_WRAPPER_KEYS",
]
