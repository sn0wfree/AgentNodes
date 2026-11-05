"""Tests for provider-profile switching (llm.json ``profiles``).

Covers:
    - Profile merge priority: base < profile < AGENTNODES__LLM__* env
    - ``LLM_PROFILE`` env selection over ``active_profile``
    - Backward compat: llm.json without ``profiles`` key
    - ``env:VAR`` expansion for profile api_key
    - ``llm`` CLI: --use (atomic + backup + auto-create), --add-key,
      --list, and ``--llm-profile`` single-run override
"""

from __future__ import annotations

import json
import logging
import os
import stat
from pathlib import Path

import pytest

# Windows has no POSIX permission bits; chmod 0o600 is not observable
# (pre-existing platform gap).
_posix_only = pytest.mark.skipif(
    os.name == "nt", reason="POSIX permission semantics unavailable on Windows"
)

from agentnodes.interfaces.cli.commands import llm as llm_cmd
from agentnodes.foundation.llm import LLMConfig
from agentnodes.foundation.llm.config import (
    ENV_PROFILE_ACTIVE,
)

PROFILE_JSON = {
    "llm": {
        "active_profile": "minimax",
        "timeout": 300,
        "profiles": {
            "minimax": {
                "provider": "minimax",
                "model": "minimax-M3",
                "api_key": "env:MINIMAX_API_KEY",
                "base_url": "https://api.minimaxi.com/v1",
                "model_max_output_tokens": 32000,
            },
            "nvidia": {
                "provider": "nvidia",
                "model": "z-ai/glm-5.2",
                "api_key": "env:NVIDIA_API_KEY",
                "base_url": "https://integrate.api.nvidia.com/v1",
            },
        },
    }
}


@pytest.fixture
def fake_llm_json(tmp_path: Path, monkeypatch) -> Path:
    p = tmp_path / "llm.json"
    monkeypatch.setattr(
        "agentnodes.foundation.llm.config._resolve_bridge_path",
        lambda env: p,
    )
    return p


@pytest.fixture
def clear_env(monkeypatch):
    for k in (
        "OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_MODEL",
        "LLM_API_KEY", ENV_PROFILE_ACTIVE,
        "MINIMAX_API_KEY", "NVIDIA_API_KEY",
        "AGENTNODES__LLM__PROVIDER", "AGENTNODES__LLM__MODEL",
        "AGENTNODES__LLM__BASE_URL", "AGENTNODES__LLM__API_KEY",
    ):
        monkeypatch.delenv(k, raising=False)


# ── Profile resolution (config layer) ────────────────────────────────


