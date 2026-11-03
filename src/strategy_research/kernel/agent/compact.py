"""Context compaction engine — opencode-aligned L4 with safety check.

Layer (relative to threshold_tokens):
    L4 LLM Summarize (95%): structured Markdown summary via LLM

Design inspired by opencode's compaction architecture:
    - Structured summary template
    - Token budget selection (preserve_recent_tokens + tail_turns)
    - Incremental summary update
    - Single L4 layer (matches opencode `packages/core/src/session/compaction.ts`)

Phase A simplification (chore):
    - Removed L1 (smart microcompact) and L3 (hard truncate)
    - Kept deprecated fields in CompactConfig for backward compat
    - Added `simplified_to_l4_only` flag to opt-in to legacy 3-layer behavior
      (read-only; legacy code path removed; flag is controller-only)
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# ── Emergency kill switch ────────────────────────────────────────────
# Set SR_KEEP_ALL_COMPACTIONS=1 to globally force
# keep_all_compactions_in_history=True without code changes or config edits.
# Useful for emergency rollback if the new filter causes issues.
# See docs/compaction-history-filter.md.
_KEEP_ALL_COMPACTIONS_OVERRIDE: bool = os.environ.get(
    "SR_KEEP_ALL_COMPACTIONS", ""
).lower() in ("1", "true", "yes", "on")


# ── In-memory monitoring counters ────────────────────────────────────
# Lightweight metrics for observability. Not thread-safe by design
# (Python GIL makes simple int++ atomic enough for monitoring).
_compaction_metrics: dict[str, int] = {
    "total_hidden": 0,           # Total older compactions hidden from LLM
    "total_kept": 0,             # Total compactions kept in LLM
    "l4_aborts": 0,              # L4 safety aborts (would produce empty context)
    "filter_calls": 0,           # Total _convert_messages_to_history calls
}


# ── Per-session compaction lock ──────────────────────────────────────
# Prevents concurrent compaction (auto from agent loop + manual /compact)
# on the same session.  Both paths must acquire this lock before calling
# compact_messages().  The lock is per-session (different sessions can
# compact concurrently).
class _CompactLockMap:
    """Per-session asyncio.Lock for compaction serialization."""

    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = {}
        self._meta_lock = asyncio.Lock()

    async def get(self, session_id: str) -> asyncio.Lock:
        async with self._meta_lock:
            if session_id not in self._locks:
                self._locks[session_id] = asyncio.Lock()
            return self._locks[session_id]

    async def clear(self, session_id: str) -> None:
        async with self._meta_lock:
            self._locks.pop(session_id, None)


_compact_locks = _CompactLockMap()


def get_compaction_metrics() -> dict[str, int]:
    """Return a snapshot of compaction metrics (read-only copy)."""
    return dict(_compaction_metrics)


def reset_compaction_metrics() -> None:
    """Reset all compaction metrics to zero. Used by tests."""
    _compaction_metrics["total_hidden"] = 0
    _compaction_metrics["total_kept"] = 0
    _compaction_metrics["l4_aborts"] = 0
    _compaction_metrics["filter_calls"] = 0


def set_keep_all_override(enabled: bool) -> None:
    """Runtime kill switch toggle (used by admin API)."""
    global _KEEP_ALL_COMPACTIONS_OVERRIDE
    _KEEP_ALL_COMPACTIONS_OVERRIDE = enabled
    logger.warning(
        "compaction kill switch: keep_all_compactions_in_history=%s",
        enabled,
    )


# ── Structured summary template (opencode-style) ─────────────────

DEFAULT_SUMMARY_TEMPLATE = """\
Output exactly the Markdown structure shown inside <template> and keep the section order unchanged. \
Do not include the <template> tags in your response.
<template>
## Objective
- [one or two brief sentences describing what the user is trying to accomplish]

## Important Details
- [constraints/preferences, decisions and why, important facts/assumptions, exact context needed to continue, or "(none)"]

## Work State
### Completed
- [finished work, verified facts, or changes made; otherwise "(none)"]

### Active
- [current work, partial changes, or investigation state; otherwise "(none)"]

### Blocked
- [blockers, failing commands, or unknowns; otherwise "(none)"]

## Next Move
1. [immediate concrete action, or "(none)"]
2. [next action if known, or "(none)"]

## Relevant Files
- [file or directory path: why it matters, or "(none)"]
</template>

