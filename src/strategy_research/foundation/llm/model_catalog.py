"""Model catalog: 3-layer fallback for LLM model metadata.

Layers (in order):
  L1 Bundled  — package data in core/llm/data/providers/<id>/<model>.toml
  L2 Cached   — ~/.quantnodes/model_catalog.json (TTL 7 days)
                refreshed from models.dev (jsdelivr → raw github)
  L3 Default  — _default_fallback.json for unknown providers/models

Public API:
    ModelCatalog  - singleton-style class with get_info / refresh_async
    get_model_info(provider, model) - module-level convenience
    reset_cache_for_tests() - test helper to clear in-memory cache

The catalog is intentionally lenient: any failure cascades to the next
layer. The caller never has to handle network errors.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import httpx

if sys.version_info >= (3, 11):
    import tomllib  # type: ignore[import-not-found]
else:
    import tomli as tomllib  # type: ignore[import-untyped,no-redef]

logger = logging.getLogger(__name__)


# ── Data class ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class ModelInfo:
    """Static metadata for a single LLM model.

    All fields are cheap to compare (int/float/bool/str). The
    ``source`` field records which layer served this entry so the
    UI can show a stale-data hint when on fallback.
    """

    context_tokens: int
    max_output_tokens: int
    supports_vision: bool
    supports_audio: bool
    supports_pdf: bool
    supports_tools: bool
    supports_reasoning: bool
    supports_structured_output: bool
    cost_input: float | None
    cost_output: float | None
    cost_cache_read: float | None
    cost_cache_write: float | None
    description: str
    release_date: str | None
    provider: str
    model: str
    models_dev_id: str
    source: Literal["bundled", "cached", "fetched", "fallback"]
    fetched_at: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ── Module data ──────────────────────────────────────────────────────


_DATA_DIR = Path(__file__).parent / "data"
_BUNDLED_INDEX = _DATA_DIR / "_index.json"
_BUNDLED_FALLBACK = _DATA_DIR / "_default_fallback.json"
_BUNDLED_PROVIDERS_DIR = _DATA_DIR / "providers"

_USER_CACHE_DIR_NAME = ".quantnodes"


def _user_cache_dir() -> Path:
    """Return the per-user cache directory, respecting $HOME."""
    return Path(os.environ.get("HOME", str(Path.home()))) / _USER_CACHE_DIR_NAME


def _user_cache_file() -> Path:
    return _user_cache_dir() / "model_catalog.json"
_CACHE_TTL_SECONDS = 7 * 24 * 3600

_BASE_URLS = [
    "https://cdn.jsdelivr.net/gh/anomalyco/models.dev@dev",
    "https://raw.githubusercontent.com/anomalyco/models.dev/dev",
]
_REQUEST_TIMEOUT = 5.0


# ── Provider name mapping (internal → models.dev) ────────────────────
#
# Single source of truth lives in core/llm/provider/__init__.py (it
# also knows adapters added later, e.g. nvidia). This dict mirrors it
# so `from .model_catalog import MODELS_DEV_ID` keeps working; keep it
# as a sync alias rather than a second hand-maintained copy.
from .provider import MODELS_DEV_ID  # noqa: F401  (re-export, see below)

MODELS_DEV_ID: dict[str, str] = dict(MODELS_DEV_ID)


def models_dev_id(provider: str) -> str:
    """Map internal provider name to models.dev provider_id.

    Unknown providers return the lowercase input unchanged.
    """
    return MODELS_DEV_ID.get(provider, provider.lower())


# ── Bundled data access ──────────────────────────────────────────────


def _load_bundled_index() -> dict[str, Any]:
    """Load the bundled provider index (JSON format)."""
    with _BUNDLED_INDEX.open("rb") as f:
        return json.load(f)


def _load_bundled_toml(provider_id: str, model_id: str) -> dict[str, Any] | None:
    """Load a single bundled TOML.

    Returns parsed dict on success, None if file missing.
    Matches filenames case-insensitively (models.dev uses both
    shadows like "MiniMax-M3.toml" and "gpt-4o-mini.toml").
    """
    provider_dir = _BUNDLED_PROVIDERS_DIR / provider_id
    if not provider_dir.exists():
        return None
    target = f"{model_id}.toml".lower()
    for entry in provider_dir.iterdir():
        if entry.name.lower() == target:
            with entry.open("rb") as f:
                return tomllib.load(f)
    return None


def _load_default_fallback() -> dict[str, Any]:
    """Load the generic fallback values."""
    with _BUNDLED_FALLBACK.open("rb") as f:
        return json.load(f)


# ── TOML → ModelInfo conversion ───────────────────────────────────────


def _toml_to_model_info(
    toml_data: dict[str, Any],
    *,
    provider: str,
    model: str,
    models_dev_id: str,
    source: Literal["bundled", "fetched"],
    fetched_at: str | None,
) -> ModelInfo:
    """Convert a parsed TOML dict to ModelInfo."""
    limit = toml_data.get("limit", {})
    modalities = toml_data.get("modalities", {})
    cost = toml_data.get("cost", {})

    inputs = set(modalities.get("input", []) or [])
    desc = toml_data.get("description", "")

    return ModelInfo(
        context_tokens=int(limit.get("context", 8192)),
        max_output_tokens=int(limit.get("output", 4096)),
        supports_vision=("image" in inputs or "vision" in inputs),
        supports_audio="audio" in inputs,
        supports_pdf="pdf" in inputs,
        supports_tools=bool(toml_data.get("tool_call", False)),
        supports_reasoning=bool(toml_data.get("reasoning", False)),
        supports_structured_output=bool(toml_data.get("structured_output", False)),
        cost_input=_to_float(cost.get("input")),
        cost_output=_to_float(cost.get("output")),
        cost_cache_read=_to_float(cost.get("cache_read")),
        cost_cache_write=_to_float(cost.get("cache_write")),
        description=desc,
        release_date=toml_data.get("release_date"),
        provider=provider,
        model=model,
        models_dev_id=models_dev_id,
        source=source,  # type: ignore[arg-type]
        fetched_at=fetched_at,
    )


def _fallback_to_model_info(
    fallback: dict[str, Any],
    *,
    provider: str,
    model: str,
    models_dev_id: str,
    source: Literal["fallback", "cached"],
    fetched_at: str | None,
) -> ModelInfo:
    """Build ModelInfo from a generic fallback dict."""
    return ModelInfo(
        context_tokens=int(fallback.get("context_tokens", 8192)),
        max_output_tokens=int(fallback.get("max_output_tokens", 4096)),
        supports_vision=bool(fallback.get("supports_vision", False)),
        supports_audio=bool(fallback.get("supports_audio", False)),
        supports_pdf=bool(fallback.get("supports_pdf", False)),
        supports_tools=bool(fallback.get("supports_tools", True)),
        supports_reasoning=bool(fallback.get("supports_reasoning", False)),
        supports_structured_output=bool(
            fallback.get("supports_structured_output", False)
        ),
        cost_input=_to_float(fallback.get("cost_input")),
        cost_output=_to_float(fallback.get("cost_output")),
        cost_cache_read=_to_float(fallback.get("cost_cache_read")),
        cost_cache_write=_to_float(fallback.get("cost_cache_write")),
        description=str(fallback.get("description", "")),
        release_date=fallback.get("release_date"),
        provider=provider,
        model=model,
        models_dev_id=models_dev_id,
        source=source,  # type: ignore[arg-type]
        fetched_at=fetched_at,
    )


def _to_float(value: Any) -> float | None:
    """Coerce to float, returning None for any non-numeric value."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _from_user_config(
    user_config: Any,
    provider: str,
    model: str,
) -> ModelInfo:
    """Build a ModelInfo from user-supplied LLMConfig overrides.

    Only the fields that the user explicitly set are used. Other fields
    are left at the LLMConfig defaults (which may be None) — the
    frontend treats them as "unknown".
    """
    dev_id = models_dev_id(provider)
    return ModelInfo(
        context_tokens=int(user_config.model_context_tokens),
        max_output_tokens=(
            int(user_config.model_max_output_tokens)
            if user_config.model_max_output_tokens is not None
            else 4096
        ),
        supports_vision=bool(user_config.model_supports_vision) or False,
        supports_audio=False,
        supports_pdf=False,
        supports_tools=True,
        supports_reasoning=bool(user_config.model_supports_reasoning) or False,
        supports_structured_output=False,
        cost_input=None,
        cost_output=None,
        cost_cache_read=None,
        cost_cache_write=None,
        description="User-configured override",
        release_date=None,
        provider=provider,
        model=model,
        models_dev_id=dev_id,
        source="fetched",  # treat as authoritative
        fetched_at=_now_iso(),
    )


