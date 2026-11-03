"""Tests for the ModelCatalog 3-layer fallback system.

Layers:
  L1 Bundled  — package data in core/llm/data/providers/<id>/<model>.toml
  L2 Cached   — disk cache ~/.quantnodes/model_catalog.json
  L3 Default  — _default_fallback.json for unknown providers/models
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest

from strategy_research.foundation.llm.model_catalog import (
    MODELS_DEV_ID,
    ModelCatalog,
    models_dev_id,
    reset_cache_for_tests,
)


@pytest.fixture(autouse=True)
def _isolate_cache(tmp_path, monkeypatch):
    """Redirect disk cache to a tmp dir for each test."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    # Reset module-level singleton so a fresh catalog picks up the
    # new home env var.
    reset_cache_for_tests()
    yield
    reset_cache_for_tests()


# ── 1. Bundled data lookup ───────────────────────────────────────────


def test_get_info_returns_bundled_for_known_minimax():
    info = ModelCatalog().get_info("minimax", "minimax-M3")
    assert info.source == "bundled"
    assert info.context_tokens == 1_000_000
    assert info.max_output_tokens == 128_000
    assert info.supports_vision is True
    assert info.supports_tools is True
    assert info.supports_reasoning is True
    assert info.provider == "minimax"
    assert info.model == "minimax-M3"
    assert info.models_dev_id == "minimax-cn-coding-plan"


def test_get_info_finds_qwen_via_alibaba_alias():
    info = ModelCatalog().get_info("qwen", "qwen-plus")
    assert info.source == "bundled"
    assert info.models_dev_id == "alibaba"
    assert info.context_tokens > 0


def test_get_info_finds_kimi_via_moonshotai_alias():
    info = ModelCatalog().get_info("kimi", "kimi-k2.5")
    assert info.source == "bundled"
    assert info.models_dev_id == "moonshotai"


# ── 2. Fallback path ────────────────────────────────────────────────


def test_get_info_uses_default_fallback_for_unknown_provider():
    from strategy_research.foundation.llm.model_catalog import _load_default_fallback

    fallback = _load_default_fallback()
    info = ModelCatalog().get_info("weird-llm", "X-1")
    assert info.source == "fallback"
    assert info.context_tokens == int(fallback.get("context_tokens", 8192))
    assert info.max_output_tokens == int(fallback.get("max_output_tokens", 4096))
    assert info.supports_vision is False
    assert info.supports_tools is True
    assert info.provider == "weird-llm"
    assert info.model == "X-1"


# ── 3. Path mapping ─────────────────────────────────────────────────


def test_models_dev_id_known_providers():
    assert models_dev_id("minimax") == "minimax-cn-coding-plan"
    assert models_dev_id("qwen") == "alibaba"
    assert models_dev_id("kimi") == "moonshotai"
    assert models_dev_id("openai") == "openai"
    assert models_dev_id("deepseek") == "deepseek"


def test_models_dev_id_unknown_passes_through_lower():
    assert models_dev_id("Custom-Name") == "custom-name"


def test_models_dev_id_mapping_complete():
    """The MODELS_DEV_ID dict has at least the 5 core providers."""
    for name in ("minimax", "openai", "deepseek", "qwen", "kimi"):
        assert name in MODELS_DEV_ID


# ── 4. Disk cache ───────────────────────────────────────────────────

# 罐头 TOML：让 refresh_async 的单测确定性（此前 4 个测试打真网
# models.dev，网络抖动即 flaky——E 收尾时发现）。断言中的 128_000
# 与此处的 context 值一致，意图（缓存/合并逻辑）不变。
_CANNED_GPT4O_MINI_TOML = """\
[limit]
context = 128000
output = 4096

[modalities]
input = ["text", "image"]

tool_call = true
description = "canned gpt-4o-mini for tests"
"""


def _mock_fetch(monkeypatch, toml_text: str = _CANNED_GPT4O_MINI_TOML) -> None:
    """把 ModelCatalog._fetch_toml 替换为返回罐头 TOML（不打网）。"""

    async def fake_fetch(self, dev_id, model):
        return toml_text

    monkeypatch.setattr(ModelCatalog, "_fetch_toml", fake_fetch)


