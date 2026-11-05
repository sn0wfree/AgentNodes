"""Parser for OpenAI-compatible Chat Completions responses.

Handles:
    - Standard {role, content} messages
    - Tool calls (parsed with 4-layer degradation)
    - Streaming chunks (SSE deltas)
    - Usage accounting
    - Finish reasons (stop / tool_calls / length / content_filter)

Provider-specific quirks are NOT handled here — callers should normalize
upstream responses to the OpenAI Chat Completions shape before parsing.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from .errors import LLMMalformedResponseError

logger = logging.getLogger(__name__)


# ── Data classes ────────────────────────────────────────────────────


@dataclass
class ToolCall:
    """A single tool invocation request from the LLM."""

    id: str
    name: str
    arguments: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name, "arguments": dict(self.arguments)}


@dataclass
class LLMResponse:
    """Parsed non-streaming chat completion response."""

    content: str = ""
    reasoning_content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str = "stop"               # stop | tool_calls | length | content_filter
    usage: dict[str, int] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)  # full original response

    def has_tool_calls(self) -> bool:
        return len(self.tool_calls) > 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "content": self.content,
            "reasoning_content": self.reasoning_content,
            "tool_calls": [tc.to_dict() for tc in self.tool_calls],
            "finish_reason": self.finish_reason,
            "usage": dict(self.usage),
        }


@dataclass
class StreamChunk:
    """One chunk from a streaming response.

    In OpenAI's SSE protocol, content arrives as delta strings and tool_calls
    arrive incrementally (arguments may span multiple chunks).

    Thinking/reasoning tokens are extracted by the provider adapter and
    surfaced via delta_thinking — see provider/registry for details.
    """

    delta_content: str = ""
    delta_thinking: str = ""
    delta_tool_calls: list[dict[str, Any]] = field(default_factory=list)
    finish_reason: str | None = None
    usage: dict[str, int] | None = None  # only in final chunk (stream_options)
    # Raw (pre-cleanup) text, captured before the pipeline's fix_delta /
    # sanitize hooks. Persisted to event_log alongside the cleaned
    # delta so the model's original output is always recoverable.
    raw_content: str = ""
    raw_thinking: str = ""


@dataclass
class ProcessedDelta:
    """Pipeline result for a single streaming delta (text fields only).

    Produced by ``_process_delta`` after Step 1 (``fix_delta``),
    Step 3 (``extract_thinking``) and Step 2 (``sanitize_delta``).
    tool_calls / finish_reason / usage never pass through provider
    hooks — they are assembled directly from the raw payload.

    ``raw_*`` fields carry the pre-cleanup field values (captured
    before ``fix_delta``) so callers can persist the original output.
    """

    content: str = ""
    thinking: str = ""
    raw_content: str = ""
    raw_thinking: str = ""


@dataclass
class ProcessedMessage:
    """Pipeline result for a non-streaming message (text fields only).

    Produced by ``_process_message`` after Step 2 (``sanitize_message``)
    and Step 3 (``extract_thinking_from_message``).
    """

    content: str = ""
    reasoning_content: str = ""


# ── Response parsing ────────────────────────────────────────────────


def _resolve_adapter(adapter: Any) -> Any:
    """Return the adapter, falling back to FallbackAdapter when None."""
    if adapter is None:
        from .provider import get_provider

        return get_provider(None)
    return adapter


def _process_message(message: dict[str, Any], adapter: Any) -> ProcessedMessage:
    """Pipeline Step 3-2 (message path): extract thinking → sanitize content.

    Order matters: ``extract_thinking_from_message`` must read the
    *raw* fields (MiniMax finds its ``<think>`` tags in the original
    ``content``; DeepSeek strips DSML inside its own extract hook),
    then ``sanitize_message`` removes the tags/markup from the content
    actually delivered to the user.

    Step 1 (``fix_delta``) is streaming-only and intentionally skipped
    here — non-streaming responses carry complete text, no boundary
    whitespace to repair.
    """
    adapter = _resolve_adapter(adapter)
    reasoning = adapter.extract_thinking_from_message(message) or ""
    stripped = adapter.sanitize_message(message)
    content = stripped.get("content") or ""
    return ProcessedMessage(content=content, reasoning_content=reasoning)


def parse_chat_response(
    raw: dict[str, Any],
    adapter: Any = None,
) -> LLMResponse:
    """Parse a complete Chat Completions response.

    Args:
        raw: The parsed JSON response dict.
        adapter: ProviderAdapter for thinking extraction / sanitization.
            None = FallbackAdapter (no provider-specific handling).

    Returns:
        LLMResponse with content, tool_calls, finish_reason, usage, reasoning_content.

    Raises:
        LLMMalformedResponseError: If structure is unexpected.
    """
    if not isinstance(raw, dict):
        raise LLMMalformedResponseError(
            f"response is not a dict: {type(raw).__name__}"
        )

    choices = raw.get("choices")
    if not isinstance(choices, list) or len(choices) == 0:
        raise LLMMalformedResponseError(
            f"missing 'choices' array (got {type(choices).__name__})"
        )

    first = choices[0]
    if not isinstance(first, dict):
        raise LLMMalformedResponseError("choice[0] is not a dict")

    message = first.get("message", {})
    if not isinstance(message, dict):
        raise LLMMalformedResponseError("choice[0].message is not a dict")

    finish_reason = first.get("finish_reason") or "stop"

    # Pipeline: sanitize → extract (thinking + <think> tag stripping)
    processed = _process_message(message, adapter)
    content = processed.content
    reasoning_content = processed.reasoning_content

    # Parse tool_calls
    raw_tool_calls = message.get("tool_calls") or []
    tool_calls: list[ToolCall] = []
    if isinstance(raw_tool_calls, list):
        for tc in raw_tool_calls:
            if not isinstance(tc, dict):
                continue
            tc_id = tc.get("id", "")
            function = tc.get("function") or {}
            if not isinstance(function, dict):
                continue
            tc_name = function.get("name", "")
            raw_args = function.get("arguments", "")
            arguments = parse_tool_arguments(raw_args)
            tool_calls.append(ToolCall(id=tc_id, name=tc_name, arguments=arguments))
    elif raw_tool_calls:  # non-empty but wrong type
        logger.warning("tool_calls field is not a list: %s", type(raw_tool_calls).__name__)

    # Parse usage
    usage = raw.get("usage") or {}
    if not isinstance(usage, dict):
        usage = {}
    usage_clean: dict[str, int] = {}
    for k, v in usage.items():
        if isinstance(v, (int, float)):
            usage_clean[k] = int(v)

    return LLMResponse(
        content=content,
        reasoning_content=reasoning_content,
        tool_calls=tool_calls,
        finish_reason=str(finish_reason),
        usage=usage_clean,
        raw=raw,
    )


# ── Tool argument parsing (4-layer degradation) ──────────────────────


def parse_tool_arguments(
    raw_args: str | Any,
    schema: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Parse tool call arguments with 4-layer degradation.

    Layers:
        1. Strict JSON parse
        2. Repair (trailing comma, single quotes, markdown fence)
        3. Regex field extraction (requires schema)
        4. Return {} (never raises)

    Args:
        raw_args: String from tool_call.function.arguments (or already-parsed dict).
        schema: Optional field schema for Layer 3 regex extraction.
                e.g. {"name": "string", "count": "number"}

    Returns:
        Parsed dict. Returns {} if all layers fail.
    """
    from agentnodes.foundation.structured_output import get_parser

    result = get_parser().parse(raw_args, schema)
    return result.data or {}


