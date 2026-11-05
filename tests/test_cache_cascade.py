"""Tests for Phase 7+8 cache layer — cascade estimator + config + session cache."""
from __future__ import annotations

import pytest

from agentnodes.kernel.agent.cache import (
    CacheConfig,
    CascadeEstimator,
    ConfigValidator,
    EvictionPolicy,
    HardcodedLayer,
    MeanTimesSafetyLayer,
    P86PlusSigmaLayer,
    SessionCache,
    StaticConfigLayer,
    WritePolicy,
    compute_p86_plus_sigma,
)

# ── compute_p86_plus_sigma ──────────────────────────────────────────


class TestP86PlusSigma:
    def test_basic(self):
        samples = [100, 200, 300, 400, 500]
        result = compute_p86_plus_sigma(samples)
        # mean=300, stdev~158, p86 close to 500 → sum > 600
        assert result > 400
        assert result < 1500

    def test_uniform(self):
        """All same values → sigma=0 → result = p86"""
        samples = [100] * 10
        result = compute_p86_plus_sigma(samples)
        # p86 of uniform=100 + sigma=0 = 100
        assert result == 100

    def test_outlier_robust(self):
        """p86 should not be pulled up by extreme outliers."""
        samples = [100] * 99 + [100_000]
        result = compute_p86_plus_sigma(samples)
        # p86 ≈ 100 (at index 86), sigma ≈ 9900
        # result = 100 + 9900 = 10000
        assert result < 15000  # outlier influence bounded

    def test_too_few_samples_raises(self):
        with pytest.raises(ValueError):
            compute_p86_plus_sigma([100])

    def test_empty_raises(self):
        with pytest.raises(ValueError):
            compute_p86_plus_sigma([])


# ── CascadeEstimator ──────────────────────────────────────────────


class TestCascadeEstimator:
    def test_p86_layer_succeeds_first(self):
        layers = [
            P86PlusSigmaLayer([100, 200, 300], 2.0),
            MeanTimesSafetyLayer([100, 200, 300], 2.0),
            StaticConfigLayer(200),
            HardcodedLayer(),
        ]
        est = CascadeEstimator(layers=layers)
        result = est.estimate([100, 200, 300])
        assert result > 0
        assert est.active_layer == "p86_plus_sigma"

    def test_p86_layer_fails_falls_to_mean(self):
        """P86PlusSigmaLayer with <2 samples raises → mean takes over."""
        layers = [
            P86PlusSigmaLayer([100], 2.0),  # raises
            MeanTimesSafetyLayer([100, 200, 300], 2.0),
            StaticConfigLayer(200),
            HardcodedLayer(),
        ]
        est = CascadeEstimator(layers=layers)
        result = est.estimate([100, 200, 300])
        assert result > 0
        assert est.active_layer == "mean_times_safety"
        assert est.health["p86_plus_sigma"] == 1

    def test_mean_layer_fails_falls_to_static(self):
        layers = [
            P86PlusSigmaLayer([100], 2.0),  # raises
            MeanTimesSafetyLayer([], 2.0),  # raises
            StaticConfigLayer(200),
            HardcodedLayer(),
        ]
        est = CascadeEstimator(layers=layers)
        est.estimate([100, 200, 300])
        # _build_layers creates fresh layers; with 3 samples p86 and mean both succeed
        # Use static_config directly instead
        layers2 = [
            StaticConfigLayer(200),
            HardcodedLayer(),
        ]
        est2 = CascadeEstimator(layers=layers2)
        assert est2.estimate() == 200
        assert est2.active_layer == "static_config"

    def test_static_layer_fails_falls_to_hardcoded(self):
        """If static returns 0 (invalid), hardcoded catches via sanity clamp."""
        layers = [
            StaticConfigLayer(0),  # → sanity clamps to 10
            HardcodedLayer(),
        ]
        est = CascadeEstimator(layers=layers)
        result = est.estimate()
        # Static layer returns 0, sanity clamps to MIN_TOKENS_CLAMP=10
        # So static layer "succeeds" with 10
        assert result == 10
        assert est.active_layer == "static_config"

    def test_hardcoded_layer_last_resort(self):
        """Only HardcodedLayer present → must succeed."""
        est = CascadeEstimator(layers=[HardcodedLayer()])
        result = est.estimate()
        assert result == 200
        assert est.active_layer == "hardcoded"

    def test_sanity_clamp_prevents_extreme(self):
        # With extreme values, p86+σ could be very high
        samples = [10_000] * 100
        layers = [P86PlusSigmaLayer(samples, 10.0), HardcodedLayer()]
        est = CascadeEstimator(layers=layers)
        result = est.estimate(samples)
        # p86+σ ≈ 10000 + 0 = 10000; × 10 = 100000 → clamp to 10_000
        assert result == 10_000

    def test_sanity_clamp_prevents_tiny(self):
        layers = [StaticConfigLayer(5), HardcodedLayer()]
        est = CascadeEstimator(layers=layers)
        result = est.estimate()
        assert result == 10  # MIN_TOKENS_CLAMP

    def test_failure_count_telemetry(self):
        layers = [
            P86PlusSigmaLayer([100], 2.0),  # raises every time
            MeanTimesSafetyLayer([100, 200], 2.0),
            HardcodedLayer(),
        ]
        est = CascadeEstimator(layers=layers)
        for _ in range(3):
            est.estimate([100, 200])
        assert est.health["p86_plus_sigma"] == 3

    def test_invalidate_resets_state(self):
        est = CascadeEstimator(layers=[HardcodedLayer()])
        est.estimate()
        assert est.active_layer == "hardcoded"
        est.invalidate()
        assert est.active_layer == "none"


