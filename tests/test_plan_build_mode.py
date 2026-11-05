"""Tests for Plan/Build mode, model override, and thinking params."""

from __future__ import annotations

from agentnodes.interfaces.api.routers.chat import ChatMessage
from agentnodes.interfaces.api.session.models import Attempt

# ── ChatMessage schema ────────────────────────────────────────────


class TestChatMessageSchema:
    """ChatMessage has mode, model, thinking fields."""

    def test_mode_optional_defaults_none(self):
        msg = ChatMessage(session_id="s1", content="hello")
        assert msg.mode is None
        assert msg.model is None
        assert msg.thinking is None

    def test_mode_plan(self):
        msg = ChatMessage(session_id="s1", content="hello", mode="plan")
        assert msg.mode == "plan"

    def test_mode_build(self):
        msg = ChatMessage(session_id="s1", content="hello", mode="build")
        assert msg.mode == "build"

    def test_model_override(self):
        msg = ChatMessage(session_id="s1", content="hello", model="deepseek/DeepSeek-V3")
        assert msg.model == "deepseek/DeepSeek-V3"

    def test_thinking_off(self):
        msg = ChatMessage(session_id="s1", content="hello", thinking="off")
        assert msg.thinking == "off"

    def test_thinking_on(self):
        msg = ChatMessage(session_id="s1", content="hello", thinking="on")
        assert msg.thinking == "on"

    def test_thinking_auto(self):
        msg = ChatMessage(session_id="s1", content="hello", thinking="auto")
        assert msg.thinking == "auto"


# ── Attempt model ─────────────────────────────────────────────────


class TestAttemptModel:
    """Attempt has mode, model_override, thinking fields."""

    def test_defaults(self):
        a = Attempt(session_id="s1")
        assert a.mode == "build"
        assert a.model_override is None
        assert a.thinking == "auto"

    def test_plan_mode(self):
        a = Attempt(session_id="s1", mode="plan")
        assert a.mode == "plan"

    def test_model_override(self):
        a = Attempt(session_id="s1", model_override="deepseek/DeepSeek-V3")
        assert a.model_override == "deepseek/DeepSeek-V3"

    def test_thinking_off(self):
        a = Attempt(session_id="s1", thinking="off")
        assert a.thinking == "off"

    def test_to_dict_includes_new_fields(self):
        a = Attempt(session_id="s1", mode="plan", thinking="on")
        d = a.to_dict()
        assert d["mode"] == "plan"
        assert d["thinking"] == "on"


# ── Plan mode tool filtering ──────────────────────────────────────


class TestPlanModeTools:
    """Plan mode restricts tools to read-only set."""

    def test_plan_readonly_tools_is_set(self):
        from agentnodes.interfaces.api.session.loop_events import _PLAN_READONLY_TOOLS
        assert "read" in _PLAN_READONLY_TOOLS
        assert "websearch" in _PLAN_READONLY_TOOLS
        assert "run_backtest" not in _PLAN_READONLY_TOOLS

    def test_plan_mode_sets_allowed_tools(self):
        """When mode='plan', allowed_tools is set to readonly list."""
        from agentnodes.interfaces.api.session.loop_events import (
            _PLAN_READONLY_TOOLS,
            _plan_mode_allowed_tools,
        )
        assert set(_plan_mode_allowed_tools("plan")) == set(_PLAN_READONLY_TOOLS)

    def test_build_mode_no_tool_restriction(self):
        """When mode='build', allowed_tools stays None (all tools)."""
        from agentnodes.interfaces.api.session.service import _plan_mode_allowed_tools
        assert _plan_mode_allowed_tools("build") is None


# ── Thinking parameter injection ──────────────────────────────────


class TestThinkingInjection:
    """Thinking params are injected into system prompt."""

    def test_thinking_off_injects_instruction(self):
        from agentnodes.interfaces.api.session.service import _thinking_instructions
        assert "Do NOT use thinking/reasoning blocks" in _thinking_instructions("off")

    def test_thinking_on_injects_instruction(self):
        from agentnodes.interfaces.api.session.service import _thinking_instructions
        assert "Use extended thinking for complex analysis" in _thinking_instructions("on")

    def test_thinking_auto_no_injection(self):
        """Auto mode should not inject any instruction."""
        from agentnodes.interfaces.api.session.service import _thinking_instructions
        assert _thinking_instructions("auto") == ""


# ── send_async passes new fields ──────────────────────────────────


class TestSendAsyncPassesFields:
    """chat.py send_async passes mode, model, thinking to service."""

    def test_send_async_includes_mode(self):
        import inspect

        from agentnodes.interfaces.api.routers.chat import send_async
        source = inspect.getsource(send_async)
        assert "mode=_mode" in source
        assert "body.mode" in source

    def test_send_async_includes_model(self):
        import inspect

        from agentnodes.interfaces.api.routers.chat import send_async
        source = inspect.getsource(send_async)
        assert "model=body.model" in source

    def test_send_async_includes_thinking(self):
        import inspect

        from agentnodes.interfaces.api.routers.chat import send_async
        source = inspect.getsource(send_async)
        assert "thinking=body.thinking" in source

    def test_send_async_plan_mode_max_iter_1(self):
        """Plan mode should set max_iterations to 1."""
        import inspect

        from agentnodes.interfaces.api.routers.chat import send_async
        source = inspect.getsource(send_async)
        assert '_max_iter_eff = 1 if _mode == "plan"' in source

    def test_send_async_plan_mode_no_shell(self):
        """Plan mode should disable shell tools (via _shell_tools_enabled)."""
        import inspect

        from agentnodes.interfaces.api.routers.chat import send_async
        source = inspect.getsource(send_async)
        assert "_shell_tools_enabled(_mode)" in source
