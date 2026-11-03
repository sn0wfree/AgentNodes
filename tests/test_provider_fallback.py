"""Unit tests for llm.provider.fallback.FallbackAdapter."""
from __future__ import annotations

import pytest

from strategy_research.foundation.llm.provider.fallback import FallbackAdapter


@pytest.fixture
def adapter() -> FallbackAdapter:
    return FallbackAdapter()


class TestMetadata:
    def test_name(self, adapter):
        assert adapter.name == "fallback"

    def test_defaults_empty(self, adapter):
        assert adapter.default_base_url == ""
        assert adapter.default_model == ""


class TestExtractThinkingFromDelta:
    def test_plain_content_returns_none(self, adapter):
        assert adapter.extract_thinking_from_delta({"content": "hello"}) is None

    def test_empty_delta_returns_none(self, adapter):
        assert adapter.extract_thinking_from_delta({}) is None

    @pytest.mark.parametrize("tag", FallbackAdapter.THINKING_TAGS)
    def test_detects_thinking_tags(self, adapter, tag):
        out = adapter.extract_thinking_from_delta({"content": f"{tag} secret {tag}"})
        assert out is None  # always discarded

    @pytest.mark.parametrize("field", FallbackAdapter.THINKING_FIELDS)
    def test_detects_thinking_fields(self, adapter, field):
        out = adapter.extract_thinking_from_delta({field: "thinking hard", "content": "x"})
        assert out is None

    def test_warns_when_thinking_detected(self, adapter, caplog):
        with caplog.at_level("WARNING"):
            adapter.extract_thinking_from_delta({"content": "<think>hidden</think>"})
        assert any("FallbackAdapter" in r.message for r in caplog.records)


class TestExtractThinkingFromMessage:
    def test_plain_message_returns_none(self, adapter):
        assert adapter.extract_thinking_from_message({"content": "ok"}) is None

    def test_tag_in_message_still_none(self, adapter):
        out = adapter.extract_thinking_from_message({"content": "<|thinking|>x"})
        assert out is None

    def test_warns_with_message_source(self, adapter, caplog):
        with caplog.at_level("WARNING"):
            adapter.extract_thinking_from_message({"reasoning": "why"})
        assert any("message" in r.message for r in caplog.records)


class TestDetectThinking:
    def test_no_indicators(self, adapter):
        assert adapter._detect_thinking({"content": "clean"}) == []

    def test_multiple_indicators(self, adapter):
        data = {"content": "<think>x</think>", "reasoning": "y"}
        detected = adapter._detect_thinking(data)
        assert any(d.startswith("tag:") for d in detected)
        assert any(d.startswith("field:reasoning") for d in detected)

    def test_none_content_safe(self, adapter):
        assert adapter._detect_thinking({"content": None}) == []
