"""Tests for the v0.5.0 init wizard.

Covers:

* :func:`agentnodes.interfaces.cli.onboard.run_onboarding` (the prompt-toolkit
  5-step wizard used by both init paths).
* :func:`agentnodes.interfaces.cli.onboard.is_onboarded`.
* :func:`agentnodes.interfaces.cli.onboard._save_partial` /
  :func:`_finalize_llm_json` / :func:`_save_tokens_to_dotenv`
  (atomic JSON write + dotenv write, both chmod 0600).
* :func:`agentnodes.interfaces.cli._auto_onboard._maybe_run_onboarding`
  (the auto-trigger on bare ``agentnodes`` invocations).
* :func:`agentnodes.interfaces.cli._auto_onboard._migrate_legacy_env`
  (one-shot copy of legacy ``.env`` files).
* :func:`agentnodes.interfaces.cli.__init__.cmd_run_onboarding` (the explicit
  CLI path driven by ``agentnodes init``).

All tests operate against ``llm_json_path`` + ``dotenv_path`` (test-only
overrides; defaults point at ``~/.quantnodes/{llm.json,.env}``).
"""
from __future__ import annotations

import argparse
import json
import stat
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from agentnodes.interfaces.cli._auto_onboard import (
    _first_existing_dotenv_path,
    _maybe_run_onboarding,
    _migrate_legacy_env,
)
from agentnodes.interfaces.cli.onboard import (
    _finalize_llm_json,
    _save_partial,
    _save_tokens_to_dotenv,
    is_onboarded,
    run_onboarding,
)


@pytest.fixture
def fresh(tmp_path, monkeypatch):
    """Use fresh llm.json + .env paths for each test."""
    qn = tmp_path / ".quantnodes"
    llm_path = qn / "llm.json"
    env_path = qn / ".env"
    return llm_path, env_path


# ============================================================
# run_onboarding — 5-step wizard
# ============================================================