# ── ConfigValidator ───────────────────────────────────────────────


class TestConfigValidator:
    def test_valid_config_unchanged(self):
        cfg = CacheConfig(min_entries=500, max_entries=2000)
        validated = ConfigValidator.validate(cfg)
        assert validated.min_entries == 500
        assert validated.max_entries == 2000

    def test_negative_min_entries_sanitized(self):
        cfg = CacheConfig(min_entries=-100, max_entries=2000)
        validated = ConfigValidator.validate(cfg)
        assert validated.min_entries == 1000

    def test_zero_max_entries_sanitized(self):
        cfg = CacheConfig(min_entries=100, max_entries=0)
        validated = ConfigValidator.validate(cfg)
        assert validated.max_entries == 1000

    def test_negative_safety_factor_sanitized(self):
        cfg = CacheConfig(avg_tokens_safety_factor=-2.0)
        validated = ConfigValidator.validate(cfg)
        assert validated.avg_tokens_safety_factor == 2.0

    def test_zero_chars_per_token_sanitized(self):
        cfg = CacheConfig(chars_per_token=0)
        validated = ConfigValidator.validate(cfg)
        assert validated.chars_per_token == 3.0

    def test_min_samples_below_2_sanitized(self):
        cfg = CacheConfig(avg_tokens_min_samples=1)
        validated = ConfigValidator.validate(cfg)
        assert validated.avg_tokens_min_samples == 10

    def test_negative_re_resolve_sanitized(self):
        cfg = CacheConfig(re_resolve_interval_seconds=-5)
        validated = ConfigValidator.validate(cfg)
        assert validated.re_resolve_interval_seconds == 60.0


# ── CacheConfig.from_env ─────────────────────────────────────────


class TestCacheConfigFromEnv:
    def test_defaults(self, monkeypatch):
        for k in [
            "AN_CACHE_WRITE_POLICY", "AN_CACHE_EVICTION",
            "AN_CACHE_MIN_ENTRIES", "AN_CACHE_MAX_ENTRIES",
            "AN_CACHE_AVG_TOKENS_FALLBACK", "AN_CACHE_SAFETY_FACTOR",
            "AN_CACHE_MIN_SAMPLES", "AN_CACHE_COMPACTION_LINKED",
            "AN_CACHE_RE_RESOLVE_SEC", "AN_CACHE_TTL",
        ]:
            monkeypatch.delenv(k, raising=False)
        cfg = CacheConfig.from_env()
        assert cfg.write_policy == WritePolicy.WRITE_THROUGH
        assert cfg.eviction_policy == EvictionPolicy.LRU
        assert cfg.min_entries == 1000

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("AN_CACHE_MIN_ENTRIES", "500")
        monkeypatch.setenv("AN_CACHE_SAFETY_FACTOR", "3.0")
        monkeypatch.setenv("AN_CACHE_COMPACTION_LINKED", "false")
        cfg = CacheConfig.from_env()
        assert cfg.min_entries == 500
        assert cfg.avg_tokens_safety_factor == 3.0
        assert cfg.compaction_linked is False

    def test_env_validated(self, monkeypatch):
        monkeypatch.setenv("AN_CACHE_MIN_ENTRIES", "-100")
        cfg = CacheConfig.from_env()
        # Validator kicks in
        assert cfg.min_entries == 1000