def test_disk_cache_written_after_refresh(tmp_path, monkeypatch):
    """refresh_async writes ~/.quantnodes/model_catalog.json on success."""
    _mock_fetch(monkeypatch)
    cache_path = Path(os.environ["HOME"]) / ".quantnodes" / "model_catalog.json"

    async def run():
        catalog = ModelCatalog()
        return await catalog.refresh_async("openai", "gpt-4o-mini")

    info = asyncio.run(run())

    assert info.source == "fetched"
    assert cache_path.exists()
    payload = json.loads(cache_path.read_text())
    assert "openai/gpt-4o-mini" in payload
    assert payload["openai/gpt-4o-mini"]["context_tokens"] == 128_000
    # And the in-memory cached entry uses the field for downstream consumers
    assert info.context_tokens == 128_000


def test_cached_entry_returned_after_refresh(tmp_path, monkeypatch):
    """After refresh, get_info returns source='cached' (disk cache)."""
    _mock_fetch(monkeypatch)

    async def setup():
        catalog = ModelCatalog()
        await catalog.refresh_async("openai", "gpt-4o-mini")

    asyncio.run(setup())

    info = ModelCatalog().get_info("openai", "gpt-4o-mini")
    assert info.source == "cached"
    assert info.context_tokens == 128_000


# ── 5. Failure path ─────────────────────────────────────────────────


def test_refresh_failure_falls_back_to_bundled(tmp_path, monkeypatch):
    """When all base URLs fail, refresh returns the bundled entry."""

    class FailFactory:
        def __call__(self):
            import httpx

            class FailClient:
                async def __aenter__(self):
                    return self

                async def __aexit__(self, *args):
                    return False

                async def get(self, url, timeout=None):

                    raise httpx.ConnectError("network unreachable")

            return FailClient()

    catalog = ModelCatalog(client_factory=FailFactory())

    async def run():
        return await catalog.refresh_async("openai", "gpt-4o-mini")

    info = asyncio.run(run())
    # Falls back to bundled (no disk cache yet)
    assert info.source == "bundled"
    assert info.context_tokens == 128_000


def test_get_info_returns_cached_when_disk_cache_exists_but_no_bundled():
    """Unknown model with disk cache: returns cached (even if stale)."""
    # Write a fake cache entry
    cache_path = Path(os.environ["HOME"]) / ".quantnodes" / "model_catalog.json"
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps({
        "custom-llm/custom-model": {
            "provider": "custom-llm",
            "model": "custom-model",
            "models_dev_id": "custom-llm",
            "context_tokens": 32000,
            "max_output_tokens": 4096,
            "supports_vision": False,
            "supports_audio": False,
            "supports_pdf": False,
            "supports_tools": True,
            "supports_reasoning": False,
            "supports_structured_output": False,
            "cost_input": 0.1,
            "cost_output": 0.2,
            "cost_cache_read": None,
            "cost_cache_write": None,
            "description": "custom",
            "release_date": "2026-01-01",
            "source": "fetched",
            "fetched_at": "2026-07-30T00:00:00+00:00",
        }
    }))

    catalog = ModelCatalog()
    info = catalog.get_info("custom-llm", "custom-model")
    assert info.context_tokens == 32000
    # Either 'cached' (fresh) or 'fallback' (stale). Cached since just-written.
    assert info.source in ("cached", "fallback")


# ── 6. User config override (highest priority) ────────────────────────


def test_user_config_context_tokens_overrides_bundled():
    """LLMConfig.model_context_tokens wins over bundled data."""
    from strategy_research.foundation.llm.config import LLMConfig

    cfg = LLMConfig(
        provider="minimax",
        model="minimax-M3",
        model_context_tokens=2_000_000,
    )
    info = ModelCatalog().get_info("minimax", "minimax-M3", user_config=cfg)
    assert info.context_tokens == 2_000_000
    # Bundled source claimed 1M; user override applied.
    assert info.source == "fetched"  # user_config is treated as authoritative


def test_user_config_can_be_partial():
    """Only model_context_tokens set; other fields fall through to defaults."""
    from strategy_research.foundation.llm.config import LLMConfig

    cfg = LLMConfig(
        provider="minimax",
        model="minimax-M3",
        model_context_tokens=123_456,
    )
    info = ModelCatalog().get_info("minimax", "minimax-M3", user_config=cfg)
    assert info.context_tokens == 123_456
    # max_output_tokens falls back to a sensible default (from
    # _from_user_config) since user didn't set it
    assert info.max_output_tokens == 4096
    # Vision/reasoning defaults to False unless user sets them
    assert info.supports_vision is False