def _merge_user_on_fetched(
    user_config: Any,
    fetched: ModelInfo,
    provider: str,
    model: str,
) -> ModelInfo:
    """Apply user_config overrides on top of a fetched ModelInfo.

    Returns a new ModelInfo using:
      - context_tokens / max_output_tokens: user_config if set, else fetched
      - all other fields: from fetched

    Used by refresh_async so that even after fetching, the user's
    declared context window is what the UI sees.
    """
    dev_id = models_dev_id(provider)
    return ModelInfo(
        context_tokens=int(
            getattr(user_config, "model_context_tokens", None)
            or fetched.context_tokens
        ),
        max_output_tokens=int(
            getattr(user_config, "model_max_output_tokens", None)
            or fetched.max_output_tokens
        ),
        supports_vision=(
            bool(user_config.model_supports_vision)
            if user_config.model_supports_vision is not None
            else fetched.supports_vision
        ),
        supports_audio=fetched.supports_audio,
        supports_pdf=fetched.supports_pdf,
        supports_tools=fetched.supports_tools,
        supports_reasoning=(
            bool(user_config.model_supports_reasoning)
            if user_config.model_supports_reasoning is not None
            else fetched.supports_reasoning
        ),
        supports_structured_output=fetched.supports_structured_output,
        cost_input=fetched.cost_input,
        cost_output=fetched.cost_output,
        cost_cache_read=fetched.cost_cache_read,
        cost_cache_write=fetched.cost_cache_write,
        description=fetched.description,
        release_date=fetched.release_date,
        provider=provider,
        model=model,
        models_dev_id=dev_id,
        source="fetched",
        fetched_at=fetched.fetched_at,
    )


