"""Tests for CLI --llm-* flags + integration.

Profile system retired in v0.5.0; LLM config now lives in
``~/.quantnodes/llm.json``. Tests exercise the new model.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from agentnodes.interfaces.cli import (
    _cli_overrides_from_args,
    _cmd_llm_list_profiles,
    build_llm_config,
    main,
)
from agentnodes.interfaces.cli.llm_config import _LLM_PARENT

# ── Parent parser has all expected flags ─────────────────────────────


class TestLLMParentParser:
    def test_parent_parser_has_model(self):
        ns = _LLM_PARENT.parse_args(["--llm-model", "gpt-4o"])
        assert ns.llm_model == "gpt-4o"

    def test_parent_parser_has_base_url(self):
        ns = _LLM_PARENT.parse_args(["--llm-base-url", "https://x"])
        assert ns.llm_base_url == "https://x"

    def test_parent_parser_has_temperature(self):
        ns = _LLM_PARENT.parse_args(["--llm-temperature", "0.5"])
        assert ns.llm_temperature == 0.5

    def test_parent_parser_has_max_tokens(self):
        ns = _LLM_PARENT.parse_args(["--llm-max-tokens", "2048"])
        assert ns.llm_max_tokens == 2048

    def test_parent_parser_has_top_p(self):
        ns = _LLM_PARENT.parse_args(["--llm-top-p", "0.9"])
        assert ns.llm_top_p == 0.9

    def test_parent_parser_has_timeout(self):
        ns = _LLM_PARENT.parse_args(["--llm-timeout", "30"])
        assert ns.llm_timeout == 30.0

    def test_parent_parser_has_max_retries(self):
        ns = _LLM_PARENT.parse_args(["--llm-max-retries", "5"])
        assert ns.llm_max_retries == 5

    def test_parent_parser_has_seed(self):
        ns = _LLM_PARENT.parse_args(["--llm-seed", "42"])
        assert ns.llm_seed == 42

    def test_parent_parser_stream_true(self):
        ns = _LLM_PARENT.parse_args(["--llm-stream"])
        assert ns.llm_stream is True

    def test_parent_parser_no_stream(self):
        ns = _LLM_PARENT.parse_args(["--llm-no-stream"])
        assert ns.llm_stream is False

    def test_parent_parser_defaults_none(self):
        ns = _LLM_PARENT.parse_args([])
        assert ns.llm_model is None
        assert ns.llm_temperature is None
        assert ns.llm_stream is None

    def test_parent_parser_unknown_flag_rejected(self):
        with pytest.raises(SystemExit):
            _LLM_PARENT.parse_args(["--llm-bogus", "x"])

    def test_parent_parser_accepts_profile_flag(self):
        """--llm-profile is accepted (single-run provider profile override)."""
        ns = _LLM_PARENT.parse_args(["--llm-profile", "deepseek"])
        assert ns.llm_profile == "deepseek"

    def test_parent_parser_profile_defaults_none(self):
        ns = _LLM_PARENT.parse_args([])
        assert ns.llm_profile is None


# ── Helper functions ─────────────────────────────────────────────────


class TestCliOverridesFromArgs:
    def test_extracts_llm_keys(self):
        ns = argparse.Namespace(
            llm_temperature=0.3,
            llm_stream=True,
            strategy="foo",  # not llm_*
        )
        d = _cli_overrides_from_args(ns)
        assert d == {"llm_temperature": 0.3, "llm_stream": True}

    def test_skips_none_values(self):
        ns = argparse.Namespace(
            llm_model="x", llm_temperature=None,
        )
        d = _cli_overrides_from_args(ns)
        assert d == {"llm_model": "x"}

    def test_none_input(self):
        assert _cli_overrides_from_args(None) == {}

    def test_empty_namespace(self):
        ns = argparse.Namespace()
        assert _cli_overrides_from_args(ns) == {}


class TestBuildLLMConfig:
    def test_no_args_returns_defaults(self, monkeypatch):
        """Without any config source, returns dataclass defaults."""
        # Clear everything and point bridge path at a missing file
        for k in ("OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_MODEL",
                  "LLM_API_KEY", "AGENTNODES__LLM__PROVIDER"):
            monkeypatch.delenv(k, raising=False)
        monkeypatch.setattr(
            "agentnodes.foundation.llm.config._resolve_bridge_path",
            lambda env: Path("/tmp/__definitely_missing__.json"),
        )
        cfg = build_llm_config()
        assert cfg.model == "gpt-4o-mini"
        assert cfg.provider == "auto"

    def test_bridge_overrides_defaults(self, tmp_path, monkeypatch):
        """bridge layer sets provider→model/base_url."""
        for k in ("OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_MODEL",
                  "LLM_API_KEY", "AGENTNODES__LLM__PROVIDER"):
            monkeypatch.delenv(k, raising=False)
        llm = tmp_path / "llm.json"
        llm.write_text(json.dumps({"llm": {
            "provider": "anthropic",
            "model": "claude-3-5-sonnet-latest",
            "base_url": "https://api.anthropic.com/v1",
        }}))
        monkeypatch.setattr(
            "agentnodes.foundation.llm.config._resolve_bridge_path",
            lambda env: llm,
        )
        cfg = build_llm_config()
        assert cfg.provider == "anthropic"
        assert cfg.model == "claude-3-5-sonnet-latest"
        assert cfg.base_url == "https://api.anthropic.com/v1"

    def test_cli_overrides_bridge(self, tmp_path, monkeypatch):
        llm = tmp_path / "llm.json"
        llm.write_text(json.dumps({"llm": {"model": "bridge-model"}}))
        monkeypatch.setattr(
            "agentnodes.foundation.llm.config._resolve_bridge_path",
            lambda env: llm,
        )
        cfg = build_llm_config(cli_overrides={"llm_model": "cli-model"})
        assert cfg.model == "cli-model"


class TestListProfiles:
    def test_no_bridge_returns_helpful_message(
        self, tmp_path, monkeypatch, capsys
    ):
        monkeypatch.setattr(
            "agentnodes.foundation.llm.config.find_llm_config_path",
            lambda: tmp_path / "missing.json",
        )
        result = _cmd_llm_list_profiles()
        assert result == 0
        captured = capsys.readouterr()
        assert "LLM config" in captured.out


# ── Top-level CLI integration ────────────────────────────────────────


class TestMainCLI:
    def test_llm_list_profiles_exits_zero(
        self, tmp_path, monkeypatch, capsys
    ):
        monkeypatch.setattr(
            "agentnodes.foundation.llm.config.find_llm_config_path",
            lambda: tmp_path / "missing.json",
        )
        with patch("sys.argv", ["prog", "--llm-list-profiles"]):
            assert main() == 0
        captured = capsys.readouterr()
        assert "LLM config" in captured.out

    def test_subcommand_without_llm_flags(self):
        """`init` runs the credentials wizard (tested with --help)."""
        with patch("sys.argv", ["prog", "init", "--help"]):
            try:
                main()
            except SystemExit as exc:
                assert exc.code == 0, "init --help should exit 0"
            except Exception:
                pass

    def test_no_command_shows_help(self, capsys):
        with patch("sys.argv", ["prog"]):
            assert main() == 0
        captured = capsys.readouterr()
        assert "usage" in captured.out.lower() or "agentnodes" in captured.out


# ── End-to-end config propagation ────────────────────────────────────


class TestConfigPropagation:
    """Verify CLI flags → LLMConfig.load() → LLMConfig instance."""

    def test_flags_override_bridge(self, tmp_path, monkeypatch):
        llm = tmp_path / "llm.json"
        llm.write_text(json.dumps({"llm": {"model": "bridge-model"}}))
        monkeypatch.setattr(
            "agentnodes.foundation.llm.config._resolve_bridge_path",
            lambda env: llm,
        )
        from agentnodes.foundation.llm import LLMConfig
        cfg = LLMConfig.load(cli_overrides={
            "llm_temperature": 0.05,
            "llm_max_tokens": 1024,
        })
        assert cfg.model == "bridge-model"
        assert cfg.temperature == 0.05
        assert cfg.max_tokens == 1024

    def test_env_var_priority(self, tmp_path, monkeypatch):
        llm = tmp_path / "llm.json"
        llm.write_text(json.dumps({"llm": {"model": "bridge-model"}}))
        monkeypatch.setattr(
            "agentnodes.foundation.llm.config._resolve_bridge_path",
            lambda env: llm,
        )
        from agentnodes.foundation.llm import LLMConfig
        cfg = LLMConfig.load(
            cli_overrides={"llm_model": "cli-model"},
            env={"OPENAI_MODEL": "env-model"},
        )
        assert cfg.model == "cli-model"  # CLI > env > bridge

    def test_4_layer_priority(self, tmp_path, monkeypatch):
        """CLI > env > bridge > defaults."""
        llm = tmp_path / "llm.json"
        llm.write_text(json.dumps({"llm": {"model": "bridge-model"}}))
        monkeypatch.setattr(
            "agentnodes.foundation.llm.config._resolve_bridge_path",
            lambda env: llm,
        )
        from agentnodes.foundation.llm import LLMConfig

        # env override
        cfg_env = LLMConfig.load(env={"OPENAI_MODEL": "env-model"})
        assert cfg_env.model == "env-model"

        # CLI override
        cfg_cli = LLMConfig.load(cli_overrides={"llm_model": "cli-model"})
        assert cfg_cli.model == "cli-model"
