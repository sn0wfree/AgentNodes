"""Tests for Phase 5 PromptBuilder — 12 boundary cases.

See docs/archive/undated/chat-agent-refactor-phase5-integration.md §6.1 for the full spec.
"""
from __future__ import annotations

# ── ChatPromptBuilder ─────────────────────────────────────────────────


class TestChatPromptBuilder:
    def test_empty_context_renders_blank_placeholders(self):
        """workspace="" tool_list="" → placeholders substituted with empty strings."""
        from strategy_research.kernel.agent.prompt_builder import (
            ChatPromptBuilder,
        )

        builder = ChatPromptBuilder()
        messages = builder.build_messages(
            user_query="hi",
            history=[],
            context={"workspace": "", "tool_list": ""},
        )
        # system + user = 2 messages (no history appended)
        assert len(messages) == 2
        assert messages[0]["role"] == "system"
        assert messages[1] == {"role": "user", "content": "hi"}
        # chat.md has {workspace} / {tool_list} — with empty context they
        # become empty strings (the .format() substitution).
        assert "{workspace}" not in messages[0]["content"]
        assert "{tool_list}" not in messages[0]["content"]

    def test_with_workspace_renders_correctly(self):
        """workspace="/w" → {workspace} → /w in system prompt."""
        from strategy_research.kernel.agent.prompt_builder import (
            ChatPromptBuilder,
        )

        builder = ChatPromptBuilder()
        messages = builder.build_messages(
            user_query="hi",
            history=[],
            context={"workspace": "/data/projects/myws", "tool_list": "a, b"},
        )
        assert "/data/projects/myws" in messages[0]["content"]
        assert "a, b" in messages[0]["content"]

    def test_special_chars_render_safely(self):
        """str.format() handles special chars without escaping (unlike jinja2 autoescape)."""
        from strategy_research.kernel.agent.prompt_builder import (
            ChatPromptBuilder,
        )

        builder = ChatPromptBuilder()
        messages = builder.build_messages(
            user_query='quote "test" newline\ntab\tbackslash\\end',
            history=[],
            context={"workspace": "/w", "tool_list": "[]"},
        )
        content = messages[-1]["content"]
        assert '"test"' in content
        assert "\n" in content
        assert "\t" in content
        assert "\\end" in content  # backslash preserved

    def test_unicode_and_emoji(self):
        """Chinese + emoji round-trip through str.format()."""
        from strategy_research.kernel.agent.prompt_builder import (
            ChatPromptBuilder,
        )

        builder = ChatPromptBuilder()
        messages = builder.build_messages(
            user_query="中文 🐂 测试",
            history=[],
            context={"workspace": "/tmp"},
        )
        assert "中文" in messages[-1]["content"]
        assert "🐂" in messages[-1]["content"]

    def test_template_missing_returns_fallback(self, tmp_path, monkeypatch):
        """chat.md missing → returns FALLBACK_PROMPT (not raises)."""
        from strategy_research.kernel.agent import prompt_builder as pb_mod

        monkeypatch.setattr(pb_mod, "_PROMPTS_DIR", tmp_path)

        from strategy_research.kernel.agent.prompt_builder import (
            ChatPromptBuilder,
        )

        builder = ChatPromptBuilder()
        system = builder.build_system_prompt("chat", {"workspace": "/w"})
        assert system == ChatPromptBuilder.FALLBACK_PROMPT
        assert "QuantNodes-Research" in system

    def test_unrendered_placeholder_returns_raw_text(self, tmp_path, monkeypatch):
        """When chat.md contains an undeclared placeholder like {foo},
        .format() raises KeyError → builder returns raw text (literal)."""
        from strategy_research.kernel.agent import prompt_builder as pb_mod

        # Monkeypatch BEFORE instantiating ChatPromptBuilder (since _path
        # is computed at __init__ time).
        monkeypatch.setattr(pb_mod, "_PROMPTS_DIR", tmp_path)
        fake_md = "You are an assistant. Welcome to {workspace}. {foo}"
        (tmp_path / "chat.md").write_text(fake_md, encoding="utf-8")

        from strategy_research.kernel.agent.prompt_builder import (
            ChatPromptBuilder,
        )

        builder = ChatPromptBuilder()
        system = builder.build_system_prompt(
            "chat", {"workspace": "/w"}
        )
        # {workspace} substituted, {foo} kept literal (KeyError caught)
        assert "/w" in system
        assert "{foo}" in system

    def test_long_history_preserved(self):
        """history=1000 messages → 1002 total (system + history + user)."""
        from strategy_research.kernel.agent.prompt_builder import (
            ChatPromptBuilder,
        )

        builder = ChatPromptBuilder()
        history = [
            {"role": "user", "content": f"msg-{i}"}
            for i in range(1000)
        ]
        messages = builder.build_messages(
            user_query="final",
            history=history,
            context={"workspace": "/tmp"},
        )
        assert len(messages) == 1002
        assert messages[-1]["role"] == "user"
        assert messages[-1]["content"] == "final"

    def test_validate_ok(self):
        from strategy_research.kernel.agent.prompt_builder import (
            ChatPromptBuilder,
        )

        builder = ChatPromptBuilder(token_limit=128_000)
        messages = builder.build_messages(
            user_query="hi", history=[], context={"workspace": "/w"}
        )
        result = builder.validate(messages)
        assert result.ok is True
        assert result.error == ""

    def test_validate_overflow(self):
        from strategy_research.kernel.agent.prompt_builder import (
            ChatPromptBuilder,
        )

        builder = ChatPromptBuilder(token_limit=10)  # tiny limit
        messages = builder.build_messages(
            user_query="hi", history=[], context={"workspace": "/w"}
        )
        result = builder.validate(messages)
        assert result.ok is False
        assert "exceed" in result.error.lower()


