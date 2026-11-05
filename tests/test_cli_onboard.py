"""Tests for ``cli.onboard`` — onboarding wizard."""

from __future__ import annotations

import json

import pytest

from agentnodes.interfaces.cli.onboard import (
    BACK,
    CANCEL,
    PROVIDERS,
    TIMEOUT_CHOICES,
    _finalize_llm_json,
    _save_partial,
    _save_tokens_to_dotenv,
    is_onboarded,
    run_onboarding,
)


@pytest.fixture
def fresh_paths(tmp_path, monkeypatch):
    """Use fresh llm.json + .env paths for each test."""
    qn = tmp_path / ".quantnodes"
    llm_path = qn / "llm.json"
    env_path = qn / ".env"
    return llm_path, env_path


# ─── Sentinels + catalog ───────────────────────────────────────────────


class TestCatalog:
    def test_providers_non_empty(self):
        assert len(PROVIDERS) >= 3

    def test_provider_keys_unique(self):
        keys = [p.key for p in PROVIDERS]
        assert len(keys) == len(set(keys))

    def test_provider_has_required_fields(self):
        for p in PROVIDERS:
            assert p.label
            assert p.default_model
            assert p.base_url
            assert isinstance(p.suggested_models, tuple)

    def test_timeout_choices(self):
        assert len(TIMEOUT_CHOICES) >= 2
        for choice in TIMEOUT_CHOICES:
            assert len(choice) == 2  # (value, label) pair


class TestSentinels:
    def test_back(self):
        assert BACK is not None

    def test_cancel(self):
        assert CANCEL is not None

    def test_back_and_cancel_distinct(self):
        assert BACK is not CANCEL


# ─── Filesystem helpers ────────────────────────────────────────────────


class TestSavePartial:
    def test_creates_partial_file(self, fresh_paths):
        llm_path, _ = fresh_paths
        llm_section = {"provider": "openai", "model": "gpt-4o"}
        _save_partial(llm_section, llm_json_path=llm_path)
        partial = llm_path.parent / f"{llm_path.name}.partial"
        assert partial.exists()
        data = json.loads(partial.read_text(encoding="utf-8"))
        assert data["llm"]["provider"] == "openai"


class TestFinalize:
    def test_writes_llm_json(self, fresh_paths):
        llm_path, _ = fresh_paths
        llm_section = {"provider": "openai", "model": "gpt-4o", "timeout": 300}
        path = _finalize_llm_json(llm_section, llm_json_path=llm_path)
        assert path == llm_path
        assert path.exists()
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["llm"]["provider"] == "openai"
        assert data["llm"]["timeout"] == 300

    def test_no_partial_left(self, fresh_paths):
        llm_path, _ = fresh_paths
        _save_partial({"A": "1"}, llm_json_path=llm_path)
        partial = llm_path.parent / f"{llm_path.name}.partial"
        assert partial.exists()
        _finalize_llm_json({"A": "1"}, llm_json_path=llm_path)
        assert not partial.exists()

    def test_preserves_other_top_level_keys(self, fresh_paths):
        llm_path, _ = fresh_paths
        # Seed file with B-side top-level keys
        llm_path.parent.mkdir(parents=True)
        llm_path.write_text(json.dumps({
            "tools": ["mcp__server1__tool1"],
            "agents": {"planner": {"model": "haiku"}},
        }))
        _finalize_llm_json({"provider": "openai"}, llm_json_path=llm_path)
        data = json.loads(llm_path.read_text(encoding="utf-8"))
        assert data["tools"] == ["mcp__server1__tool1"]
        assert data["agents"]["planner"]["model"] == "haiku"
        assert data["llm"]["provider"] == "openai"


class TestSaveTokensToDotenv:
    def test_writes_new_keys(self, fresh_paths):
        _, env_path = fresh_paths
        path = _save_tokens_to_dotenv({"LLM_API_KEY": "sk-test"}, dotenv_path=env_path)
        assert path == env_path
        content = env_path.read_text(encoding="utf-8")
        assert "LLM_API_KEY=sk-test" in content

    def test_preserves_existing_keys(self, fresh_paths):
        _, env_path = fresh_paths
        env_path.parent.mkdir(parents=True)
        env_path.write_text("IFIND_MCP_TOKEN=keepme\nLLM_API_KEY=stale\n")
        _save_tokens_to_dotenv({"TUSHARE_TOKEN": "newtoken"}, dotenv_path=env_path)
        content = env_path.read_text(encoding="utf-8")
        assert "IFIND_MCP_TOKEN=keepme" in content
        assert "TUSHARE_TOKEN=newtoken" in content
        # new write of same key replaces old
        assert "LLM_API_KEY=stale" in content

    def test_empty_value_skipped(self, fresh_paths):
        _, env_path = fresh_paths
        _save_tokens_to_dotenv({"LLM_API_KEY": ""}, dotenv_path=env_path)
        content = env_path.read_text(encoding="utf-8")
        assert "LLM_API_KEY" not in content


class TestIsOnboarded:
    def test_false_when_no_file(self, fresh_paths):
        llm_path, _ = fresh_paths
        assert is_onboarded(llm_json_path=llm_path) is False

    def test_false_when_empty_llm_section(self, fresh_paths):
        llm_path, _ = fresh_paths
        llm_path.parent.mkdir(parents=True)
        llm_path.write_text(json.dumps({"llm": {}}))
        assert is_onboarded(llm_json_path=llm_path) is False

    def test_true_after_finalize(self, fresh_paths):
        llm_path, _ = fresh_paths
        _finalize_llm_json({"provider": "openai"}, llm_json_path=llm_path)
        assert is_onboarded(llm_json_path=llm_path) is True


