"""Content formatting for assistant messages before Markdown rendering.

The LLM frequently emits structured JSON as part of its response. When
this JSON is passed directly to Rich's ``Markdown`` renderer, it is
treated as paragraph text — no syntax highlighting, no word-wrap at
meaningful boundaries, and the result looks like a wall of garbled
characters.

This module detects JSON-like content in the body and wraps it in a
fenced `````json``` `` code block so the Markdown renderer applies
monokai syntax highlighting and natural word-wrap at punctuation /
whitespace boundaries.

Detection heuristic:
    * The content contains ``{`` followed by ``": `` (a JSON key-value
      pair indicator).
    * Everything from the first ``{`` to end-of-string is treated as
      the JSON body and wrapped in a fenced block.
    * A preceding markdown prefix (e.g. ``> blockquote``) is split out
      and rendered as its own Markdown paragraph.

If the JSON is incomplete (truncated mid-stream by the LLM), the code
block still renders — Rich shows what it can and the user can scroll.
"""
from __future__ import annotations

import re

# Heuristic: does the substring after the first ``{`` look like JSON?
# We look for at least one ``": `` pattern (key-value separator) which
# is overwhelmingly common in JSON and extremely rare in natural text.
_JSON_KEY_RE = re.compile(r'"\s*:\s*')


def reformat_body_content(content: str) -> str:
    """Reformat body content for better Markdown display.

    If the content contains a JSON-like block (detected by ``{``
    followed by ``": ``), split it into:

        1. A markdown prefix (everything before the first ``{``).
        2. A fenced `````json``` `` code block (everything from ``{``
           to end-of-string).

    The prefix is rendered as its own Markdown paragraph (preserving
    blockquotes, headers, lists, etc.). The JSON block gets monokai
    syntax highlighting and natural word-wrap.

    Args:
        content: Raw body text from the LLM (after think-tag extraction).

    Returns:
        Reformatted string ready for ``write_markdown``. If no JSON is
        detected, the original content is returned unchanged.
    """
    if not content or not content.strip():
        return content

    # Find the first ``{`` that starts a potential JSON object.
    brace_idx = content.find("{")
    if brace_idx == -1:
        return content

    # Check if the content after ``{`` looks like JSON (has a key-value
    # separator ``": ``). This avoids false positives on plain-text
    # content that happens to contain a ``{``.
    after_brace = content[brace_idx:]
    if not _JSON_KEY_RE.search(after_brace):
        return content

    # Split: prefix = markdown, rest = JSON code block.
    prefix = content[:brace_idx].rstrip()
    json_body = content[brace_idx:]

    # Strip trailing ``}``-only lines that are just closing braces with
    # no content — these are often orphaned from a truncated JSON and
    # look ugly inside a code block.  (If there IS content after the
    # last ``}``, keep it as-is.)
    json_body = json_body.rstrip()

    if prefix:
        return f"{prefix}\n\n```json\n{json_body}\n```"
    else:
        return f"```json\n{json_body}\n```"


__all__ = ["reformat_body_content"]