def test_user_config_none_falls_through_to_bundled():
    """When user_config is given but model_context_tokens is None, fall through."""
    from strategy_research.foundation.llm.config import LLMConfig

    cfg = LLMConfig(
        provider="minimax",
        model="minimax-M3",
        model_context_tokens=None,
    )
    info = ModelCatalog().get_info("minimax", "minimax-M3", user_config=cfg)
    # Falls through to bundled (or disk cache if present)
    assert info.source in ("bundled", "cached")
    assert info.context_tokens == 1_000_000  # bundled value


def test_user_config_wins_over_disk_cache():
    """User config overrides even when disk cache has a fresh entry."""
    from strategy_research.foundation.llm.config import LLMConfig

    # Write a fresh disk cache entry with a different context value
    cache_path = Path(os.environ["HOME"]) / ".quantnodes" / "model_catalog.json"
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps({
        "minimax-cn-coding-plan/minimax-M3": {
            "provider": "minimax",
            "model": "minimax-M3",
            "models_dev_id": "minimax-cn-coding-plan",
            "context_tokens": 999_000,
            "max_output_tokens": 64000,
            "supports_vision": True,
            "supports_audio": False,
            "supports_pdf": False,
            "supports_tools": True,
            "supports_reasoning": True,
            "supports_structured_output": False,
            "cost_input": None,
            "cost_output": None,
            "cost_cache_read": None,
            "cost_cache_write": None,
            "description": "cached",
            "release_date": "2026-06-01",
            "source": "fetched",
            "fetched_at": "2026-07-30T00:00:00+00:00",
        }
    }))

    cfg = LLMConfig(
        provider="minimax",
        model="minimax-M3",
        model_context_tokens=2_000_000,
    )
    info = ModelCatalog().get_info("minimax", "minimax-M3", user_config=cfg)
    # User config wins over disk cache
    assert info.context_tokens == 2_000_000
    # Not from disk cache or bundled
    assert info.source == "fetched"


# ── 7. Refresh merges user_config on top of fetched ────────────────────


def test_refresh_async_merges_user_overrides_on_top_of_fetched(tmp_path, monkeypatch):
    """When both fetch and user_config succeed, user wins for context_tokens."""
    _mock_fetch(monkeypatch)
    from strategy_research.foundation.llm.config import LLMConfig

    cfg = LLMConfig(
        provider="openai",
        model="gpt-4o-mini",
        model_context_tokens=500_000,
    )

    async def run():
        catalog = ModelCatalog()
        return await catalog.refresh_async(
            "openai", "gpt-4o-mini", user_config=cfg
        )

    info = asyncio.run(run())
    # User override wins
    assert info.context_tokens == 500_000
    # Other fields come from fetched — source must be "fetched" (merge
    # happened), but capability flags are not asserted: they come from
    # the canned TOML now, not live models.dev data.
    assert info.source == "fetched"


def test_refresh_async_failure_serves_real_data_not_synthesized_override():
    """When fetch fails, do NOT echo a user_config override back.

    A synthesized model_context_tokens (e.g. produced by
    LLMConfig's provider-default fallback) must not mask the real
    catalog data — otherwise the value locks in a feedback loop.
    Genuine overrides still win at the call site via
    get_info(user_config=...).
    """
    from unittest.mock import patch

    from strategy_research.foundation.llm.config import LLMConfig

    cfg = LLMConfig(
        provider="openai",
        model="gpt-4o-mini",
        model_context_tokens=750_000,
    )

    async def run():
        catalog = ModelCatalog()
        with patch.object(
            catalog, "_fetch_toml", return_value=None,
        ):
            return await catalog.refresh_async(
                "openai", "gpt-4o-mini", user_config=cfg
            )

    info = asyncio.run(run())
    # Real bundled data wins; the synthesized override is not echoed.
    assert info.context_tokens == 128_000
    assert info.source == "bundled"

    # The caller can still apply its override explicitly.
    async def run_with_override():
        catalog = ModelCatalog()
        with patch.object(catalog, "_fetch_toml", return_value=None):
            await catalog.refresh_async(
                "openai", "gpt-4o-mini", user_config=cfg
            )
            return catalog.get_info(
                "openai", "gpt-4o-mini", user_config=cfg
            )

    info2 = asyncio.run(run_with_override())
    assert info2.context_tokens == 750_000