class TestProfileResolution:
    def test_active_profile_merges_over_base(
        self, clear_env, fake_llm_json, monkeypatch
    ):
        fake_llm_json.write_text(json.dumps(PROFILE_JSON))
        monkeypatch.setenv("MINIMAX_API_KEY", "sk-minimax-test")
        cfg = LLMConfig.load()
        assert cfg.provider == "minimax"
        assert cfg.model == "minimax-M3"
        assert cfg.base_url == "https://api.minimaxi.com/v1"
        assert cfg.api_key == "sk-minimax-test"
        assert cfg.max_tokens == 32000  # profile-provided
        assert cfg.timeout_s == 300.0   # base-level, untouched

    def test_profile_env_var_expansion(self, clear_env, fake_llm_json, monkeypatch):
        fake_llm_json.write_text(json.dumps(PROFILE_JSON))
        monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-nvidia-test")
        monkeypatch.setenv(ENV_PROFILE_ACTIVE, "nvidia")
        cfg = LLMConfig.load()
        assert cfg.provider == "nvidia"
        assert cfg.api_key == "nvapi-nvidia-test"

    def test_context_window_backfilled_from_catalog(
        self, clear_env, fake_llm_json, monkeypatch
    ):
        """Profile without model_context_tokens gets the real window
        from the bundled catalog (deepseek-chat TOML: 1M context)."""
        fake_llm_json.write_text(json.dumps({
            "llm": {
                "active_profile": "deepseek",
                "profiles": {
                    "deepseek": {
                        "provider": "deepseek",
                        "model": "deepseek-chat",
                        "api_key": "env:DEEPSEEK_API_KEY",
                    },
                },
            },
        }))
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-ds-test")
        cfg = LLMConfig.load()
        assert cfg.model_context_tokens == 1_000_000
        assert cfg.model_max_output_tokens == 384_000

    def test_context_window_falls_back_to_adapter_default(
        self, clear_env, fake_llm_json, monkeypatch
    ):
        """No bundled TOML for the model (siliconflow) → the adapter's
        default_context_tokens (131072) is used, not the 8192 base."""
        fake_llm_json.write_text(json.dumps({
            "llm": {
                "active_profile": "siliconflow",
                "profiles": {
                    "siliconflow": {
                        "provider": "siliconflow",
                        "model": "deepseek-ai/DeepSeek-V4-Flash",
                        "api_key": "env:SILICONFLOW_API_KEY",
                    },
                },
            },
        }))
        monkeypatch.setenv("SILICONFLOW_API_KEY", "sk-sf-test")
        cfg = LLMConfig.load()
        # Catalog may refresh; DeepSeek-V4-Flash window is at least 128k.
        assert cfg.model_context_tokens is not None
        assert cfg.model_context_tokens >= 131_072

    def test_context_window_explicit_profile_value_wins(
        self, clear_env, fake_llm_json, monkeypatch
    ):
        """A profile-provided model_context_tokens beats catalog data."""
        fake_llm_json.write_text(json.dumps({
            "llm": {
                "active_profile": "deepseek",
                "profiles": {
                    "deepseek": {
                        "provider": "deepseek",
                        "model": "deepseek-chat",
                        "api_key": "env:DEEPSEEK_API_KEY",
                        "model_context_tokens": 65536,
                    },
                },
            },
        }))
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-ds-test")
        cfg = LLMConfig.load()
        assert cfg.model_context_tokens == 65536

    def test_llm_profile_env_beats_active_profile(
        self, clear_env, fake_llm_json, monkeypatch
    ):
        fake_llm_json.write_text(json.dumps(PROFILE_JSON))
        monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-x")
        monkeypatch.setenv(ENV_PROFILE_ACTIVE, "nvidia")  # file says minimax
        cfg = LLMConfig.load()
        assert cfg.provider == "nvidia"

    def test_bridge_env_override_beats_profile(
        self, clear_env, fake_llm_json, monkeypatch
    ):
        fake_llm_json.write_text(json.dumps(PROFILE_JSON))
        monkeypatch.setenv("AGENTNODES__LLM__PROVIDER", "openai")
        cfg = LLMConfig.load()
        assert cfg.provider == "openai"
        # but profile fields for non-overridden keys still apply
        assert cfg.base_url == "https://api.minimaxi.com/v1"

    def test_profile_unknown_falls_back_to_base(
        self, clear_env, fake_llm_json
    ):
        fake_llm_json.write_text(json.dumps({
            "llm": {
                "active_profile": "ghost",
                "provider": "openai",
                "model": "gpt-4o-mini",
                "profiles": {},
            }
        }))
        cfg = LLMConfig.load()
        assert cfg.provider == "openai"
        assert cfg.model == "gpt-4o-mini"

    def test_old_format_no_profiles_still_works(
        self, clear_env, fake_llm_json
    ):
        fake_llm_json.write_text(json.dumps({
            "llm": {"provider": "minimax", "model": "minimax-M3"},
        }))
        cfg = LLMConfig.load()
        assert cfg.provider == "minimax"

    def test_active_profile_none_uses_base(self, clear_env, fake_llm_json):
        fake_llm_json.write_text(json.dumps(PROFILE_JSON))
        cfg = LLMConfig.load()
        assert cfg.provider == "minimax"  # file's active_profile still applies

    def test_profiles_keys_never_leak_into_config(
        self, clear_env, fake_llm_json
    ):
        fake_llm_json.write_text(json.dumps(PROFILE_JSON))
        cfg = LLMConfig.load()
        cfg_dict = cfg.masked_dict()
        assert "profiles" not in cfg_dict
        assert "active_profile" not in cfg_dict


