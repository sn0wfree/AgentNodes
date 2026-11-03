"""Tests for cli/llm_config_check.py — LLM 配置检测逻辑。

覆盖：
- 文件存在性 + JSON 解析
- api_key 来源优先级（env > .env > llm.json）
- `env:` 引用形式
- ``configured`` 综合判断
- ``get_install_message`` 输出
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from strategy_research.interfaces.cli import llm_config_check


@pytest.fixture
def isolated_quantnodes(tmp_path, monkeypatch):
    """把 ~/.quantnodes/ 重定向到临时目录，避免污染用户实际配置。"""
    qdir = tmp_path / ".quantnodes"
    qdir.mkdir()
    monkeypatch.setattr(llm_config_check, "QUANTNODES_DIR", qdir)
    monkeypatch.setattr(llm_config_check, "LLM_JSON_PATH", qdir / "llm.json")
    monkeypatch.setattr(llm_config_check, "DOTENV_PATH", qdir / ".env")
    # 清空 env
    for k in ("OPENAI_API_KEY", "LLM_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    return qdir


@pytest.fixture
def isolated_no_dir(tmp_path, monkeypatch):
    """~/.quantnodes/ 不存在的场景。"""
    qdir = tmp_path / ".quantnodes"  # 不 mkdir
    monkeypatch.setattr(llm_config_check, "QUANTNODES_DIR", qdir)
    monkeypatch.setattr(llm_config_check, "LLM_JSON_PATH", qdir / "llm.json")
    monkeypatch.setattr(llm_config_check, "DOTENV_PATH", qdir / ".env")
    for k in ("OPENAI_API_KEY", "LLM_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    return qdir


def _write_llm_json(path: Path, **llm_section):
    path.write_text(json.dumps({"llm": llm_section}, ensure_ascii=False), encoding="utf-8")


def _write_dotenv(path: Path, key: str, value: str):
    path.write_text(f"{key}={value}\n", encoding="utf-8")


# ─────────────────────────── 文件不存在 / 损坏 ───────────────────────────

class TestMissingConfig:
    def test_no_quantnodes_dir(self, isolated_no_dir):
        """~/.quantnodes/ 不存在 → configured=False, all flags False。"""
        status = llm_config_check.check_llm_config()

        assert status["configured"] is False
        assert status["quantnodes_dir_exists"] is False
        assert status["llm_json_exists"] is False
        assert status["dotenv_exists"] is False
        assert status["env_var_set"] is False
        assert status["api_key_source"] == "none"
        assert status["model"] == ""
        assert status["provider"] == ""

    def test_empty_quantnodes_dir(self, isolated_quantnodes):
        """目录存在但完全空 → configured=False。"""
        status = llm_config_check.check_llm_config()

        assert status["quantnodes_dir_exists"] is True
        assert status["configured"] is False
        assert status["api_key_source"] == "none"

    def test_malformed_json(self, isolated_quantnodes):
        """llm.json 是无效 JSON → 降级为无 llm 段。"""
        (isolated_quantnodes / "llm.json").write_text("{invalid json", encoding="utf-8")

        status = llm_config_check.check_llm_config()

        assert status["llm_json_exists"] is True
        assert status["llm_json_has_llm_section"] is False
        assert status["configured"] is False


# ─────────────────────────── llm.json 段 ───────────────────────────

class TestLLMJson:
    def test_llm_section_present(self, isolated_quantnodes):
        _write_llm_json(isolated_quantnodes / "llm.json", provider="openai", model="gpt-4")
        status = llm_config_check.check_llm_config()

        assert status["llm_json_has_llm_section"] is True
        assert status["provider"] == "openai"
        assert status["model"] == "gpt-4"

    def test_no_llm_section(self, isolated_quantnodes):
        """llm.json 存在但无 llm 段。"""
        (isolated_quantnodes / "llm.json").write_text('{"other": 1}', encoding="utf-8")

        status = llm_config_check.check_llm_config()

        assert status["llm_json_exists"] is True
        assert status["llm_json_has_llm_section"] is False
        assert status["provider"] == ""
        assert status["model"] == ""

    def test_llm_section_is_not_dict(self, isolated_quantnodes):
        """llm 段存在但不是 dict（极端情况）→ 视为无效。"""
        (isolated_quantnodes / "llm.json").write_text('{"llm": "not a dict"}', encoding="utf-8")

        status = llm_config_check.check_llm_config()

        assert status["llm_json_exists"] is True
        assert status["llm_json_has_llm_section"] is False


# ─────────────────────────── api_key 来源优先级 ───────────────────────────

class TestAPIKeySources:
    def test_env_var_wins(self, isolated_quantnodes, monkeypatch):
        """env var 优先级最高（即使其他来源都有值）。"""
        _write_llm_json(
            isolated_quantnodes / "llm.json",
            provider="openai", model="gpt-4", api_key="llm-json-key"
        )
        _write_dotenv(isolated_quantnodes / ".env", "LLM_API_KEY", "dotenv-key")
        monkeypatch.setenv("OPENAI_API_KEY", "env-key")

        status = llm_config_check.check_llm_config()

        assert status["api_key_source"] == "env"
        assert status["env_var_set"] is True
        assert status["configured"] is True

    def test_dotenv_beats_llm_json(self, isolated_quantnodes):
        """没有 env var 时，.env 优先于 llm.json。"""
        _write_llm_json(
            isolated_quantnodes / "llm.json",
            provider="openai", model="gpt-4", api_key="llm-json-key"
        )
        _write_dotenv(isolated_quantnodes / ".env", "OPENAI_API_KEY", "dotenv-key")

        status = llm_config_check.check_llm_config()

        assert status["api_key_source"] == "dotenv"
        assert status["dotenv_has_api_key"] is True
        assert status["configured"] is True

    def test_llm_json_fallback(self, isolated_quantnodes):
        """只有 llm.json 有 api_key 时使用它。"""
        _write_llm_json(
            isolated_quantnodes / "llm.json",
            provider="openai", model="gpt-4", api_key="llm-json-key"
        )

        status = llm_config_check.check_llm_config()

        assert status["api_key_source"] == "llm.json"
        assert status["configured"] is True

    def test_env_reference_is_skipped(self, isolated_quantnodes):
        """llm.json 中的 api_key 是 'env:LLM_API_KEY' 引用 → 视为无 api_key。"""
        _write_llm_json(
            isolated_quantnodes / "llm.json",
            provider="openai", model="gpt-4", api_key="env:LLM_API_KEY"
        )

        status = llm_config_check.check_llm_config()

        # 没有 .env，所以 api_key 来源应该是 "none"
        assert status["api_key_source"] == "none"
        assert status["configured"] is False

    def test_env_reference_with_dotenv(self, isolated_quantnodes):
        """llm.json 引用 + .env 有真实 key → 用 .env。"""
        _write_llm_json(
            isolated_quantnodes / "llm.json",
            provider="openai", model="gpt-4", api_key="env:LLM_API_KEY"
        )
        _write_dotenv(isolated_quantnodes / ".env", "LLM_API_KEY", "real-key")

        status = llm_config_check.check_llm_config()

        assert status["api_key_source"] == "dotenv"
        assert status["configured"] is True

    def test_llm_api_key_env_var(self, isolated_quantnodes, monkeypatch):
        """支持 LLM_API_KEY env var（不仅 OPENAI_API_KEY）。"""
        _write_llm_json(isolated_quantnodes / "llm.json", provider="openai", model="gpt-4")
        monkeypatch.setenv("LLM_API_KEY", "llm-env-key")

        status = llm_config_check.check_llm_config()

        assert status["env_var_set"] is True
        assert status["api_key_source"] == "env"
        assert status["configured"] is True


# ─────────────────────────── 综合判断 ───────────────────────────

class TestConfigured:
    def test_all_three_pieces(self, isolated_quantnodes):
        """provider + model + api_key 齐全 → configured=True。"""
        _write_llm_json(isolated_quantnodes / "llm.json", provider="openai", model="gpt-4")
        _write_dotenv(isolated_quantnodes / ".env", "OPENAI_API_KEY", "key")

        status = llm_config_check.check_llm_config()

        assert status["configured"] is True
        assert status["provider"] == "openai"
        assert status["model"] == "gpt-4"

    def test_missing_provider(self, isolated_quantnodes):
        _write_llm_json(isolated_quantnodes / "llm.json", model="gpt-4")
        _write_dotenv(isolated_quantnodes / ".env", "OPENAI_API_KEY", "key")

        status = llm_config_check.check_llm_config()

        assert status["provider"] == ""
        assert status["configured"] is False

    def test_missing_model(self, isolated_quantnodes):
        _write_llm_json(isolated_quantnodes / "llm.json", provider="openai")
        _write_dotenv(isolated_quantnodes / ".env", "OPENAI_API_KEY", "key")

        status = llm_config_check.check_llm_config()

        assert status["model"] == ""
        assert status["configured"] is False


# ─────────────────────────── get_install_message ───────────────────────────

class TestInstallMessage:
    def test_configured_message(self, isolated_quantnodes):
        _write_llm_json(
            isolated_quantnodes / "llm.json",
            provider="openai", model="gpt-4",
        )
        _write_dotenv(isolated_quantnodes / ".env", "OPENAI_API_KEY", "key")

        status = llm_config_check.check_llm_config()
        msg = llm_config_check.get_install_message(status)

        assert "openai" in msg
        assert "gpt-4" in msg
        assert "跳过" in msg

    def test_no_dir_message(self, isolated_no_dir):
        status = llm_config_check.check_llm_config()
        msg = llm_config_check.get_install_message(status)

        assert "~/.quantnodes/" in msg
        # 提示内容应引导用户运行 init
        assert "init" in msg.lower() or "不存在" in msg

    def test_no_api_key_message(self, isolated_quantnodes):
        _write_llm_json(
            isolated_quantnodes / "llm.json",
            provider="openai", model="gpt-4",
        )

        status = llm_config_check.check_llm_config()
        msg = llm_config_check.get_install_message(status)

        assert "API key" in msg or "OPENAI_API_KEY" in msg
        assert "init" in msg.lower() or "export" in msg.lower()


# ─────────────────────────── _read_dotenv_api_key ───────────────────────────

class TestReadDotenv:
    def test_strips_quotes(self, isolated_quantnodes):
        """单/双引号应被剥除。"""
        _write_dotenv(isolated_quantnodes / ".env", "OPENAI_API_KEY", '"sk-quoted"')

        result = llm_config_check._read_dotenv_api_key(isolated_quantnodes / ".env")

        assert result == "sk-quoted"

    def test_skips_comments(self, isolated_quantnodes):
        (isolated_quantnodes / ".env").write_text(
            "# comment\nOPENAI_API_KEY=key1\n# other comment\n",
            encoding="utf-8",
        )

        result = llm_config_check._read_dotenv_api_key(isolated_quantnodes / ".env")

        assert result == "key1"

    def test_skips_empty_and_malformed(self, isolated_quantnodes):
        (isolated_quantnodes / ".env").write_text(
            "\n# comment\nnotakeyline\nOPENAI_API_KEY=value\n",
            encoding="utf-8",
        )

        result = llm_config_check._read_dotenv_api_key(isolated_quantnodes / ".env")

        assert result == "value"

    def test_returns_empty_for_missing_file(self, isolated_quantnodes):
        result = llm_config_check._read_dotenv_api_key(isolated_quantnodes / "nonexistent")

        assert result == ""
