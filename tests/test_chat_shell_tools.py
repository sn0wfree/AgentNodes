"""Tests for the chat shell-tool gate — ``_shell_tools_enabled``.

The web chat offers ``run_command`` (bash/python) to the agent only when
``SR_ALLOW_SHELL_TOOLS`` is set (1/true/yes), and never in plan mode.
"""
from __future__ import annotations

import pytest

from strategy_research.interfaces.api.routers.chat import _shell_tools_enabled


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "True", "yes", "YES"])
def test_enabled_for_truthy_values(monkeypatch, value: str):
    monkeypatch.setenv("SR_ALLOW_SHELL_TOOLS", value)
    assert _shell_tools_enabled() is True


@pytest.mark.parametrize("value", ["0", "false", "no", "off", "2", "anything"])
def test_disabled_for_other_values(monkeypatch, value: str):
    monkeypatch.setenv("SR_ALLOW_SHELL_TOOLS", value)
    assert _shell_tools_enabled() is False


def test_disabled_when_unset(monkeypatch):
    monkeypatch.delenv("SR_ALLOW_SHELL_TOOLS", raising=False)
    assert _shell_tools_enabled() is False


def test_build_mode_uses_env_var(monkeypatch):
    monkeypatch.delenv("SR_ALLOW_SHELL_TOOLS", raising=False)
    assert _shell_tools_enabled("build") is False
    monkeypatch.setenv("SR_ALLOW_SHELL_TOOLS", "1")
    assert _shell_tools_enabled("build") is True


def test_plan_mode_never_enables_shell(monkeypatch):
    """Plan mode is analysis-only: shell tools are hard-disabled."""
    monkeypatch.setenv("SR_ALLOW_SHELL_TOOLS", "1")
    assert _shell_tools_enabled("plan") is False


def test_none_mode_behaves_like_default(monkeypatch):
    monkeypatch.setenv("SR_ALLOW_SHELL_TOOLS", "1")
    assert _shell_tools_enabled(None) is True