class TestRunOnboarding:
    """The prompt_toolkit-style wizard. Inputs are pre-canned via the
    test-mode ``inputs`` parameter."""

    def test_minimal_5_step_flow_writes_llm_json(self, fresh):
        llm_path, env_path = fresh
        result = run_onboarding(
            inputs=["OpenAI", "gpt-4o", "sk-test123", "300", ""],
            llm_json_path=llm_path,
            dotenv_path=env_path,
        )
        assert result == llm_path
        data = json.loads(result.read_text(encoding="utf-8"))
        assert data["llm"]["provider"] == "openai"
        assert data["llm"]["model"] == "gpt-4o"
        assert data["llm"]["api_key"] == "env:LLM_API_KEY"
        assert "base_url" in data["llm"]
        assert data["llm"]["timeout"] == 300
        assert data["llm"]["max_retries"] == 2
        # Token side
        env_text = env_path.read_text(encoding="utf-8")
        assert "LLM_API_KEY=sk-test123" in env_text
        assert "TUSHARE_TOKEN" not in env_text

    def test_unknown_provider_raises_value_error(self, fresh):
        llm_path, env_path = fresh
        with pytest.raises(ValueError, match="provider not selected"):
            run_onboarding(
                inputs=["__not_a_provider__"],
                llm_json_path=llm_path,
                dotenv_path=env_path,
            )

    def test_ollama_provider_skips_key_step(self, fresh):
        """Ollama has ``key_required=False`` so wizard must not prompt for
        an API key — only 4 inputs suffice."""
        llm_path, env_path = fresh
        result = run_onboarding(
            inputs=["Ollama", "qwen2.5:32b", "300", ""],
            llm_json_path=llm_path,
            dotenv_path=env_path,
        )
        data = json.loads(result.read_text(encoding="utf-8"))
        assert data["llm"]["provider"] == "ollama"
        assert data["llm"]["model"] == "qwen2.5:32b"
        assert "api_key" not in data["llm"]

    def test_tushare_token_optional(self, fresh):
        llm_path, env_path = fresh
        run_onboarding(
            inputs=["OpenAI", "gpt-4o", "sk-test", "300", "tushare_token_xyz"],
            llm_json_path=llm_path,
            dotenv_path=env_path,
        )
        env_text = env_path.read_text(encoding="utf-8")
        assert "TUSHARE_TOKEN=tushare_token_xyz" in env_text

    def test_wizard_writes_profiles_and_active(self, fresh):
        """The wizard now seeds profiles + active_profile so the `llm --use`
        switching machinery works immediately after onboarding."""
        llm_path, env_path = fresh
        result = run_onboarding(
            inputs=["OpenAI", "gpt-4o", "sk-test123", "300", ""],
            llm_json_path=llm_path,
            dotenv_path=env_path,
        )
        data = json.loads(result.read_text(encoding="utf-8"))
        assert data["llm"]["active_profile"] == "openai"
        assert data["llm"]["profiles"]["openai"] == {
            "provider": "openai",
            "base_url": "https://api.openai.com/v1",
            "model": "gpt-4o",
            "api_key": "env:LLM_API_KEY",
        }

    def test_wizard_ollama_profile_omits_key_and_base_url(self, fresh):
        llm_path, env_path = fresh
        result = run_onboarding(
            inputs=["Ollama", "qwen2.5:32b", "300", ""],
            llm_json_path=llm_path,
            dotenv_path=env_path,
        )
        data = json.loads(result.read_text(encoding="utf-8"))
        assert data["llm"]["active_profile"] == "ollama"
        profile = data["llm"]["profiles"]["ollama"]
        assert "api_key" not in profile
        assert profile["base_url"] == "http://localhost:11434"
        assert profile["model"] == "qwen2.5:32b"

    def test_skip_tushare_short_circuits_final_step(self, fresh):
        llm_path, env_path = fresh
        run_onboarding(
            inputs=["OpenAI", "gpt-4o", "sk-test", "300"],
            skip_tushare=True,
            llm_json_path=llm_path,
            dotenv_path=env_path,
        )
        env_text = env_path.read_text(encoding="utf-8")
        assert "TUSHARE_TOKEN" not in env_text

    def test_no_inputs_raises_runtime_error_in_non_tty(self, fresh):
        llm_path, env_path = fresh
        with pytest.raises(RuntimeError, match="TTY"):
            run_onboarding(
                inputs=None,
                llm_json_path=llm_path,
                dotenv_path=env_path,
            )

    def test_exhausted_inputs_raises_runtime_error(self, fresh):
        llm_path, env_path = fresh
        with pytest.raises(RuntimeError, match="ran out"):
            run_onboarding(
                inputs=[],
                llm_json_path=llm_path,
                dotenv_path=env_path,
            )


# ============================================================
# is_onboarded
# ============================================================


class TestIsOnboarded:
    def test_false_when_no_file(self, fresh):
        llm_path, _ = fresh
        assert not is_onboarded(llm_json_path=llm_path)

    def test_false_when_empty_llm_section(self, fresh):
        llm_path, _ = fresh
        llm_path.parent.mkdir(parents=True, exist_ok=True)
        llm_path.write_text(json.dumps({"llm": {}}))
        assert not is_onboarded(llm_json_path=llm_path)

    def test_true_when_llm_section_present(self, fresh):
        llm_path, _ = fresh
        _finalize_llm_json({"provider": "openai"}, llm_json_path=llm_path)
        assert is_onboarded(llm_json_path=llm_path)


# ============================================================
# _save_partial / _finalize_llm_json / _save_tokens_to_dotenv
# ============================================================


