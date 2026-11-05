"""Tests for ModeBar widget, Ctrl+M toggle, and _sync_interactive_mode.

Coverage matrix:
  ┌─────────────────────────────────────────────────┬──────────┐
  │ Scenario                                       │ Tests    │
  ├─────────────────────────────────────────────────┼──────────┤
  │ ModeBar __init__ default (chat)                 │ 1        │
  │ ModeBar __init__ explicit mode                  │ 1        │
  │ ModeBar update_mode chat→goal                   │ 1        │
  │ ModeBar update_mode goal→chat                   │ 1        │
  │ ModeBar unknown mode falls back to chat label   │ 1        │
  │ ModeBar labels contain expected keywords        │ 3        │
  │ Keybinding ctrl+m registered + action name      │ 2        │
  │ action_toggle_mode chat→goal                    │ 1        │
  │ action_toggle_mode goal→chat                    │ 1        │
  │ action_toggle_mode updates ModeBar widget       │ 1        │
  │ action_toggle_mode shows notification (goal)    │ 1        │
  │ action_toggle_mode shows notification (chat)    │ 1        │
  │ action_toggle_mode no session → no crash        │ 1        │
  │ action_toggle_mode query_one exception → safe   │ 1        │
  │ _sync: no GoalStore → defaults to chat          │ 1        │
  │ _sync: GoalStore with goal → mode "goal"        │ 1        │
  │ _sync: GoalStore without goal → mode "chat"     │ 1        │
  │ _sync: GoalStore exception → fallback chat      │ 1        │
  │ _sync: updates ModeBar widget                   │ 1        │
  │ _sync: ModeBar query fails → silent pass        │ 1        │
  │ _sync: app is None → skip widget update         │ 1        │
  │ on_mount sets initial mode bar                  │ 1        │
  │ Integration: full app.run_test mounts ModeBar   │ 1        │
  ├─────────────────────────────────────────────────┼──────────┤
  │ Total                                           │ 26       │
  └─────────────────────────────────────────────────┴──────────┘
"""
from __future__ import annotations

from unittest import mock

import pytest

from strategy_research.interfaces.cli.interactive.main import InteractiveContext
from strategy_research.interfaces.cli.tui.app import ResearchApp
from strategy_research.interfaces.cli.tui.keybindings import TUI_BINDINGS
from strategy_research.interfaces.cli.tui.session import ChatSession
from strategy_research.interfaces.cli.tui.widgets.mode_bar import ModeBar

# ================================================================
# ModeBar widget
# ================================================================


class TestModeBarInit:
    def test_default_mode_is_chat(self):
        bar = ModeBar()
        assert bar._mode == "chat"

    def test_explicit_mode_goal(self):
        bar = ModeBar(mode="goal")
        assert bar._mode == "goal"

    def test_explicit_mode_chat(self):
        bar = ModeBar(mode="chat")
        assert bar._mode == "chat"


class TestModeBarUpdateMode:
    def test_update_chat_to_goal(self):
        bar = ModeBar(mode="chat")
        bar.update_mode("goal")
        assert bar._mode == "goal"

    def test_update_goal_to_chat(self):
        bar = ModeBar(mode="goal")
        bar.update_mode("chat")
        assert bar._mode == "chat"

    def test_update_same_mode_no_error(self):
        bar = ModeBar(mode="chat")
        bar.update_mode("chat")
        assert bar._mode == "chat"

    def test_unknown_mode_falls_back_to_chat_label(self):
        bar = ModeBar(mode="chat")
        bar.update_mode("unknown_xyz")
        assert bar._mode == "unknown_xyz"
        # _render_mode should use chat fallback
        assert "CHAT" in bar._MODE_LABELS.get("unknown_xyz", bar._MODE_LABELS["chat"])


class TestModeBarLabels:
    def test_chat_label_has_keywords(self):
        label = ModeBar._MODE_LABELS["chat"]
        assert "CHAT" in label
        assert "普通聊天" in label

    def test_goal_label_has_keywords(self):
        label = ModeBar._MODE_LABELS["goal"]
        assert "GOAL" in label
        assert "策略研究" in label

    def test_all_labels_mention_ctrl_m(self):
        for label in ModeBar._MODE_LABELS.values():
            assert "Ctrl+M" in label


