"""Tests for MiniMax provider adapter error handling.

Verifies that MiniMax-specific error codes (2013, 429 quota, 403 quota)
are correctly mapped to appropriate LLMError subclasses.
"""

from __future__ import annotations

import pytest

from strategy_research.foundation.llm.errors import LLMConfigError, LLMQuotaError
from strategy_research.foundation.llm.provider import get_provider


@pytest.fixture
def minimax_adapter():
    return get_provider("minimax")


class TestMinimaxHandleError:
    def test_2013_chat_content_empty_maps_to_config_error(self, minimax_adapter):
        """MiniMax 400 with code 2013 → LLMConfigError (no stream fallback)."""
        body = {
            "type": "bad_request_error",
            "error": {
                "code": "2013",
                "message": "invalid params, chat content is empty (2013)",
            },
        }
        exc = minimax_adapter.handle_error(400, body)
        assert exc is not None
        assert isinstance(exc, LLMConfigError)
        assert "2013" in str(exc)
        assert "chat content is empty" in str(exc).lower()

    def test_2013_in_body_string_maps_to_config_error(self, minimax_adapter):
        """2013 anywhere in body → LLMConfigError."""
        body = "Error 2013: chat content is empty"
        exc = minimax_adapter.handle_error(400, body)
        assert exc is not None
        assert isinstance(exc, LLMConfigError)

    def test_403_quota_maps_to_quota_error(self, minimax_adapter):
        """MiniMax 403 with quota code → LLMQuotaError."""
        body = {"error": {"code": "quota_exceeded", "message": "balance 0"}}
        exc = minimax_adapter.handle_error(403, body)
        assert isinstance(exc, LLMQuotaError)

    def test_429_quota_maps_to_quota_error(self, minimax_adapter):
        """MiniMax 429 with quota code → LLMQuotaError."""
        body = {"error": {"code": "quota_exceeded"}}
        exc = minimax_adapter.handle_error(429, body)
        assert isinstance(exc, LLMQuotaError)

    def test_429_without_quota_returns_none(self, minimax_adapter):
        """MiniMax 429 (rate limit) without quota code → handled by default path."""
        body = {"error": {"message": "rate limited"}}
        exc = minimax_adapter.handle_error(429, body)
        # Adapter doesn't handle 429 rate limits — default path raises LLMRateLimitError
        assert exc is None

    def test_400_without_2013_returns_none(self, minimax_adapter):
        """MiniMax 400 without 2013 → handled by default path."""
        body = {"error": {"message": "malformed request"}}
        exc = minimax_adapter.handle_error(400, body)
        # Adapter doesn't handle generic 400 — default path raises LLMError
        assert exc is None

    def test_500_returns_none(self, minimax_adapter):
        """5xx errors → default path (raise LLMServerError)."""
        body = {"error": {"message": "internal"}}
        exc = minimax_adapter.handle_error(500, body)
        assert exc is None


class TestMinimaxConfigError:
    """Verify LLMConfigError is recognized by _is_stream_required_error."""

    def test_llm_config_error_in_stream_required_list(self):
        """LLMConfigError is a stream-required error (no stream→achat fallback)."""
        from strategy_research.core.agent.loop import AgentLoop
        from strategy_research.foundation.llm.errors import LLMConfigError

        # _is_stream_required_error is a static method, test it directly
        exc = LLMConfigError("test")
        is_required = AgentLoop._is_stream_required_error(exc)
        # LLMConfigError is in the required list — no fallback
        assert is_required is True