# ── CLI: llm command ─────────────────────────────────────────────────


@pytest.fixture
def cli_paths(tmp_path: Path, monkeypatch) -> tuple[Path, Path]:
    llm_json = tmp_path / "llm.json"
    dotenv = tmp_path / ".env"
    monkeypatch.setattr(llm_cmd, "LLM_JSON_PATH", llm_json)
    monkeypatch.setattr(llm_cmd, "DOTENV_PATH", dotenv)
    monkeypatch.setattr(
        "agentnodes.foundation.llm.config._resolve_bridge_path",
        lambda env: llm_json,
    )
    return llm_json, dotenv


class TestCliUse:
    def test_use_switches_active_profile_atomically(
        self, clear_env, cli_paths, monkeypatch
    ):
        llm_json, _ = cli_paths
        llm_json.write_text(json.dumps(PROFILE_JSON))
        monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-x")
        rc = llm_cmd._action_use("nvidia")
        assert rc == 0
        data = json.loads(llm_json.read_text())
        assert data["llm"]["active_profile"] == "nvidia"
        # other top-level keys survive
        assert data["llm"]["timeout"] == 300

    def test_use_backs_up_before_switch(self, clear_env, cli_paths, monkeypatch):
        llm_json, _ = cli_paths
        llm_json.write_text(json.dumps(PROFILE_JSON))
        rc = llm_cmd._action_use("minimax")
        assert rc == 0
        backups = sorted(llm_json.parent.glob("llm.json.bak-*"))
        assert len(backups) == 1
        assert json.loads(backups[0].read_text())["llm"]["active_profile"] == "minimax"

    def test_use_autocreates_profile_from_defaults(
        self, clear_env, cli_paths, monkeypatch
    ):
        llm_json, _ = cli_paths
        llm_json.write_text(json.dumps({
            "llm": {"active_profile": "minimax", "timeout": 300},
        }))
        rc = llm_cmd._action_use("nvidia")
        assert rc == 0
        data = json.loads(llm_json.read_text())
        profile = data["llm"]["profiles"]["nvidia"]
        assert profile["provider"] == "nvidia"
        assert profile["api_key"] == "env:NVIDIA_API_KEY"
        assert profile["base_url"] == "https://integrate.api.nvidia.com/v1"

    def test_use_autocreates_siliconflow_profile(
        self, clear_env, cli_paths
    ):
        llm_json, _ = cli_paths
        llm_json.write_text(json.dumps({
            "llm": {"active_profile": "minimax", "timeout": 300},
        }))
        rc = llm_cmd._action_use("siliconflow")
        assert rc == 0
        data = json.loads(llm_json.read_text())
        profile = data["llm"]["profiles"]["siliconflow"]
        assert profile["provider"] == "siliconflow"
        assert profile["api_key"] == "env:SILICONFLOW_API_KEY"
        assert profile["base_url"] == "https://api.siliconflow.cn/v1"
        assert profile["model"] == "deepseek-ai/DeepSeek-V4-Flash"

    def test_use_unknown_provider_errors(self, clear_env, cli_paths):
        llm_json, _ = cli_paths
        llm_json.write_text(json.dumps(PROFILE_JSON))
        assert llm_cmd._action_use("ghost") == 1

    def test_use_warns_when_key_missing(
        self, clear_env, cli_paths, capsys
    ):
        llm_json, _ = cli_paths
        llm_json.write_text(json.dumps(PROFILE_JSON))
        rc = llm_cmd._action_use("minimax")
        assert rc == 0
        assert "未设置" in capsys.readouterr().out