class TestFileHelpers:
    def test_finalize_creates_llm_json_atomic(self, fresh):
        llm_path, _ = fresh
        llm_section = {"provider": "openai", "api_key": "env:LLM_API_KEY"}
        path = _finalize_llm_json(llm_section, llm_json_path=llm_path)
        assert path == llm_path
        assert path.exists()
        assert not (llm_path.parent / f"{llm_path.name}.partial").exists()
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["llm"]["provider"] == "openai"

    def test_save_partial_leaves_artifact(self, fresh):
        llm_path, _ = fresh
        _save_partial({"provider": "openai"}, llm_json_path=llm_path)
        partial = llm_path.parent / f"{llm_path.name}.partial"
        assert partial.exists()
        data = json.loads(partial.read_text(encoding="utf-8"))
        assert data["llm"]["provider"] == "openai"

    def test_finalize_sets_mode_0o600_best_effort(self, fresh):
        llm_path, _ = fresh
        if sys.platform == "win32":
            pytest.skip("chmod semantics differ on Windows")
        path = _finalize_llm_json({"provider": "openai"}, llm_json_path=llm_path)
        mode = stat.S_IMODE(path.stat().st_mode)
        assert mode == 0o600, f"expected 0o600, got {oct(mode)}"

    def test_finalize_preserves_other_top_level_keys(self, fresh):
        llm_path, _ = fresh
        llm_path.parent.mkdir(parents=True, exist_ok=True)
        llm_path.write_text(json.dumps({"tools": ["mcp_tool"], "cron": []}))
        _finalize_llm_json({"provider": "openai"}, llm_json_path=llm_path)
        data = json.loads(llm_path.read_text(encoding="utf-8"))
        assert data["tools"] == ["mcp_tool"]
        assert data["cron"] == []
        assert data["llm"]["provider"] == "openai"

    def test_save_tokens_writes_new_keys(self, fresh):
        _, env_path = fresh
        path = _save_tokens_to_dotenv({"LLM_API_KEY": "sk-x"}, dotenv_path=env_path)
        assert path == env_path
        content = env_path.read_text(encoding="utf-8")
        assert "LLM_API_KEY=sk-x" in content

    def test_save_tokens_preserves_existing_keys(self, fresh):
        _, env_path = fresh
        env_path.parent.mkdir(parents=True, exist_ok=True)
        env_path.write_text("IFIND_MCP_TOKEN=keepme\n")
        _save_tokens_to_dotenv({"TUSHARE_TOKEN": "new"}, dotenv_path=env_path)
        content = env_path.read_text(encoding="utf-8")
        assert "IFIND_MCP_TOKEN=keepme" in content
        assert "TUSHARE_TOKEN=new" in content


# ============================================================
# _auto_onboard — 3-candidate probe + migration + auto-trigger
# ============================================================


class TestEnvProbe:
    def test_first_existing_returns_home_first(self, tmp_path: Path, monkeypatch):
        home_env = tmp_path / "home.env"
        home_env.write_text("LLM_API_KEY=sk-home")
        monkeypatch.setattr(
            "agentnodes.interfaces.cli._auto_onboard._QUANTNODES_DOTENV_PATH",
            home_env,
        )
        cwd_env = tmp_path / "cwd.env"
        cwd_env.write_text("LLM_API_KEY=sk-cwd")
        monkeypatch.setattr(
            "agentnodes.interfaces.cli._auto_onboard._CWD_DOTENV_PATH",
            cwd_env,
        )
        monkeypatch.setattr(
            "agentnodes.interfaces.cli._auto_onboard._PROJECT_DOTENV_PATH",
            tmp_path / "missing.env",
        )
        assert _first_existing_dotenv_path() == tmp_path / "home.env"

    def test_first_existing_returns_none_when_all_missing(
        self, tmp_path: Path, monkeypatch
    ):
        monkeypatch.setattr(
            "agentnodes.interfaces.cli._auto_onboard._QUANTNODES_DOTENV_PATH",
            tmp_path / "missing1.env",
        )
        monkeypatch.setattr(
            "agentnodes.interfaces.cli._auto_onboard._PROJECT_DOTENV_PATH",
            tmp_path / "missing2.env",
        )
        monkeypatch.setattr(
            "agentnodes.interfaces.cli._auto_onboard._CWD_DOTENV_PATH",
            tmp_path / "missing3.env",
        )
        assert _first_existing_dotenv_path() is None


