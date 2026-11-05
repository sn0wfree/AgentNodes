"""Base interface for provider adapters.

Each LLM provider (OpenAI, DeepSeek, MiniMax, Qwen, Kimi, ...) gets its own
adapter that encapsulates ALL provider-specific behavior:
- Default endpoint, model, max_tokens
- Thinking/reasoning token extraction
- HTTP headers, payload modifications
- Error code parsing and exception mapping
- User-friendly error messages

Adding a new provider = create a new file in this directory + register it
in __init__.py. NO core file (parser.py, openai_client.py, loop.py) needs
to be modified.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from typing import Any


class ProviderAdapter(ABC):
    """Provider-specific behavior adapter."""

    # ── Metadata (replaces PROVIDER_DEFAULTS) ────────────────────

    @property
    @abstractmethod
    def name(self) -> str:
        """Provider identifier (e.g. 'openai', 'deepseek', 'minimax')."""
        ...

    @property
    @abstractmethod
    def default_base_url(self) -> str:
        """Default API endpoint."""
        ...

    @property
    @abstractmethod
    def default_model(self) -> str:
        """Default model name."""
        ...

    @property
    def default_max_tokens(self) -> int:
        """Default max output tokens."""
        return 8192

    @property
    def min_output_tokens(self) -> int:
        """Floor for ``max_tokens`` so thinking + answer aren't starved.

        Thinking models (DeepSeek-R1 / MiniMax-M3 / …) burn a large share
        of the output budget on reasoning before any user-visible tokens.
        A tiny ``max_tokens`` (e.g. 16) yields empty content. Override on
        those adapters with a safer floor.
        """
        return 256

    @property
    def default_context_tokens(self) -> int:
        """Default context window size (input + output).

        Used as the static fallback when models.dev is unreachable.
        Override per-provider for a more accurate value.
        """
        return 8192

    # ── Thinking Tokens ──────────────────────────────────────────

    @abstractmethod
    def extract_thinking_from_delta(self, delta: dict[str, Any]) -> str | None:
        """Extract thinking tokens from a streaming delta.

        Returns normalized thinking text, or None if no thinking present.
        """
        ...

    @abstractmethod
    def extract_thinking_from_message(self, message: dict[str, Any]) -> str | None:
        """Extract thinking tokens from a non-streaming message.

        Returns normalized thinking text, or None if no thinking present.
        """
        ...

    def sanitize_delta(self, delta: dict[str, Any]) -> dict[str, Any]:
        """Clean model-noise out of a streaming delta's text fields.

        Consolidated single hook replacing the former
        ``strip_thinking_from_delta`` / ``strip_dsml_from_delta``:
        providers that embed pseudo-markup in text fields (MiniMax
        ``<think>`` tags, DeepSeek DSML leakage) override this one
        method. Default: passthrough.

        NOTE for streaming overrides: keep chunk boundary whitespace —
        the BPE tokenizer encodes a leading space *inside* the token
        (``" me"``), and a per-chunk ``.strip()`` destroys it. Use
        ``strip_edges=False`` on the underlying strippers (see
        ``normalize_thinking``).
        """
        return dict(delta)

    def sanitize_message(self, message: dict[str, Any]) -> dict[str, Any]:
        """Message-level variant of :meth:`sanitize_delta`. Default: passthrough."""
        return dict(message)

    def fix_delta(self, delta: dict[str, Any]) -> dict[str, Any]:
        """Pipeline Step 1: stream-repair hook, runs on every chunk.

        Used by DeepSeek-family adapters for *cross-chunk* DSML block
        removal: streaming splits markup tags across BPE tokens, so
        per-chunk regex (``sanitize_delta``) can never match them. A
        stateful fixer (see ``_dsml_patterns.StreamDsmlFixer``) buffers
        partial markers and drops whole blocks across chunk boundaries.

        State lives on the adapter instance, which the client creates
        fresh per request — stream state is naturally request-scoped
        (no reset protocol needed). Default: passthrough.
        """
        return dict(delta)

    def normalize_thinking(self, text: str, strip_edges: bool = True) -> str:
        """Normalize thinking content to plain text.

        Strips markdown code blocks, inline code, bold/italic, normalizes
        whitespace. Override for provider-specific normalization needs.

        Args:
            strip_edges: when True (message path) trim leading/trailing
                whitespace; when False (streaming delta path) keep chunk
                boundary whitespace so BPE leading spaces survive.
        """
        text = re.sub(r"```[\s\S]*?```", "", text)
        text = re.sub(r"`[^`]+`", "", text)
        text = re.sub(r"\*{1,2}([^*]+)\*{1,2}", r"\1", text)
        text = re.sub(r"\s+", " ", text)
        if strip_edges:
            text = text.strip()
        return text

    # ── HTTP Layer ────────────────────────────────────────────────

    def custom_headers(self, config: Any) -> dict[str, str]:
        """Additional HTTP headers. Default: none beyond Bearer auth."""
        return {}

    def custom_payload(self, payload: dict[str, Any], config: Any) -> dict[str, Any]:
        """Modify request payload before sending. Default: passthrough."""
        return payload

    def custom_stream_options(self) -> dict[str, Any] | None:
        """Provider-specific stream_options field. None = don't send.

        OpenAI uses {"include_usage": True}. Other providers may differ.
        """
        return None

    # ── Error Handling ────────────────────────────────────────────

    def extract_error_code(self, body: Any) -> str:
        """Extract error code string from provider response body.

        Handles multiple JSON shapes:
            {"error": {"code": "..."}}
            {"error": "msg", "code": "..."}
            {"code": "..."}
        Returns empty string if no code found.
        """
        if not isinstance(body, dict):
            return ""
        error_section = body.get("error", {})
        if isinstance(error_section, dict) and error_section.get("code"):
            return str(error_section["code"]).lower()
        if isinstance(error_section, str) and error_section:
            return error_section.lower()
        if body.get("code"):
            return str(body["code"]).lower()
        return ""

    def handle_error(self, status: int, body: Any) -> Exception | None:
        """Map HTTP status + body to a provider-specific exception.

        Return None to use the default status-code mapping.
        Override for provider-specific error semantics (e.g. MiniMax 403).
        """
        return None

    def quota_error_message(self) -> str:
        """User-friendly quota error message."""
        return "quota exceeded"

    # ── UI / TUI ──────────────────────────────────────────────────

    def reasoning_tag_patterns(self) -> list[str]:
        """Regex patterns for reasoning tags used in TUI display.

        Used by text_filters.py to strip reasoning tags from displayed text.
        """
        return []
