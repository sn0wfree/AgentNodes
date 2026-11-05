"""Tests for chat/goal mode switching and prompt selection.

Verifies that:
  1. ``InteractiveContext.interactive_mode`` defaults to ``"chat"``
  2. ``ChatSession._sync_interactive_mode`` reads GoalStore correctly
  3. ``_run_agent_loop`` selects the right prompt per mode
  4. ``chat.md`` exists and contains the right instructions
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

import pytest

from agentnodes import _TEMPLATES_DIR
from agentnodes.interfaces.cli.interactive.main import InteractiveContext
from agentnodes.interfaces.cli.tui.session import ChatSession

_CHAT_PROMPT_PATH = _TEMPLATES_DIR / ".prompts" / "chat.md"


# ---------------------------------------------------------------- InteractiveContext default


class TestDefaultMode:
    def test_default_mode_is_chat(self):
        ctx = InteractiveContext()
        assert ctx.interactive_mode == "chat"

    def test_mode_is_settable(self):
        ctx = InteractiveContext()
        ctx.interactive_mode = "goal"
        assert ctx.interactive_mode == "goal"


# ---------------------------------------------------------------- _sync_interactive_mode




# ---------------------------------------------------------------- prompt selection




# ---------------------------------------------------------------- chat.md file


class TestChatPromptFile:
    def test_chat_md_exists(self):
        assert _CHAT_PROMPT_PATH.exists()

    def test_chat_md_not_empty(self):
        content = _CHAT_PROMPT_PATH.read_text(encoding="utf-8")
        assert len(content.strip()) > 0

    def test_chat_md_no_json_instruction(self):
        """chat.md must NOT instruct the LLM to output JSON."""
        content = _CHAT_PROMPT_PATH.read_text(encoding="utf-8").lower()
        assert "纯 json" not in content
        assert "必须返回" not in content or "json" not in content
        assert "直接以 {" not in content

    def test_chat_md_mentions_natural_language(self):
        content = _CHAT_PROMPT_PATH.read_text(encoding="utf-8")
        assert "自然语言" in content