# ── SSE stream parsing ──────────────────────────────────────────────


def parse_stream_chunk(raw_line: str, adapter: Any = None) -> StreamChunk | None:
    """Parse one SSE line into a StreamChunk.

    Format (OpenAI):
        data: {json}
        data: [DONE]

    Args:
        raw_line: SSE line string.
        adapter: ProviderAdapter handling the per-chunk pipeline
            (fix_delta → sanitize_delta → extract_thinking). None =
            FallbackAdapter. Pass the *same* adapter instance across
            the whole stream so Step 1's reserved stream-repair hook
            can hold cross-chunk state.

    Returns None for empty lines or the [DONE] sentinel.
    """
    line = raw_line.strip()
    if not line:
        return None
    if line == "data: [DONE]":
        return StreamChunk(finish_reason="stop")
    if not line.startswith("data: "):
        return None

    payload_str = line[len("data: "):].strip()
    if not payload_str:
        return None
    try:
        payload = json.loads(payload_str)
    except json.JSONDecodeError:
        logger.warning("malformed SSE payload: %r", payload_str[:80])
        return None

    return _chunk_from_dict(payload, adapter)


def _chunk_from_dict(
    payload: dict[str, Any],
    adapter: Any = None,
) -> StreamChunk | None:
    """Convert a chunk payload dict to StreamChunk.

    Runs the standardized 4-step pipeline on the delta:

        Step 1: adapter.fix_delta          (reserved stream-repair hook)
        Step 2: adapter.sanitize_delta     (DSML / <think> noise removal)
        Step 3: adapter.extract_thinking   (reasoning_content extraction)
        Step 4: assemble StreamChunk       (framework-only, no adapter)

    ``tool_calls`` / ``finish_reason`` / ``usage`` never pass through
    provider hooks — assembled directly.
    """
    if not isinstance(payload, dict):
        return None

    choices = payload.get("choices")
    usage = payload.get("usage")
    usage_clean: dict[str, int] | None = None
    if isinstance(usage, dict):
        usage_clean = {k: int(v) for k, v in usage.items() if isinstance(v, (int, float))}

    if not isinstance(choices, list) or len(choices) == 0:
        # Some providers send usage-only chunks at end
        return StreamChunk(usage=usage_clean)

    first = choices[0]
    if not isinstance(first, dict):
        return None

    delta = first.get("delta") or {}
    if not isinstance(delta, dict):
        return StreamChunk(usage=usage_clean)

    finish_reason = first.get("finish_reason")
    raw_dtc = delta.get("tool_calls")
    delta_tool_calls: list[dict[str, Any]] = []
    if isinstance(raw_dtc, list):
        for dtc in raw_dtc:
            if isinstance(dtc, dict):
                delta_tool_calls.append(dtc)

    # Pipeline Steps 1, 3, 2 (text fields only)
    processed = _process_delta(delta, adapter)

    logger.debug(
        "[DIAG] _chunk_from_dict: content=%.100r reasoning=%.100r "
        "finish_reason=%s tool_calls=%d",
        processed.content[:100],
        processed.thinking[:100],
        finish_reason,
        len(delta_tool_calls),
    )

    return StreamChunk(
        delta_content=processed.content,
        delta_thinking=processed.thinking,
        raw_content=processed.raw_content,
        raw_thinking=processed.raw_thinking,
        delta_tool_calls=delta_tool_calls,
        finish_reason=str(finish_reason) if finish_reason else None,
        usage=usage_clean,
    )