# ── Disk cache ───────────────────────────────────────────────────────


def _read_cache() -> dict[str, Any]:
    """Read the disk cache file. Returns empty dict on any failure."""
    cache_file = _user_cache_file()
    if not cache_file.exists():
        return {}
    try:
        with cache_file.open() as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _write_cache(cache: dict[str, Any]) -> None:
    """Atomically write the disk cache file."""
    try:
        cache_dir = _user_cache_dir()
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_file = _user_cache_file()
        tmp = cache_file.with_suffix(".json.tmp")
        with tmp.open("w") as f:
            json.dump(cache, f, indent=2)
        tmp.replace(cache_file)
    except OSError as exc:
        logger.warning("Failed to write model catalog cache: %s", exc)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_cache_fresh(entry: dict[str, Any]) -> bool:
    """Check if a cache entry is within TTL."""
    fetched_at = entry.get("fetched_at")
    if not fetched_at:
        return False
    try:
        ts = datetime.fromisoformat(fetched_at)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - ts).total_seconds()
        return age < _CACHE_TTL_SECONDS
    except (TypeError, ValueError):
        return False


# ── Catalog ──────────────────────────────────────────────────────────


class ModelCatalog:
    """Centralized model metadata lookup with 3-layer fallback.

    Thread/process safe (no shared mutable state outside the in-memory
    cache dict, which is updated atomically). The async methods use
    httpx.AsyncClient and tolerate network failures gracefully.
    """

    def __init__(
        self,
        *,
        client_factory: Any | None = None,
        bundled_index: dict[str, Any] | None = None,
        bundled_fallback: dict[str, Any] | None = None,
    ) -> None:
        self._memory_cache: dict[str, ModelInfo] = {}
        self._client_factory = client_factory or _default_client_factory
        # Freeze bundled data at construction so tests can inject mocks
        self._bundled_index = bundled_index if bundled_index is not None else _load_bundled_index()
        self._bundled_fallback = (
            bundled_fallback if bundled_fallback is not None else _load_default_fallback()
        )

    # ── Public: synchronous lookup ─────────────────────────────────

    def get_info(
        self,
        provider: str,
        model: str,
        *,
        user_config: Any | None = None,
    ) -> ModelInfo:
        """Return ModelInfo using user_config → cache → bundled → fallback.

        Resolution order:
          1. user_config (LLMConfig with model_context_tokens set)
          2. In-memory cache
          3. Disk cache (fresh or stale — both serve as "have cached data")
          4. Bundled TOML in core/llm/data/providers/<id>/<model>.toml
          5. Default fallback (_default_fallback.json)

        Always returns successfully. Latency is bounded by the disk
        read at <1ms.
        """
        # 1. User config — highest priority (note: NOT cached in memory,
        # because a different user_config value should still take effect
        # on the same call site).
        if user_config is not None and getattr(
            user_config, "model_context_tokens", None
        ) is not None:
            return _from_user_config(user_config, provider, model)

        dev_id = models_dev_id(provider)
        cache_key = f"{dev_id}/{model}"

        # 2. In-memory cache
        if cache_key in self._memory_cache:
            return self._memory_cache[cache_key]

        # 3. Disk cache (any entry, fresh or stale)
        cache = _read_cache()
        entry = cache.get(cache_key)
        if entry:
            info = self._entry_to_model_info(entry, source_override="cached")
            self._memory_cache[cache_key] = info
            return info

        # 4. Bundled data
        bundled_info = self._lookup_bundled(provider, model, dev_id)
        if bundled_info is not None:
            self._memory_cache[cache_key] = bundled_info
            return bundled_info

        # 5. Generic fallback
        info = _fallback_to_model_info(
            self._bundled_fallback,
            provider=provider,
            model=model,
            models_dev_id=dev_id,
            source="fallback",
            fetched_at=None,
        )
        self._memory_cache[cache_key] = info
        return info

    # ── Public: async refresh ──────────────────────────────────────

    async def refresh_async(
        self,
        provider: str,
        model: str,
        *,
        user_config: Any | None = None,
    ) -> ModelInfo:
        """Fetch from models.dev, update cache, return latest.

        Tries each base URL in order; updates the disk cache on success.

        If user_config supplies ``model_context_tokens``, the fetched
        values for capability fields (vision/reasoning/cost) are still
        applied, but user_config wins for context_tokens /
        max_output_tokens. The result is then written to disk cache so
        subsequent get_info() calls return immediately.

        On total failure, returns whatever get_info() resolves to
        (cached, bundled, or fallback).
        """
        dev_id = models_dev_id(provider)
        cache_key = f"{dev_id}/{model}"

        toml_text = await self._fetch_toml(dev_id, model)
        if toml_text is not None:
            try:
                parsed = tomllib.loads(toml_text)
            except (tomllib.TOMLDecodeError, ValueError):
                parsed = None
            if parsed is not None:
                fetched_info = _toml_to_model_info(
                    parsed,
                    provider=provider,
                    model=model,
                    models_dev_id=dev_id,
                    source="fetched",
                    fetched_at=_now_iso(),
                )
                # Merge user_config overrides on top of fetched
                if user_config is not None and getattr(
                    user_config, "model_context_tokens", None
                ) is not None:
                    final = _merge_user_on_fetched(
                        user_config, fetched_info, provider, model
                    )
                else:
                    final = fetched_info
                # Persist + cache
                cache = _read_cache()
                cache[cache_key] = final.to_dict()
                _write_cache(cache)
                self._memory_cache[cache_key] = final
                return final

        # Refresh failed: serve cached (even if stale) or fall back.
        # Deliberately resolve WITHOUT user_config here: if a previous
        # LLMConfig.load() synthesized model_context_tokens from a
        # provider default, echoing it back would freeze that value and
        # mask the real catalog data. Genuine user overrides still win
        # because every call site applies them via get_info(user_config=...).
        return self.get_info(provider, model)

    async def refresh_all_async(self, providers: list[str]) -> int:
        """Refresh a list of (provider, model) pairs concurrently.

        Returns count of successful refreshes. Failures are logged
        but not raised.
        """
        tasks = []
        for entry in providers:
            # entry is "provider/model" string
            if "/" in entry:
                p, m = entry.split("/", 1)
                tasks.append(self.refresh_async(p, m))
        if not tasks:
            return 0
        results = await asyncio.gather(*tasks, return_exceptions=True)
        ok = sum(1 for r in results if isinstance(r, ModelInfo) and r.source == "fetched")
        return ok

    # ── Internal helpers ───────────────────────────────────────────

    def _lookup_bundled(
        self, provider: str, model: str, dev_id: str
    ) -> ModelInfo | None:
        """Check bundled data for a known provider/model.

        Returns None if the bundled provider directory does not exist
        (so the caller falls through to generic fallback).

        Uses an internal-name → bundled-dir lookup so the bundled
        directory always uses the canonical internal name (e.g.
        ``minimax/`` for provider ``minimax``) even when the
        models.dev provider_id is ``minimax-cn-coding-plan``.
        """
        # Bundled directory uses the internal name (lowercase).
        bundled_dir = provider.lower()
        toml_data = _load_bundled_toml(bundled_dir, model)
        if toml_data is not None:
            return _toml_to_model_info(
                toml_data,
                provider=provider,
                model=model,
                models_dev_id=dev_id,
                source="bundled",
                fetched_at=None,
            )
        # Last-resort: try the models_dev_id directly (untouched bundled packs)
        if dev_id != bundled_dir:
            toml_data = _load_bundled_toml(dev_id, model)
            if toml_data is not None:
                return _toml_to_model_info(
                    toml_data,
                    provider=provider,
                    model=model,
                    models_dev_id=dev_id,
                    source="bundled",
                    fetched_at=None,
                )
        return None

    async def _fetch_toml(self, dev_id: str, model: str) -> str | None:
        """Try each base URL; return first successful body.

        models.dev uses inconsistent capitalization in model filenames
        (e.g. ``MiniMax-M3.toml`` vs ``gpt-4o-mini.toml``). We try the
        literal model name first, then a case-insensitive lookup via
        the GitHub contents API as a fallback.
        """
        async with self._client_factory() as client:
            for base in _BASE_URLS:
                url = f"{base}/providers/{dev_id}/models/{model}.toml"
                try:
                    resp = await client.get(url, timeout=_REQUEST_TIMEOUT)
                    if resp.status_code == 200:
                        return resp.text
                except (httpx.HTTPError, asyncio.TimeoutError) as exc:
                    logger.debug("Failed to fetch %s: %s", url, exc)
                    continue

            # Case-insensitive fallback: ask GitHub contents API for the
            # canonical filename, then fetch it.
            try:
                api_url = (
                    f"https://api.github.com/repos/anomalyco/models.dev"
                    f"/contents/providers/{dev_id}/models"
                )
                resp = await client.get(api_url, timeout=_REQUEST_TIMEOUT)
                if resp.status_code == 200:
                    files = resp.json()
                    target = f"{model}.toml".lower()
                    for entry in files:
                        if entry.get("name", "").lower() == target:
                            download_url = entry.get("download_url")
                            if download_url:
                                resp2 = await client.get(
                                    download_url, timeout=_REQUEST_TIMEOUT
                                )
                                if resp2.status_code == 200:
                                    return resp2.text
            except (httpx.HTTPError, asyncio.TimeoutError, ValueError) as exc:
                logger.debug("GitHub contents API fallback failed: %s", exc)
        return None

    def _entry_to_model_info(
        self,
        entry: dict[str, Any],
        *,
        source_override: str,
    ) -> ModelInfo:
        """Build a ModelInfo from a cached entry dict."""
        provider = entry.get("provider", "")
        model = entry.get("model", "")
        dev_id = entry.get("models_dev_id", models_dev_id(provider))
        return ModelInfo(
            context_tokens=int(entry.get("context_tokens", 8192)),
            max_output_tokens=int(entry.get("max_output_tokens", 4096)),
            supports_vision=bool(entry.get("supports_vision", False)),
            supports_audio=bool(entry.get("supports_audio", False)),
            supports_pdf=bool(entry.get("supports_pdf", False)),
            supports_tools=bool(entry.get("supports_tools", True)),
            supports_reasoning=bool(entry.get("supports_reasoning", False)),
            supports_structured_output=bool(
                entry.get("supports_structured_output", False)
            ),
            cost_input=_to_float(entry.get("cost_input")),
            cost_output=_to_float(entry.get("cost_output")),
            cost_cache_read=_to_float(entry.get("cost_cache_read")),
            cost_cache_write=_to_float(entry.get("cost_cache_write")),
            description=str(entry.get("description", "")),
            release_date=entry.get("release_date"),
            provider=provider,
            model=model,
            models_dev_id=dev_id,
            source=source_override,  # type: ignore[arg-type]
            fetched_at=entry.get("fetched_at"),
        )


