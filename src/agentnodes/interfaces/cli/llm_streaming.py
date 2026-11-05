"""LLM streaming bridge between :class:`ChatSession` and the Textual TUI.

Implements the TUI display philosophy (see ``docs/design/tui-display-philosophy.md``):

* **Principle 1 - process/record separation**: streaming tokens update
  the *process layer* (StreamingText, in-place replacement); the final
  text is written to the *record layer* (TranscriptView, append-once).
* **Principle 3 - in-place, not append**: each token delta calls
  ``app.update_streaming(full_text)`` which re-renders a single Static
  widget. No new lines are created during streaming.
* **Principle 4 - lifecycle**: thinking spinner -> streaming text ->
  transcript record, with explicit start/end transitions.

The module exposes a single public function:

>>> await stream_chat_to_tui(client, messages, *, app, ctx)
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, AsyncIterator, Iterable, List, Optional

from agentnodes.interfaces.cli.interactive.main import InteractiveContext
from agentnodes.interfaces.cli.tui.messages import WriteTranscript
from agentnodes.interfaces.cli.tui.widgets import TranscriptView
from agentnodes.foundation.llm.errors import LLMError
from agentnodes.foundation.llm.openai_client import OpenAICompatClient
from agentnodes.foundation.llm.parser import StreamChunk

logger = logging.getLogger(__name__)

_FLUSH_INTERVAL = 0.04  # 40ms between StreamingText re-renders


# ──────────────────────────────────────────────────────────────────────
# Streaming primitives
# ──────────────────────────────────────────────────────────────────────


def _chunks_to_messages(chunks: Iterable[StreamChunk]) -> tuple[str, int]:
    """Fold stream chunks into ``(full_content, char_count)``."""
    parts: List[str] = []
    char_count = 0
    for c in chunks:
        if c.delta_content:
            parts.append(c.delta_content)
            char_count += len(c.delta_content)
    return "".join(parts), char_count


def _build_messages(ctx: InteractiveContext) -> list[dict[str, Any]]:
    """Build a chat-completions messages list from :attr:`InteractiveContext.history`.

    Truncated to the most recent ~12 turns so the prompt stays small
    enough to fit a typical 8k context window with room for the answer.
    """
    out: list[dict[str, Any]] = []
    for turn in ctx.history[-12:]:
        role = turn.get("role")
        content = turn.get("content") or ""
        if not content.strip():
            continue
        if role in {"user", "assistant", "system"}:
            out.append({"role": role, "content": content})
    return out


# ──────────────────────────────────────────────────────────────────────
# Textual bridge
# ──────────────────────────────────────────────────────────────────────


async def stream_chat_to_tui(
    client: OpenAICompatClient,
    messages: list[dict[str, Any]],
    *,
    app: Any,
    ctx: Optional[InteractiveContext] = None,
) -> int:
    """Stream a chat completion into the bound TUI app.

    Lifecycle (philosophy principle 4):

    1. **thinking** - show ThinkingSpinner while waiting for first token.
    2. **streaming** - hide spinner, show StreamingText, update in-place
       on every token (principle 3).
    3. **done** - hide StreamingText, write full text to TranscriptView
       as a single record (principle 1 - record layer gets complete data).

    Falls back to sync ``client.stream()`` via a thread when the client
    has no ``astream``.

    Returns:
        ``0`` if the stream produced content, ``1`` if it raised.
    """
    write = _make_writer(app)

    start = time.perf_counter()
    _start_thinking(app)

    full_text = ""
    try:
        astream = getattr(client, "astream", None)
        if astream is not None:
            full_text = await _consume_async_stream(astream, messages, app)
        else:
            full_text, _ = await asyncio.to_thread(
                _consume_sync_stream, client, messages
            )
            _start_streaming(app)
            _update_streaming(app, full_text)
    except LLMError as exc:
        elapsed = time.perf_counter() - start
        _stop_thinking(app)
        write(f"[red]LLM error after {elapsed:.1f}s:[/red] {exc}")
        return 1
    except Exception as exc:  # noqa: BLE001
        elapsed = time.perf_counter() - start
        _stop_thinking(app)
        write(f"[red]unexpected error after {elapsed:.1f}s:[/red] {exc}")
        return 1

    elapsed = time.perf_counter() - start
    _stop_thinking(app)

    if full_text:
        suffix = f"[muted]({len(full_text)} chars \u00b7 {elapsed:.1f}s)[/muted]"
        _end_streaming(app, suffix)
    else:
        _end_streaming(app)
        write("[dim](empty response)[/dim]")

    if ctx is not None and full_text:
        ctx.history.append({"role": "assistant", "content": full_text})

    return 0


async def _consume_async_stream(astream, messages, app) -> str:
    """Drive ``client.astream()`` and update the StreamingText in-place.

    Tokens are accumulated into ``full_text`` and the StreamingText widget
    is re-rendered at most every ``_FLUSH_INTERVAL`` (40ms) to avoid
    flooding the Textual message queue (philosophy principle 3).

    The first token triggers the transition from ThinkingSpinner to
    StreamingText (principle 4).
    """
    full_text = ""
    last_flush = time.perf_counter()
    started = False

    async for chunk in astream(messages):
        if not chunk.delta_content:
            continue
        if not started:
            started = True
            _start_streaming(app)
        full_text += chunk.delta_content
        now = time.perf_counter()
        if (now - last_flush) >= _FLUSH_INTERVAL:
            _update_streaming(app, full_text)
            last_flush = now

    if started:
        _update_streaming(app, full_text)

    return full_text


# ──────────────────────────────────────────────────────────────────────
# App lifecycle helpers (thin wrappers that swallow errors)
# ──────────────────────────────────────────────────────────────────────


def _start_thinking(app: Any) -> None:
    try:
        app.start_thinking()
    except Exception:
        pass


def _stop_thinking(app: Any) -> None:
    try:
        app.stop_thinking()
    except Exception:
        pass


def _start_streaming(app: Any) -> None:
    try:
        app.start_streaming()
    except Exception:
        pass


def _update_streaming(app: Any, full_text: str) -> None:
    try:
        app.update_streaming(full_text)
    except Exception:
        pass


def _end_streaming(app: Any, suffix: str = "") -> None:
    try:
        app.end_streaming(suffix=suffix)
    except Exception:
        pass


def _make_writer(app: Any):
    """Build a callable that posts WriteTranscript messages to the TUI.

    Resolves the ``TranscriptView`` widget on every call so the writer
    works even when the chat session is created before ``on_mount``
    has finished running.
    """

    def _write(content: Any) -> None:
        try:
            tv = app.query_one(TranscriptView)
        except Exception:
            return
        try:
            tv.post_message(WriteTranscript(content=content))
        except Exception as exc:  # noqa: BLE001
            logger.warning("WriteTranscript post failed: %s", exc)

    return _write


def _consume_sync_stream(
    client: OpenAICompatClient,
    messages: list[dict[str, Any]],
) -> tuple[str, int]:
    """Drive the sync ``OpenAICompatClient.stream`` from a thread.

    The sync iterator yields :class:`StreamChunk` objects whose
    ``delta_content`` field is non-empty during token deltas. We fold
    them into one final string.

    This helper runs in ``asyncio.to_thread`` so it never blocks the
    Textual event loop.
    """
    parts: List[str] = []
    char_count = 0
    for chunk in client.stream(messages):
        if chunk.delta_content:
            parts.append(chunk.delta_content)
            char_count += len(chunk.delta_content)
    return "".join(parts), char_count


async def stream_chat_async(
    client: OpenAICompatClient,
    messages: list[dict[str, Any]],
) -> AsyncIterator[StreamChunk]:
    """Async version of the streaming bridge (used by tests + future HTTP/2 use).

    Yields each :class:`StreamChunk` as it arrives so callers can
    decide whether to flush to the transcript immediately or batch.
    """
    async for chunk in client.astream(messages):
        yield chunk


__all__ = [
    "stream_chat_to_tui",
    "stream_chat_async",
    "_build_messages",
]
