"""Fallback adapter for unknown providers.

Returns safe defaults and ignores thinking tokens. Used when a provider
name is not registered in the adapter registry.

When thinking-like content IS detected, a warning is logged so users
know they should register the provider properly.
"""

from __future__ import annotations

import logging
from typing import Any

from .base import ProviderAdapter

logger = logging.getLogger(__name__)


class FallbackAdapter(ProviderAdapter):
    # Patterns used to detect thinking-like content in unparsed streams.
    THINKING_TAGS = ["<think>", "<reasoning>", "<|reasoning|>", "<|thinking|>"]
    THINKING_FIELDS = ["reasoning_content", "reasoning"]

    @property
    def name(self) -> str:
        return "fallback"

    @property
    def default_base_url(self) -> str:
        return ""

    @property
    def default_model(self) -> str:
        return ""

    def _detect_thinking(self, data: dict[str, Any]) -> list[str]:
        """Return list of thinking indicators present in data."""
        detected: list[str] = []
        content = str(data.get("content", ""))
        for tag in self.THINKING_TAGS:
            if tag in content:
                detected.append(f"tag:{tag}")
        for field in self.THINKING_FIELDS:
            value = data.get(field)
            if value:
                detected.append(f"field:{field}")
        return detected

    def _warn_unknown_provider(self, indicators: list[str], source: str) -> None:
        logger.warning(
            "FallbackAdapter: thinking tokens detected but no provider "
            "registered to handle them (source=%s, indicators=%s). "
            "Register the provider in provider/__init__.py to extract "
            "thinking tokens properly. The thinking content will be lost.",
            source,
            indicators,
        )

    def extract_thinking_from_delta(self, delta: dict[str, Any]) -> str | None:
        indicators = self._detect_thinking(delta)
        if indicators:
            self._warn_unknown_provider(indicators, "delta")
        return None

    def extract_thinking_from_message(self, message: dict[str, Any]) -> str | None:
        indicators = self._detect_thinking(message)
        if indicators:
            self._warn_unknown_provider(indicators, "message")
        return None