# ── Default client factory ───────────────────────────────────────────


def _default_client_factory() -> Any:
    """Build an httpx.AsyncClient with sensible defaults."""
    return httpx.AsyncClient(
        follow_redirects=True,
        headers={"User-Agent": "quantnodes-strategy-research/0.6"},
    )


# ── Singleton accessor ───────────────────────────────────────────────


_default_catalog: ModelCatalog | None = None


def get_default_catalog() -> ModelCatalog:
    """Get the process-wide default catalog instance."""
    global _default_catalog
    if _default_catalog is None:
        _default_catalog = ModelCatalog()
    return _default_catalog


def reset_cache_for_tests() -> None:
    """Reset module-level caches (test helper)."""
    global _default_catalog
    _default_catalog = None


def get_model_info(
    provider: str,
    model: str,
    *,
    user_config: Any | None = None,
) -> ModelInfo:
    """Convenience: get info from the default catalog."""
    return get_default_catalog().get_info(provider, model, user_config=user_config)


async def refresh_model_info(
    provider: str,
    model: str,
    *,
    user_config: Any | None = None,
) -> ModelInfo:
    """Convenience: refresh info from the default catalog."""
    return await get_default_catalog().refresh_async(
        provider, model, user_config=user_config
    )


__all__ = [
    "ModelInfo",
    "ModelCatalog",
    "MODELS_DEV_ID",
    "models_dev_id",
    "get_model_info",
    "refresh_model_info",
    "get_default_catalog",
    "reset_cache_for_tests",
]
