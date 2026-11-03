"""Tests for cli/_auto_onboard.py — first-launch wizard helpers."""

from __future__ import annotations

import pytest

# ────────────────────────── _first_existing_dotenv_path ──────────────────────────


class TestFirstExistingDotenv:
    def test_returns_none_when_no_files(self, tmp_path, monkeypatch):
        """三个候选都不存在 → None。"""
        from strategy_research.interfaces.cli import _auto_onboard

        fake_q = tmp_path / "q.env"
        fake_p = tmp_path / "p.env"
        fake_c = tmp_path / "c.env"
        monkeypatch.setattr(_auto_onboard, "_QUANTNODES_DOTENV_PATH", fake_q)
        monkeypatch.setattr(_auto_onboard, "_PROJECT_DOTENV_PATH", fake_p)
        monkeypatch.setattr(_auto_onboard, "_CWD_DOTENV_PATH", fake_c)
        # also patch the onboard.py reference
        monkeypatch.setattr(_auto_onboard, "_QUANTNODES_DOTENV_PATH", fake_q, raising=True)

        assert _auto_onboard._first_existing_dotenv_path() is None

    def test_returns_first_existing(self, tmp_path, monkeypatch):
        """HOME 候选存在 → 返回它（不检查后续）。"""
        from strategy_research.interfaces.cli import _auto_onboard

        fake_q = tmp_path / "q.env"
        fake_q.write_text("KEY=value")
        fake_p = tmp_path / "p.env"
        fake_p.write_text("KEY=value")
        monkeypatch.setattr(_auto_onboard, "_QUANTNODES_DOTENV_PATH", fake_q)
        monkeypatch.setattr(_auto_onboard, "_PROJECT_DOTENV_PATH", fake_p)
        monkeypatch.setattr(_auto_onboard, "_CWD_DOTENV_PATH", tmp_path / "c.env")

        result = _auto_onboard._first_existing_dotenv_path()
        assert result == fake_q


# ────────────────────────── _migrate_legacy_env ──────────────────────────


class TestMigrateLegacyEnv:
    def test_skips_when_new_exists(self, tmp_path, monkeypatch):
        """新位置已有 .env → 不复制。"""
        from strategy_research.interfaces.cli import _auto_onboard

        qdir = tmp_path / ".quantnodes"
        qdir.mkdir()
        new_env = qdir / ".env"
        new_env.write_text("NEW=value")

        monkeypatch.setattr(_auto_onboard, "_QUANTNODES_DOTENV_PATH", new_env)

        legacy = tmp_path / ".strategy-research" / ".env"
        legacy.parent.mkdir()
        legacy.write_text("OLD=value")

        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

        _auto_onboard._migrate_legacy_env()

        # new file should be untouched
        assert new_env.read_text() == "NEW=value"

    def test_copies_legacy_when_new_missing(self, tmp_path, monkeypatch):
        """新位置无文件 + 老位置有 → 复制。"""
        from strategy_research.interfaces.cli import _auto_onboard

        qdir = tmp_path / ".quantnodes"
        qdir.mkdir()
        new_env = qdir / ".env"
        # 不写 new_env
        monkeypatch.setattr(_auto_onboard, "_QUANTNODES_DOTENV_PATH", new_env)

        legacy_dir = tmp_path / ".strategy-research"
        legacy_dir.mkdir()
        legacy = legacy_dir / ".env"
        legacy.write_text("LEGACY_KEY=value")

        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

        _auto_onboard._migrate_legacy_env()

        assert new_env.exists()
        assert "LEGACY_KEY" in new_env.read_text()


# ────────────────────────── _maybe_run_onboarding ──────────────────────────


class TestMaybeRunOnboarding:
    def test_non_tty_returns_true(self):
        """非 TTY → 直接返回 True，不跑 wizard。"""
        from strategy_research.interfaces.cli import _auto_onboard

        # In test environment, stdin/stdout aren't TTYs
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("sys.stdin.isatty", lambda: False)
            mp.setattr("sys.stdout.isatty", lambda: False)
            result = _auto_onboard._maybe_run_onboarding(console=None)

        assert result is True

    def test_tty_with_existing_env_returns_true(self, tmp_path, monkeypatch):
        """TTY + 已有 .env → 跳过 wizard，返回 True。"""
        from strategy_research.interfaces.cli import _auto_onboard

        qdir = tmp_path / ".quantnodes"
        qdir.mkdir()
        new_env = qdir / ".env"
        new_env.write_text("KEY=value")
        monkeypatch.setattr(_auto_onboard, "_QUANTNODES_DOTENV_PATH", new_env)
        monkeypatch.setattr(_auto_onboard, "_PROJECT_DOTENV_PATH", tmp_path / "p.env")
        monkeypatch.setattr(_auto_onboard, "_CWD_DOTENV_PATH", tmp_path / "c.env")

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("sys.stdin.isatty", lambda: True)
            mp.setattr("sys.stdout.isatty", lambda: True)

            with pytest.raises(StopIteration) if False else __import__("contextlib").nullcontext():
                result = _auto_onboard._maybe_run_onboarding(console=None)

        assert result is True