# ─── Full flow ────────────────────────────────────────────────────────


class TestRunOnboarding:
    def test_minimal_openai(self, fresh_paths):
        llm_path, env_path = fresh_paths
        inputs = [
            "OpenAI",
            "",
            "sk-test1234",
            "300",
            "",
        ]
        result = run_onboarding(
            llm_json_path=llm_path, dotenv_path=env_path, inputs=inputs
        )
        assert result == llm_path
        data = json.loads(result.read_text(encoding="utf-8"))
        assert data["llm"]["provider"] == "openai"
        assert data["llm"]["base_url"] == "https://api.openai.com/v1"
        assert data["llm"]["model"] == "gpt-4o"
        assert data["llm"]["api_key"] == "env:LLM_API_KEY"
        assert data["llm"]["timeout"] == 300
        assert data["llm"]["max_retries"] == 2

        env_content = env_path.read_text(encoding="utf-8")
        assert "LLM_API_KEY=sk-test1234" in env_content
        assert "TUSHARE_TOKEN" not in env_content

    def test_ollama_no_key(self, fresh_paths):
        llm_path, env_path = fresh_paths
        inputs = [
            "Ollama",
            "llama3.3:70b",
            "120",
            "",
        ]
        result = run_onboarding(
            llm_json_path=llm_path, dotenv_path=env_path, inputs=inputs
        )
        data = json.loads(result.read_text(encoding="utf-8"))
        assert data["llm"]["provider"] == "ollama"
        assert data["llm"]["model"] == "llama3.3:70b"
        # Ollama has key_required=False → no api_key field
        assert "api_key" not in data["llm"]

    def test_skip_tushare(self, fresh_paths):
        llm_path, env_path = fresh_paths
        inputs = ["OpenAI", "gpt-4o-mini", "sk-test", "120"]
        run_onboarding(llm_json_path=llm_path, dotenv_path=env_path, inputs=inputs, skip_tushare=True)
        env_content = env_path.read_text(encoding="utf-8")
        assert "TUSHARE_TOKEN" not in env_content

    def test_tushare_when_provided(self, fresh_paths):
        llm_path, env_path = fresh_paths
        inputs = ["OpenAI", "", "sk-test", "300", "tushare_token_xyz"]
        run_onboarding(
            llm_json_path=llm_path, dotenv_path=env_path, inputs=inputs
        )
        env_content = env_path.read_text(encoding="utf-8")
        assert "TUSHARE_TOKEN=tushare_token_xyz" in env_content

    def test_unknown_provider_raises(self, fresh_paths):
        llm_path, env_path = fresh_paths
        inputs = ["NonexistentProvider", "", "key", "300", ""]
        with pytest.raises(ValueError):
            run_onboarding(
                llm_json_path=llm_path, dotenv_path=env_path, inputs=inputs
            )

    def test_no_inputs_in_non_tty_raises(self, fresh_paths):
        llm_path, env_path = fresh_paths
        with pytest.raises(RuntimeError, match="TTY"):
            run_onboarding(
                llm_json_path=llm_path, dotenv_path=env_path, inputs=None
            )

    def test_exhausted_inputs_raises(self, fresh_paths):
        llm_path, env_path = fresh_paths
        with pytest.raises(RuntimeError, match="ran out"):
            run_onboarding(
                llm_json_path=llm_path, dotenv_path=env_path, inputs=[]
            )

    def test_plaintext_migrate_yes(self, fresh_paths):
        llm_path, env_path = fresh_paths
        # Pre-seed llm.json with plaintext
        llm_path.parent.mkdir(parents=True)
        llm_path.write_text(json.dumps({"llm": {"api_key": "sk-existing"}}))
        inputs = [
            "OpenAI", "", "sk-new", "300", "",
            "y",  # migrate → yes
        ]
        run_onboarding(
            llm_json_path=llm_path, dotenv_path=env_path, inputs=inputs
        )
        data = json.loads(llm_path.read_text(encoding="utf-8"))
        assert data["llm"]["api_key"] == "env:LLM_API_KEY"
        env_content = env_path.read_text(encoding="utf-8")
        assert "LLM_API_KEY=sk-new" in env_content

    def test_plaintext_migrate_no(self, fresh_paths):
        llm_path, env_path = fresh_paths
        llm_path.parent.mkdir(parents=True)
        llm_path.write_text(json.dumps({"llm": {"api_key": "sk-existing"}}))
        inputs = [
            "OpenAI", "", "sk-new", "300", "",
            "n",  # migrate → no
        ]
        run_onboarding(
            llm_json_path=llm_path, dotenv_path=env_path, inputs=inputs
        )
        data = json.loads(llm_path.read_text(encoding="utf-8"))
        # Step 0 auto-fixes C1 (plaintext → env:LLM_API_KEY) before wizard runs,
        # so by K3 time there's no plaintext left to migrate. The wizard then
        # sets api_key=env:LLM_API_KEY (from _LLM_API_KEY_REF) and the real
        # key goes to .env via _save_tokens_to_dotenv.
        assert data["llm"]["api_key"] == "env:LLM_API_KEY"
        env_content = env_path.read_text(encoding="utf-8")
        assert "LLM_API_KEY=sk-new" in env_content