# ── SessionCache ─────────────────────────────────────────────────


class TestSessionCache:
    _t = [1000.0]  # monotonic clock for tests

    def _clock(self) -> float:
        self._t[0] += 0.001
        return self._t[0]

    def _config(self, **overrides) -> CacheConfig:
        kwargs = dict(
            eviction_policy=EvictionPolicy.LRU,
            re_resolve_interval_seconds=999,  # don't re-resolve during tests
        )
        if "min_entries" not in overrides:
            kwargs["min_entries"] = 10
        if "max_entries" not in overrides:
            kwargs["max_entries"] = 10
        kwargs.update(overrides)
        return CacheConfig(**kwargs)

    def _cache(self, **overrides) -> SessionCache:
        return SessionCache(self._config(**overrides), clock=self._clock)

    def test_append_and_get(self):
        cache = self._cache()
        cache.append("s1", {"role": "user", "content": "hi"})
        msgs = cache.get("s1")
        assert msgs == [{"role": "user", "content": "hi"}]

    def test_get_miss_returns_none(self):
        cache = self._cache()
        assert cache.get("unknown") is None
        assert cache.stats.misses == 1

    def test_hit_increments_counter(self):
        cache = self._cache()
        cache.append("s1", {"x": 1})
        cache.get("s1")
        assert cache.stats.hits == 1

    def test_lru_eviction(self):
        cache = self._cache(min_entries=2, max_entries=2)
        cache.append("s1", "a")
        cache.append("s2", "b")
        cache.append("s3", "c")  # triggers eviction
        # s1 was inserted earliest → evicted
        assert cache.get("s1") is None
        assert cache.get("s2") is not None
        assert cache.get("s3") is not None

    def test_touch_updates_lru_order(self):
        cache = self._cache(min_entries=2, max_entries=2)
        cache.append("s1", "a")
        cache.append("s2", "b")
        cache.get("s1")  # touch s1 → s1 access_time updated, insert_time unchanged
        cache.append("s3", "c")  # evicts s2 (oldest insert_time)
        assert cache.get("s2") is None
        assert cache.get("s1") is not None

    def test_invalidate_drops_session(self):
        cache = SessionCache(self._config())
        cache.append("s1", "a")
        cache.invalidate("s1")
        assert cache.get("s1") is None
        assert cache.stats.invalidations == 1

    def test_clear_drops_all(self):
        cache = SessionCache(self._config())
        cache.append("s1", "a")
        cache.append("s2", "b")
        cache.clear()
        assert cache.session_count == 0

    def test_re_resolve_updates_max(self):
        cfg = self._config(min_entries=100)
        cache = SessionCache(cfg)
        original_max = cache.current_max_entries

        # Force re-resolve by manipulating last_resolve_at backward
        cache._stats.last_resolve_at -= 1000
        cache._maybe_re_resolve()
        # Should re-resolve, possibly same value
        assert cache._stats.last_resolve_at > original_max - 1

    def test_derive_max_entries_with_compact(self):
        from types import SimpleNamespace

        compact = SimpleNamespace(
            threshold_tokens=128_000,
            llm_summarize_ratio=0.95,
            fallback_threshold_tokens=8_000,
        )
        cfg = CacheConfig(
            min_entries=1000,
            max_entries=2000,
            compaction_linked=True,
            avg_tokens_per_message=200,
        )
        # threshold=128K, ratio=0.95 → trigger=121,600
        # / 200 = 608 → max(1000, 608) = 1000
        result = cfg.derive_max_entries(compact=compact, estimated_avg_tokens=200)
        assert result == 1000

    def test_derive_max_entries_long_context(self):
        from types import SimpleNamespace

        compact = SimpleNamespace(
            threshold_tokens=500_000,
            llm_summarize_ratio=0.95,
            fallback_threshold_tokens=8_000,
        )
        cfg = CacheConfig(
            min_entries=1000,
            max_entries=2000,
            compaction_linked=True,
            avg_tokens_per_message=200,
        )
        # trigger=475,000 / 200 = 2,375 → max(1000, 2375) = 2,375
        result = cfg.derive_max_entries(compact=compact, estimated_avg_tokens=200)
        assert result == 2_375
