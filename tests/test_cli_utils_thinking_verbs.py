"""Tests for ``cli.utils.thinking_verbs``."""

from __future__ import annotations

from agentnodes.interfaces.cli.utils.thinking_verbs import (
    THINKING_VERBS,
    pick_thinking_verb,
)


class TestVerbPool:
    def test_verbs_is_non_empty_tuple(self):
        assert isinstance(THINKING_VERBS, tuple)
        assert len(THINKING_VERBS) >= 3

    def test_no_empty_strings(self):
        for v in THINKING_VERBS:
            assert v.strip()
            assert not v.endswith("…")  # raw verbs do not carry the suffix


class TestPickVerb:
    def test_returns_string_with_ellipsis_suffix(self):
        result = pick_thinking_verb(seed=0)
        assert result.endswith("…")

    def test_seed_is_deterministic(self):
        a = pick_thinking_verb(seed=42)
        b = pick_thinking_verb(seed=42)
        assert a == b

    def test_unseeded_returns_from_pool(self):
        seen = set()
        for _ in range(100):
            seen.add(pick_thinking_verb())
        # With 100 picks the pool should at least hit ≥2 distinct verbs (≈85%
        # coverage). Realistically all 6.
        assert len(seen) >= 2
        for verb in seen:
            assert verb.endswith("…")
            assert verb.rstrip("…") in THINKING_VERBS

    def test_different_seeds_may_differ(self):
        # Across many distinct seeds we should eventually see variety.
        results = {pick_thinking_verb(seed=s) for s in range(50)}
        assert len(results) >= 2

    def test_strips_correctly(self):
        for s in range(20):
            picked = pick_thinking_verb(seed=s)
            stripped = picked[:-1]  # remove trailing …
            assert stripped in THINKING_VERBS