class TestMigrateLegacyEnv:
    def test_copies_legacy_to_new_path(self, tmp_path: Path, monkeypatch):
        legacy_dir = tmp_path / ".strategy-research"
        legacy_dir.mkdir()
        legacy_file = legacy_dir / ".env"
        legacy_file.write_text("LLM_API_KEY=sk-legacy\n")

        monkeypatch.setattr("pathlib.Path.home", classmethod(lambda cls: tmp_path))

        new_path = tmp_path / "new_env" / ".env"
        monkeypatch.setattr(
            "agentnodes.interfaces.cli._auto_onboard._QUANTNODES_DOTENV_PATH",
            new_path,
        )

        _migrate_legacy_env()

        assert new_path.exists(), "new .env was not created"
        assert new_path.read_text(encoding="utf-8") == "LLM_API_KEY=sk-legacy\n"
        assert legacy_file.exists()  # legacy NOT deleted

    def test_copies_post_rebrand_legacy(self, tmp_path: Path, monkeypatch):
        """The v0.4.x path ``~/.quantnodes/agentnodes/.env`` is also migrated."""
        legacy_dir = tmp_path / ".quantnodes" / "agentnodes"
        legacy_dir.mkdir(parents=True)
        legacy_file = legacy_dir / ".env"
        legacy_file.write_text("LLM_API_KEY=sk-postrebrand\n")

        monkeypatch.setattr("pathlib.Path.home", classmethod(lambda cls: tmp_path))

        new_path = tmp_path / "new_env" / ".env"
        monkeypatch.setattr(
            "agentnodes.interfaces.cli._auto_onboard._QUANTNODES_DOTENV_PATH",
            new_path,
        )

        _migrate_legacy_env()

        assert new_path.exists()
        assert new_path.read_text(encoding="utf-8") == "LLM_API_KEY=sk-postrebrand\n"

    def test_idempotent_when_new_already_exists(
        self, tmp_path: Path, monkeypatch
    ):
        legacy_dir = tmp_path / ".strategy-research"
        legacy_dir.mkdir()
        (legacy_dir / ".env").write_text("LLM_API_KEY=sk-legacy")

        new_path = tmp_path / "new" / ".env"
        new_path.parent.mkdir(parents=True)
        new_path.write_text("LLM_API_KEY=sk-current")

        monkeypatch.setattr(
            "agentnodes.interfaces.cli._auto_onboard._QUANTNODES_DOTENV_PATH",
            new_path,
        )
        monkeypatch.setattr("pathlib.Path.home", classmethod(lambda cls: tmp_path))

        _migrate_legacy_env()
        assert new_path.read_text(encoding="utf-8") == "LLM_API_KEY=sk-current"

    def test_no_op_when_legacy_missing(self, tmp_path: Path, monkeypatch):
        new_path = tmp_path / "new" / ".env"
        monkeypatch.setattr(
            "agentnodes.interfaces.cli._auto_onboard._QUANTNODES_DOTENV_PATH",
            new_path,
        )
        monkeypatch.setattr("pathlib.Path.home", classmethod(lambda cls: tmp_path))
        _migrate_legacy_env()
        assert not new_path.exists()


class TestMaybeRunOnboarding:
    def test_skips_when_env_exists(self, tmp_path: Path, monkeypatch):
        home_env = tmp_path / "home.env"
        home_env.write_text("LLM_API_KEY=sk-x")
        monkeypatch.setattr(
            "agentnodes.interfaces.cli._auto_onboard._QUANTNODES_DOTENV_PATH",
            home_env,
        )
        monkeypatch.setattr(
            "agentnodes.interfaces.cli._auto_onboard.run_onboarding",
            lambda **kw: pytest.fail("wizard was invoked"),
        )
        assert _maybe_run_onboarding(console=None) is True

    def test_returns_true_in_non_tty(self, tmp_path: Path, monkeypatch):
        for attr in ("_QUANTNODES_DOTENV_PATH",
                     "_PROJECT_DOTENV_PATH",
                     "_CWD_DOTENV_PATH"):
            monkeypatch.setattr(
                f"agentnodes.interfaces.cli._auto_onboard.{attr}",
                tmp_path / f"nonexistent_{attr}.env",
            )
        monkeypatch.setattr(
            "agentnodes.interfaces.cli._auto_onboard.sys.stdin",
            type("Mock", (), {"isatty": staticmethod(lambda: False)})(),
        )
        monkeypatch.setattr(
            "agentnodes.interfaces.cli._auto_onboard.run_onboarding",
            lambda **kw: (_ for _ in ()).throw(
                AssertionError("wizard reached in non-TTY"),
            ),
        )
        assert _maybe_run_onboarding(console=None) is True

    def test_returns_false_when_wizard_returns_none(
        self, tmp_path: Path, monkeypatch
    ):
        monkeypatch.setattr(
            "agentnodes.interfaces.cli._auto_onboard._first_existing_dotenv_path",
            lambda: None,
        )
        monkeypatch.setattr(
            "agentnodes.interfaces.cli._auto_onboard.sys.stdin",
            type("Mock", (), {"isatty": staticmethod(lambda: True)})(),
        )
        monkeypatch.setattr(
            "agentnodes.interfaces.cli._auto_onboard.sys.stdout",
            type("Mock", (), {"isatty": staticmethod(lambda: True)})(),
        )
        monkeypatch.setattr(
            "agentnodes.interfaces.cli._auto_onboarding.run_onboarding" if False else
            "agentnodes.interfaces.cli._auto_onboard.run_onboarding",
            lambda **kw: None,
        )
        assert _maybe_run_onboarding(console=None) is False


