"""Unit tests for NvidiaAdapter — dual reasoning fields + NIM error mapping."""
from __future__ import annotations

import pytest

from strategy_research.foundation.llm.errors import LLMQuotaError
from strategy_research.foundation.llm.provider import get_provider
from strategy_research.foundation.llm.provider.nvidia import NvidiaAdapter


@pytest.fixture
def adapter() -> NvidiaAdapter:
    return NvidiaAdapter()


class TestMetadata:
    def test_registered_under_nvidia(self):
        p = get_provider("nvidia")
        assert isinstance(p, NvidiaAdapter)

    def test_defaults(self, adapter):
        assert adapter.name == "nvidia"
        assert "nvidia" in adapter.default_base_url
        assert adapter.default_model
        assert adapter.default_context_tokens == 131072


class TestDualReasoningFields:
    def test_reasoning_field(self, adapter):
        out = adapter.extract_thinking_from_delta({"reasoning": "plan A"})
        assert out is not None
        assert "plan A" in out

    def test_reasoning_content_field(self, adapter):
        out = adapter.extract_thinking_from_delta({"reasoning_content": "plan B"})
        assert out is not None
        assert "plan B" in out

    def test_reasoning_wins_when_both_present(self, adapter):
        out = adapter.extract_thinking_from_delta(
            {"reasoning": "first", "reasoning_content": "second"}
        )
        assert out is not None
        assert "first" in out

    def test_message_reads_both_fields(self, adapter):
        assert adapter.extract_thinking_from_message({"reasoning": "x"}) is not None
        assert adapter.extract_thinking_from_message({"reasoning_content": "y"}) is not None

    def test_neither_field_returns_none(self, adapter):
        assert adapter.extract_thinking_from_delta({"content": "plain"}) is None
        assert adapter.extract_thinking_from_message({"content": "plain"}) is None

    def test_non_string_reasoning_ignored(self, adapter):
        assert adapter.extract_thinking_from_delta({"reasoning": 123}) is None


class TestHandleError:
    def test_402_is_quota(self, adapter):
        err = adapter.handle_error(402, {"error": {"code": "billing"}})
        assert isinstance(err, LLMQuotaError)

    def test_403_with_quota_code(self, adapter):
        err = adapter.handle_error(403, {"error": {"code": "quota_exceeded"}})
        assert isinstance(err, LLMQuotaError)

    def test_401_plain_returns_none(self, adapter):
        assert adapter.handle_error(401, {"error": {"code": "bad_key"}}) is None

    def test_500_returns_none(self, adapter):
        assert adapter.handle_error(500, {"error": {}}) is None

    def test_quota_message_localized(self, adapter):
        assert "NVIDIA" in adapter.quota_error_message() or "NIM" in adapter.quota_error_message()
