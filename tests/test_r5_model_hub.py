"""R5 ModelHub 测试——解析序、拒绝语义、客户端构造、等价性。

设计：docs/archive/2026-10/stage-r5-execution-plane-assessment-20261030.md §2.1
"""
from __future__ import annotations

import pytest

from agentnodes.foundation.llm.model_hub import (
    ModelBinding,
    ModelHubError,
    get_client,
    resolve,
)


@pytest.fixture()
def no_config(tmp_path, monkeypatch):
    """隔离用户 llm.json，避免读到真实配置。"""
    cfg = tmp_path / "llm.json"
    cfg.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("STRATEGY_RESEARCH_LLM_CONFIG", str(cfg))
    monkeypatch.setenv("LLM_API_KEY", "test-key-not-real")
    for key in ("LLM_PROVIDER", "LLM_MODEL", "OPENAI_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    return cfg


# ── A. 显式 overrides ────────────────────────────────────


def test_explicit_provider_and_model_win(no_config):
    b = resolve(provider="minimax", model="abab-test")
    assert isinstance(b, ModelBinding)
    assert b.provider == "minimax"
    assert b.model == "abab-test"
    assert b.source == "explicit"


def test_explicit_provider_only_uses_its_default_model(no_config):
    b = resolve(provider="minimax")
    assert b.provider == "minimax"
    assert b.model  # provider 默认已填充
    assert b.source == "explicit"


# ── B. 回落配置 ──────────────────────────────────────────


def test_config_provider_is_used(no_config):
    no_config.write_text(
        '{"llm": {"provider": "minimax", "model": "from-config"}}',
        encoding="utf-8",
    )
    b = resolve()
    assert b.provider == "minimax"
    assert b.model == "from-config"
    assert b.source == "config"


# ── C. 拒绝语义（不降级）────────────────────────────────


def test_unknown_provider_is_rejected(no_config):
    with pytest.raises(ModelHubError, match="未知 provider"):
        resolve(provider="not-a-provider")


def test_auto_provider_is_allowed_as_documented_mode(no_config):
    """``auto`` 是 LLMConfig 文档定义的合法值（全局兜底），不算未知。"""
    no_config.write_text(
        '{"llm": {"provider": "auto", "model": "x"}}', encoding="utf-8",
    )
    b = resolve()
    assert b.provider == "auto"
    assert b.model == "x"


def test_provider_without_registered_default_still_resolves_via_global_fallback(no_config):
    """LLMConfig 层已有全局兜底 model；ModelHub 沿用它，不重复判空。

    这条锁定「ModelHub 不改动装载器语义」——model 兜底属 LLMConfig，
    ModelHub 只保证 provider 合法 + 结果非空。
    """
    from agentnodes.foundation.llm.provider import register_provider
    from agentnodes.foundation.llm.provider.base import ProviderAdapter

    class _NoDefault(ProviderAdapter):
        default_base_url = "http://x"
        default_model = ""
        default_max_tokens = 8

    register_provider("tests-nodefault", _NoDefault)
    try:
        b = resolve(provider="tests-nodefault")
        assert b.provider == "tests-nodefault"
        assert b.model  # 全局兜底提供了 model
    finally:
        from agentnodes.foundation.llm.provider import _REGISTRY
        _REGISTRY.pop("tests-nodefault", None)


# ── D. 客户端构造 ────────────────────────────────────────


def test_get_client_returns_configured_client(no_config):
    client = get_client(provider="minimax", model="m1")
    cfg = client.config
    assert cfg.provider == "minimax"
    assert cfg.model == "m1"


def test_get_client_matches_load_without_overrides(no_config):
    """等价门：无 overrides 时与 OpenAICompatClient(LLMConfig.load()) 同参。"""
    from agentnodes.foundation.llm.config import LLMConfig
    from agentnodes.foundation.llm.openai_client import OpenAICompatClient

    no_config.write_text(
        '{"llm": {"provider": "minimax", "model": "eq-model"}}',
        encoding="utf-8",
    )
    direct = OpenAICompatClient(LLMConfig.load())
    via_hub = get_client()
    assert via_hub.config.provider == direct.config.provider
    assert via_hub.config.model == direct.config.model
    assert via_hub.config.base_url == direct.config.base_url


# ── E. 脱敏摘要 ──────────────────────────────────────────


def test_binding_describe_has_no_secrets(no_config):
    b = resolve(provider="minimax", model="m")
    d = b.describe()
    assert set(d) == {"provider", "model", "source"}
    assert "key" not in str(d).lower()