def _process_delta(delta: dict[str, Any], adapter: Any = None) -> ProcessedDelta:
    """Pipeline Steps 1, 3, 2 (streaming path).

    Order is fixed by the framework:

        1. fix_delta           — reserved stream-repair hook (no-op)
        3. extract_thinking    — reasoning extraction from the *raw*
                                 fields (MiniMax needs the original
                                 ``<think>`` tags; DeepSeek strips DSML
                                 inside its extract hook)
        2. sanitize_delta      — remove tags/markup from the content
                                 actually delivered to the user

    Extract runs before sanitize: sanitize deletes markup (e.g. MiniMax
    ``<think>``) that extraction relies on.

    The pre-cleanup field values are captured *before* ``fix_delta`` so
    callers can persist the model's original output (raw_* fields).
    """
    adapter = _resolve_adapter(adapter)
    raw_content = delta.get("content") or ""
    raw_thinking = delta.get("reasoning_content") or ""
    # Step 1: reserved stream-repair hook (default passthrough).
    delta = adapter.fix_delta(delta)
    # Step 3: extract reasoning tokens (reads raw reasoning_content).
    thinking = adapter.extract_thinking_from_delta(delta) or ""
    # Step 2: sanitize model noise out of the delivered text fields.
    delta = adapter.sanitize_delta(delta)
    content = delta.get("content") or ""
    return ProcessedDelta(
        content=content,
        thinking=thinking,
        raw_content=raw_content,
        raw_thinking=raw_thinking,
    )
