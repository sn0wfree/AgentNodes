"""Unit tests for resolve_max_tokens — model-aware auto sizing."""
from __future__ import annotations

from types import SimpleNamespace

from agentnodes.foundation.llm.config import (
    _DEFAULT_MAX_TOKENS,
    _MIN_OUTPUT_TOKENS,
    resolve_max_tokens,
)
from agentnodes.foundation.llm.provider.deepseek import DeepSeekAdapter
from agentnodes.foundation.llm.provider.minimax import MiniMaxAdapter
from agentnodes.foundation.llm.provider.openai import OpenAIAdapter


def _cfg(**kw):
    defaults = dict(
        provider="openai",
        model="gpt-4o-mini",
        max_tokens=None,
        model_max_output_tokens=None,
    )
    defaults.update(kw)
    return SimpleNamespace(**defaults)


class TestAutoWhenUnset:
    def test_generic_provider_uses_full_ceiling(self):
        # Unset → model's full output budget (most reasonable default).
        v = resolve_max_tokens(_cfg(), None, adapter=OpenAIAdapter())
        assert v == 16384

    def test_thinking_provider_gets_full_ceiling(self):
        v = resolve_max_tokens(_cfg(), None, adapter=MiniMaxAdapter())
        assert v == 32000

    def test_deepseek_thinking_floor(self):
        v = resolve_max_tokens(_cfg(), None, adapter=DeepSeekAdapter())
        assert v >= 4096

    def test_model_max_output_as_ceiling(self):
        cfg = _cfg(model_max_output_tokens=4096)
        v = resolve_max_tokens(cfg, None, adapter=OpenAIAdapter())
        assert v == 4096

    def test_zero_treated_as_unset(self):
        v = resolve_max_tokens(_cfg(), 0, adapter=MiniMaxAdapter())
        assert v == 32000

    def test_negative_treated_as_unset(self):
        v = resolve_max_tokens(_cfg(), -5, adapter=OpenAIAdapter())
        assert v == 16384

    def test_non_int_treated_as_unset(self):
        v = resolve_max_tokens(_cfg(), "auto", adapter=MiniMaxAdapter())
        assert v == 32000


class TestExplicitRequested:
    def test_normal_value_passes_through(self):
        v = resolve_max_tokens(_cfg(), 1024, adapter=OpenAIAdapter())
        assert v == 1024

    def test_tiny_value_raised_to_thinking_floor(self):
        v = resolve_max_tokens(_cfg(), 16, adapter=MiniMaxAdapter())
        assert v == 4096

    def test_tiny_value_raised_to_generic_floor(self):
        v = resolve_max_tokens(_cfg(), 1, adapter=OpenAIAdapter())
        assert v == _MIN_OUTPUT_TOKENS

    def test_oversize_clamped_to_ceiling(self):
        cfg = _cfg(model_max_output_tokens=2048)
        v = resolve_max_tokens(cfg, 100000, adapter=OpenAIAdapter())
        assert v == 2048

    def test_oversize_clamped_to_adapter_default(self):
        v = resolve_max_tokens(_cfg(), 999999, adapter=MiniMaxAdapter())
        assert v == 32000

    def test_non_int_requested_is_auto(self):
        v = resolve_max_tokens(_cfg(), "oops", adapter=OpenAIAdapter())
        assert v == 16384


class TestFallbacks:
    def test_none_adapter_uses_generic_floor(self):
        v = resolve_max_tokens(_cfg(), 10, adapter=None)
        assert v >= _MIN_OUTPUT_TOKENS

    def test_mock_like_adapter_without_int_floor(self):
        class Weird:
            min_output_tokens = "not-int"
            default_max_tokens = "not-int"

        v = resolve_max_tokens(_cfg(), None, adapter=Weird())
        assert v == _DEFAULT_MAX_TOKENS

    def test_ceiling_below_floor_promoted(self):
        cfg = _cfg(model_max_output_tokens=10)
        v = resolve_max_tokens(cfg, None, adapter=MiniMaxAdapter())
        assert v == 4096  # floor wins over broken ceiling