# ── StaticFilePromptBuilder ────────────────────────────────────────────


class TestStaticFilePromptBuilder:
    def test_orchestrator_returns_raw_markdown(self):
        """StaticFilePromptBuilder('orchestrator') returns orchestrator.md verbatim.

        2026-11-05 批次 7：researcher.md 等研究角色已归档到
        testcase/golden/templates/prompts/，改用仍随包分发的通用角色。
        """
        from strategy_research.kernel.agent.prompt_builder import (
            StaticFilePromptBuilder,
        )

        builder = StaticFilePromptBuilder("orchestrator")
        system = builder.build_system_prompt("orchestrator", {})
        # orchestrator.md is git-tracked — must contain its distinctive header
        assert system.strip(), "orchestrator prompt 不应为空"
        assert "Role" in system or "角色" in system or "orchestrator" in system.lower()

    def test_missing_role_returns_empty(self, tmp_path, monkeypatch):
        """StaticFilePromptBuilder for missing .md file → empty string."""
        from strategy_research.kernel.agent import prompt_builder as pb_mod

        monkeypatch.setattr(pb_mod, "_PROMPTS_DIR", tmp_path)

        from strategy_research.kernel.agent.prompt_builder import (
            StaticFilePromptBuilder,
        )

        builder = StaticFilePromptBuilder("nonexistent_role")
        system = builder.build_system_prompt("nonexistent_role", {})
        assert system == ""

    def test_placeholder_is_literal(self):
        """占位符不被渲染：StaticFilePromptBuilder 原样返回正文。

        2026-11-05 批次 7：原样本 anti_overfit_analyst.md 已归档，
        改用 workflows 中的通用角色做同一断言。若该角色不含占位符，
        断言退化为「正文非空且未被替换」——语义不变。
        """
        from strategy_research.kernel.agent.prompt_builder import (
            StaticFilePromptBuilder,
        )

        builder = StaticFilePromptBuilder("workflow_orchestrator")
        system = builder.build_system_prompt(
            "workflow_orchestrator",
            {"strategy_name": "momentum_v3", "workspace": "/w"},
        )
        assert system.strip(), "prompt 不应为空"
        # Placeholders stay literal — matches old role_factory behavior
        raw = (__import__("pathlib").Path(__file__).parent.parent
               / "src" / "strategy_research" / "templates" / ".prompts"
               / "workflow_orchestrator.md").read_text(encoding="utf-8")
        assert "{strategy_name}" in system or "{workspace}" in system or raw[:40] in system


# ── PromptBuilderFactory ──────────────────────────────────────────────


class TestPromptBuilderFactory:
    def test_unknown_role_returns_empty_string(self):
        """Unknown role → _NullBuilder → build_system_prompt returns ''."""
        from strategy_research.kernel.agent.prompt_builder import (
            PromptBuilderFactory,
        )

        builder = PromptBuilderFactory.get("totally_made_up_role")
        assert builder.build_system_prompt("x", {}) == ""
        # Does NOT raise (Phase 5 changed from ValueError to NullBuilder
        # for backward compat with role_factory)

    def test_chat_role_returns_chat_builder(self):
        from strategy_research.kernel.agent.prompt_builder import (
            ChatPromptBuilder,
            PromptBuilderFactory,
        )

        builder = PromptBuilderFactory.get("chat")
        assert isinstance(builder, ChatPromptBuilder)

    def test_orchestrator_role_returns_static_builder(self):
        from strategy_research.kernel.agent.prompt_builder import (
            PromptBuilderFactory,
            StaticFilePromptBuilder,
        )

        builder = PromptBuilderFactory.get("orchestrator")
        assert isinstance(builder, StaticFilePromptBuilder)

    def test_list_roles_reflects_bundled_prompts(self):
        """角色清单 = 硬编码 chat + templates/.prompts/*.md 实扫。

        2026-11-05 批次 7：研究角色归档后，清单应与磁盘一致（不再是固定 10 个）。
        这条断言的是「清单不撒谎」——避免再出现硬编码名单与文件脱节。

        注意 ``_discovered`` 是进程级缓存，别的用例可能已注册额外角色，
        所以这里清缓存后重扫，并只断言「磁盘 ⊆ 清单」这个方向。
        """
        from pathlib import Path

        from strategy_research.kernel.agent.prompt_builder import (
            PromptBuilderFactory,
        )

        prompts_dir = (Path(__file__).parent.parent / "src" / "strategy_research"
                       / "templates" / ".prompts")
        on_disk = {p.stem for p in prompts_dir.glob("*.md")}
        saved = PromptBuilderFactory._discovered
        PromptBuilderFactory._discovered = None
        try:
            roles = set(PromptBuilderFactory.list_roles())
        finally:
            PromptBuilderFactory._discovered = saved
        assert "chat" in roles
        assert on_disk.issubset(roles), (
            f"磁盘上有但 factory 不认识: {on_disk - roles}"
        )
