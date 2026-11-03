"""Integration tests for Phase 5 — verify 4 call sites were unified onto
``PromptBuilderFactory``.

See docs/chat-agent-refactor-phase5-integration.md §6.2 for the full spec.
"""
from __future__ import annotations

import importlib


def _reload_module(name: str):
    """Force-reload a module to pick up monkeypatched attributes."""
    importlib.invalidate_caches()
    if name in importlib.sys.modules:
        return importlib.reload(importlib.sys.modules[name])
    return importlib.import_module(name)


class TestPhase5RemovedLegacyPaths:
    """Phase 5 should have removed the inline system_prompt loaders."""

    def test_chat_py_no_more_get_system_prompt(self):
        """``from .chat import _get_system_prompt`` must raise ImportError."""
        with __import__("pytest").raises(ImportError):
            from strategy_research.interfaces.api.routers.chat import _get_system_prompt  # noqa: F401

    def test_no_chat_prompt_path_constant(self):
        """``from cli.tui import _CHAT_PROMPT_PATH`` must raise ImportError."""
        with __import__("pytest").raises(ImportError):
            from strategy_research.cli.tui import _CHAT_PROMPT_PATH  # noqa: F401

    def test_no_load_role_system_prompt_function(self):
        """``from role_factory import _load_role_system_prompt`` must raise."""
        with __import__("pytest").raises(ImportError):
            from strategy_research.kernel.agent.role_factory import (  # noqa: F401
                _load_role_system_prompt,
            )


class TestPhase5CallSitesUseFactory:
    """All 4 call sites should now route through the PromptBuilder layer.

    Phase 5: each call site referenced ``PromptBuilderFactory`` directly.
    Phase 6: the 3 chat-mode call sites were further unified via
    ``build_chat_agent_loop`` (which itself calls ``PromptBuilderFactory``).
    We accept either pattern as "routed through PromptBuilder".
    """

    def _assert_routed(self, src: str, file: str) -> None:
        assert (
            "PromptBuilderFactory" in src or "build_chat_agent_loop" in src
        ), f"{file} should reference PromptBuilderFactory or build_chat_agent_loop"

    def test_chat_py_routes_through_factory(self):
        from strategy_research.interfaces.api.routers import chat

        src = open(chat.__file__, encoding="utf-8").read()
        self._assert_routed(src, "chat.py")
        assert "_get_system_prompt" not in src, (
            "chat.py should no longer reference _get_system_prompt"
        )

    def test_service_py_routes_through_factory(self):
        from strategy_research.interfaces.api.session import service

        src = open(service.__file__, encoding="utf-8").read()
        self._assert_routed(src, "service.py")
        assert "from ..routers.chat import _get_system_prompt" not in src

    def test_tui_session_routes_through_factory(self):
        """P5-1c ④ 后 TUI 不再直接构造 AgentLoop。

        旧实现里 ``session.py`` 通过 ``build_chat_agent_loop`` 走统一 prompt
        工厂；现在它只接 Gateway（``_run_via_gateway``），prompt 装配发生在
        引擎侧（``runtimes/nodes/react.py`` → AgentLoop → 同一 factory）。
        所以这里断言的是**新链路的完整性**，而不是旧的字符串存在。
        """
        from strategy_research.cli.tui import session as tui_session

        src = open(tui_session.__file__, encoding="utf-8").read()
        assert "_run_via_gateway" in src, "TUI 必须经 Gateway 入口"
        # 旧直连路径必须彻底消失
        assert "build_chat_agent_loop" not in src
        assert "_run_agent_loop" not in src.replace("_run_agent_loop_background", "")
        assert "_CHAT_PROMPT_PATH" not in src
        assert "_load_role_system_prompt" not in src

    def test_role_factory_routes_through_factory(self):
        from strategy_research.kernel.agent import role_factory

        src = open(role_factory.__file__, encoding="utf-8").read()
        # role_factory routes prompt rendering through the shared
        # assembly core (loop_assembly.assemble_loop_parts), which calls
        # PromptBuilderFactory internally (phase 3 dual-factory
        # consolidation). The old "directly calls PromptBuilderFactory"
        # lock is superseded.
        assert "assemble_loop_parts" in src
        assert "def _load_role_system_prompt" not in src
        assert "def _prompts_dir" not in src


class TestPhase5BehaviorUnchanged:
    """Functional smoke tests — call sites still produce the same output."""

    def test_chat_prompt_matches_legacy_chat_md(self):
        """PromptBuilderFactory.get('chat') returns chat.md content (with empty context)."""
        from strategy_research.kernel.agent.prompt_builder import (
            PromptBuilderFactory,
        )

        prompt = PromptBuilderFactory.get("chat").build_system_prompt(
            "chat", {"workspace": "", "tool_list": ""}
        )
        # Should start with the chat.md header
        assert "# Role: QuantNodes-Research Chat Assistant" in prompt
        # Should contain key sections (verbatim from chat.md)
        assert "## 工作区" in prompt or "## Workspace" in prompt
        # Placeholders substituted to empty strings (default)
        assert "{workspace}" not in prompt

    def test_role_prompts_load_from_prompts_dir(self):
        """All 9 roles load their .md from templates/.prompts/."""
        from strategy_research.kernel.agent.prompt_builder import (
            PromptBuilderFactory,
        )

        roles = [
            "researcher", "data_quality", "factor_analyst", "strategist",
            "portfolio_construction", "risk_controller", "attribution_analyst",
            "anti_overfit_analyst", "backtest_diagnostics", "critic",
        ]
        for role in roles:
            prompt = PromptBuilderFactory.get(role).build_system_prompt(role, {})
            # Common layer prepends principles.md; role header follows.
            assert "# Role:" in prompt, (
                f"{role} prompt missing '# Role:' header, got: "
                f"{prompt[:60]!r}"
            )


class TestPhase5FactoryConsistency:
    """Factory state invariants."""

    def test_factory_singleton_per_role(self):
        """Same role returns the same builder instance (no churn)."""
        from strategy_research.kernel.agent.prompt_builder import (
            PromptBuilderFactory,
        )

        a = PromptBuilderFactory.get("chat")
        b = PromptBuilderFactory.get("chat")
        assert a is b

    def test_register_persists_across_get(self):
        """Custom registered builder survives subsequent ``get`` calls."""
        from strategy_research.kernel.agent.prompt_builder import (
            PromptBuilderFactory,
            _NullBuilder,
        )

        class _CustomBuilder(_NullBuilder):
            def build_system_prompt(self, role, context):
                return "CUSTOM"

        PromptBuilderFactory.register("custom_role_test", _CustomBuilder())
        try:
            assert (
                PromptBuilderFactory.get("custom_role_test").build_system_prompt(
                    "x", {}
                )
                == "CUSTOM"
            )
        finally:
            PromptBuilderFactory._BUILDERS.pop("custom_role_test", None)
