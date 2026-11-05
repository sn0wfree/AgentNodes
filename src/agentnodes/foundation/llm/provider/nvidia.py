"""NVIDIA NIM provider adapter.

NVIDIA NIM (https://integrate.api.nvidia.com) exposes an OpenAI
Chat Completions-compatible API, so this adapter subclasses
OpenAIAdapter and only overrides provider metadata, thinking-field
extraction (some NIM reasoning models use ``reasoning_content``
instead of ``reasoning``), and error mapping.

Thinking tokens: GLM-style models on NIM may surface reasoning via
either the standard ``reasoning`` field or DeepSeek-style
``reasoning_content``; we accept both.
"""

from __future__ import annotations

from typing import Any

from ..errors import LLMQuotaError
from .openai import OpenAIAdapter


class NvidiaAdapter(OpenAIAdapter):
    @property
    def name(self) -> str:
        return "nvidia"

    @property
    def default_base_url(self) -> str:
        return "https://integrate.api.nvidia.com/v1"

    @property
    def default_model(self) -> str:
        return "z-ai/glm-5.2"

    @property
    def default_max_tokens(self) -> int:
        return 16384

    @property
    def default_context_tokens(self) -> int:
        return 131072

    def _extract_reasoning(self, data: dict[str, Any]) -> str | None:
        for field in ("reasoning", "reasoning_content"):
            content = data.get(field)
            if content and isinstance(content, str):
                return self.normalize_thinking(content)
        return None

    def extract_thinking_from_delta(self, delta: dict[str, Any]) -> str | None:
        content = self._extract_reasoning(delta)
        if content is not None:
            return content
        return super().extract_thinking_from_delta(delta)

    def extract_thinking_from_message(self, message: dict[str, Any]) -> str | None:
        content = self._extract_reasoning(message)
        if content is not None:
            return content
        return super().extract_thinking_from_message(message)

    def handle_error(self, status: int, body: Any) -> Exception | None:
        # NIM signals quota/billing exhaustion with 402/403 (and 429 can
        # be either rate limit or quota).
        if status in (401, 402, 403, 429):
            error_code = self.extract_error_code(body)
            if status == 402 or "quota" in error_code or "billing" in error_code:
                return LLMQuotaError(f"quota exceeded ({status}): {body}")
        return None

    def quota_error_message(self) -> str:
        return "NVIDIA NIM 配额不足"