class TestCliAddKey:
    def test_add_key_writes_dotenv(self, clear_env, cli_paths, monkeypatch):
        llm_json, dotenv = cli_paths
        dotenv.write_text("EXISTING=1\n", encoding="utf-8")
        monkeypatch.setattr("getpass.getpass", lambda prompt: "sk-qwen-123")
        assert llm_cmd._action_add_key("qwen") == 0
        content = dotenv.read_text(encoding="utf-8")
        assert "QWEN_API_KEY=sk-qwen-123" in content
        assert "EXISTING=1" in content  # other lines preserved

    def test_add_key_empty_cancels(self, clear_env, cli_paths, monkeypatch):
        _, dotenv = cli_paths
        monkeypatch.setattr("getpass.getpass", lambda prompt: "   ")
        assert llm_cmd._action_add_key("qwen") == 1
        assert not dotenv.exists()


class TestCliList:
    def test_list_marks_active_and_key_state(
        self, clear_env, cli_paths, capsys, monkeypatch
    ):
        llm_json, _ = cli_paths
        llm_json.write_text(json.dumps(PROFILE_JSON))
        monkeypatch.setenv("MINIMAX_API_KEY", "sk-x")
        assert llm_cmd._action_list() == 0
        out = capsys.readouterr().out
        assert "* minimax" in out
        assert "MINIMAX_API_KEY" in out
        assert "NVIDIA_API_KEY" in out


class TestCliProfileFlag:
    def test_llm_profile_flag_sets_env(
        self, clear_env, monkeypatch
    ):
        from types import SimpleNamespace

        from agentnodes.interfaces.cli.llm_config import build_llm_config
        args = SimpleNamespace(llm_profile="nvidia")
        build_llm_config(args)
        assert os.environ.get(ENV_PROFILE_ACTIVE) == "nvidia"
        # build_llm_config writes os.environ DIRECTLY (not via
        # monkeypatch), so it survives the test — clean it up or it
        # leaks into every later test that resolves profiles
        # (e.g. test_system_llm_api::test_profiles_only_config_...).
        os.environ.pop(ENV_PROFILE_ACTIVE, None)

    def test_llm_profile_excluded_from_cli_overrides(self, monkeypatch):
        from types import SimpleNamespace

        from agentnodes.interfaces.cli.llm_config import _cli_overrides_from_args
        args = SimpleNamespace(llm_profile="nvidia", llm_model="x")
        out = _cli_overrides_from_args(args)
        assert out == {"llm_model": "x"}


# ── A: profile resolution edge cases ────────────────────────────────


