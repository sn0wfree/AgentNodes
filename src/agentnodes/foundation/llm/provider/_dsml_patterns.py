"""DSML pseudo-tool-call pattern (shared between SiliconFlow + DeepSeek).

DeepSeek-V4-Flash (and similar reasoning models served via SiliconFlow
or the official DeepSeek API) sometimes leaks pseudo-tool-call markup
into ``reasoning_content`` / ``content`` to *express intent* to call a
tool. The real tool call travels through ``delta.tool_calls`` in a
separate stream, so these in-text blocks are pure UI noise.

Shapes observed in production (verified against the local SQLite
``event_log`` of past sessions — see ``d8d58926-...`` session):

* Standard XML: ``<tools>...</tools>``, ``<tool>...</tool>``
  (greedy match across the whole block).
* DSML bracket / pipe variants (emitted as *individual tokens* during
  streaming, so a single chunk is often just one of these):
  - ``<|DSML|tool_calls>``
  - ``<|DSML|invoke name="list">``
  - ``<|DSML|parameter name="workspace" string="true">...<|DSML|parameter>``
  - ``<|DSML|tool_calls>...<``  (unclosed)
  - ``[DSML | tool_calls>...<]``
  - ``｜DSML｜``  (full-width pipes — confirmed in ``event_log``)
* Unclosed blocks: model started a block but the SSE stream ended
  before the closing tag. Drop everything from the opening tag.

Used by :class:`SiliconFlowAdapter` and :class:`DeepSeekAdapter` —
NOT applied for other providers (OpenAI / Qwen / Kimi / MiniMax),
which default to passthrough via the base implementation.
"""
from __future__ import annotations

import re

# Closed ``<tools>...</tools>`` (or ``<tool>...</tool>`` / ``<tool_calls>``
# variants) block.
_DSML_BLOCK_RE = re.compile(
    r"<\s*(?:tool_calls?|tools?)\s*>[\s\S]*?<\s*/\s*(?:tool_calls?|tools?)\s*>",
    re.IGNORECASE,
)

# Single DSML token variants — each matches as a standalone token
# (no need to find a closing tag). Combined into one alternation so
# the stripper can ``re.sub`` them all in one pass.
_DSML_TOKEN_RE = re.compile(
    r"<\|DSML\|[^<>]*>"            # <|DSML|tool_calls>, <|DSML|parameter name="...">
    r"|\uFF5CDSML\uFF5C"           # ｜DSML｜ (full-width pipes)
    r"|\|DSML\|"                   # |DSML| bare
    r"|\[DSML\s*\|[^\]]*?\]"       # [DSML | tool_calls>...] (legacy)
)

# Opening tokens for unclosed-block truncation.
_DSML_OPEN_RE = re.compile(
    r"<\s*(?:tool_calls?|tools?)\s*>"
    r"|<\|DSML\|"
    r"|\uFF5CDSML\uFF5C"
    r"|\|DSML\|"
    r"|\[DSML\s*\|",
    re.IGNORECASE,
)

# Streaming cross-chunk markers for :class:`StreamDsmlFixer`.
# Maps an opening marker prefix to its matching close marker (or None
# for single-token markers whose "close" is any ``</``).
_STREAM_OPENS: dict[str, str | None] = {
    "<tool_calls": "</tool_calls>",
    "<tools": "</tools>",
    "<tool": "</tool>",
    "<invoke": "</invoke>",
    "<parameter": "</parameter>",
    "<|DSML|": None,
    "[DSML": None,
    "\uFF5CDSML\uFF5C": None,
    "|DSML|": None,
}


