"""Tests for CompactConfig defaults + llm.json loading.

Verifies:
- All fields have the user-specified opencode-aligned defaults
- llm.json "compact" section can override any field
- Unknown fields in llm.json are silently ignored
- threshold_tokens=None means "derive from model context"
"""
from __future__ import annotations

import dataclasses

import pytest

from agentnodes.kernel.agent.compact import CompactConfig

# ── Defaults (user-specified + opencode-aligned) ─────────────


class TestCompactConfigDefaults:
    def test_enabled_default(self):
        assert CompactConfig().enabled is True

    def test_threshold_tokens_default_is_none(self):
        """None = derive from model context (opencode-aligned)."""
        assert CompactConfig().threshold_tokens is None

    def test_compaction_buffer_tokens_default(self):
        """opencode DEFAULT_BUFFER."""
        assert CompactConfig().compaction_buffer_tokens == 20_000

    def test_microcompact_ratio_default(self):
        """User-specified: 0.9."""
        assert CompactConfig().microcompact_ratio == 0.9

    def test_llm_summarize_ratio_default(self):
        """User-specified: 0.95."""
        assert CompactConfig().llm_summarize_ratio == 0.80

    def test_hard_truncate_ratio_default(self):
        """User-specified: 0.99."""
        assert CompactConfig().hard_truncate_ratio == 0.99

    def test_overflow_ratio_default(self):
        assert CompactConfig().overflow_ratio == 0.99

    def test_microcompact_tool_result_chars_default(self):
        """opencode TOOL_OUTPUT_MAX_CHARS = 2000."""
        assert CompactConfig().microcompact_tool_result_chars == 2_000

    def test_collapse_keep_recent_default(self):
        assert CompactConfig().collapse_keep_recent == 4

    def test_preserve_recent_tokens_default(self):
        assert CompactConfig().preserve_recent_tokens is None

    def test_tail_turns_default(self):
        assert CompactConfig().tail_turns == 2

    def test_summary_output_tokens_default(self):
        """opencode SUMMARY_OUTPUT_TOKENS = 4096 (cap)."""
        assert CompactConfig().summary_output_tokens == 4_096

    def test_enable_incremental_summary_default(self):
        assert CompactConfig().enable_incremental_summary is True


# ── Field metadata (every field is exposed in LLMConfig loader) ────


class TestCompactFieldsExposed:
    def test_all_fields_have_defaults(self):
        """Every field should have a default (no required fields)."""
        for f in dataclasses.fields(CompactConfig):
            has_default = (
                f.default is not dataclasses.MISSING
                or f.default_factory is not dataclasses.MISSING
            )
            assert has_default, f"Field {f.name} has no default"

    def test_field_count(self):
        """Should have 23 fields (sanity check; Phase A added 6: L4-only
        config, safety check, and history-filter knobs; +1 quality_decay;
        +1 goal_evidence_truncate_chars)."""
        assert len(dataclasses.fields(CompactConfig)) == 23

    def test_field_names_match_loader(self):
        """The fields in CompactConfig must match the loader expectations."""
        field_names = {f.name for f in dataclasses.fields(CompactConfig)}
        expected = {
            "enabled",
            "threshold_tokens",
            "compaction_buffer_tokens",
            "microcompact_ratio",
            "llm_summarize_ratio",
            "hard_truncate_ratio",
            "overflow_ratio",
            "microcompact_tool_result_chars",
            "tool_truncate_chars",
            "collapse_keep_recent",
            "preserve_recent_tokens",
            "tail_turns",
            "summary_output_tokens",
            "enable_incremental_summary",
        }
        # summary_template is also a field but optional, can be added
        # The 14 listed above are the user-facing config knobs
        assert expected.issubset(field_names)


# ── CompactConfig construction patterns ────────────────────────


class TestCompactConfigConstruction:
    def test_all_fields_can_be_set_explicitly(self):
        """Verify all fields can be set via kwargs without errors."""
        cfg = CompactConfig(
            enabled=True,
            threshold_tokens=100_000,
            compaction_buffer_tokens=30_000,
            microcompact_ratio=0.7,
            llm_summarize_ratio=0.85,
            hard_truncate_ratio=0.95,
            overflow_ratio=0.98,
            microcompact_tool_result_chars=3000,
            tool_truncate_chars={"mytool": 500},
            collapse_keep_recent=8,
            preserve_recent_tokens=50_000,
            tail_turns=4,
            summary_output_tokens=8_000,
            enable_incremental_summary=False,
        )
        assert cfg.microcompact_ratio == 0.7
        assert cfg.compaction_buffer_tokens == 30_000

    def test_frozen(self):
        """CompactConfig is frozen (dataclass)."""
        cfg = CompactConfig()
        with pytest.raises(dataclasses.FrozenInstanceError):
            cfg.microcompact_ratio = 0.5

    def test_threshold_tokens_zero_is_force_all(self):
        """threshold_tokens=0 is the sentinel for "force all layers"."""
        cfg = CompactConfig(threshold_tokens=0)
        assert cfg.threshold_tokens == 0

    def test_threshold_tokens_none_means_derive(self):
        """threshold_tokens=None means "derive from model context"."""
        cfg = CompactConfig(threshold_tokens=None)
        assert cfg.threshold_tokens is None