class TestProfileResolutionEdge:
    def test_enabled_false_skips_profile(self, clear_env, fake_llm_json):
        data = dict(PROFILE_JSON)
        data["llm"] = dict(data["llm"], enabled=False)
        fake_llm_json.write_text(json.dumps(data))
        cfg = LLMConfig.load()
        assert cfg.provider == "auto"  # bridge disabled → defaults

    def test_empty_llm_profile_env_falls_back_to_file(
        self, clear_env, fake_llm_json, monkeypatch
    ):
        fake_llm_json.write_text(json.dumps(PROFILE_JSON))
        monkeypatch.setenv("MINIMAX_API_KEY", "sk-x")
        monkeypatch.setenv(ENV_PROFILE_ACTIVE, "")  # empty → falsy
        cfg = LLMConfig.load()
        assert cfg.provider == "minimax"  # file's active_profile used

    def test_profile_empty_values_keep_base(self, clear_env, fake_llm_json):
        fake_llm_json.write_text(json.dumps({
            "llm": {
                "active_profile": "minimax",
                "provider": "openai",
                "model": "gpt-4o-mini",
                "base_url": "https://api.openai.com/v1",
                "profiles": {
                    "minimax": {
                        "provider": "minimax",
                        "model": None,
                        "base_url": "",
                        "api_key": "env:MINIMAX_API_KEY",
                    },
                },
            }
        }))
        cfg = LLMConfig.load()
        assert cfg.provider == "minimax"
        assert cfg.model == "gpt-4o-mini"          # base kept
        assert cfg.base_url == "https://api.openai.com/v1"  # base kept

    def test_profile_meta_keys_skipped(self, clear_env, fake_llm_json):
        fake_llm_json.write_text(json.dumps({
            "llm": {
                "active_profile": "minimax",
                "profiles": {
                    "minimax": {
                        "provider": "minimax",
                        "profiles": {"nested": 1},
                        "active_profile": "nested",
                    },
                },
            }
        }))
        cfg = LLMConfig.load()
        assert cfg.provider == "minimax"  # meta keys never merged

    def test_unknown_profile_warns(self, clear_env, fake_llm_json, caplog):
        fake_llm_json.write_text(json.dumps({
            "llm": {"active_profile": "ghost", "provider": "openai",
                    "profiles": {"minimax": {"provider": "minimax"}}}
        }))
        with caplog.at_level(logging.WARNING,
                             logger="agentnodes.foundation.llm.config"):
            cfg = LLMConfig.load()
        assert cfg.provider == "openai"  # base untouched
        assert "ghost" in caplog.text

    def test_profiles_not_a_dict_ignored(self, clear_env, fake_llm_json):
        fake_llm_json.write_text(json.dumps({
            "llm": {"active_profile": "minimax", "provider": "openai",
                    "model": "gpt-4o-mini", "profiles": "nope"}
        }))
        cfg = LLMConfig.load()
        assert cfg.provider == "openai"
        assert cfg.model == "gpt-4o-mini"

    def test_profile_env_var_unset_yields_empty_key(
        self, clear_env, fake_llm_json
    ):
        fake_llm_json.write_text(json.dumps(PROFILE_JSON))
        # load_dotenv=False keeps the (deleted) MINIMAX_API_KEY unset,
        # otherwise _try_load_dotenv would re-import it from ~/.quantnodes/.env
        cfg = LLMConfig.load(load_dotenv=False)
        assert cfg.provider == "minimax"
        assert cfg.api_key == ""

    def test_cli_overrides_beat_profile(self, clear_env, fake_llm_json):
        fake_llm_json.write_text(json.dumps(PROFILE_JSON))
        cfg = LLMConfig.load(cli_overrides={"llm_model": "cli-model"})
        assert cfg.model == "cli-model"  # cli > profile
        assert cfg.provider == "minimax"  # non-overridden still from profile

    def test_bridge_env_api_key_beats_profile(
        self, clear_env, fake_llm_json, monkeypatch
    ):
        fake_llm_json.write_text(json.dumps(PROFILE_JSON))
        monkeypatch.setenv("AGENTNODES__LLM__API_KEY", "env-api-key")
        cfg = LLMConfig.load()
        assert cfg.api_key == "env-api-key"  # env override > profile


# ── B: CLI sub-actions & edge cases ─────────────────────────────────