Rules:
- Keep every section, even when empty.
- Use terse bullets, not prose paragraphs.
- Preserve exact file paths, symbols, commands, error strings, URLs, and identifiers when known.
- Do not mention the summary process or that context was compacted.
- "[Assistant reasoning]" blocks are internal thought process — extract only final decisions and conclusions, not the deliberation itself."""


# ── Config ────────────────────────────────────────────────────────

# DEPRECATED since Phase A: was the L1 per-tool-type threshold table.
# Kept only as documentation reference for the (now-unused) tool_truncate_chars
# field default. Field default now uses an empty dict; L4 doesn't pre-truncate.
_LEGACY_L1_TOOL_LIMITS_DOC: dict[str, int] = {
    "read": 3000,
    "search_code": 2000,
    "shell": 2000,
    "backtest_run": 4000,
    "list": 1000,
    "_default": 2000,
}


# CompactConfig 已下沉 foundation（W5-8）；re-export 保持 API 不变。
from strategy_research.foundation.compact_config import CompactConfig  # noqa: F401


# ── Token estimation ──────────────────────────────────────────────


def _estimate_tokens(messages: list[dict[str, Any]], chars_per_token: float = 3.0) -> int:
    """Rough token count for a list of messages (shared impl)."""
    from strategy_research.foundation.utils.token_utils import estimate_tokens
    return estimate_tokens(messages, chars_per_token=chars_per_token)


def _estimate_single_tokens(text: str, chars_per_token: float = 3.0) -> int:
    """Rough token count for a plain string (shared impl)."""
    from strategy_research.foundation.utils.token_utils import estimate_tokens_text
    return estimate_tokens_text(text, chars_per_token=chars_per_token)


# ── Message serialization (opencode-style) ───────────────────────

_THINK_RE = re.compile(r"<think>([\s\S]*?)</think>", re.IGNORECASE)

TOOL_OUTPUT_MAX_CHARS = 2000


def _serialize_message(msg: dict[str, Any], tool_max_chars: int = TOOL_OUTPUT_MAX_CHARS) -> str:
    """Serialize a message for LLM summarization input.

    Format aligned with opencode core compaction serialize():
      [User]: ...
      [Assistant]: ... (text content)
      [Assistant reasoning]: ... (extracted from person thinking tags)
      [Assistant tool call]: name(input)
      [Tool result]: ... (truncated)
      [Tool error]: ...
      [System update]: ...
    """
    role = msg.get("role", "?")
    content = msg.get("content", "") or ""

    if role == "user":
        return f"[User]: {content}"

    if role == "system":
        return f"[System update]: {content}"

    if role == "assistant":
        return _serialize_assistant(msg, content)

    if role == "tool":
        return _serialize_tool_result(content, tool_max_chars)

    return ""


def _serialize_assistant(msg: dict[str, Any], content: str) -> str:
    """Serialize an assistant message (reasoning + text + tool calls)."""
    parts: list[str] = []

    # Extract person thinking blocks as reasoning content
    if content:
        think_matches = _THINK_RE.findall(content)
        if think_matches:
            reasoning_text = "\n".join(m.strip() for m in think_matches if m.strip())
            if reasoning_text:
                parts.append(f"[Assistant reasoning]: {reasoning_text}")
        # Remove think blocks from main content
        clean_content = _THINK_RE.sub("", content).strip()
        if clean_content:
            parts.append(f"[Assistant]: {clean_content}")

    # Tool calls
    for tc in msg.get("tool_calls") or []:
        if isinstance(tc, dict):
            fn = tc.get("function", {})
            args_str = fn.get("arguments", "")
            if isinstance(args_str, str) and len(args_str) > 100:
                args_str = args_str[:100] + "..."
            elif not isinstance(args_str, str):
                args_str = str(args_str)[:100]
            parts.append(f"[Assistant tool call]: {fn.get('name', '?')}({args_str})")
    return "\n".join(parts)


def _serialize_tool_result(content: str, tool_max_chars: int) -> str:
    """Serialize a tool message, detecting error status."""
    truncated = content[:tool_max_chars] + "\n[truncated]" if len(content) > tool_max_chars else content
    is_error = False
    if truncated.startswith("{") and '"status"' in truncated[:100]:
        try:
            import json
            parsed = json.loads(truncated[:tool_max_chars])
            if isinstance(parsed, dict) and parsed.get("status") == "error":
                is_error = True
                error_msg = parsed.get("message") or parsed.get("error") or str(parsed)
                truncated = error_msg
        except Exception:
            pass
    if is_error:
        return f"[Tool error]: {truncated}"
    return f"[Tool result]: {truncated}"


# ── L1: Smart Microcompact (DEPRECATED, removed in Phase A) ───────
# L1 was over-aggressive (truncated tool outputs before L4 sees them).
# Removed in commit A2. L4 + _serialize_message tool truncation is sufficient.
# Config fields (microcompact_tool_result_chars, tool_truncate_chars,
# collapse_keep_recent) kept for backward compat; ignored at runtime.


# ── L4: LLM Summarize (structured template + token budget) ────────

_MIN_PRESERVE_RECENT = 2_000
_MAX_PRESERVE_RECENT = 8_000
_TOOL_OUTPUT_MAX_CHARS = 2_000

# Quality decay: exponential half-life in turns
_QUALITY_DECAY_HALF_LIFE = 3.0


def _split_into_turns(messages: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Split non-system messages into user→assistant turns.

    Kept as a backward-compat shim (no production callers since
    _select_by_token_budget was rewritten). Tests still import it.
    """
    turns: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    for msg in messages:
        current.append(msg)
        if msg.get("role") == "assistant":
            turns.append(current)
            current = []
    if current:
        turns.append(current)
    return turns


