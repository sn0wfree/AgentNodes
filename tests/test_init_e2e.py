"""E2E tests for quantnodes-research init via CLI entry point.

Tests the full path: CLI argparse → cmd_run_onboarding → run_onboarding
test-mode branch → ``_finalize_llm_json`` → ``~/.quantnodes/llm.json``
+ ``~/.quantnodes/.env``.

Selectors are bypassed by calling run_onboarding(inputs=...) directly,
which exercises the test-mode branch without needing a real TTY.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

# ── Helpers ─────────────────────────────────────────────────────────


def _write_fake_llm_json(path: Path, content: dict | None = None):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(content or {"llm": {"provider": "openai"}}),
                    encoding="utf-8")


def _patch_paths(tmp_path: Path, monkeypatch):
    """Point llm.json + .env constants to tmp_path / 'qn'."""
    qn = tmp_path / "qn"
    llm_path = qn / "llm.json"
    env_path = qn / ".env"

    monkeypatch.setattr(
        "agentnodes.interfaces.cli.onboard._QUANTNODES_LLM_JSON_PATH", llm_path
    )
    monkeypatch.setattr(
        "agentnodes.interfaces.cli.onboard._QUANTNODES_DOTENV_PATH", env_path
    )
    monkeypatch.setattr(
        "agentnodes.interfaces.cli._auto_onboard._QUANTNODES_LLM_JSON_PATH", llm_path
    )
    monkeypatch.setattr(
        "agentnodes.interfaces.cli._auto_onboard._QUANTNODES_DOTENV_PATH", env_path
    )
    return llm_path, env_path


def _run_init(monkeypatch, llm_path, env_path, args, inputs):
    """Run CLI init with test-mode inputs.

    Patches run_onboarding to call the test-mode branch (inputs-driven)
    so no TTY is needed.

    ``_auto_onboard`` binds ``run_onboarding`` at import time (module-level
    ``from agentnodes.interfaces.cli.onboard import run_onboarding``), so once
    it has been imported by an earlier test, patching ``onboard.run_onboarding``
    is a no-op for the ``main()`` entry path — patch the bound symbol too.
    """
    from agentnodes.interfaces.cli.onboard import run_onboarding as real_run

    def fake_run(**kw):
        return real_run(inputs=inputs,
                        llm_json_path=llm_path, dotenv_path=env_path)

    monkeypatch.setattr(
        "agentnodes.interfaces.cli.onboard.run_onboarding", fake_run
    )
    monkeypatch.setattr(
        "agentnodes.interfaces.cli._auto_onboard.run_onboarding", fake_run
    )

    with patch("sys.argv", ["prog", "init"] + args):
        from agentnodes.interfaces.cli import main
        return main()


# ── E2E Tests ──────────────────────────────────────────────────────


class TestInitE2E:
    """E2E tests for quantnodes-research init via CLI entry point."""

    def test_force_overwrites_existing(self, tmp_path, monkeypatch):
        """--force 跳过确认，直接覆盖旧 llm.json。"""
        llm_path, env_path = _patch_paths(tmp_path, monkeypatch)
        _write_fake_llm_json(llm_path, {"llm": {"provider": "old", "model": "old"}})

        rc = _run_init(
            monkeypatch, llm_path, env_path,
            args=["--force"],
            inputs=["OpenAI", "gpt-4o", "sk-test1234567890", "300", ""],
        )

        assert rc == 0
        data = json.loads(llm_path.read_text(encoding="utf-8"))
        assert data["llm"]["provider"] == "openai"
        assert data["llm"]["model"] == "gpt-4o"
        assert data["llm"]["api_key"] == "env:LLM_API_KEY"
        env_content = env_path.read_text(encoding="utf-8")
        assert "LLM_API_KEY=sk-test1234567890" in env_content

    def test_new_user_creates_env(self, tmp_path, monkeypatch):
        """无 llm.json → 创建新文件 + chmod 0600。"""
        llm_path, env_path = _patch_paths(tmp_path, monkeypatch)

        rc = _run_init(
            monkeypatch, llm_path, env_path,
            args=["--force"],
            inputs=["OpenAI", "gpt-4o", "sk-test1234567890", "300", ""],
        )

        assert rc == 0
        assert llm_path.exists()
        if os.name != "nt":
            mode = oct(llm_path.stat().st_mode)[-3:]
            assert mode == "600"

    def test_cancel_returns_nonzero(self, tmp_path, monkeypatch):
        """Wizard CANCEL → cmd_run_onboarding 返回 1。"""
        _patch_paths(tmp_path, monkeypatch)

        def fake_cancel(**kw):
            return None

        monkeypatch.setattr(
            "agentnodes.interfaces.cli.onboard.run_onboarding", fake_cancel
        )

        with patch("sys.argv", ["prog", "init", "--force"]):
            from agentnodes.interfaces.cli import main
            rc = main()

        assert rc == 1

    def test_ollama_no_key(self, tmp_path, monkeypatch):
        """Ollama → 无 api_key 写入 llm.json。"""
        llm_path, env_path = _patch_paths(tmp_path, monkeypatch)

        rc = _run_init(
            monkeypatch, llm_path, env_path,
            args=["--force"],
            inputs=["Ollama", "qwen2.5:32b", "300", ""],
        )

        assert rc == 0
        data = json.loads(llm_path.read_text(encoding="utf-8"))
        assert data["llm"]["provider"] == "ollama"
        assert "api_key" not in data["llm"]
        # Ollama has no key → no .env file is created at all
        assert not env_path.exists()

    def test_tushare_token_included(self, tmp_path, monkeypatch):
        """Paste Tushare token → .env 包含 TUSHARE_TOKEN。"""
        llm_path, env_path = _patch_paths(tmp_path, monkeypatch)

        rc = _run_init(
            monkeypatch, llm_path, env_path,
            args=["--force"],
            inputs=["OpenAI", "gpt-4o", "sk-test1234567890", "300",
                    "tushare_token_xyz"],
        )

        assert rc == 0
        env_content = env_path.read_text(encoding="utf-8")
        assert "TUSHARE_TOKEN=tushare_token_xyz" in env_content

    def test_help_shows_wizard_description(self, capsys):
        """--help 输出含 'credentials wizard'。"""
        with patch("sys.argv", ["prog", "init", "--help"]):
            from agentnodes.interfaces.cli import main
            with pytest.raises(SystemExit) as exc:
                main()
        assert exc.value.code == 0
        captured = capsys.readouterr()
        assert "credentials wizard" in captured.out

    def test_auto_trigger_skips_when_env_exists(self, tmp_path, monkeypatch):
        """_maybe_run_onboarding: llm.json 已存在 → 跳过 wizard。"""
        llm_path, env_path = _patch_paths(tmp_path, monkeypatch)
        _write_fake_llm_json(llm_path)

        def fail_if_called(*a, **kw):
            raise AssertionError("wizard called when env exists")

        monkeypatch.setattr(
            "agentnodes.interfaces.cli._auto_onboard.run_onboarding", fail_if_called,
        )

        from agentnodes.interfaces.cli._auto_onboard import _maybe_run_onboarding
        from agentnodes.interfaces.cli.theme import get_console

        assert _maybe_run_onboarding(get_console()) is True

    def test_auto_trigger_runs_when_missing(self, tmp_path, monkeypatch):
        """_maybe_run_onboarding: 无 .env + TTY → 调用 wizard。"""
        llm_path, env_path = _patch_paths(tmp_path, monkeypatch)

        # Skip migration so it doesn't create env_path in tmp.
        monkeypatch.setattr(
            "agentnodes.interfaces.cli._auto_onboard._migrate_legacy_env",
            lambda: None,
        )
        monkeypatch.setattr(
            "agentnodes.interfaces.cli._auto_onboard._first_existing_dotenv_path",
            lambda: None,
        )
        monkeypatch.setattr(
            "agentnodes.interfaces.cli._auto_onboard.sys.stdin",
            type("M", (), {"isatty": staticmethod(lambda: True)})(),
        )
        monkeypatch.setattr(
            "agentnodes.interfaces.cli._auto_onboard.sys.stdout",
            type("M", (), {"isatty": staticmethod(lambda: True)})(),
        )

        def fake_run_onboarding(**kw):
            llm_path.parent.mkdir(parents=True, exist_ok=True)
            llm_path.write_text(
                json.dumps({"llm": {"provider": "openai"}}),
                encoding="utf-8",
            )
            env_path.write_text("", encoding="utf-8")
            return llm_path

        monkeypatch.setattr(
            "agentnodes.interfaces.cli._auto_onboard.run_onboarding",
            fake_run_onboarding,
        )

        from agentnodes.interfaces.cli._auto_onboard import _maybe_run_onboarding
        from agentnodes.interfaces.cli.theme import get_console

        assert _maybe_run_onboarding(get_console()) is True
        assert llm_path.exists()

    def test_existing_llm_not_overwritten_without_force(self, tmp_path, monkeypatch):
        """无 --force + llm.json 已存在 + 用户拒绝 → 原文件不变。"""
        llm_path, env_path = _patch_paths(tmp_path, monkeypatch)
        _write_fake_llm_json(llm_path, {"llm": {"provider": "keep_me"}})

        with patch("rich.prompt.Confirm.ask", return_value=False):
            with patch("sys.argv", ["prog", "init"]):
                from agentnodes.interfaces.cli import main
                rc = main()

        assert rc == 0
        data = json.loads(llm_path.read_text(encoding="utf-8"))
        assert data["llm"]["provider"] == "keep_me"

    def test_validate_key_unit(self):
        """_validate_key: prefix / length / empty checks."""
        from agentnodes.interfaces.cli.onboard import _validate_key

        # Wrong prefix
        err = _validate_key("sk-", "wrong-1234567890")
        assert "sk-" in err
        # Too short
        err = _validate_key("sk-", "sk-short")
        assert "short" in err.lower()
        # Empty
        err = _validate_key("sk-", "")
        assert "empty" in err.lower()
        # Valid
        assert _validate_key("sk-", "sk-test1234567890") is None

    def test_anthropic_provider(self, tmp_path, monkeypatch):
        """Anthropic → correct base_url + api_key。"""
        llm_path, env_path = _patch_paths(tmp_path, monkeypatch)

        rc = _run_init(
            monkeypatch, llm_path, env_path,
            args=["--force"],
            inputs=["Anthropic", "claude-3-5-sonnet-latest",
                    "sk-ant-test1234567890", "300", ""],
        )

        assert rc == 0
        data = json.loads(llm_path.read_text(encoding="utf-8"))
        assert data["llm"]["provider"] == "anthropic"
        assert data["llm"]["base_url"] == "https://api.anthropic.com/v1"
        assert data["llm"]["api_key"] == "env:LLM_API_KEY"
        env_content = env_path.read_text(encoding="utf-8")
        assert "LLM_API_KEY=sk-ant-test1234567890" in env_content

    def test_skip_tushare(self, tmp_path, monkeypatch):
        """skip_tushare=True → TUSHARE_TOKEN 不在 .env。"""
        llm_path, env_path = _patch_paths(tmp_path, monkeypatch)

        from agentnodes.interfaces.cli.onboard import run_onboarding

        result = run_onboarding(
            inputs=["OpenAI", "gpt-4o", "sk-test1234567890", "300"],
            skip_tushare=True,
            llm_json_path=llm_path,
            dotenv_path=env_path,
        )

        assert result is not None
        env_content = env_path.read_text(encoding="utf-8")
        assert "TUSHARE_TOKEN" not in env_content