class TestCliUseEdge:
    def test_use_preserves_other_top_level_keys(self, clear_env, cli_paths):
        llm_json, _ = cli_paths
        data = dict(PROFILE_JSON)
        data["tools"] = ["mcp_tool"]
        data["agents"] = {"planner": "haiku"}
        llm_json.write_text(json.dumps(data))
        rc = llm_cmd._action_use("minimax")
        assert rc == 0
        saved = json.loads(llm_json.read_text())
        assert saved["tools"] == ["mcp_tool"]
        assert saved["agents"] == {"planner": "haiku"}

    def test_use_creates_file_when_missing(self, clear_env, cli_paths):
        llm_json, _ = cli_paths
        assert not llm_json.exists()
        assert llm_cmd._action_use("nvidia") == 0
        assert llm_json.exists()
        saved = json.loads(llm_json.read_text())
        assert saved["llm"]["active_profile"] == "nvidia"
        assert "nvidia" in saved["llm"]["profiles"]

    def test_use_twice_creates_distinct_backups(
        self, clear_env, cli_paths, monkeypatch
    ):
        llm_json, _ = cli_paths
        llm_json.write_text(json.dumps(PROFILE_JSON))
        monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-x")
        assert llm_cmd._action_use("minimax") == 0
        assert llm_cmd._action_use("nvidia") == 0
        backups = sorted(llm_json.parent.glob("llm.json.bak-*"))
        assert len(backups) == 2          # microsecond timestamps never collide
        assert backups[0].name != backups[1].name
        saved = json.loads(llm_json.read_text())
        assert saved["llm"]["active_profile"] == "nvidia"  # final state

    def test_backup_missing_file_returns_none(self, clear_env, cli_paths):
        llm_json, _ = cli_paths
        assert not llm_json.exists()
        assert llm_cmd._backup_llm_json() is None

    def test_load_profiles_malformed_json(self, clear_env, cli_paths):
        llm_json, _ = cli_paths
        llm_json.write_text("{ not json !!!", encoding="utf-8")
        assert llm_cmd._load_profiles() == {}

    @_posix_only
    def test_atomic_write_llm_json_chmod_600(self, clear_env, cli_paths):
        llm_json, _ = cli_paths
        llm_json.write_text(json.dumps({"llm": {}}))
        llm_json.chmod(0o644)
        llm_cmd._atomic_write_llm_json({"llm": {"active_profile": "x"}})
        mode = stat.S_IMODE(llm_json.stat().st_mode)
        assert mode == 0o600

    def test_add_key_overwrites_existing(self, clear_env, cli_paths, monkeypatch):
        llm_json, dotenv = cli_paths
        dotenv.write_text("QWEN_API_KEY=old-key\n", encoding="utf-8")
        monkeypatch.setattr("getpass.getpass", lambda prompt: "sk-new-key")
        assert llm_cmd._action_add_key("qwen") == 0
        content = dotenv.read_text(encoding="utf-8")
        assert content.count("QWEN_API_KEY=") == 1
        assert "QWEN_API_KEY=sk-new-key" in content

    @_posix_only
    def test_write_dotenv_chmod_600(self, clear_env, cli_paths):
        llm_json, dotenv = cli_paths
        llm_cmd._write_dotenv({"SOME_KEY": "v"})
        mode = stat.S_IMODE(dotenv.stat().st_mode)
        assert mode == 0o600

    def test_profile_defaults_unknown_provider(self):
        profile = llm_cmd._profile_defaults("ghost")
        assert profile["provider"] == "ghost"
        assert profile["api_key"] == "env:GHOST_API_KEY"


class TestCliShow:
    def test_show_prints_masked_config(
        self, clear_env, cli_paths, capsys, monkeypatch
    ):
        llm_json, _ = cli_paths
        llm_json.write_text(json.dumps(PROFILE_JSON))
        monkeypatch.setenv("MINIMAX_API_KEY", "sk-secret-123")
        assert llm_cmd._action_show() == 0
        out = capsys.readouterr().out
        assert "minimax" in out
        assert "minimax-M3" in out
        assert "sk-" in out and "secret-123" not in out  # masked
        assert "active_profile = minimax" in out

    def test_show_no_profile_line_when_none(self, clear_env, cli_paths, capsys):
        llm_json, _ = cli_paths
        llm_json.write_text(json.dumps({"llm": {"provider": "openai"}}))
        assert llm_cmd._action_show() == 0
        out = capsys.readouterr().out
        assert "active_profile = (none)" in out


