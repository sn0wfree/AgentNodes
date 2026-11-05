"""Tests for the ``core/llm/quantnodes_bridge.py`` module.

Covers the verbatim copy of QuantNodes' llm.json reader + the
``env:VAR`` expander + env-override application, plus the integration
with :func:`agentnodes.foundation.llm.config.LLMConfig.load`.

Direct re-implementation of B-side semantics. See MIT attribution at the
top of ``quantnodes_bridge.py``.
"""
from __future__ import annotations

import json
from pathlib import Path

from agentnodes.foundation.llm.config import LLMConfig
from agentnodes.foundation.llm.quantnodes_bridge import (
    CONFIG_PATH,
    _apply_env_overrides,
    _expand_env_var,
    _load_single_path,
    load_quantnodes_llm_config,
)

# ── _expand_env_var ──────────────────────────────────────────────────


class TestExpandEnvVar:
    def test_passthrough_for_literal(self):
        assert _expand_env_var("sk-literal") == "sk-literal"

    def test_resolves_env_reference(self, monkeypatch):
        monkeypatch.setenv("MY_TEST_KEY", "sk-resolved")
        assert _expand_env_var("env:MY_TEST_KEY") == "sk-resolved"

    def test_returns_empty_when_unset(self, monkeypatch):
        monkeypatch.delenv("DEFINITELY_UNSET_VAR_XYZ", raising=False)
        assert _expand_env_var("env:DEFINITELY_UNSET_VAR_XYZ") == ""

    def test_passthrough_for_non_string(self):
        # Defensive: only strings are processed
        assert _expand_env_var(42) == 42  # type: ignore[arg-type]


# ── _load_single_path ────────────────────────────────────────────────


class TestLoadSinglePath:
    def test_missing_returns_none(self, tmp_path):
        assert _load_single_path(tmp_path / "absent.json") is None

    def test_no_llm_section_returns_empty_dict(self, tmp_path):
        p = tmp_path / "llm.json"
        p.write_text(json.dumps({"tools": []}))
        assert _load_single_path(p) == {}

    def test_non_dict_root_returns_empty(self, tmp_path):
        p = tmp_path / "llm.json"
        p.write_text(json.dumps([1, 2, 3]))
        assert _load_single_path(p) == {}

    def test_llm_section_not_dict_returns_empty(self, tmp_path):
        p = tmp_path / "llm.json"
        p.write_text(json.dumps({"llm": "not a dict"}))
        assert _load_single_path(p) == {}

    def test_valid_section_returned(self, tmp_path):
        p = tmp_path / "llm.json"
        p.write_text(json.dumps({"llm": {"provider": "openai", "model": "gpt-4o"}}))
        result = _load_single_path(p)
        assert result == {"provider": "openai", "model": "gpt-4o"}

    def test_malformed_json_returns_empty(self, tmp_path):
        p = tmp_path / "llm.json"
        p.write_text("not valid json {{{")
        assert _load_single_path(p) == {}


# ── _apply_env_overrides ─────────────────────────────────────────────


class TestApplyEnvOverrides:
    def test_no_env_vars_returns_shallow_copy(self, monkeypatch):
        for k in ("AGENTNODES__LLM__PROVIDER", "AGENTNODES__LLM__MODEL",
                  "AGENTNODES__LLM__BASE_URL", "AGENTNODES__LLM__API_KEY",
                  "AGENTNODES__LLM__TIMEOUT", "AGENTNODES__LLM__MAX_RETRIES",
                  "AGENTNODES__LLM__MAX_TOKENS", "AGENTNODES__LLM__ENABLED"):
            monkeypatch.delenv(k, raising=False)
        base = {"provider": "openai", "model": "gpt-4o"}
        result = _apply_env_overrides(base)
        assert result == base
        assert result is not base  # shallow copy

    def test_env_overrides_string_field(self, monkeypatch):
        monkeypatch.setenv("AGENTNODES__LLM__PROVIDER", "anthropic")
        result = _apply_env_overrides({"provider": "openai"})
        assert result["provider"] == "anthropic"

    def test_empty_env_var_does_not_overwrite(self, monkeypatch):
        monkeypatch.setenv("AGENTNODES__LLM__PROVIDER", "")
        result = _apply_env_overrides({"provider": "openai"})
        assert result["provider"] == "openai"

    def test_enabled_coerces_to_bool(self, monkeypatch):
        monkeypatch.setenv("AGENTNODES__LLM__ENABLED", "true")
        assert _apply_env_overrides({})["enabled"] is True

        monkeypatch.setenv("AGENTNODES__LLM__ENABLED", "false")
        assert _apply_env_overrides({})["enabled"] is False


