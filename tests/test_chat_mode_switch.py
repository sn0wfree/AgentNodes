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

from strategy_research import _TEMPLATES_DIR
from strategy_research.cli.interactive.main import InteractiveContext
from strategy_research.cli.tui.session import ChatSession

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


class TestSyncMode:
    def _make_session(self, *, session_id="cli"):
        ctx = InteractiveContext()
        ctx.session_id = session_id
        session = ChatSession.__new__(ChatSession)
        session.ctx = ctx
        session.app = None
        session.llm_client = None
        session.transcript_width = 120
        session.session_logger = None
        session._pending_input = None
        return session

    def test_no_goal_sets_chat(self, monkeypatch):
        session = self._make_session()
        mock_store = mock.MagicMock()
        mock_store.get_current_goal.return_value = None
        monkeypatch.setattr(
            "strategy_research.core.goal.GoalStore",
            mock.MagicMock(return_value=mock_store),
        )
        session._sync_interactive_mode()
        assert session.ctx.interactive_mode == "chat"

    def test_active_goal_sets_goal(self, monkeypatch):
        session = self._make_session()
        fake_goal = SimpleNamespace(goal_id="g1", status="active")
        mock_store = mock.MagicMock()
        mock_store.get_current_goal.return_value = fake_goal
        monkeypatch.setattr(
            "strategy_research.core.goal.GoalStore",
            mock.MagicMock(return_value=mock_store),
        )
        session._sync_interactive_mode()
        assert session.ctx.interactive_mode == "goal"

    def test_store_failure_falls_back_to_chat(self, monkeypatch):
        session = self._make_session()
        monkeypatch.setattr(
            "strategy_research.core.goal.GoalStore",
            mock.MagicMock(side_effect=RuntimeError("db fail")),
        )
        session._sync_interactive_mode()
        assert session.ctx.interactive_mode == "chat"

    def test_goal_completed_sets_back_to_chat(self, monkeypatch):
        session = self._make_session()
        session.ctx.interactive_mode = "goal"
        mock_store = mock.MagicMock()
        mock_store.get_current_goal.return_value = None
        monkeypatch.setattr(
            "strategy_research.core.goal.GoalStore",
            mock.MagicMock(return_value=mock_store),
        )
        session._sync_interactive_mode()
        assert session.ctx.interactive_mode == "chat"


# ---------------------------------------------------------------- prompt selection


class TestPromptSelection:
    def test_chat_mode_uses_chat_prompt(self, monkeypatch):
        """_run_agent_loop should load chat.md when mode is 'chat'."""
        session = ChatSession.__new__(ChatSession)
        ctx = InteractiveContext()
        ctx.interactive_mode = "chat"
        session.ctx = ctx
        session.app = mock.MagicMock()
        session.llm_client = mock.MagicMock()
        session.llm_client.config = mock.MagicMock()
        session.transcript_width = 120
        session.session_logger = None
        session._pending_input = None

        _CHAT_PROMPT_PATH.read_text(encoding='utf-8')

        # Capture what system_prompt is passed to AgentLoop
        captured_prompts = []
        None

        def capture_agent_loop_init(self_loop, **kwargs):
            captured_prompts.append(kwargs.get("system_prompt", ""))
            # Don't actually construct — just capture
            raise SystemExit(0)  # abort construction

        monkeypatch.setattr(
            "strategy_research.core.agent.loop.AgentLoop.__init__",
            capture_agent_loop_init,
        )
        monkeypatch.setattr(
            "strategy_research.core.agent.builtin_tools.build_default_registry",
            mock.MagicMock(return_value=None),
        )
        monkeypatch.setattr(
            "strategy_research.foundation.llm.LLMConfig.load",
            mock.MagicMock(return_value=SimpleNamespace(api_key="sk-test")),
        )

        with pytest.raises(SystemExit):
            import asyncio
            asyncio.run(session._run_agent_loop("test"))

        assert len(captured_prompts) == 1
        assert "自然语言" in captured_prompts[0] or "chat" in captured_prompts[0].lower()

    def test_goal_mode_uses_researcher_prompt(self, monkeypatch):
        """_run_agent_loop should load researcher.md when mode is 'goal'."""
        session = ChatSession.__new__(ChatSession)
        ctx = InteractiveContext()
        ctx.interactive_mode = "goal"
        session.ctx = ctx
        session.app = mock.MagicMock()
        session.llm_client = mock.MagicMock()
        session.llm_client.config = mock.MagicMock()
        session.transcript_width = 120
        session.session_logger = None
        session._pending_input = None

        captured_prompts = []

        def capture_agent_loop_init(self_loop, **kwargs):
            captured_prompts.append(kwargs.get("system_prompt", ""))
            raise SystemExit(0)

        monkeypatch.setattr(
            "strategy_research.core.agent.loop.AgentLoop.__init__",
            capture_agent_loop_init,
        )
        monkeypatch.setattr(
            "strategy_research.core.agent.builtin_tools.build_default_registry",
            mock.MagicMock(return_value=None),
        )
        monkeypatch.setattr(
            "strategy_research.foundation.llm.LLMConfig.load",
            mock.MagicMock(return_value=SimpleNamespace(api_key="sk-test")),
        )

        with pytest.raises(SystemExit):
            import asyncio
            asyncio.run(session._run_agent_loop("test"))

        assert len(captured_prompts) == 1
        assert "JSON" in captured_prompts[0] or "researcher" in captured_prompts[0].lower()


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
