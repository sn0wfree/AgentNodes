"""Text filters for TUI display.

Centralised regex-based filters applied at the display boundary to
remove content that should never reach the user, even when the model
emits it as part of its streamed response.
"""
from __future__ import annotations

import re

# ── thinking / reasoning tag stripping ──────────────────────────────

# Closed-form tags emitted by various providers when surfacing their
# internal reasoning alongside the user-visible answer:
#   - <think>...</think>      (OpenAI o1-style / Anthropic extended thinking)
#   - <reasoning>...</reasoning>
#   - <thinking>...</thinking>
#   - <|reasoning|>...</|reasoning|>  (Qwen / DeepSeek)
#   - <|thinking|>...</|thinking|>
#   - <think/>                (self-closing marker)
_THINK_PATTERNS_CLOSED = [
    re.compile(r"<think(?:ing)?>.*?</think(?:ing)?>", re.DOTALL),
    re.compile(r"<reasoning>.*?</reasoning>", re.DOTALL),
    re.compile(r"<thinking>.*?</thinking>", re.DOTALL),
    re.compile(r"<\|reasoning\|>.*?<\|/reasoning\|>", re.DOTALL),
    re.compile(r"<\|thinking\|>.*?<\|/thinking\|>", re.DOTALL),
    re.compile(r"<think\s*/>", re.DOTALL),
]

# Capture variants (same patterns, with the inner content in group 1).
# Used by :func:`extract_thinking_tags` to *keep* the reasoning content
# for foldable rendering, rather than discarding it.
_THINK_CAPTURE_CLOSED = [
    re.compile(r"<think(?:ing)?>(.*?)</think(?:ing)?>", re.DOTALL),
    re.compile(r"<reasoning>(.*?)</reasoning>", re.DOTALL),
    re.compile(r"<thinking>(.*?)</thinking>", re.DOTALL),
    re.compile(r"<\|reasoning\|>(.*?)<\|/reasoning\|>", re.DOTALL),
    re.compile(r"<\|thinking\|>(.*?)<\|/thinking\|>", re.DOTALL),
]

# Unclosed (truncated mid-stream) forms. Strip from the opening tag
# to end of string — the rest is presumed to be reasoning content.
_THINK_PATTERNS_UNCLOSED = [
    re.compile(r"<think(?:ing)?>.*$", re.DOTALL),
    re.compile(r"<reasoning>.*$", re.DOTALL),
    re.compile(r"<thinking>.*$", re.DOTALL),
    re.compile(r"<\|reasoning\|>.*$", re.DOTALL),
    re.compile(r"<\|thinking\|>.*$", re.DOTALL),
]


def strip_thinking_tags(text: str) -> str:
    """Remove all reasoning / thinking tags from LLM output.

    Closed tags (``<think>...</think>``) are removed entirely.
    Unclosed tags (truncated mid-stream) are stripped from the opening
    tag to end-of-string on the assumption that everything after is
    internal reasoning the user shouldn't see.

    Args:
        text: Raw text from LLM (may be empty).

    Returns:
        Cleaned text with all reasoning tags removed and trailing
        whitespace stripped. Empty input returns empty output.
    """
    if not text:
        return text
    for pat in _THINK_PATTERNS_CLOSED:
        text = pat.sub("", text)
    for pat in _THINK_PATTERNS_UNCLOSED:
        text = pat.sub("", text)
    return text.strip()


def extract_thinking_tags(text: str) -> tuple[str, str]:
    """Split text into ``(think_content, body_content)``.

    Unlike :func:`strip_thinking_tags` which *discards* reasoning,
    this function *preserves* it so the TUI can render think content
    as a foldable section (collapsed by default; ``Ctrl+E`` to expand).

    Extraction rules:
        * **Closed** tags (``<think>...</think>``): inner content goes
          into the think bucket; the entire tag including delimiters
          is removed from the body.
        * **Unclosed** tags (truncated mid-stream — e.g. stream cut
          off after ``<think>foo``): everything from the opening tag
          to end of string is treated as reasoning.
        * **Self-closing** tags (``<think/>``): no content to extract;
          removed entirely from the body.

    Multiple think sections are joined with ``"\\n\\n"`` in capture order.
    Both think and body are stripped of leading/trailing whitespace.
    Empty / no-tag input returns ``("", text.strip())``.

    Args:
        text: Raw text from LLM (may be empty).

    Returns:
        ``(think_content, body_content)`` tuple.
    """
    if not text:
        return "", ""

    think_parts: list[str] = []

    # 1) Closed forms — extract inner content into think_parts.
    for pat in _THINK_CAPTURE_CLOSED:
        for m in pat.finditer(text):
            inner = (m.group(1) or "").strip()
            if inner:
                think_parts.append(inner)
        text = pat.sub("", text)

    # 2) Self-closing markers (<think/>) — no content to extract.
    text = re.sub(r"<think\s*/>", "", text)

    # 3) Unclosed forms — slice from opening tag to end-of-string.
    #    Walk left-to-right so multiple unclosed tags accumulate.
    for pat in _THINK_PATTERNS_UNCLOSED:
        m = pat.search(text)
        if m:
            tail = m.group(0)
            # Strip the opening tag itself ("<think>", "<reasoning>", etc.)
            tail_inner = tail.split(">", 1)[1].strip() if ">" in tail else tail.strip()
            if tail_inner:
                think_parts.append(tail_inner)
            text = text[: m.start()]

    think_content = "\n\n".join(think_parts)
    body_content = text.strip()
    return think_content, body_content


__all__ = ["strip_thinking_tags", "extract_thinking_tags"]