# ============================================================
# cmd_run_onboarding — explicit CLI subcommand
# ============================================================


class TestCmdRunOnboarding:
    def test_help_describes_wizard(self, capsys):
        from agentnodes.interfaces.cli import main as cli_main
        with patch.object(sys, "argv", ["prog", "init", "--help"]):
            with pytest.raises(SystemExit) as exc:
                cli_main()
        assert exc.value.code == 0
        captured = capsys.readouterr()
        assert "credentials wizard" in captured.out
        assert "vibe-trading" in captured.out

    def test_overwrite_existing_requires_force(self, fresh, monkeypatch):
        llm_path, _ = fresh
        llm_path.parent.mkdir(parents=True, exist_ok=True)
        _finalize_llm_json({"provider": "openai"}, llm_json_path=llm_path)

        with patch("rich.prompt.Confirm.ask", return_value=False), patch(
            "agentnodes.interfaces.cli.onboard._QUANTNODES_LLM_JSON_PATH", llm_path
        ), patch(
            "agentnodes.interfaces.cli.onboard._QUANTNODES_DOTENV_PATH",
            llm_path.parent / ".env",
        ):
            from agentnodes.interfaces.cli import cmd_run_onboarding
            args = argparse.Namespace(force=False)
            rc = cmd_run_onboarding(args)
        assert rc == 0
        # File untouched
        data = json.loads(llm_path.read_text(encoding="utf-8"))
        assert data["llm"]["provider"] == "openai"

    def test_force_overwrites(self, fresh, monkeypatch):
        llm_path, env_path = fresh
        llm_path.parent.mkdir(parents=True, exist_ok=True)
        _finalize_llm_json({"provider": "openai", "model": "old"},
                           llm_json_path=llm_path)

        def fake_run(*, console=None, **_):
            _finalize_llm_json(
                {"provider": "anthropic", "model": "new"},
                llm_json_path=llm_path,
            )
            return llm_path

        monkeypatch.setattr(
            "agentnodes.interfaces.cli.onboard.run_onboarding", fake_run,
        )
        from agentnodes.interfaces.cli import cmd_run_onboarding
        args = argparse.Namespace(force=True)
        rc = cmd_run_onboarding(args)
        assert rc == 0
        data = json.loads(llm_path.read_text(encoding="utf-8"))
        assert data["llm"]["provider"] == "anthropic"

    def test_wizard_cancel_returns_nonzero(self, fresh, monkeypatch):
        # is_onboarded() reads real ~/.quantnodes/llm.json; force False
        # so cmd_run_onboarding reaches run_onboarding without prompting.
        monkeypatch.setattr(
            "agentnodes.interfaces.cli.onboard.is_onboarded", lambda **kw: False,
        )
        monkeypatch.setattr(
            "agentnodes.interfaces.cli.onboard.run_onboarding",
            lambda **kw: None,
        )
        from agentnodes.interfaces.cli import cmd_run_onboarding
        args = argparse.Namespace(force=False)
        rc = cmd_run_onboarding(args)
        assert rc == 1


