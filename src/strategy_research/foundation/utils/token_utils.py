"""Shared token estimation (chars→token approximation).

Previously duplicated verbatim in core/agent/context.py
(``estimate_tokens``) and core/agent/compact.py (``_estimate_tokens``).
"""

from __future__ import annotations

import json
from typing import Any

CHARS_PER_TOKEN = 3.0


def estimate_tokens(
    messages: list[dict[str, Any]],
    chars_per_token: float = CHARS_PER_TOKEN,
) -> int:
    """Rough token count for a list of messages.

    Counts:
    - string content length
    - tool_calls function arguments JSON
    - a fixed 100-char overhead per tool-role message

    Args:
        messages: List of {"role", "content", "tool_calls", ...} dicts.
        chars_per_token: chars per token divisor.

    Returns:
        Estimated token count (at least 1).
    """
    total_chars = 0
    for msg in messages:
        content = msg.get("content") or ""
        if isinstance(content, str):
            total_chars += len(content)
        for tc in msg.get("tool_calls") or []:
            if isinstance(tc, dict):
                fn = tc.get("function") or {}
                total_chars += len(json.dumps(fn.get("arguments", "")))
        if msg.get("role") == "tool":
            total_chars += 100  # overhead for role
    return max(1, int(total_chars / chars_per_token))


def estimate_tokens_text(text: str, chars_per_token: float = CHARS_PER_TOKEN) -> int:
    """Rough token count for a plain string (at least 1)."""
    return max(1, int(len(text) / chars_per_token))