def _truncate_for_budget(text: str, budget_chars: int) -> str:
    """Truncate text to fit within budget_chars, appending a marker."""
    if len(text) <= budget_chars:
        return text
    return text[:budget_chars] + "\n[truncated]"


# ── Quality decay scoring ─────────────────────────────────────────


def _score_turn(turn: list[dict[str, Any]], turn_index: int, total_turns: int) -> float:
    """Score a turn for quality-decay weighting.

    Returns a weight in (0, 1] combining:
    - Recency: exponential decay (half-life = _QUALITY_DECAY_HALF_LIFE turns)
    - Content signals: tool errors → boost, tool results → moderate boost

    Args:
        turn: Messages in this turn.
        turn_index: 0-based index from the END (0 = most recent).
        total_turns: Total number of turns.
    """
    import math

    # Recency weight: exponential decay from most recent
    recency = math.exp(-0.693 * turn_index / _QUALITY_DECAY_HALF_LIFE)

    # Content weight: boost turns with tool results or errors
    content_weight = 1.0
    for msg in turn:
        role = msg.get("role", "")
        content = msg.get("content", "") or ""
        if role == "tool":
            if content.startswith("{") and '"status"' in content[:100]:
                try:
                    import json
                    parsed = json.loads(content[:2000])
                    if isinstance(parsed, dict) and parsed.get("status") == "error":
                        content_weight = max(content_weight, 1.5)
                except Exception:
                    pass
            else:
                content_weight = max(content_weight, 1.2)
        elif role == "assistant" and msg.get("tool_calls"):
            content_weight = max(content_weight, 1.1)

    return recency * content_weight