# ============================================================
# TTY-mode: run_onboarding(inputs=None) with mocked selectors
# ============================================================


class TestRunOnboardingTTY:
    """Test the prompt_toolkit TTY branch of run_onboarding."""

    def test_tty_full_flow_openai(self, fresh, monkeypatch):
        llm_path, env_path = fresh

        monkeypatch.setattr("sys.stdin.isatty", lambda: True)
        monkeypatch.setattr("sys.stdout.isatty", lambda: True)

        call_count = {"n": 0}

        def mock_select(prompt, choices, *, default_index=0):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return "openai"
            if call_count["n"] == 2:
                return "gpt-4o"
            if call_count["n"] == 5:
                return "__skip__"
            return choices[0][0]

        monkeypatch.setattr(
            "agentnodes.interfaces.cli.onboard._select_with_back", mock_select,
        )
        monkeypatch.setattr(
            "agentnodes.interfaces.cli.onboard._prompt_secret",
            lambda prompt: "sk-test1234567890",
        )
        # Mock Step 0 auto-fix prompt (prompt_toolkit.prompt)
        monkeypatch.setattr(
            "prompt_toolkit.prompt",
            lambda *a, **kw: "y",
        )

        result = run_onboarding(
            llm_json_path=llm_path, dotenv_path=env_path,
        )
        assert result is not None
        data = json.loads(result.read_text(encoding="utf-8"))
        assert data["llm"]["provider"] == "openai"
        assert data["llm"]["api_key"] == "env:LLM_API_KEY"
        env_text = env_path.read_text(encoding="utf-8")
        assert "LLM_API_KEY=sk-test1234567890" in env_text

    def test_tty_cancel_returns_none(self, fresh, monkeypatch):
        from agentnodes.interfaces.cli.onboard import CANCEL
        llm_path, env_path = fresh

        monkeypatch.setattr("sys.stdin.isatty", lambda: True)
        monkeypatch.setattr("sys.stdout.isatty", lambda: True)
        monkeypatch.setattr(
            "prompt_toolkit.prompt",
            lambda *a, **kw: "y",
        )
        monkeypatch.setattr(
            "agentnodes.interfaces.cli.onboard._select_with_back",
            lambda *a, **kw: CANCEL,
        )

        result = run_onboarding(
            llm_json_path=llm_path, dotenv_path=env_path,
        )
        assert result is None

    def test_tty_back_at_step0_returns_none(self, fresh, monkeypatch):
        from agentnodes.interfaces.cli.onboard import BACK
        llm_path, env_path = fresh

        monkeypatch.setattr("sys.stdin.isatty", lambda: True)
        monkeypatch.setattr("sys.stdout.isatty", lambda: True)
        monkeypatch.setattr(
            "prompt_toolkit.prompt",
            lambda *a, **kw: "y",
        )
        monkeypatch.setattr(
            "agentnodes.interfaces.cli.onboard._select_with_back",
            lambda *a, **kw: BACK,
        )

        result = run_onboarding(
            llm_json_path=llm_path, dotenv_path=env_path,
        )
        assert result is None

    def test_tty_back_goes_to_previous_step(self, fresh, monkeypatch):
        from agentnodes.interfaces.cli.onboard import BACK
        llm_path, env_path = fresh

        monkeypatch.setattr("sys.stdin.isatty", lambda: True)
        monkeypatch.setattr("sys.stdout.isatty", lambda: True)
        monkeypatch.setattr(
            "prompt_toolkit.prompt",
            lambda *a, **kw: "y",
        )

        step_calls = {"provider": 0, "model": 0, "timeout": 0, "tushare": 0}

        def mock_select(prompt, choices, *, default_index=0):
            if "provider" in prompt.lower():
                step_calls["provider"] += 1
                return "openai"
            if "model" in prompt.lower():
                step_calls["model"] += 1
                if step_calls["model"] == 1:
                    return BACK
                return "gpt-4o"
            if "timeout" in prompt.lower():
                step_calls["timeout"] += 1
                return "300"
            if "tushare" in prompt.lower():
                step_calls["tushare"] += 1
                return "__skip__"
            return choices[0][0]

        monkeypatch.setattr(
            "agentnodes.interfaces.cli.onboard._select_with_back", mock_select,
        )
        monkeypatch.setattr(
            "agentnodes.interfaces.cli.onboard._prompt_secret",
            lambda prompt: "sk-test1234567890",
        )

        result = run_onboarding(
            llm_json_path=llm_path, dotenv_path=env_path,
        )
        assert result is not None
        assert step_calls["provider"] == 2
        assert step_calls["model"] == 2

    def test_tty_ollama_skips_key_step(self, fresh, monkeypatch):
        llm_path, env_path = fresh

        monkeypatch.setattr("sys.stdin.isatty", lambda: True)
        monkeypatch.setattr("sys.stdout.isatty", lambda: True)
        monkeypatch.setattr(
            "prompt_toolkit.prompt",
            lambda *a, **kw: "y",
        )

        step = {"n": 0}

        def mock_select(prompt, choices, *, default_index=0):
            step["n"] += 1
            if step["n"] == 1:
                return "ollama"
            if step["n"] == 2:
                return "qwen2.5:32b"
            if step["n"] == 3:
                return "300"
            if step["n"] == 4:
                return "__skip__"
            return choices[0][0]

        monkeypatch.setattr(
            "agentnodes.interfaces.cli.onboard._select_with_back", mock_select,
        )

        result = run_onboarding(
            llm_json_path=llm_path, dotenv_path=env_path,
        )
        assert result is not None
        data = json.loads(result.read_text(encoding="utf-8"))
        assert data["llm"]["provider"] == "ollama"
        assert "api_key" not in data["llm"]

    def test_tty_non_tty_raises_runtime_error(self, fresh, monkeypatch):
        llm_path, env_path = fresh
        monkeypatch.setattr("sys.stdin.isatty", lambda: False)
        monkeypatch.setattr("sys.stdout.isatty", lambda: True)
        with pytest.raises(RuntimeError, match="TTY"):
            run_onboarding(
                llm_json_path=llm_path, dotenv_path=env_path,
            )


