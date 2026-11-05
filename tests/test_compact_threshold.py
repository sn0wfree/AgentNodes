"""Tests for _resolve_threshold_tokens — edge cases and opencode-aligned formula."""
from __future__ import annotations

from agentnodes.kernel.agent.compact import (
    CompactConfig,
    _resolve_threshold_tokens,
)


class TestResolveThresholdExplicit:
    """When config.threshold_tokens is set, return it as-is."""

    def test_explicit_1000(self):
        cfg = CompactConfig(threshold_tokens=1000)
        assert _resolve_threshold_tokens(cfg, 1_000_000, 128_000) == 1000

    def test_explicit_zero(self):
        """threshold_tokens=0 is a sentinel meaning 'force all layers'."""
        cfg = CompactConfig(threshold_tokens=0)
        assert _resolve_threshold_tokens(cfg, 1_000_000, 128_000) == 0

    def test_explicit_negative(self):
        """Even negative is returned as-is — caller's responsibility."""
        cfg = CompactConfig(threshold_tokens=-500)
        assert _resolve_threshold_tokens(cfg, 1_000_000, 128_000) == -500


class TestResolveThresholdDerived:
    """When threshold_tokens is None, derive from model context."""

    def test_1m_context_128k_output(self):
        """Standard: 1M context - max(128K, 20K) = 872K."""
        cfg = CompactConfig()
        result = _resolve_threshold_tokens(cfg, 1_000_000, 128_000)
        assert result == 1_000_000 - 128_000  # 872_000

    def test_1m_context_no_output(self):
        """No output token info: use buffer."""
        cfg = CompactConfig()
        result = _resolve_threshold_tokens(cfg, 1_000_000, None)
        assert result == 1_000_000 - 20_000  # 980_000

    def test_200k_context_16k_output(self):
        """200K context, 16K output: buffer=20K > output, so trigger = 200K-20K."""
        cfg = CompactConfig()
        result = _resolve_threshold_tokens(cfg, 200_000, 16_000)
        assert result == 200_000 - 20_000  # 180_000

    def test_200k_context_64k_output(self):
        """200K context, 64K output: output > buffer, trigger = 200K-64K."""
        cfg = CompactConfig()
        result = _resolve_threshold_tokens(cfg, 200_000, 64_000)
        assert result == 200_000 - 64_000  # 136_000

    def test_custom_buffer(self):
        """Custom compaction_buffer_tokens overrides default."""
        cfg = CompactConfig(compaction_buffer_tokens=50_000)
        result = _resolve_threshold_tokens(cfg, 200_000, 10_000)
        assert result == 200_000 - 50_000  # buffer=50K > output=10K

    def test_custom_buffer_larger_than_output(self):
        cfg = CompactConfig(compaction_buffer_tokens=100_000)
        result = _resolve_threshold_tokens(cfg, 200_000, 64_000)
        assert result == 200_000 - 100_000  # 100K


class TestResolveThresholdFloor:
    """Guard: trigger must be at least 8K."""

    def test_floor_at_8k_10k_context(self):
        """10K context: derived trigger = 10K-20K < 0, floor to 8K."""
        cfg = CompactConfig()
        result = _resolve_threshold_tokens(cfg, 10_000, 1_000)
        assert result == 8_000

    def test_floor_at_8k_28k_context(self):
        """28K context: derived trigger = 28K-20K = 8K, exactly at floor."""
        cfg = CompactConfig()
        result = _resolve_threshold_tokens(cfg, 28_000, 1_000)
        assert result == 8_000

    def test_floor_at_8k_29k_context(self):
        """29K context: derived trigger = 29K-20K = 9K, above floor."""
        cfg = CompactConfig()
        result = _resolve_threshold_tokens(cfg, 29_000, 1_000)
        assert result == 9_000

    def test_unknown_context_returns_8k(self):
        """No model context: fallback to 8K."""
        cfg = CompactConfig()
        result = _resolve_threshold_tokens(cfg, None, None)
        assert result == 8_000

    def test_zero_context_returns_8k(self):
        """model_context_tokens=0: treated as unknown."""
        cfg = CompactConfig()
        result = _resolve_threshold_tokens(cfg, 0, None)
        assert result == 8_000


class TestResolveThresholdCombinations:
    """Various config + model parameter combinations."""

    def test_explicit_overrides_derived(self):
        """Explicit threshold_tokens ignores model context entirely."""
        cfg = CompactConfig(threshold_tokens=50_000)
        result = _resolve_threshold_tokens(cfg, 1_000_000, 128_000)
        assert result == 50_000

    def test_large_model_max_output(self):
        """model_max_output_tokens > context: still clamps to floor."""
        cfg = CompactConfig()
        result = _resolve_threshold_tokens(cfg, 100_000, 200_000)
        assert result == 8_000  # floor

    def test_only_model_context_no_output(self):
        cfg = CompactConfig()
        result = _resolve_threshold_tokens(cfg, 500_000, None)
        assert result == 500_000 - 20_000  # 480K