class StreamDsmlFixer:
    """Cross-chunk DSML stripper for streaming deltas (``fix_delta`` hook).

    DeepSeek streams one BPE token per SSE chunk, so an opening tag
    like ``<tool_calls>`` arrives split across chunks (``<`` + ``tool``
    + ``_c`` + ``alls`` + ``>``). Per-chunk regex can never match such
    a marker — this state machine buffers partial markers, enters
    discard mode once a complete opening marker is seen, and drops
    everything (including nested content and inner close tags) until
    the matching close marker arrives.

    Two modes:

    * ``normal``: buffer candidates that are prefixes of an opening
      marker (``<``, ``<t``, ``<tool_`` …); on a complete marker switch
      to ``discarding``; otherwise flush the buffer as plain text.
      Plain text never starts with ``<``, so no legitimate word is
      ever held back for more than a few tokens.
    * ``discarding``: drop chunks until the close marker for the
      current opening marker appears; inner close tags (``</parameter>``,
      ``</invoke>``) are consumed as block content and do NOT exit.

    One instance per text field (reasoning_content / content), held on
    the adapter — the client creates a fresh adapter per request, so
    stream state is naturally request-scoped.
    """

    __slots__ = ("_mode", "_pending", "_close")

    def __init__(self) -> None:
        self._mode = "normal"  # normal | discarding
        self._pending = ""
        self._close: str | None = None

    def fix(self, text: str) -> str:
        """Process one chunk; returns the cleaned text for this chunk."""
        out: list[str] = []
        work = text
        while work:
            if self._mode == "normal":
                candidate = self._pending + work
                match = self._find_open(candidate)
                if match is not None:
                    open_marker, close_marker, idx = match
                    # Emit text before the marker, then start discarding.
                    out.append(candidate[:idx])
                    self._mode = "discarding"
                    self._close = close_marker
                    self._pending = ""
                    work = candidate[idx + len(open_marker):].lstrip("> ")
                    continue
                if candidate and self._is_open_prefix(candidate):
                    # May still grow into a complete marker — hold back.
                    self._pending = candidate
                    break
                out.append(candidate)
                self._pending = ""
                break
            # discarding: find the close marker anywhere in the candidate
            # (block content may precede it in the same chunk).
            candidate = self._pending + work
            close = self._close
            if close is not None:
                idx = candidate.find(close)
                if idx >= 0:
                    self._mode = "normal"
                    self._pending = ""
                    work = candidate[idx + len(close):]
                    continue
            elif candidate.find("</") >= 0:
                # Unclosed-marker variant (close=None): any ``</`` ends it.
                idx = candidate.find("</")
                self._mode = "normal"
                self._pending = ""
                work = candidate[idx + 2:]
                continue
            if close is not None and close.startswith(candidate) and candidate:
                self._pending = candidate
                break
            self._pending = ""
            break
        return "".join(out)

    def _find_open(
        self, candidate: str,
    ) -> tuple[str, str | None, int] | None:
        """Find the leftmost complete opening marker in ``candidate``.

        Returns (open_marker, close_marker, index) or None. A marker
        counts as complete when it is followed by ``>`` / space / end,
        and when the candidate is NOT a prefix of a longer marker
        (``"<tool"`` keeps buffering instead of matching the short
        ``<tool`` while it could grow into ``<tool_calls``).
        """
        best: tuple[str, str | None, int] | None = None
        for open_marker, close_marker in _STREAM_OPENS.items():
            idx = candidate.find(open_marker)
            if idx < 0:
                continue
            rest = candidate[idx + len(open_marker):]
            if not rest or rest[0] in (">", " "):
                tail = candidate[idx:]
                if any(
                    m.startswith(tail) and len(m) > len(open_marker)
                    for m in _STREAM_OPENS
                ):
                    continue  # may still grow into a longer marker
                if best is None or idx < best[2]:
                    best = (open_marker, close_marker, idx)
        return best

    def _is_open_prefix(self, candidate: str) -> bool:
        return any(
            open_marker.startswith(candidate) for open_marker in _STREAM_OPENS
        )


def strip_dsml_text(text: str, strip_edges: bool = True) -> str:
    """Strip DSML pseudo-tool-call markup from a single text fragment.

    Two-pass strategy:

    1. **Closed blocks + single DSML tokens** — the bulk of leakage
       looks like ``<tools>...</tools>`` (greedy non-greedy match
       across a whole block) or a standalone ``<|DSML|tool_calls>``
       token. Both are removed in a single ``re.sub`` call.
    2. **Unclosed openings** — if step 1 left text that still starts a
       block but the stream ended mid-block (e.g. ``"...prefix
       <tools>half-written"``), truncate at the opening position so
       we never leak a half-baked block to the user.

    Idempotent — running twice has no additional effect.

    Args:
        strip_edges: when True (message path) trim the result; when
            False (streaming delta path) keep chunk boundary whitespace
            so BPE leading spaces survive (see ``normalize_thinking``).
    """
    if not text:
        return text
    # 1. Drop every closed block + standalone DSML token.
    cleaned = _DSML_BLOCK_RE.sub("", text)
    cleaned = _DSML_TOKEN_RE.sub("", cleaned)
    # 2. Defensive: if any opening tag still survives (unclosed block
    # the model started before the stream ended), drop from there on.
    m = _DSML_OPEN_RE.search(cleaned)
    if m:
        cleaned = cleaned[: m.start()]
    if strip_edges:
        cleaned = cleaned.strip()
    return cleaned