# ── load_quantnodes_llm_config ───────────────────────────────────────


class TestLoadQuantnodesLlmConfig:
    def test_returns_empty_when_file_missing(self, tmp_path, monkeypatch):
        # Make sure no env vars are set either
        for k in ("AGENTNODES__LLM__PROVIDER", "AGENTNODES__LLM__MODEL",
                  "AGENTNODES__LLM__BASE_URL", "AGENTNODES__LLM__API_KEY",
                  "AGENTNODES__LLM__TIMEOUT", "AGENTNODES__LLM__MAX_RETRIES",
                  "AGENTNODES__LLM__MAX_TOKENS", "AGENTNODES__LLM__ENABLED"):
            monkeypatch.delenv(k, raising=False)
        result = load_quantnodes_llm_config(tmp_path / "missing.json")
        assert result == {}

    def test_loads_explicit_path(self, tmp_path):
        p = tmp_path / "llm.json"
        p.write_text(json.dumps({"llm": {"provider": "openai", "model": "gpt-4o"}}))
        result = load_quantnodes_llm_config(p)
        assert result == {"provider": "openai", "model": "gpt-4o"}

    def test_resolves_env_api_key(self, tmp_path, monkeypatch):
        monkeypatch.setenv("LLM_API_KEY", "sk-real-key")
        p = tmp_path / "llm.json"
        p.write_text(json.dumps({"llm": {"provider": "openai",
                                         "api_key": "env:LLM_API_KEY"}}))
        result = load_quantnodes_llm_config(p)
        assert result["api_key"] == "sk-real-key"

    def test_passes_through_literal_api_key(self, tmp_path):
        """Plaintext api_key (the legacy state) is returned as-is."""
        p = tmp_path / "llm.json"
        p.write_text(json.dumps({"llm": {"api_key": "sk-plaintext"}}))
        result = load_quantnodes_llm_config(p)
        assert result["api_key"] == "sk-plaintext"

    def test_env_overrides_file(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AGENTNODES__LLM__PROVIDER", "anthropic")
        p = tmp_path / "llm.json"
        p.write_text(json.dumps({"llm": {"provider": "openai"}}))
        result = load_quantnodes_llm_config(p)
        assert result["provider"] == "anthropic"

    def test_resolve_api_key_false_skips_expansion(self, tmp_path, monkeypatch):
        monkeypatch.setenv("LLM_API_KEY", "sk-real")
        p = tmp_path / "llm.json"
        p.write_text(json.dumps({"llm": {"api_key": "env:LLM_API_KEY"}}))
        result = load_quantnodes_llm_config(p, resolve_api_key=False)
        assert result["api_key"] == "env:LLM_API_KEY"

    def test_backcompat_alias_load_llm_config(self, tmp_path):
        from agentnodes.foundation.llm.quantnodes_bridge import load_llm_config
        p = tmp_path / "llm.json"
        p.write_text(json.dumps({"llm": {"provider": "openai"}}))
        assert load_llm_config(p) == {"provider": "openai"}


# ── LLMConfig.load integration ───────────────────────────────────────


class TestLLMConfigIntegration:
    def test_load_uses_bridge_when_yaml_absent(self, tmp_path, monkeypatch):
        """Without a yaml file and without env vars, LLMConfig.load()
        reads from the bridge path and merges provider→base_url fallbacks."""
        for k in ("OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_MODEL",
                  "LLM_API_KEY", "AGENTNODES__LLM__PROVIDER"):
            monkeypatch.delenv(k, raising=False)
        # Override bridge path so we don't read the real ~/.quantnodes/llm.json
        llm = tmp_path / "llm.json"
        llm.write_text(json.dumps({"llm": {
            "provider": "minimax",  # no base_url → A falls back to defaults
            "model": "minimax-M3",
        }}))
        monkeypatch.setattr(
            "agentnodes.foundation.llm.config._resolve_bridge_path",
            lambda env: llm,
        )
        cfg = LLMConfig.load()
        assert cfg.provider == "minimax"
        assert cfg.model == "minimax-M3"
        # Bridge provider→base_url fallback applied
        assert cfg.base_url == "https://api.minimaxi.com/v1"

    def test_env_api_key_overrides_bridge_when_no_real_key(self, tmp_path, monkeypatch):
        """When bridge has no api_key (or env:VAR is unresolvable),
        OPENAI_API_KEY / LLM_API_KEY env var fills the gap."""
        for k in ("OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_MODEL",
                  "LLM_API_KEY"):
            monkeypatch.delenv(k, raising=False)
        llm = tmp_path / "llm.json"
        llm.write_text(json.dumps({"llm": {"provider": "openai"}}))
        monkeypatch.setattr(
            "agentnodes.foundation.llm.config._resolve_bridge_path",
            lambda env: llm,
        )
        monkeypatch.setenv("LLM_API_KEY", "sk-from-shell-env")
        cfg = LLMConfig.load()
        assert cfg.api_key == "sk-from-shell-env"

    def test_bridge_api_key_wins_when_present(self, tmp_path, monkeypatch):
        for k in ("OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_MODEL",
                  "LLM_API_KEY"):
            monkeypatch.delenv(k, raising=False)
        llm = tmp_path / "llm.json"
        llm.write_text(json.dumps({"llm": {
            "provider": "openai",
            "api_key": "sk-from-bridge",
        }}))
        monkeypatch.setattr(
            "agentnodes.foundation.llm.config._resolve_bridge_path",
            lambda env: llm,
        )
        monkeypatch.setenv("LLM_API_KEY", "sk-from-shell")
        cfg = LLMConfig.load()
        # Bridge value wins
        assert cfg.api_key == "sk-from-bridge"

    def test_missing_bridge_file_returns_defaults(self, tmp_path, monkeypatch):
        for k in ("OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_MODEL",
                  "LLM_API_KEY"):
            monkeypatch.delenv(k, raising=False)
        llm = tmp_path / "missing.json"  # never created
        monkeypatch.setattr(
            "agentnodes.foundation.llm.config._resolve_bridge_path",
            lambda env: llm,
        )
        cfg = LLMConfig.load()
        # Falls through to dataclass defaults
        assert cfg.provider == "auto"
        assert cfg.base_url == "https://api.openai.com/v1"

    def test_malformed_bridge_file_falls_back_to_defaults(self, tmp_path, monkeypatch):
        for k in ("OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_MODEL",
                  "LLM_API_KEY"):
            monkeypatch.delenv(k, raising=False)
        llm = tmp_path / "llm.json"
        llm.write_text("not json {{{")
        monkeypatch.setattr(
            "agentnodes.foundation.llm.config._resolve_bridge_path",
            lambda env: llm,
        )
        cfg = LLMConfig.load()
        assert cfg.provider == "auto"


# ── Module metadata ──────────────────────────────────────────────────


def test_module_has_mit_attribution():
    """Top-of-file SPDX-License-Identifier must say MIT."""
    import agentnodes.foundation.llm.quantnodes_bridge as m
    src = Path(m.__file__).read_text(encoding="utf-8")
    assert "SPDX-License-Identifier: MIT" in src
    assert "Derived from QuantNodes" in src


def test_config_path_canonical():
    assert CONFIG_PATH == Path.home() / ".quantnodes" / "llm.json"
