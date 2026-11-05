"""Phase 7+8 — Cache abstraction layer.

Provides:
- ``CacheConfig`` + ``ConfigValidator`` (env vars → safe config)
- ``WritePolicy`` + ``EvictionPolicy`` enums
- ``CascadeEstimator`` (4-layer fallback: p86+σ → mean → config → hardcoded)
- ``SessionCache`` (LRU/TTL per-session cache)
- ``SessionLockMap`` (per-session asyncio.Lock)
"""
from __future__ import annotations

import asyncio
import logging
import os
import statistics
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


# ── Enums ─────────────────────────────────────────────────────────────


class WritePolicy(str, Enum):
    """Write-path cache strategies."""
    WRITE_THROUGH = "write_through"   # SQLite + cache atomically (default)
    WRITE_BEHIND = "write_behind"     # cache first, async write SQLite
    WRITE_AROUND = "write_around"     # SQLite only, cache via TTL/LRU
    CACHE_ASIDE = "cache_aside"       # app manages cache explicitly
    READ_THROUGH = "read_through"     # cache miss auto-loads


class EvictionPolicy(str, Enum):
    """Cache eviction strategies."""
    LRU = "lru"      # least-recently-used (default)
    TTL = "ttl"      # time-to-live
    NONE = "none"    # no auto-eviction


# ── Constants ─────────────────────────────────────────────────────────

HARDCODED_FALLBACK_TOKENS = 200  # Last-resort avg tokens/message
MIN_TOKENS_CLAMP = 10
MAX_TOKENS_CLAMP = 10_000
SANITY_MAX_ENTRIES = 100_000  # hard upper bound


# ── CacheConfig ───────────────────────────────────────────────────────


