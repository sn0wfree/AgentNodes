"""Tests for LLMConfig 4-layer merge.

Covers:
    - Code defaults
    - Bridge layer (``~/.quantnodes/llm.json`` via quantnodes_bridge)
    - Env var overrides
    - CLI overrides
    - Priority chain (CLI > env > bridge > defaults)
    - .env loading (no-op when python-dotenv missing)
    - CLI flag mapping helpers

(Yaml/profile system retired in v0.5.0; see ``quantnodes_bridge.py`` for
the canonical reader.)
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

import agentnodes.foundation.llm.config as _cfg_mod
from agentnodes.foundation.llm import LLMConfig
from agentnodes.foundation.llm.config import (
    DEFAULT_LLM_CONFIG_PATH,
    ENV_API_KEY,
    ENV_BASE_URL,
    ENV_CONFIG_PATH,
    ENV_LLM_API_KEY,
    ENV_MODEL,
    ENV_PROFILE,
    _cli_to_overrides,
    _env_to_overrides,
    apply_api_key,
    find_llm_config_path,
    load_api_key_from_env,
)

# conftest._purge_llm_env replaces _try_load_dotenv with a no-op per test;
# capture the original here (module import happens before any fixture runs)
# so dotenv-loading tests can re-enable the real implementation.
_ORIGINAL_TRY_LOAD_DOTENV = _cfg_mod._try_load_dotenv


# ── Fixtures ────────────────────────────────────────────────────────


@pytest.fixture
def fake_llm_json(tmp_path: Path, monkeypatch) -> Path:
    """Bridge-shaped JSON file pointing LLMConfig at tmp_path."""
    p = tmp_path / "llm.json"
    monkeypatch.setattr(
        "agentnodes.foundation.llm.config._resolve_bridge_path",
        lambda env: p,
    )
    return p


@pytest.fixture
def clear_env(monkeypatch):
    for k in (ENV_API_KEY, ENV_BASE_URL, ENV_MODEL, ENV_LLM_API_KEY,
              "AGENTNODES__LLM__PROVIDER", "AGENTNODES__LLM__MODEL",
              "AGENTNODES__LLM__BASE_URL", "AGENTNODES__LLM__API_KEY",
              "AGENTNODES__LLM__TIMEOUT", "AGENTNODES__LLM__MAX_RETRIES",
              "AGENTNODES__LLM__MAX_TOKENS", "AGENTNODES__LLM__ENABLED"):
        monkeypatch.delenv(k, raising=False)


# ── Code defaults ────────────────────────────────────────────────────


class TestCodeDefaults:
    def test_defaults_when_no_config(self, clear_env, fake_llm_json):
        fake_llm_json.write_text(json.dumps({"llm": {}}))
        cfg = LLMConfig.load()
        assert cfg.provider == "auto"
        assert cfg.base_url == "https://api.openai.com/v1"
        assert cfg.model == "gpt-4o-mini"
        assert cfg.temperature == 0.7
        # max_tokens falls through to the global conservative default when
        # no provider is specified (provider="auto" → no provider default).
        from agentnodes.foundation.llm.config import _DEFAULT_MAX_TOKENS
        assert cfg.max_tokens == _DEFAULT_MAX_TOKENS
        assert cfg.timeout_s == 60.0
        assert cfg.max_retries == 3
        assert cfg.stream is True
        # Iteration budget defaults (chat bounded, agent high ceiling)
        assert cfg.max_iterations == 50
        assert cfg.agent_max_iterations == 9999


class TestIterationBudget:
    """max_iterations / agent_max_iterations from llm.json."""

    def test_custom_values_from_json(self, clear_env, fake_llm_json):
        fake_llm_json.write_text(json.dumps({
            "llm": {"max_iterations": 30, "agent_max_iterations": 5000},
        }))
        cfg = LLMConfig.load()
        assert cfg.max_iterations == 30
        assert cfg.agent_max_iterations == 5000

    def test_partial_override_preserves_default(self, clear_env, fake_llm_json):
        fake_llm_json.write_text(json.dumps({"llm": {"max_iterations": 7}}))
        cfg = LLMConfig.load()
        assert cfg.max_iterations == 7
        assert cfg.agent_max_iterations == 9999  # untouched default

    def test_non_numeric_ignored(self, clear_env, fake_llm_json):
        fake_llm_json.write_text(json.dumps({
            "llm": {"max_iterations": "many", "agent_max_iterations": 123},
        }))
        cfg = LLMConfig.load()
        # invalid int is dropped → default survives
        assert cfg.max_iterations == 50
        assert cfg.agent_max_iterations == 123


# ── Bridge layer ─────────────────────────────────────────────────────


class TestBridgeLayer:
    def test_bridge_provider_overrides_default(self, clear_env, fake_llm_json):
        fake_llm_json.write_text(json.dumps({"llm": {"provider": "anthropic"}}))
        cfg = LLMConfig.load()
        assert cfg.provider == "anthropic"

    def test_bridge_timeout_overrides_default(self, clear_env, fake_llm_json):
        fake_llm_json.write_text(json.dumps({"llm": {"timeout": 600}}))
        cfg = LLMConfig.load()
        assert cfg.timeout_s == 600.0

    def test_bridge_provider_to_base_url_fallback(
        self, clear_env, fake_llm_json
    ):
        """If provider is set but base_url is omitted, the bridge
        translator falls back to PROVIDER_DEFAULTS."""
        fake_llm_json.write_text(json.dumps({"llm": {"provider": "minimax"}}))
        cfg = LLMConfig.load()
        assert cfg.base_url == "https://api.minimaxi.com/v1"

    def test_bridge_preserves_other_top_level_keys(
        self, clear_env, fake_llm_json
    ):
        """B-side keys like "tools" / "agents" survive the bridge read."""
        fake_llm_json.write_text(json.dumps({
            "tools": ["mcp_tool"],
            "agents": {"planner": "haiku"},
            "llm": {"provider": "openai"},
        }))
        # Reload + verify by re-reading file
        cfg = LLMConfig.load()
        assert cfg.provider == "openai"
        # File still has the B-side keys
        data = json.loads(fake_llm_json.read_text())
        assert data["tools"] == ["mcp_tool"]
        assert data["agents"] == {"planner": "haiku"}

    def test_enabled_false_skips_bridge(
        self, clear_env, fake_llm_json
    ):
        fake_llm_json.write_text(json.dumps(
            {"llm": {"provider": "anthropic", "enabled": False}}
        ))
        cfg = LLMConfig.load()
        assert cfg.provider == "auto"  # bridge disabled → defaults


# ── Env layer ────────────────────────────────────────────────────────


class TestEnvLayer:
    def test_env_model_overrides_default(self, clear_env, fake_llm_json, monkeypatch):
        monkeypatch.setenv(ENV_MODEL, "gpt-4-turbo")
        cfg = LLMConfig.load()
        assert cfg.model == "gpt-4-turbo"

    def test_env_base_url_overrides_default(self, clear_env, fake_llm_json, monkeypatch):
        monkeypatch.setenv(ENV_BASE_URL, "https://custom.example/v1")
        cfg = LLMConfig.load()
        assert cfg.base_url == "https://custom.example/v1"

    def test_env_overrides_bridge(self, clear_env, fake_llm_json, monkeypatch):
        fake_llm_json.write_text(json.dumps({"llm": {"model": "bridge-model"}}))
        monkeypatch.setenv(ENV_MODEL, "env-model")
        cfg = LLMConfig.load()
        assert cfg.model == "env-model"


# ── CLI layer ────────────────────────────────────────────────────────


class TestCliLayer:
    def test_cli_overrides_env(self, clear_env, fake_llm_json, monkeypatch):
        monkeypatch.setenv(ENV_MODEL, "env-model")
        cfg = LLMConfig.load(cli_overrides={"llm_model": "cli-model"})
        assert cfg.model == "cli-model"

    def test_cli_overrides_bridge(self, clear_env, fake_llm_json):
        fake_llm_json.write_text(json.dumps({"llm": {"model": "bridge-model"}}))
        cfg = LLMConfig.load(cli_overrides={"llm_model": "cli-model"})
        assert cfg.model == "cli-model"

    def test_cli_ignores_none_values(self, clear_env, fake_llm_json):
        cfg = LLMConfig.load(cli_overrides={"llm_model": None})
        assert cfg.model == "gpt-4o-mini"  # default

    def test_cli_stream_no_stream(self, clear_env, fake_llm_json):
        cfg = LLMConfig.load(cli_overrides={"llm_stream": False})
        assert cfg.stream is False
        cfg = LLMConfig.load(cli_overrides={"llm_no_stream": True})
        assert cfg.stream is False


# ── Priority chain ───────────────────────────────────────────────────


class TestPriority:
    def test_cli_wins_over_all(
        self, clear_env, fake_llm_json, monkeypatch
    ):
        fake_llm_json.write_text(json.dumps(
            {"llm": {"model": "bridge", "base_url": "https://bridge/v1"}}
        ))
        monkeypatch.setenv(ENV_MODEL, "env")
        monkeypatch.setenv(ENV_BASE_URL, "https://env/v1")
        cfg = LLMConfig.load(cli_overrides={
            "llm_model": "cli",
            "llm_base_url": "https://cli/v1",
        })
        assert cfg.model == "cli"
        assert cfg.base_url == "https://cli/v1"

    def test_env_wins_over_bridge(
        self, clear_env, fake_llm_json, monkeypatch
    ):
        fake_llm_json.write_text(json.dumps(
            {"llm": {"model": "bridge"}}
        ))
        monkeypatch.setenv(ENV_MODEL, "env")
        cfg = LLMConfig.load()
        assert cfg.model == "env"


# ── Helpers ──────────────────────────────────────────────────────────


class TestHelpers:
    def test_env_to_overrides_only_known_keys(self):
        out = _env_to_overrides({
            ENV_BASE_URL: "x", ENV_MODEL: "y",
            "UNRELATED_KEY": "z",
        })
        assert out == {"base_url": "x", "model": "y"}

    def test_env_to_overrides_skips_empty(self):
        out = _env_to_overrides({ENV_BASE_URL: "", ENV_MODEL: "y"})
        assert out == {"model": "y"}

    def test_cli_to_overrides_only_llm_prefixed(self):
        out = _cli_to_overrides({
            "llm_model": "x", "model": "y", "unrelated": "z",
        })
        assert out["model"] == "x"
        assert "unrelated" not in out

    def test_cli_to_overrides_handles_special_keys(self):
        out = _cli_to_overrides({
            "llm_temperature": "0.5",
            "llm_max_tokens": "100",
            "llm_timeout": "120",
            "llm_max_retries": "5",
            "llm_seed": "42",
        })
        assert out["temperature"] == 0.5
        assert out["max_tokens"] == 100
        assert out["timeout_s"] == 120.0
        assert out["max_retries"] == 5
        assert out["seed"] == 42

    def test_load_api_key_precedence(self, monkeypatch):
        monkeypatch.delenv(ENV_API_KEY, raising=False)
        monkeypatch.delenv(ENV_LLM_API_KEY, raising=False)
        # Neither set
        assert load_api_key_from_env() == ""
        # Only LLM_API_KEY
        monkeypatch.setenv(ENV_LLM_API_KEY, "sk-quantnodes")
        assert load_api_key_from_env() == "sk-quantnodes"
        # Both set: OPENAI wins
        monkeypatch.setenv(ENV_API_KEY, "sk-openai")
        assert load_api_key_from_env() == "sk-openai"

    def test_apply_api_key_only_when_empty(self):
        cfg = LLMConfig(api_key="")
        out = apply_api_key(cfg, env={ENV_API_KEY: "sk-x"})
        assert out.api_key == "sk-x"
        # already set → unchanged
        cfg2 = LLMConfig(api_key="existing")
        out2 = apply_api_key(cfg2, env={ENV_API_KEY: "sk-x"})
        assert out2.api_key == "existing"

    def test_find_llm_config_path(self, monkeypatch):
        # conftest sets STRATEGY_RESEARCH_LLM_CONFIG for test isolation;
        # clear it here to verify the default path resolution.
        monkeypatch.delenv(ENV_CONFIG_PATH, raising=False)
        p = find_llm_config_path()
        assert p.name == "llm.json"
        assert ".quantnodes" in str(p)


# ── Constants ────────────────────────────────────────────────────────


class TestConstants:
    def test_env_vars_present(self):
        assert ENV_API_KEY == "OPENAI_API_KEY"
        assert ENV_BASE_URL == "OPENAI_BASE_URL"
        assert ENV_MODEL == "OPENAI_MODEL"
        assert ENV_CONFIG_PATH == "STRATEGY_RESEARCH_LLM_CONFIG"
        assert ENV_LLM_API_KEY == "LLM_API_KEY"

    def test_legacy_env_profile_kept(self):
        """ENV_PROFILE is a back-compat alias and still importable."""
        assert ENV_PROFILE == "STRATEGY_RESEARCH_LLM_PROFILE"

    def test_default_config_path_points_at_quantnodes(self):
        assert DEFAULT_LLM_CONFIG_PATH.name == "llm.json"
        assert ".quantnodes" in str(DEFAULT_LLM_CONFIG_PATH)


# ── .env loading ─────────────────────────────────────────────────────


class TestDotenvLoading:
    def test_no_crash_when_no_dotenv(self, clear_env, fake_llm_json):
        fake_llm_json.write_text(json.dumps({"llm": {}}))
        # Should not raise even if dotenv import fails
        cfg = LLMConfig.load(load_dotenv=False)
        assert cfg.provider == "auto"

    def test_load_dotenv_false_skips(self, clear_env, fake_llm_json):
        fake_llm_json.write_text(json.dumps({"llm": {}}))
        cfg = LLMConfig.load(load_dotenv=False)
        assert cfg.api_key == ""

    def _restore_dotenv(self, monkeypatch) -> None:
        """Re-enable the real _try_load_dotenv (neutralized by conftest)."""
        monkeypatch.setattr(_cfg_mod, "_try_load_dotenv", _ORIGINAL_TRY_LOAD_DOTENV)

    def test_cwd_dotenv_loaded_from_library_frame(self, monkeypatch, tmp_path):
        """Regression: bare ``load_dotenv()`` searches from the library file's
        location, not the process cwd, so a workspace ``.env`` was never
        picked up in the serve process. The fix loads ``cwd/.env`` explicitly.
        """
        (tmp_path / ".env").write_text("AN_DOTENV_CWD_ONLY=1\n")
        # Isolate from the host's ~/.quantnodes/.env (real API keys).
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("USERPROFILE", str(tmp_path))
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("AN_DOTENV_CWD_ONLY", raising=False)
        self._restore_dotenv(monkeypatch)
        _ORIGINAL_TRY_LOAD_DOTENV()
        assert os.environ.get("AN_DOTENV_CWD_ONLY") == "1"

    def test_cwd_dotenv_does_not_override_process_env(self, monkeypatch, tmp_path):
        """dotenv uses override=False: explicit process env vars always win."""
        (tmp_path / ".env").write_text("AN_DOTENV_CWD_ONLY=from-file\n")
        monkeypatch.setenv("AN_DOTENV_CWD_ONLY", "from-process-env")
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("USERPROFILE", str(tmp_path))
        monkeypatch.chdir(tmp_path)
        self._restore_dotenv(monkeypatch)
        _ORIGINAL_TRY_LOAD_DOTENV()
        assert os.environ["AN_DOTENV_CWD_ONLY"] == "from-process-env"

    def test_quantnodes_dotenv_still_loaded(self, monkeypatch, tmp_path):
        """The canonical ~/.quantnodes/.env load must keep working."""
        qdir = tmp_path / ".quantnodes"
        qdir.mkdir()
        (qdir / ".env").write_text("AN_DOTENV_QUANTNODES=1\n")
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("USERPROFILE", str(tmp_path))
        monkeypatch.chdir(tmp_path)  # cwd has no .env — only quantnodes has it
        self._restore_dotenv(monkeypatch)
        _ORIGINAL_TRY_LOAD_DOTENV()
        assert os.environ.get("AN_DOTENV_QUANTNODES") == "1"

    def test_cwd_dotenv_takes_priority_over_quantnodes_env(self, monkeypatch, tmp_path):
        """cwd .env is loaded before ~/.quantnodes/.env; with override=False
        the earlier (cwd) value wins over a conflicting quantnodes value."""
        (tmp_path / ".env").write_text("AN_DOTENV_BOTH=cwd\n")
        qdir = tmp_path / ".quantnodes"
        qdir.mkdir()
        (qdir / ".env").write_text("AN_DOTENV_BOTH=quantnodes\n")
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("USERPROFILE", str(tmp_path))
        monkeypatch.chdir(tmp_path)
        self._restore_dotenv(monkeypatch)
        _ORIGINAL_TRY_LOAD_DOTENV()
        assert os.environ.get("AN_DOTENV_BOTH") == "cwd"
