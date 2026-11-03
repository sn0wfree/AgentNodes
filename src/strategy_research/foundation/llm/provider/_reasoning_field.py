"""Shared base for providers using the OpenAI-style `reasoning_content` field.

Providers that emit thinking/reasoning tokens via a dedicated field
(reasoning_content) — rather than embedding them inside content as XML tags
— share the same extraction logic. This base class encapsulates that logic.

Concrete subclasses (DeepSeek, Qwen, GLM, …) only need to provide:
- name (str)
- default_base_url (str)
- default_model (str)
- (optional) default_max_tokens override
- (optional) custom_stream_options, custom_headers, custom_payload, handle_error

If a provider ever needs different extraction semantics, override
``extract_thinking_from_delta`` / ``extract_thinking_from_message`` directly.
"""

from __future__ import annotations

from typing import Any

from .base import ProviderAdapter


class OpenAIReasoningFieldAdapter(ProviderAdapter):
    """Base class for providers using ``reasoning_content`` field for thinking.

    Subclasses must still implement ``name``, ``default_base_url``,
    ``default_model`` (these remain abstract on ``ProviderAdapter``).
    """

    def extract_thinking_from_delta(self, delta: dict[str, Any]) -> str | None:
        """Extract thinking from ``reasoning_content`` field if present.

        ``strip_edges=False`` is critical: DeepSeek-family streams one
        BPE token per SSE chunk and encodes the leading space inside the
        token (``" me"``). A per-chunk ``.strip()`` would delete that
        space and concatenate words into ``Letmeexplore``.
        """
        content = delta.get("reasoning_content")
        if content:
            return self.normalize_thinking(content, strip_edges=False)
        return None

    def extract_thinking_from_message(self, message: dict[str, Any]) -> str | None:
        """Extract thinking from ``reasoning_content`` field if present."""
        content = message.get("reasoning_content")
        if content:
            return self.normalize_thinking(content)
        return None