@dataclass
class CacheConfig:
    """Tunable cache parameters with env-var loading and validation."""

    # Write/eviction policy
    write_policy: WritePolicy = WritePolicy.WRITE_THROUGH
    eviction_policy: EvictionPolicy = EvictionPolicy.LRU

    # Capacity
    min_entries: int = 1000
    max_entries: int = 1000

    # Dynamic avg-tokens estimation
    avg_tokens_per_message: int = 200
    avg_tokens_estimation_window: int = 100
    avg_tokens_safety_factor: float = 2.0
    avg_tokens_min_samples: int = 10
    chars_per_token: float = 3.0

    # Compaction linkage
    compaction_linked: bool = True
    compact_config: Any = None  # CompactConfig (avoid circular import)

    # Re-resolve interval (option C)
    re_resolve_interval_seconds: float = 60.0

    # TTL
    ttl_seconds: float = 60.0

    @classmethod
    def from_env(cls) -> "CacheConfig":
        """Build config from SR_CACHE_* env vars, then validate."""
        config = cls(
            write_policy=WritePolicy(
                os.environ.get("SR_CACHE_WRITE_POLICY", "write_through")
            ),
            eviction_policy=EvictionPolicy(
                os.environ.get("SR_CACHE_EVICTION", "lru")
            ),
            min_entries=int(os.environ.get("SR_CACHE_MIN_ENTRIES", "1000")),
            max_entries=int(os.environ.get("SR_CACHE_MAX_ENTRIES", "1000")),
            avg_tokens_per_message=int(
                os.environ.get("SR_CACHE_AVG_TOKENS_FALLBACK", "200")
            ),
            avg_tokens_estimation_window=int(
                os.environ.get("SR_CACHE_WINDOW", "100")
            ),
            avg_tokens_safety_factor=float(
                os.environ.get("SR_CACHE_SAFETY_FACTOR", "2.0")
            ),
            avg_tokens_min_samples=int(
                os.environ.get("SR_CACHE_MIN_SAMPLES", "10")
            ),
            chars_per_token=float(
                os.environ.get("SR_CACHE_CHARS_PER_TOKEN", "3.0")
            ),
            compaction_linked=os.environ.get(
                "SR_CACHE_COMPACTION_LINKED", "true"
            ).lower() == "true",
            re_resolve_interval_seconds=float(
                os.environ.get("SR_CACHE_RE_RESOLVE_SEC", "60")
            ),
            ttl_seconds=float(os.environ.get("SR_CACHE_TTL", "60")),
        )
        return ConfigValidator.validate(config)

    def derive_max_entries(
        self,
        compact: Any = None,
        estimated_avg_tokens: int | None = None,
    ) -> int:
        """Resolve effective max_entries with cascade fallback.

        Layer 1: compaction_linked + compact + estimated → derive
        Layer 2: compaction_linked + compact + config fallback → derive
        Layer 3: config.max_entries
        Layer 4: config.min_entries (1000 floor)
        """
        if self.compaction_linked and compact is not None:
            try:
                trigger_tokens = self._resolve_trigger(compact)
                avg = (
                    estimated_avg_tokens
                    if (
                        estimated_avg_tokens is not None
                        and MIN_TOKENS_CLAMP <= estimated_avg_tokens <= MAX_TOKENS_CLAMP
                    )
                    else self.avg_tokens_per_message
                )
                derived = max(1, trigger_tokens // max(avg, 1))
                return max(self.min_entries, min(derived, SANITY_MAX_ENTRIES))
            except Exception as exc:
                logger.warning("derive_max_entries (Layer 1) failed: %s", exc)

        if self.max_entries >= self.min_entries:
            return min(self.max_entries, SANITY_MAX_ENTRIES)

        return self.min_entries

    def _resolve_trigger(self, compact: Any) -> int:
        threshold = (
            compact.threshold_tokens
            or getattr(compact, "fallback_threshold_tokens", 8_000)
        )
        ratio = getattr(compact, "llm_summarize_ratio", 0.95)
        return int(threshold * ratio)


# ── ConfigValidator ───────────────────────────────────────────────────


class ConfigValidator:
    """Sanitize CacheConfig to prevent corruption from bad env vars."""

    @staticmethod
    def validate(config: CacheConfig) -> CacheConfig:
        issues: list[str] = []

        if config.min_entries <= 0:
            issues.append(f"min_entries={config.min_entries} → 1000")
            config.min_entries = 1000

        if config.max_entries <= 0:
            issues.append(f"max_entries={config.max_entries} → 1000")
            config.max_entries = 1000

        if config.avg_tokens_safety_factor <= 0:
            issues.append(
                f"avg_tokens_safety_factor={config.avg_tokens_safety_factor} → 2.0"
            )
            config.avg_tokens_safety_factor = 2.0

        if config.chars_per_token <= 0:
            issues.append(f"chars_per_token={config.chars_per_token} → 3.0")
            config.chars_per_token = 3.0

        if config.avg_tokens_per_message < 0:
            issues.append(
                f"avg_tokens_per_message={config.avg_tokens_per_message} → 200"
            )
            config.avg_tokens_per_message = 200

        if config.avg_tokens_min_samples < 2:
            issues.append(
                f"avg_tokens_min_samples={config.avg_tokens_min_samples} < 2 → 10"
            )
            config.avg_tokens_min_samples = 10

        if config.re_resolve_interval_seconds <= 0:
            issues.append(
                f"re_resolve_interval_seconds={config.re_resolve_interval_seconds} → 60"
            )
            config.re_resolve_interval_seconds = 60.0

        if config.ttl_seconds <= 0:
            issues.append(f"ttl_seconds={config.ttl_seconds} → 60")
            config.ttl_seconds = 60.0

        for issue in issues:
            logger.warning("ConfigValidator: %s", issue)

        return config


# ── Cascade Estimator ─────────────────────────────────────────────────


def compute_p86_plus_sigma(samples: list[int]) -> int:
    """Calculate (p86 + 1σ) from token counts.

    Args:
        samples: token counts (need ≥ 2 elements)

    Returns:
        p86 + sample_stddev, rounded to int.

    Raises:
        ValueError: if samples < 2
    """
    if len(samples) < 2:
        raise ValueError(f"need ≥ 2 samples, got {len(samples)}")

    sorted_samples = sorted(samples)
    quantiles = statistics.quantiles(sorted_samples, n=100, method="inclusive")
    p86 = quantiles[86]  # 0-indexed
    sigma = statistics.stdev(samples)
    return int(p86 + sigma)


class EstimatorLayer:
    """Single layer in the cascade estimator."""

    name: str = "base"

    def compute(self) -> int:  # pragma: no cover - abstract
        raise NotImplementedError


class P86PlusSigmaLayer(EstimatorLayer):
    name = "p86_plus_sigma"

    def __init__(self, samples: list[int], safety_factor: float):
        self._samples = samples
        self._safety_factor = safety_factor

    def compute(self) -> int:
        p86_plus_sigma = compute_p86_plus_sigma(self._samples)
        return int(p86_plus_sigma * self._safety_factor)


class MeanTimesSafetyLayer(EstimatorLayer):
    name = "mean_times_safety"

    def __init__(self, samples: list[int], safety_factor: float):
        self._samples = samples
        self._safety_factor = safety_factor

    def compute(self) -> int:
        if not self._samples:
            raise ValueError("no samples for mean")
        mean = statistics.mean(self._samples)
        return int(mean * self._safety_factor)


class StaticConfigLayer(EstimatorLayer):
    name = "static_config"

    def __init__(self, fallback_value: int):
        self._fallback = fallback_value

    def compute(self) -> int:
        return self._fallback


class HardcodedLayer(EstimatorLayer):
    name = "hardcoded"

    def compute(self) -> int:
        return HARDCODED_FALLBACK_TOKENS


class CascadeEstimator:
    """Multi-layer cascade estimator. First successful layer wins."""

    def __init__(
        self,
        layers: list[EstimatorLayer] | None = None,
        min_value: int = MIN_TOKENS_CLAMP,
        max_value: int = MAX_TOKENS_CLAMP,
    ):
        self._layers = layers or [HardcodedLayer()]
        self._min_value = min_value
        self._max_value = max_value
        self._last_active_layer: str | None = None
        self._failure_counts: dict[str, int] = {
            layer.name: 0 for layer in self._layers
        }

    def estimate(self, samples: list[int] | None = None) -> int:
        """Try each layer; first non-error result wins. Clamp result.

        Layers are taken from constructor (static config). The ``samples``
        argument is currently unused (kept for future API expansion); layer
        constructors already capture their own samples.
        """
        for layer in self._layers:
            try:
                result = layer.compute()
                result = self._sanity_clamp(result, layer.name)
                self._last_active_layer = layer.name
                return result
            except Exception as exc:
                self._failure_counts[layer.name] = (
                    self._failure_counts.get(layer.name, 0) + 1
                )
                logger.warning(
                    "Estimator layer %s failed: %s (count=%d)",
                    layer.name, exc, self._failure_counts[layer.name],
                )
                continue

        # Last-resort: hardcoded layer MUST succeed
        self._last_active_layer = "hardcoded"
        return HARDCODED_FALLBACK_TOKENS

    def _sanity_clamp(self, value: int, layer_name: str) -> int:
        if value < self._min_value:
            logger.warning(
                "Estimator %s returned %d, clamping to %d",
                layer_name, value, self._min_value,
            )
            return self._min_value
        if value > self._max_value:
            logger.warning(
                "Estimator %s returned %d, clamping to %d",
                layer_name, value, self._max_value,
            )
            return self._max_value
        return value

    def invalidate(self) -> None:
        """Reset active layer + cached values (force re-estimate)."""
        self._last_active_layer = None
        self._failure_counts = {layer.name: 0 for layer in self._layers}

    @property
    def active_layer(self) -> str:
        return self._last_active_layer or "none"

    @property
    def health(self) -> dict[str, int]:
        return dict(self._failure_counts)


# ── SessionLockMap ────────────────────────────────────────────────────


class SessionLockMap:
    """Per-session asyncio.Lock for thread-safe concurrent access."""

    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = {}
        self._meta_lock = asyncio.Lock()

    async def get(self, session_id: str) -> asyncio.Lock:
        async with self._meta_lock:
            if session_id not in self._locks:
                self._locks[session_id] = asyncio.Lock()
            return self._locks[session_id]

    async def clear(self, session_id: str) -> None:
        async with self._meta_lock:
            self._locks.pop(session_id, None)


# ── SessionCache ──────────────────────────────────────────────────────


@dataclass
class CacheStats:
    """Telemetry for SessionCache."""
    hits: int = 0
    misses: int = 0
    evictions: int = 0
    invalidations: int = 0
    last_resolve_at: float = 0.0
    last_resolve_value: int = 0

    @property
    def hit_rate(self) -> float:
        """P0-1 B4 — fraction of ``get`` calls that found the entry cached.

        Returns 0.0 when no reads have happened yet (avoids ZeroDivisionError).
        Useful as a sizing signal: a low hit rate suggests the cap is too
        small (working set exceeds LRU capacity).
        """
        total = self.hits + self.misses
        return self.hits / total if total > 0 else 0.0


class SessionCache:
    """LRU/TTL per-session cache. Bounded by config-driven max_entries."""

    def __init__(
        self,
        config: CacheConfig,
        estimator: CascadeEstimator | None = None,
        compact_config: Any = None,
        clock: callable = time.time,
    ):
        self._config = config
        self._estimator = estimator
        self._compact_config = compact_config
        self._clock = clock
        self._data: dict[str, list[Any]] = {}
        self._access_times: dict[str, float] = {}
        self._insert_times: dict[str, float] = {}
        # Init stats BEFORE resolving max entries (which writes to stats)
        self._stats = CacheStats(last_resolve_at=self._clock())
        self._current_max_entries: int = self._resolve_max_entries()

    # ── Read / write API ───────────────────────────────────────────

    def append(self, session_id: str, message: Any) -> None:
        """Append message to session. Triggers LRU eviction if over capacity."""
        self._maybe_re_resolve()
        self._data.setdefault(session_id, []).append(message)
        self._touch(session_id)
        self._maybe_evict()

    def get(self, session_id: str) -> list[Any] | None:
        """Return cached messages or None on miss."""
        msgs = self._data.get(session_id)
        if msgs is not None:
            self._stats.hits += 1
            self._touch(session_id)
            return msgs
        self._stats.misses += 1
        return None

    def set(self, session_id: str, messages: list[Any]) -> None:
        """Replace session cache with full message list."""
        self._maybe_re_resolve()
        self._data[session_id] = list(messages)
        self._touch(session_id)
        self._maybe_evict()

    def invalidate(self, session_id: str) -> None:
        """Drop a session from cache."""
        self._data.pop(session_id, None)
        self._access_times.pop(session_id, None)
        self._insert_times.pop(session_id, None)
        self._stats.invalidations += 1

    def clear(self) -> None:
        """Drop all sessions from cache."""
        self._data.clear()
        self._access_times.clear()
        self._insert_times.clear()

    @property
    def session_count(self) -> int:
        return len(self._data)

    @property
    def stats(self) -> CacheStats:
        return self._stats

    @property
    def current_max_entries(self) -> int:
        return self._current_max_entries

    # ── Internal helpers ───────────────────────────────────────────

    def _touch(self, session_id: str) -> None:
        now = self._clock()
        self._access_times[session_id] = now
        self._insert_times.setdefault(session_id, now)

    def _maybe_evict(self) -> None:
        if self._config.eviction_policy == EvictionPolicy.TTL:
            self._evict_ttl()
        elif self._config.eviction_policy == EvictionPolicy.LRU:
            self._evict_lru()

    def _evict_lru(self) -> None:
        while len(self._data) > self._current_max_entries:
            oldest = min(
                self._access_times.items(),
                key=lambda kv: kv[1],
                default=None,
            )
            if oldest is None:
                break
            sid, _ = oldest
            self.invalidate(sid)
            self._stats.evictions += 1

    def _evict_ttl(self) -> None:
        now = self._clock()
        expired = [
            sid for sid, t in self._insert_times.items()
            if now - t > self._config.ttl_seconds
        ]
        for sid in expired:
            self.invalidate(sid)
            self._stats.evictions += 1

    def _maybe_re_resolve(self) -> None:
        now = self._clock()
        if (
            now - self._stats.last_resolve_at
            >= self._config.re_resolve_interval_seconds
        ):
            self._current_max_entries = self._resolve_max_entries()
            self._stats.last_resolve_at = now

    def _resolve_max_entries(self) -> int:
        estimated_avg = (
            self._estimator.estimate() if self._estimator is not None else None
        )
        result = self._config.derive_max_entries(
            compact=self._compact_config,
            estimated_avg_tokens=estimated_avg,
        )
        self._stats.last_resolve_value = result
        return result


__all__ = [
    "CascadeEstimator",
    "CacheConfig",
    "CacheStats",
    "ConfigValidator",
    "EvictionPolicy",
    "HARDCODED_FALLBACK_TOKENS",
    "HardcodedLayer",
    "MeanTimesSafetyLayer",
    "P86PlusSigmaLayer",
    "SessionCache",
    "SessionLockMap",
    "StaticConfigLayer",
    "WritePolicy",
    "compute_p86_plus_sigma",
]
