"""Tests for ``cli.commands.slash_chat``."""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest
from rich.console import Console

from strategy_research.interfaces.cli.commands.slash_chat import (
    cmd_clear,
    cmd_debug,
    cmd_journal,
    cmd_model,
    cmd_quit,
    cmd_shadow,
    run_clear,
    run_debug,
    run_journal,
    run_model,
    run_quit,
    run_shadow,
)


@pytest.fixture
def console():
    return Console(record=True, force_terminal=False, width=120)


@dataclass
class _Ctx:
    history: list = field(default_factory=list)
    debug: bool = False
    pending_prompt: str = ""


# ─── cmd_model ─────────────────────────────────────────────────────────


class TestCmdModel:
    def test_renders_provider_and_model(self, monkeypatch):
        monkeypatch.setenv("LANGCHAIN_PROVIDER", "openai")
        monkeypatch.setenv("LANGCHAIN_MODEL_NAME", "gpt-4o")
        rc = cmd_model()
        assert rc == 0

    def test_no_env(self, monkeypatch):
        # Env vars not set → show "(not set)"
        monkeypatch.delenv("LANGCHAIN_PROVIDER", raising=False)
        monkeypatch.delenv("LANGCHAIN_MODEL_NAME", raising=False)
        monkeypatch.delenv("MINIMAX_PROVIDER", raising=False)
        monkeypatch.delenv("MINIMAX_MODEL", raising=False)
        rc = cmd_model()
        assert rc == 0


# ─── cmd_clear ─────────────────────────────────────────────────────────


class TestCmdClear:
    def test_clears_history(self):
        ctx = _Ctx(history=["a", "b"])
        rc = cmd_clear(ctx)
        assert rc == 0
        assert ctx.history == []

    def test_no_ctx(self):
        rc = cmd_clear()
        assert rc == 0


# ─── cmd_quit ──────────────────────────────────────────────────────────


class TestCmdQuit:
    def test_returns_2(self):
        assert cmd_quit() == 2


# ─── cmd_debug ─────────────────────────────────────────────────────────


class TestCmdDebug:
    def test_toggles_on(self):
        ctx = _Ctx(debug=False)
        rc = cmd_debug(ctx)
        assert rc == 0
        assert ctx.debug is True

    def test_toggles_off(self):
        ctx = _Ctx(debug=True)
        rc = cmd_debug(ctx)
        assert rc == 0
        assert ctx.debug is False

    def test_no_ctx(self):
        rc = cmd_debug()
        assert rc == 0


# ─── cmd_journal / cmd_shadow ──────────────────────────────────────────


class TestJournalShadow:
    def test_journal_no_args(self):
        rc = cmd_journal()
        assert rc == 0

    def test_journal_with_path_queues_prompt(self):
        ctx = _Ctx()
        rc = cmd_journal(ctx, "/path/journal.csv")
        assert rc == 0
        assert "journal" in ctx.pending_prompt.lower()
        assert "/path/journal.csv" in ctx.pending_prompt

    def test_shadow_no_args(self):
        rc = cmd_shadow()
        assert rc == 0

    def test_shadow_with_path_queues_prompt(self):
        ctx = _Ctx()
        rc = cmd_shadow(ctx, "/path/journal.csv")
        assert rc == 0
        assert "shadow" in ctx.pending_prompt.lower()


# ─── Slash router entrypoints ──────────────────────────────────────────


class TestRouterEntrypoints:
    def test_run_quit(self):
        assert run_quit() == 2

    def test_run_clear(self):
        ctx = _Ctx(history=["x"])
        rc = run_clear(ctx=ctx)
        assert rc == 0
        assert ctx.history == []

    def test_run_model(self):
        rc = run_model()
        assert rc == 0

    def test_run_debug(self):
        ctx = _Ctx()
        rc = run_debug(ctx=ctx)
        assert rc == 0
        assert ctx.debug is True

    def test_run_journal_path(self):
        ctx = _Ctx()
        rc = run_journal(ctx, "/p.csv")
        assert rc == 0

    def test_run_shadow_path(self):
        ctx = _Ctx()
        rc = run_shadow(ctx, "/p.csv")
        assert rc == 0