def _select_by_token_budget(
    non_system: list[dict[str, Any]],
    config: CompactConfig,
    model_context_tokens: int | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Select messages: head (for summarization) vs recent (keep verbatim).

    Turn-level approach:
    - Build turns (user→assistant pairs + orphan messages)
    - Walk backwards turn-by-turn (never split a turn)
    - Fill the budget as fully as possible
    - tail_turns is a *minimum* guarantee (at least N turns in recent)

    When quality_decay=True:
    - Score each turn with recency × content weight
    - Prefer higher-scored turns within budget
    - Low-scored turns may be skipped even if they fit
    """
    if not non_system:
        return [], []

    # Resolve budget
    if config.preserve_recent_tokens is not None:
        budget = config.preserve_recent_tokens
    elif model_context_tokens is not None:
        usable = model_context_tokens - config.compaction_buffer_tokens
        budget = min(
            _MAX_PRESERVE_RECENT,
            max(_MIN_PRESERVE_RECENT, int(usable * 0.25)),
        )
    else:
        budget = _MAX_PRESERVE_RECENT

    # Build turns: atomic units for selection
    turns = _split_into_turns(non_system)
    turn_tokens = [_estimate_tokens(t) for t in turns]

    if config.quality_decay:
        recent_turn_indices = _select_quality_decay_turns(turns, turn_tokens, budget)
    else:
        recent_turn_indices = _select_backward_turns(turn_tokens, budget)

    # Ensure at least tail_turns full turns in recent
    recent_turn_indices = _ensure_tail_turns(recent_turn_indices, len(turns), config.tail_turns)

    # Convert turn indices to message indices
    recent_msg_indices: set[int] = set()
    for ti in recent_turn_indices:
        start = sum(len(turns[j]) for j in range(ti))
        for k in range(start, start + len(turns[ti])):
            recent_msg_indices.add(k)

    recent_start = min(recent_msg_indices) if recent_msg_indices else len(non_system)

    head = non_system[:recent_start]
    recent = non_system[recent_start:]

    return head, recent


def _select_backward_turns(turn_tokens: list[int], budget: int) -> list[int]:
    """Standard mode: keep turns from the end backwards until budget is full."""
    indices: list[int] = []
    remaining = budget
    for i in range(len(turn_tokens) - 1, -1, -1):
        if turn_tokens[i] <= remaining:
            indices.append(i)
            remaining -= turn_tokens[i]
        else:
            break
    indices.sort()
    return indices


def _select_quality_decay_turns(
    turns: list[Any], turn_tokens: list[int], budget: int
) -> list[int]:
    """Quality-decay mode: prefer higher-scored turns within the budget."""
    total_turns = len(turns)
    turn_scores = [_score_turn(turn, total_turns - 1 - i, total_turns)
                   for i, turn in enumerate(turns)]

    candidates: list[tuple[int, float]] = []
    remaining_budget = budget
    for i in range(total_turns - 1, -1, -1):
        if turn_tokens[i] <= remaining_budget:
            candidates.append((i, turn_scores[i]))
            remaining_budget -= turn_tokens[i]

    candidates.sort(key=lambda x: x[1], reverse=True)

    indices: list[int] = []
    remaining_budget = budget
    for idx, score in candidates:
        if turn_tokens[idx] <= remaining_budget:
            indices.append(idx)
            remaining_budget -= turn_tokens[idx]

    indices.sort()
    return indices


def _ensure_tail_turns(
    indices: list[int], turn_count: int, tail_turns: int
) -> list[int]:
    """Guarantee at least ``tail_turns`` full turns are kept in recent."""
    if len(indices) >= tail_turns:
        return indices
    needed = tail_turns - len(indices)
    for i in range(turn_count - 1, -1, -1):
        if i not in indices:
            indices.append(i)
            needed -= 1
            if needed == 0:
                break
    indices.sort()
    return indices


def _build_summary_prompt(
    conversation: str,
    previous_summary: str | None,
    template: str,
) -> str:
    """Build prompt for LLM summary with incremental update support."""
    if previous_summary:
        return (
            f"{conversation}\n\n"
            f"Update the anchored summary below using the conversation history above.\n"
            f"Preserve still-true details, remove stale details, and merge in the new facts.\n"
            f"<previous-summary>\n{previous_summary}\n</previous-summary>\n\n"
            f"{template}"
        )
    return f"{conversation}\n\nCreate a new anchored summary from the conversation history.\n\n{template}"


def _llm_summarize_v2(
    messages: list[dict[str, Any]],
    config: CompactConfig,
    model_context_tokens: int | None,
    model_max_output_tokens: int | None,
    llm_client: Any,
    previous_summary: str | None = None,
    session_id: str | None = None,
) -> tuple[list[dict[str, Any]], str, str] | None:
    """L4: LLM summarization with structured template + token budget.

    Key improvements:
    1. Token budget selection (not fixed message count)
    2. Structured Markdown template (opencode-style)
    3. Incremental summary update
    4. opencode-aligned:
       - Returns 3-tuple (messages, summary_text, recent_text)
       - recent_text is pre-serialized in compact (no recompute in loop)
       - max_tokens for LLM = min(model_max_output, config.summary_output_tokens)
         (opencode line 183: Math.min(output || SUMMARY_OUTPUT_TOKENS, SUMMARY_OUTPUT_TOKENS))
    """
    system_msgs = [m for m in messages if m.get("role") == "system"]
    non_system = [m for m in messages if m.get("role") != "system"]

    if len(non_system) <= config.tail_turns:
        return None

    # Token budget selection
    head, recent = _select_by_token_budget(non_system, config, model_context_tokens)

    if not head:
        return None

    # Serialize messages for LLM input
    serialized = [_serialize_message(m) for m in head]
    conversation = "\n\n".join(s for s in serialized if s)

    if not conversation.strip():
        return None

    # Build prompt with incremental update support
    template = config.summary_template or DEFAULT_SUMMARY_TEMPLATE
    prompt = _build_summary_prompt(conversation, previous_summary, template)

    # opencode formula: max_tokens = min(model_max_output, summary_output_tokens)
    # See packages/core/src/session/compaction.ts:183:
    #   const summaryOutput = Math.min(output || SUMMARY_OUTPUT_TOKENS, SUMMARY_OUTPUT_TOKENS)
    summary_max_tokens = config.summary_output_tokens
    if model_max_output_tokens is not None:
        summary_max_tokens = min(
            model_max_output_tokens, config.summary_output_tokens,
        )

    try:
        summary_response = llm_client.chat(
            [
                {"role": "system", "content": "You are a concise conversation summarizer for a quant research agent."},
                {"role": "user", "content": prompt},
            ],
            max_tokens=summary_max_tokens,
        )
        summary_text = summary_response.content or ""
        if not summary_text.strip():
            return None
    except Exception as exc:  # noqa: BLE001
        logger.debug("LLM summarize failed: %s", exc)
        return None

    # opencode-aligned: return (messages, summary_text, recent_text).
    # The returned messages do NOT contain an inline summary; the
    # caller persists the CompactionMessage separately and projects
    # it via to_llm_message() with <conversation-checkpoint> wrap.
    # This fixes the bug where the LLM sees the prior [context summary]
    # as a previous assistant turn and continues the summary task on
    # the next user message.
    #
    # recent_text is pre-serialized here in compact (single source of
    # truth for "what's recent"). The loop no longer recomputes it.
    recent_text = "\n\n".join(
        _serialize_message(m) for m in recent if m
    )

    # ── Safety check: ensure L4 result is usable ──
    # If new_messages is too short or has no user role, the LLM would
    # receive a context without user content — causing provider errors
    # like MiniMax 2013 "chat content is empty". Return None to let
    # the caller skip L4 and keep the original messages.
    new_messages = system_msgs + recent
    if len(new_messages) < config.l4_min_messages:
        logger.warning(
            "L4 produced too few messages (len=%d, min=%d), aborting compaction",
            len(new_messages), config.l4_min_messages,
        )
        _compaction_metrics["l4_aborts"] += 1
        return None
    if not any(m.get("role") == "user" for m in new_messages):
        logger.warning(
            "L4 produced messages without any user role, aborting compaction",
        )
        _compaction_metrics["l4_aborts"] += 1
        return None

    return new_messages, summary_text, recent_text


# ── L3: Hard Truncate (DEPRECATED, removed in Phase A) ────────────
# L3 was redundant: L4 summary + safety check with l4_min_messages
# already prevents hung-state. L3 dropped oldest messages, which
# could lose user context. Removed in commit A3.
# collapse_keep_recent config field kept (deprecated) for backward compat.


# ── Tool pair repair ─────────────────────────────────────────────

def _fix_tool_pairs(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Repair orphaned tool_call / tool_result pairs after compression."""
    assistant_call_ids: set[str] = set()
    result_ids: set[str] = set()

    for msg in messages:
        if msg.get("role") == "assistant":
            for tc in msg.get("tool_calls") or []:
                tc_id = tc.get("id") if isinstance(tc, dict) else None
                if tc_id:
                    assistant_call_ids.add(tc_id)
        elif msg.get("role") == "tool":
            tc_id = msg.get("tool_call_id")
            if tc_id:
                result_ids.add(tc_id)

    orphan_results = result_ids - assistant_call_ids
    orphan_calls = assistant_call_ids - result_ids

    if not orphan_results and not orphan_calls:
        return messages

    logger.debug(
        "fix_tool_pairs: removing %d orphan results, %d orphan calls",
        len(orphan_results), len(orphan_calls),
    )

    fixed: list[dict[str, Any]] = []
    for msg in messages:
        role = msg.get("role")

        if role == "tool":
            tc_id = msg.get("tool_call_id")
            if tc_id in orphan_results:
                continue
            fixed.append(msg)
            continue

        if role == "assistant" and orphan_calls:
            tool_calls = msg.get("tool_calls") or []
            if tool_calls:
                cleaned = [tc for tc in tool_calls if not (tc.get("id") in orphan_calls if isinstance(tc, dict) else False)]
                if cleaned:
                    msg = dict(msg, tool_calls=cleaned)
                elif not msg.get("content"):
                    continue

        fixed.append(msg)

    return fixed


# ── Main entry point ─────────────────────────────────────────────


def _resolve_threshold_tokens(
    config: CompactConfig,
    model_context_tokens: int | None,
    model_max_output_tokens: int | None,
) -> int:
    """Derive trigger threshold from model context (opencode-aligned).

    Opencode formula (packages/core/src/session/compaction.ts:225-235):
        trigger = context - max(model_max_output, buffer)

    We extend with explicit override: if config.threshold_tokens is
    set, use it as-is. Otherwise derive. Fall back to
    ``config.fallback_threshold_tokens`` when model context is unknown.
    """
    if config.threshold_tokens is not None:
        return config.threshold_tokens
    if model_context_tokens and model_context_tokens > 0:
        buffer = config.compaction_buffer_tokens
        output = model_max_output_tokens or buffer
        trigger = model_context_tokens - max(output, buffer)
        # Guard: trigger must be reasonable (at least fallback_threshold_tokens)
        return max(config.fallback_threshold_tokens, trigger)
    return config.fallback_threshold_tokens


def compact_messages(
    messages: list[dict[str, Any]],
    config: CompactConfig | None = None,
    threshold_tokens: int | None = None,
    model_context_tokens: int | None = None,
    model_max_output_tokens: int | None = None,
    llm_client: Any | None = None,
    previous_summary: str | None = None,
    session_id: str | None = None,
) -> tuple[list[dict[str, Any]], list[str], str | None, str | None]:
    """Apply context compression if over threshold (opencode-aligned L4-only).

    opencode-aligned (Phase A):
        - Single L4 layer (no L1/L3 pre/post processing)
        - Trigger derived from model context when threshold_tokens is None
        - Returns 4-tuple including recent_text pre-serialized by compact
        - threshold_tokens=0 still forces L4 to run (manual /compact)
        - L4 safety check (l4_min_messages) prevents hung-state

    L4 layer:
        1. Resolve threshold (opencode formula: context - max(output, buffer))
        2. Skip if under llm_summarize_ratio * threshold
        3. Run _llm_summarize_v2 with safety check
        4. Fix orphaned tool pairs (post-L4 repair)
        5. Return (messages, applied_layers, l4_summary, l4_recent)

    Args:
        messages: Conversation messages in OpenAI format.
        config: Compaction parameters (defaults to CompactConfig()).
        threshold_tokens: Absolute token budget for trigger. None =
            derive from model context via opencode formula. 0 = force L4.
        model_context_tokens: Model's context window size.
        model_max_output_tokens: Model's max output tokens (for
            summary cap formula).
        llm_client: LLM client for L4 summarization (sync .chat()).
        previous_summary: Previous summary text for incremental update.
        session_id: Session id (required for L4 to persist event).

    Returns:
        (compressed_messages, applied_layers, l4_summary_text, l4_recent_text)
        - l4_summary_text: L4 output, or None if L4 didn't run
        - l4_recent_text: pre-serialized recent messages for the LLM's
          <recent-context> section, or None if L4 didn't run.
          Caller persists both as a CompactionMessage event.
    """
    cfg = config or CompactConfig()
    applied: list[str] = []
    l4_summary_text: str | None = None
    l4_recent_text: str | None = None

    if not cfg.enabled:
        return messages, applied, l4_summary_text, l4_recent_text

    # Resolve threshold (opencode-style derivation)
    if threshold_tokens is None:
        threshold_tokens = _resolve_threshold_tokens(
            cfg, model_context_tokens, model_max_output_tokens,
        )

    tokens = _estimate_tokens(messages)

    # threshold_tokens=0 is a sentinel meaning "force L4 to run"
    # (used by manual /compact). Bypass ratio gating in force mode.
    force_l4 = threshold_tokens == 0
    l4_threshold = 0 if force_l4 else threshold_tokens * cfg.llm_summarize_ratio

    # Early exit: below L4 threshold (skip when force_l4)
    if not force_l4 and tokens < l4_threshold:
        return messages, applied, l4_summary_text, l4_recent_text

    # ── L4: LLM Summarize (opencode-aligned single layer) ─────
    if llm_client is not None:
        old_len = len(messages)
        l4_result = _llm_summarize_v2(
            messages, cfg, model_context_tokens, model_max_output_tokens,
            llm_client, previous_summary, session_id=session_id,
        )
        if l4_result is not None:
            new_messages, summary_text, recent_text = l4_result
            if len(new_messages) < old_len and summary_text.strip():
                messages = new_messages
                applied.append(f"llm_summarize({old_len}->{len(messages)})")
                l4_summary_text = summary_text
                l4_recent_text = recent_text

    # ── Fix orphaned tool pairs (post-L4 repair) ───────────────
    pre_fix_len = len(messages)
    messages = _fix_tool_pairs(messages)
    if len(messages) < pre_fix_len:
        applied.append(f"fix_pairs({pre_fix_len}->{len(messages)})")

    return messages, applied, l4_summary_text, l4_recent_text
