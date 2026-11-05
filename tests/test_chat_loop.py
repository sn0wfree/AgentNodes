"""Tests for Phase 6 P1+P2+P3 — build_chat_agent_loop factory.

See docs/archive/undated/chat-agent-refactor-phase6-agent-loop-factory.md §7.1 for the full spec.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

# ── Fixtures ───────────────────────────────────────────────────────────


class _FakeRegistry:
    """Minimal stand-in for ``ToolRegistry`` with ``_tools`` dict and
    ``get_definitions()`` (called by ContextBuilder.build_system_prompt)."""

    def __init__(self, names: list[str]) -> None:
        self._tools = {
            n: _FakeToolDef(n) for n in names
        }

    def get(self, name):
        return self._tools.get(name)

    def get_definitions(self):
        return [
            {
                "type": "function",
                "function": {
                    "name": n,
                    "description": f"fake tool {n}",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
            for n in self._tools
        ]


class _FakeToolDef:
    """Stand-in tool with an ``is_readonly`` attr (used by AgentLoop
    readonly filtering)."""

    is_readonly = True

    def __init__(self, name: str) -> None:
        self.name = name


@pytest.fixture
def fake_registry(monkeypatch):
    """Patch ``build_default_registry`` to return a deterministic registry."""
    fake = _FakeRegistry(["read", "list", "run_backtest", "websearch"])
    monkeypatch.setattr(
        "strategy_research.kernel.agent.builtin_tools.build_default_registry",
        lambda: fake,
    )
    return fake


@pytest.fixture
def fake_config():
    """LLMConfig-shaped object with a compact_config attr."""
    cfg = MagicMock()
    cfg.compact_config = MagicMock()
    return cfg


# ── P1: default factory behavior ───────────────────────────────────────


class TestChatLoopFactoryDefaults:
    def test_default_registry_loaded(self, fake_config, fake_registry):
        from strategy_research.kernel.agent.chat_loop import build_chat_agent_loop

        loop = build_chat_agent_loop(config=fake_config, session_id="s1")
        # Registry should be the one returned by build_default_registry()
        assert loop.registry is fake_registry

    def _system_prompt(self, loop) -> str:
        """AgentLoop stores system_prompt in ContextBuilder, not as self attr."""
        return loop.context_builder.build_system_prompt()

    def test_chat_role_uses_chat_builder(self, fake_config, fake_registry):
        """role='chat' → PromptBuilderFactory renders via ChatPromptBuilder."""
        from strategy_research.kernel.agent.chat_loop import build_chat_agent_loop

        loop = build_chat_agent_loop(
            config=fake_config, session_id="s1", role="chat"
        )
        sp = self._system_prompt(loop)
        # Common layer prepends principles.md; role content follows.
        assert "# Role: QuantNodes-Research Chat Assistant" in sp

    def test_researcher_role_uses_static_builder(self, fake_config, fake_registry):
        """role='researcher' → StaticFilePromptBuilder (verbatim researcher.md)."""
        from strategy_research.kernel.agent.chat_loop import build_chat_agent_loop

        loop = build_chat_agent_loop(
            config=fake_config, session_id="s1", role="researcher"
        )
        sp = self._system_prompt(loop)
        assert "# Role: Researcher" in sp

    def test_stream_mode_forced_true(self, fake_config, fake_registry):
        from strategy_research.kernel.agent.chat_loop import build_chat_agent_loop

        loop = build_chat_agent_loop(config=fake_config, session_id="s1")
        assert loop._stream_mode is True

    def test_max_iterations_default_one(self, fake_config, fake_registry):
        from strategy_research.kernel.agent.chat_loop import build_chat_agent_loop

        loop = build_chat_agent_loop(config=fake_config, session_id="s1")
        assert loop.max_iterations == 1

    def test_compact_config_from_config(self, fake_config, fake_registry):
        from strategy_research.kernel.agent.chat_loop import build_chat_agent_loop

        loop = build_chat_agent_loop(config=fake_config, session_id="s1")
        assert loop.cc is fake_config.compact_config

    def test_event_bus_passed_through(self, fake_config, fake_registry):
        from strategy_research.kernel.agent.chat_loop import build_chat_agent_loop

        bus = MagicMock()
        loop = build_chat_agent_loop(
            config=fake_config, session_id="s1", event_bus=bus
        )
        assert loop._event_bus is bus

    def test_on_event_passed_through(self, fake_config, fake_registry):
        from strategy_research.kernel.agent.chat_loop import build_chat_agent_loop

        cb = MagicMock()
        loop = build_chat_agent_loop(
            config=fake_config, session_id="s1", on_event=cb
        )
        assert loop._on_event is cb

    def test_session_id_passed(self, fake_config, fake_registry):
        from strategy_research.kernel.agent.chat_loop import build_chat_agent_loop

        loop = build_chat_agent_loop(config=fake_config, session_id="abc-123")
        assert loop.session_id == "abc-123"

    def test_goal_and_hypothesis_disabled(self, fake_config, fake_registry):
        """Chat mode disables goal injection + hypothesis auto-create."""
        from strategy_research.kernel.agent.chat_loop import build_chat_agent_loop

        loop = build_chat_agent_loop(config=fake_config, session_id="s1")
        assert loop.enable_goal_injection is False
        assert loop.enable_hypothesis_auto_create is False


# ── P2: allowed_tools unlock ──────────────────────────────────────────


class TestChatLoopAllowedToolsUnlock:
    def test_default_allowed_tools_means_all_tools(
        self, fake_config, fake_registry
    ):
        """P2: default allowed_tools=None → AgentLoop keeps full registry."""
        from strategy_research.kernel.agent.chat_loop import build_chat_agent_loop

        loop = build_chat_agent_loop(config=fake_config, session_id="s1")
        # None in AgentLoop means "use all tools" → registry not filtered
        # (see loop.py:184: if allowed_tools is not None: filtered...)
        assert loop.registry is fake_registry
        assert sorted(loop.registry._tools.keys()) == [
            "list", "read", "run_backtest", "websearch"
        ]

    def test_explicit_empty_list_filters_to_empty(
        self, fake_config, fake_registry
    ):
        """P2: explicit allowed_tools=[] still disables tools (compat)."""
        from strategy_research.kernel.agent.chat_loop import build_chat_agent_loop

        loop = build_chat_agent_loop(
            config=fake_config, session_id="s1", allowed_tools=[]
        )
        # Empty list → AgentLoop creates empty filtered registry
        assert loop.registry is not fake_registry
        assert len(loop.registry._tools) == 0

    def test_explicit_allowlist(self, fake_config, fake_registry):
        from strategy_research.kernel.agent.chat_loop import build_chat_agent_loop

        loop = build_chat_agent_loop(
            config=fake_config, session_id="s1",
            allowed_tools=["read", "list"],
        )
        # Only the two allowed tools survive
        assert sorted(loop.registry._tools.keys()) == [
            "list", "read"
        ]


# ── P3: workspace / tool_list rendering ───────────────────────────────


class TestChatLoopWorkspaceRendering:
    def _system_prompt(self, loop) -> str:
        return loop.context_builder.build_system_prompt()

    def test_workspace_injected_to_system_prompt(
        self, fake_config, fake_registry, tmp_path
    ):
        """P3: workspace path is rendered into {workspace} placeholder."""
        from strategy_research.kernel.agent.chat_loop import build_chat_agent_loop

        ws = tmp_path / "myws"
        loop = build_chat_agent_loop(
            config=fake_config, session_id="s1",
            role="chat", workspace=ws,
        )
        sp = self._system_prompt(loop)
        assert str(ws) in sp
        assert "{workspace}" not in sp

    def test_workspace_none_no_injection(self, fake_config, fake_registry):
        """P3: workspace=None → {workspace} replaced by empty string."""
        from strategy_research.kernel.agent.chat_loop import build_chat_agent_loop

        loop = build_chat_agent_loop(
            config=fake_config, session_id="s1",
            role="chat", workspace=None,
        )
        sp = self._system_prompt(loop)
        # str.replace({workspace}, "") removes the placeholder
        assert "{workspace}" not in sp

    def test_tool_list_rendered_from_registry(
        self, fake_config, fake_registry
    ):
        """P3: tool_list placeholder rendered from build_default_registry()."""
        from strategy_research.kernel.agent.chat_loop import build_chat_agent_loop

        loop = build_chat_agent_loop(config=fake_config, session_id="s1")
        sp = self._system_prompt(loop)
        # All 4 fake tools must appear in the system prompt
        for tool_name in ["read", "list", "run_backtest", "websearch"]:
            assert tool_name in sp

    def test_extra_context_overrides_defaults(
        self, fake_config, fake_registry, tmp_path
    ):
        """extra_context["workspace"] wins over auto-injected workspace."""
        from strategy_research.kernel.agent.chat_loop import build_chat_agent_loop

        ws = tmp_path / "auto_injected"
        loop = build_chat_agent_loop(
            config=fake_config, session_id="s1",
            role="chat", workspace=ws,
            extra_context={"workspace": "/explicit/override"},
        )
        sp = self._system_prompt(loop)
        assert "/explicit/override" in sp
        assert str(ws) not in sp

    def test_system_prompt_override_skips_factory_render(
        self, fake_config, fake_registry, tmp_path
    ):
        """system_prompt_override bypasses PromptBuilderFactory rendering."""
        from strategy_research.kernel.agent.chat_loop import build_chat_agent_loop

        custom = "CUSTOM_SYSTEM_PROMPT"
        loop = build_chat_agent_loop(
            config=fake_config, session_id="s1",
            role="chat",
            workspace=tmp_path,
            system_prompt_override=custom,
        )
        sp = self._system_prompt(loop)
        assert sp == custom

    def test_compact_config_override_takes_precedence(
        self, fake_config, fake_registry
    ):
        """explicit compact_config overrides config.compact_config."""
        from strategy_research.kernel.agent.chat_loop import build_chat_agent_loop

        custom_cc = MagicMock()
        loop = build_chat_agent_loop(
            config=fake_config, session_id="s1",
            compact_config=custom_cc,
        )
        assert loop.cc is custom_cc

    def test_explicit_registry_used(self, fake_config):
        """registry= parameter bypasses build_default_registry()."""
        from strategy_research.kernel.agent.chat_loop import build_chat_agent_loop

        custom_registry = _FakeRegistry(["my_custom_tool"])
        loop = build_chat_agent_loop(
            config=fake_config, session_id="s1",
            registry=custom_registry,
        )
        assert loop.registry is custom_registry


# ── Shell tool gating (real registry) ─────────────────────────────────


class TestChatLoopShellGating:
    """``allow_shell_tools`` controls whether ``run_command`` is offered.

    Uses the real ``build_default_registry()`` (no fake_registry fixture) so
    the gating logic in ``build_chat_agent_loop`` is exercised end-to-end,
    including the ``{tool_list}`` rendering the LLM actually sees.
    """

    def _loop(self, allow_shell_tools: bool, fake_config):
        from strategy_research.kernel.agent.chat_loop import build_chat_agent_loop

        return build_chat_agent_loop(
            config=fake_config,
            session_id="shell-gating",
            allow_shell_tools=allow_shell_tools,
        )

    def _system_prompt(self, loop) -> str:
        return loop.context_builder.build_system_prompt()

    def test_disabled_removes_run_command_from_registry(self, fake_config):
        loop = self._loop(False, fake_config)
        assert "shell" not in loop.registry._tools

    def test_enabled_keeps_run_command_in_registry(self, fake_config):
        loop = self._loop(True, fake_config)
        assert "shell" in loop.registry._tools

    def test_disabled_hides_run_command_from_tool_list(self, fake_config):
        """The LLM's system prompt must not advertise the shell tool when off.

        chat.md prose mentions ``- shell 命令…`` in both modes, so a bare
        substring check is a false positive; compare the rendered
        ``{tool_list}`` contribution via bullet count (the registry tool
        is named ``shell``, matching the tool-list bullet format).
        """
        sp_off = self._system_prompt(self._loop(False, fake_config))
        sp_on = self._system_prompt(self._loop(True, fake_config))
        # disabled must have strictly fewer "- shell" bullets than enabled
        # (the delta is exactly the tool_list entry)
        assert sp_off.count("- shell") < sp_on.count("- shell")

    def test_enabled_shows_run_command_in_tool_list(self, fake_config):
        """The LLM's system prompt must list the shell tool when on."""
        sp_off = self._system_prompt(self._loop(False, fake_config))
        sp_on = self._system_prompt(self._loop(True, fake_config))
        assert sp_on.count("- shell") > sp_off.count("- shell")

    def test_explicit_registry_is_gated_too(self, fake_config):
        """allow_shell_tools=False pops run_command from caller-provided
        registries as well — no path leaks the shell tool when disabled."""
        from strategy_research.kernel.agent.chat_loop import build_chat_agent_loop

        custom_registry = _FakeRegistry(["shell", "read"])
        loop = build_chat_agent_loop(
            config=fake_config, session_id="s1",
            registry=custom_registry, allow_shell_tools=False,
        )
        assert "shell" not in loop.registry._tools

    def test_explicit_registry_keeps_tool_when_enabled(self, fake_config):
        from strategy_research.kernel.agent.chat_loop import build_chat_agent_loop

        custom_registry = _FakeRegistry(["shell", "read"])
        loop = build_chat_agent_loop(
            config=fake_config, session_id="s1",
            registry=custom_registry, allow_shell_tools=True,
        )
        assert "shell" in loop.registry._tools