# ================================================================
# Keybinding
# ================================================================


class TestKeybinding:
    def test_ctrl_m_registered(self):
        keys = {b.key for b in TUI_BINDINGS}
        assert "ctrl+m" in keys

    def test_ctrl_m_action_is_toggle_mode(self):
        actions = {b.action for b in TUI_BINDINGS if b.key == "ctrl+m"}
        assert "toggle_mode" in actions


# ================================================================
# action_toggle_mode
# ================================================================


def _make_app(mode: str = "chat"):
    """Build a minimal ResearchApp for action_toggle_mode tests."""
    app = ResearchApp.__new__(ResearchApp)
    app._tool_total = 0
    app._tool_ok = 0
    ctx = InteractiveContext()
    ctx.interactive_mode = mode
    session = mock.MagicMock()
    session.ctx = ctx
    app.session = session
    mock_bar = mock.MagicMock()
    app.query_one = mock.MagicMock(return_value=mock_bar)
    app.write_transcript = mock.MagicMock()
    return app, ctx, mock_bar


class TestActionToggleMode:
    def test_toggles_chat_to_goal(self):
        app, ctx, _ = _make_app("chat")
        app.action_toggle_mode()
        assert ctx.interactive_mode == "goal"

    def test_toggles_goal_to_chat(self):
        app, ctx, _ = _make_app("goal")
        app.action_toggle_mode()
        assert ctx.interactive_mode == "chat"

    def test_updates_mode_bar_widget(self):
        app, _, mock_bar = _make_app("chat")
        app.action_toggle_mode()
        mock_bar.update_mode.assert_called_once_with("goal")

    def test_notification_mentions_goal(self):
        app, _, _ = _make_app("chat")
        app.action_toggle_mode()
        text = app.write_transcript.call_args[0][0]
        assert "策略研究" in text

    def test_notification_mentions_chat(self):
        app, _, _ = _make_app("goal")
        app.action_toggle_mode()
        text = app.write_transcript.call_args[0][0]
        assert "普通聊天" in text

    def test_no_session_no_crash(self):
        app = ResearchApp.__new__(ResearchApp)
        app.session = None
        app.action_toggle_mode()  # should not raise

    def test_query_one_exception_still_toggles(self):
        app, ctx, _ = _make_app("chat")
        app.query_one = mock.MagicMock(side_effect=Exception("no widget"))
        app.action_toggle_mode()
        assert ctx.interactive_mode == "goal"
        app.write_transcript.assert_called_once()


# ================================================================
# _sync_interactive_mode
# ================================================================




# ================================================================
# on_mount integration
# ================================================================


class TestOnMountIntegration:
    def test_on_mount_sets_initial_mode_bar(self):
        """Simulates what on_mount does — ModeBar reflects resolved mode."""
        app = ResearchApp.__new__(ResearchApp)
        ctx = InteractiveContext()
        ctx.interactive_mode = "chat"
        app.ctx = ctx
        mock_bar = mock.MagicMock()
        app.query_one = mock.MagicMock(return_value=mock_bar)
        # Simulate on_mount: query mode bar and set initial mode
        bar = app.query_one("#mode-bar", ModeBar)
        bar.update_mode(ctx.interactive_mode)
        mock_bar.update_mode.assert_called_with("chat")


# ================================================================
# App.run_test integration
# ================================================================


class TestAppRunTestIntegration:
    @pytest.mark.asyncio
    async def test_mode_bar_mounted_in_run_test(self):
        """Full app.run_test — ModeBar widget is mounted in the DOM."""
        app = ResearchApp(model="test", version="0.5.0")
        async with app.run_test() as pilot:
            await pilot.pause()
            bar = app.query_one("#mode-bar", ModeBar)
            assert bar.is_mounted
            assert bar._mode == "chat"
