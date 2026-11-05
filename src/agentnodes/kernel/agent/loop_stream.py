"""W-split: LLM streaming and tool-call accumulation (from AgentLoop)."""
from __future__ import annotations

from typing import Any

import asyncio
import logging
import uuid
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

class LoopStreamMixin:
# ── Host surface (provided by AgentLoop / AutoresearchRunner) ──
    _effective_tools: Any
    _emit: Any
    client: Any

    def _stream_chat(self, messages: list[dict[str, Any]], iteration: int) -> Any:
        """Stream chat completion and emit text_delta events.

        Thinking/reasoning tokens are already extracted by the provider
        adapter into chunk.delta_thinking — we just forward them as
        thinking_delta SSE events.

        Text streaming uses an opencode-style 3-step protocol keyed by a
        per-iteration text_id:
            text.started { text_id }
            text_delta   { text_id, text }
            text.ended   { text_id, text }

        Each LLM iteration gets a fresh text_id; consumers can route
        deltas to the correct text part via findLast by id. This fixes
        the bug where text_delta streamed after a tool_call was appended
        to the FIRST text part of the message instead of a new one.

        Returns an LLMResponse-like object with content and tool_calls.
        """
        text_id = str(uuid.uuid4())
        # thinking events share the text protocol's id discipline: a
        # per-iteration think_id lets the frontend dedupe SSE replays and
        # route deltas to the exact block instead of "last one wins".
        think_id = str(uuid.uuid4())
        # thinking_start BEFORE text.started: both the frontend parts
        # array and the projector persist parts in event order, so the
        # thinking block must be created first to render above the text
        # body (both live and after refresh).
        self._emit("thinking_start", {"think_id": think_id})
        self._emit("text.started", {"text_id": text_id})
        full_content = ""
        accumulated_tool_calls: list[dict[str, Any]] = []
        usage: dict[str, int] | None = None

        tools = self._effective_tools(iteration)
        try:
            for chunk in self.client.stream(messages, tools=tools):
                # Thinking tokens (extracted by provider adapter). The raw
                # pre-cleanup text travels alongside so event_log keeps the
                # model's original output (raw) while consumers read the
                # cleaned delta.
                if chunk.delta_thinking:
                    self._emit("thinking_delta", {
                        "delta": chunk.delta_thinking,
                        "think_id": think_id,
                        "raw": chunk.raw_thinking,
                    })

                if chunk.delta_content:
                    if full_content == "" and chunk.delta_content:
                        # Transition: thinking_done before first text token
                        self._emit("thinking_done", {"think_id": think_id})
                    full_content += chunk.delta_content
                    self._emit("text_delta", {
                        "text": chunk.delta_content,
                        "text_id": text_id,
                        "raw": chunk.raw_content,
                    })

                if chunk.delta_tool_calls:
                    for tc_delta in chunk.delta_tool_calls:
                        self._accumulate_tool_call(accumulated_tool_calls, tc_delta)

                if chunk.usage:
                    # Keep only the final usage snapshot; emitting per chunk
                    # floods the SSE channel and double-counts input tokens
                    # downstream (mirrors the async path).
                    usage = chunk.usage

                if chunk.finish_reason:
                    break
        except Exception:  # noqa: BLE001
            self._emit("thinking_end", {"think_id": think_id})
            self._emit("text.ended", {"text_id": text_id, "text": full_content})
            raise

        self._emit("thinking_end", {"think_id": think_id})
        self._emit("text.ended", {"text_id": text_id, "text": full_content})
        # Single llm_usage at call end (not per chunk) — see _astream_chat.
        if usage:
            self._emit("llm_usage", usage)

        # Convert accumulated tool_calls to LLMResponse format
        raw_response: dict[str, Any] = {
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": full_content,
                    "tool_calls": [
                        {
                            "id": tc["id"],
                            "type": tc["type"],
                            "function": tc["function"],
                        }
                        for tc in accumulated_tool_calls if tc["function"]["name"]
                    ] or None,
                },
                "finish_reason": "stop" if not accumulated_tool_calls else "tool_calls",
            }]
        }
        if usage:
            raw_response["usage"] = usage

        return self.client.parse_response(raw_response)

    @staticmethod
    def _accumulate_tool_call(
        accumulated: list[dict[str, Any]], tc_delta: dict[str, Any]
    ) -> None:
        """Merge a streaming tool-call delta into the accumulated list."""
        idx = tc_delta.get("index", 0)
        while len(accumulated) <= idx:
            accumulated.append({
                "id": tc_delta.get("id", ""),
                "type": "function",
                "function": {"name": "", "arguments": ""},
            })
        tc = accumulated[idx]
        if tc_delta.get("id"):
            tc["id"] = tc_delta["id"]
        if tc_delta.get("type"):
            tc["type"] = tc_delta["type"]
        func_delta = tc_delta.get("function", {})
        if func_delta.get("name"):
            tc["function"]["name"] = func_delta["name"]
        if func_delta.get("arguments"):
            tc["function"]["arguments"] += func_delta["arguments"]

    # ── Async helpers ─────────────────────────────
    async def _astream_chat(self, messages: list[dict[str, Any]], iteration: int) -> Any:
        """Async version of _stream_chat using client.astream().

        Uses the same 3-step text protocol as _stream_chat (text.started /
        text_delta{text_id} / text.ended). Each async LLM iteration gets
        a fresh text_id so the frontend can route deltas correctly even
        when text and tool calls interleave.
        """
        text_id = str(uuid.uuid4())
        # Per-iteration think_id — same rationale as _stream_chat.
        think_id = str(uuid.uuid4())
        # thinking_start BEFORE text.started: both the frontend parts
        # array and the projector persist parts in event order, so the
        # thinking block must be created first to render above the text
        # body (both live and after refresh).
        self._emit("thinking_start", {"think_id": think_id})
        self._emit("text.started", {"text_id": text_id})
        full_content = ""
        accumulated_tool_calls: list[dict[str, Any]] = []
        usage: dict[str, int] | None = None

        tools = self._effective_tools(iteration)
        try:
            chunk_count = 0
            async for chunk in self.client.astream(messages, tools=tools):
                chunk_count += 1
                if chunk_count <= 3:
                    logger.debug(
                        "[DIAG] _astream_chat chunk#%d: delta_content=%.100r "
                        "delta_thinking=%.100r finish_reason=%r tool_calls=%d usage=%r",
                        chunk_count,
                        chunk.delta_content[:100] if chunk.delta_content else "",
                        chunk.delta_thinking[:100] if chunk.delta_thinking else "",
                        chunk.finish_reason,
                        len(chunk.delta_tool_calls),
                        chunk.usage,
                    )
                if chunk.delta_thinking:
                    self._emit("thinking_delta", {
                        "delta": chunk.delta_thinking,
                        "think_id": think_id,
                        "raw": chunk.raw_thinking,
                    })
                    # 让出 event loop，让前端逐字看到 thinking
                    await asyncio.sleep(0)

                if chunk.delta_content:
                    if full_content == "":
                        self._emit("thinking_done", {"think_id": think_id})
                    full_content += chunk.delta_content
                    self._emit("text_delta", {
                        "text": chunk.delta_content,
                        "text_id": text_id,
                        "raw": chunk.raw_content,
                    })
                    # 强制让出 event loop，让 SSE _event_generator 有机会
                    # 逐个 yield text_delta（避免 async for 连续处理多个
                    # chunk 导致 _event_generator 批量 yield → "一段一段"）
                    await asyncio.sleep(0)

                if chunk.delta_tool_calls:
                    for tc_delta in chunk.delta_tool_calls:
                        self._accumulate_tool_call(accumulated_tool_calls, tc_delta)

                if chunk.usage:
                    usage = chunk.usage

                if chunk.finish_reason:
                    break
        except Exception:  # noqa: BLE001
            self._emit("thinking_end", {"think_id": think_id})
            self._emit("text.ended", {"text_id": text_id, "text": full_content})
            raise

        self._emit("thinking_end", {"think_id": think_id})
        self._emit("text.ended", {"text_id": text_id, "text": full_content})
        # llm_usage 只在 LLM call 结束时 emit 一次（而非每 chunk）。
        # 每 chunk emit 会导致 event_callback 再 emit session_total_tokens，
        # 3 倍事件洪流淹没 text_delta，且 input_tokens 会被错误累加。
        if usage:
            self._emit("llm_usage", usage)

        raw_response: dict[str, Any] = {
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": full_content,
                    "tool_calls": [
                        {
                            "id": tc["id"],
                            "type": tc["type"],
                            "function": tc["function"],
                        }
                        for tc in accumulated_tool_calls if tc["function"]["name"]
                    ] or None,
                },
                "finish_reason": "stop" if not accumulated_tool_calls else "tool_calls",
            }]
        }
        if usage:
            raw_response["usage"] = usage

        logger.debug(
            "[DIAG] _astream_chat final: full_content=%.200r tool_calls=%d usage=%r",
            full_content[:200] if full_content else "",
            len(accumulated_tool_calls),
            usage,
        )

        return self.client.parse_response(raw_response)