# ============================================================
# TTY helper functions
# ============================================================


class TestTTYHelpers:
    """Test the TTY helper functions."""

    def test_validate_key_valid(self):
        from agentnodes.interfaces.cli.onboard import _validate_key
        assert _validate_key("sk-", "sk-test1234567890") is None

    def test_validate_key_empty(self):
        from agentnodes.interfaces.cli.onboard import _validate_key
        err = _validate_key("sk-", "")
        assert "empty" in err.lower()

    def test_validate_key_wrong_prefix(self):
        from agentnodes.interfaces.cli.onboard import _validate_key
        err = _validate_key("sk-", "wrong-prefix-123456")
        assert "sk-" in err

    def test_validate_key_too_short(self):
        from agentnodes.interfaces.cli.onboard import _validate_key
        err = _validate_key("sk-", "sk-short")
        assert "short" in err.lower()


class TestStepProviderSwitch:
    """Verify _step_provider drops stale keys when the user BACK-reselects."""

    def test_switch_from_openai_to_ollama_drops_openai_keys(self, fresh, monkeypatch):
        """OpenAI 残留 → BACK → Ollama → llm.json 不带 api_key/base_url。"""
        import unittest.mock as _mock

        import agentnodes.interfaces.cli.onboard as onboard_mod
        from agentnodes.interfaces.cli.onboard import _step_provider

        llm_path, env_path = fresh
        with _mock.patch.object(
            onboard_mod, "_select_with_back", return_value="ollama",
        ):
            llm = {
                "provider": "openai",
                "model": "gpt-4o",
                "api_key": "env:LLM_API_KEY",
                "base_url": "https://api.openai.com/v1",
            }
            state: dict = {"provider_key": "openai"}

            result = _step_provider(llm, state, skip_tushare=False)

            assert result == "ok"
            assert llm["provider"] == "ollama"
            # api_key (OpenAI credential) dropped — Ollama has no key
            assert "api_key" not in llm
            # base_url now points to Ollama's default (not OpenAI's)
            assert llm["base_url"] == "http://localhost:11434"
            # model was re-cleared so user re-picks in step 2
            assert "model" not in llm