class TestCliHelpers:
    def test_key_var_for(self):
        assert llm_cmd._key_var_for("openai") == "OPENAI_API_KEY"
        assert llm_cmd._key_var_for("deepseek") == "DEEPSEEK_API_KEY"

    def test_key_is_set(self, clear_env, monkeypatch):
        assert llm_cmd._key_is_set("openai") is False
        monkeypatch.setenv("OPENAI_API_KEY", "sk-x")
        assert llm_cmd._key_is_set("openai") is True

    def test_list_empty_registry(self, clear_env, cli_paths, capsys, monkeypatch):
        llm_json, _ = cli_paths
        llm_json.write_text(json.dumps({"llm": {}}))  # no profiles either
        monkeypatch.setattr(llm_cmd, "_registry_providers", lambda: {})
        assert llm_cmd._action_list() == 0
        out = capsys.readouterr().out
        assert "(no providers registered)" in out

    def test_list_profile_only_row(self, clear_env, cli_paths, capsys):
        llm_json, _ = cli_paths
        llm_json.write_text(json.dumps({
            "llm": {"active_profile": "homelab",
                    "profiles": {"homelab": {"provider": "homelab"}}}
        }))
        assert llm_cmd._action_list() == 0
        out = capsys.readouterr().out
        assert "* homelab" in out
        assert "(profile only)" in out

    def test_cmd_llm_no_flags_prints_usage(self, clear_env):
        from types import SimpleNamespace

        args = SimpleNamespace(list=False, use=None, show=False, add_key=None)
        assert llm_cmd.cmd_llm(args) == 0


# ── C: onboard._attach_profile ──────────────────────────────────────


class TestAttachProfile:
    @pytest.fixture
    def provider(self):
        from agentnodes.interfaces.cli.onboard import PROVIDERS
        return {p.key: p for p in PROVIDERS}

    def test_attach_copies_fields(self, provider):
        from agentnodes.interfaces.cli.onboard import _attach_profile
        llm = {"provider": "openai", "model": "gpt-4o",
               "api_key": "env:LLM_API_KEY"}
        out = _attach_profile(dict(llm), provider["openai"])
        assert out["active_profile"] == "openai"
        assert out["profiles"]["openai"] == {
            "provider": "openai",
            "base_url": "https://api.openai.com/v1",
            "model": "gpt-4o",
            "api_key": "env:LLM_API_KEY",
        }

    def test_attach_omits_missing_model(self, provider):
        from agentnodes.interfaces.cli.onboard import _attach_profile
        llm = {"provider": "openai"}
        out = _attach_profile(llm, provider["openai"])
        assert "model" not in out["profiles"]["openai"]

    def test_attach_omits_missing_api_key(self, provider):
        from agentnodes.interfaces.cli.onboard import _attach_profile
        llm = {"provider": "ollama", "model": "qwen2.5:32b"}
        out = _attach_profile(llm, provider["ollama"])
        profile = out["profiles"]["ollama"]
        assert "api_key" not in profile
        assert profile["base_url"] == "http://localhost:11434"
        assert profile["model"] == "qwen2.5:32b"
        assert out["active_profile"] == "ollama"

    def test_attach_preserves_other_llm_keys(self, provider):
        from agentnodes.interfaces.cli.onboard import _attach_profile
        llm = {"provider": "openai", "timeout": 300, "max_retries": 2}
        out = _attach_profile(llm, provider["openai"])
        assert out["timeout"] == 300
        assert out["max_retries"] == 2


# ── E: cli/llm_config.py edge cases ─────────────────────────────────


class TestCliProfileFlagEdge:
    def test_llm_profile_none_leaves_env_untouched(
        self, clear_env, monkeypatch
    ):
        from types import SimpleNamespace

        from agentnodes.interfaces.cli.llm_config import _cli_overrides_from_args
        monkeypatch.setenv(ENV_PROFILE_ACTIVE, "existing")
        args = SimpleNamespace(llm_profile=None, llm_model="x")
        out = _cli_overrides_from_args(args)
        assert os.environ.get(ENV_PROFILE_ACTIVE) == "existing"
        assert out == {"llm_model": "x"}

    def test_build_llm_config_none_args(self, clear_env, monkeypatch):
        from agentnodes.interfaces.cli.llm_config import build_llm_config
        monkeypatch.setenv(ENV_PROFILE_ACTIVE, "existing")
        cfg = build_llm_config(None)
        assert cfg is not None
        assert os.environ.get(ENV_PROFILE_ACTIVE) == "existing"
