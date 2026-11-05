"""Tests for the Common Layer (truthfulness-design).

Validates:
- ``_common/principles.md`` exists with required sections
- ``_common/rules/`` directory structure and required files
- principles injected into all role prompts; rules NOT auto-injected
- chat.md inlines high-frequency rules but not low-frequency ones
- JSON roles have inline JSON conventions
- opt-out mechanism works

See docs/design/truthfulness-common-rules-design.md for the design spec.
"""
from __future__ import annotations

from pathlib import Path

from agentnodes.kernel.agent.prompt_builder import (
    PromptBuilderFactory,
    StaticFilePromptBuilder,
)

_PROMPTS_DIR = Path(__file__).parent.parent / "src" / "agentnodes" / "templates" / ".prompts"
_COMMON_DIR = _PROMPTS_DIR / "_common"
_RULES_DIR = _COMMON_DIR / "rules"

JSON_ROLES = [
    "orchestrator",
]

# 2026-11-05 批次 7：研究域角色（researcher / strategist / backtest_diagnostics /
# critic）随 templates 去研究化归档到 testcase/golden/templates/prompts/。
# 只剩 orchestrator 是通用 JSON role。


# ── principles.md ──────────────────────────────────────────────────────


class TestPrinciplesFile:
    def test_principles_file_exists_with_all_sections(self):
        text = (_COMMON_DIR / "principles.md").read_text(encoding="utf-8")
        assert "共识原则" in text
        assert "## 诚实" in text
        assert "## 数据真实性" in text
        assert "## 探究" in text
        assert "## 简洁" in text
        assert "## 受众区分" in text
        assert "## ⚠️ 红线" in text
        assert "## 思考模式" in text
        assert "引导" in text

    def test_red_lines_present(self):
        text = (_COMMON_DIR / "principles.md").read_text(encoding="utf-8")
        assert "没调用工具，就没有数据" in text
        assert "编造" in text
        assert "已做 / 将做 / 未做" in text

    def test_thinking_mode_has_examples(self):
        text = (_COMMON_DIR / "principles.md").read_text(encoding="utf-8")
        assert "例 1" in text
        assert "例 2" in text

    def test_principles_guides_to_rules_index(self):
        text = (_COMMON_DIR / "principles.md").read_text(encoding="utf-8")
        assert "_common/rules/INDEX.md" in text


# ── rules/ directory ───────────────────────────────────────────────────


class TestRulesDirectory:
    def test_rules_dir_exists_with_expected_files(self):
        assert _RULES_DIR.is_dir()
        # 批次 7：backtest.md（业务规则）已归档，只剩通用规则
        for f in ["INDEX.md", "tools.md", "json-output.md", "iteration.md",
                  "long-task.md"]:
            assert (_RULES_DIR / f).exists(), f"{f} missing"

    def test_index_has_trigger_table(self):
        text = (_RULES_DIR / "INDEX.md").read_text(encoding="utf-8")
        assert "触发情况" in text or "何时读哪个 rule" in text
        assert "tools.md" in text
        assert "json-output.md" in text
        assert "iteration.md" in text

    def test_each_rule_has_examples(self):
        """Anthropic 实验证明：带示例的规则比裸规则提升 76%。"""
        for rule_file in ["tools.md", "json-output.md", "iteration.md"]:
            text = (_RULES_DIR / rule_file).read_text(encoding="utf-8")
            assert "例" in text, f"{rule_file} 缺示例"
        # long-task.md 是操作型规则（判断条件 + 命令），不含正反例章节；
        # 它靠可执行的判据而不是例子，故单独断言其核心原则存在。
        lt = (_RULES_DIR / "long-task.md").read_text(encoding="utf-8")
        assert "日志在推进" in lt

    def test_no_skills_directory_conflict(self):
        """未来 skills/ 目录不与 rules/ 冲突（保留命名空间）。"""
        assert not (_COMMON_DIR / "skills").exists()


# ── injection behavior ─────────────────────────────────────────────────


class TestCommonInjection:
    def test_principles_injected_into_all_roles(self):
        for role in PromptBuilderFactory.list_roles():
            sp = PromptBuilderFactory.get(role).build_system_prompt(role, {})
            assert "共识原则" in sp, f"{role} 缺 principles"
            assert "⚠️ 红线" in sp, f"{role} 缺红线"
            assert "思考模式" in sp, f"{role} 缺思考模式"

    def test_rules_not_auto_injected(self):
        """rules/ 不进 system prompt（按需读取）。"""
        for role in ["chat", "orchestrator"]:
            sp = PromptBuilderFactory.get(role).build_system_prompt(role, {})
            assert "Rule Index" not in sp, f"{role} 不应自动注入 rules"
            assert "何时读哪个 rule" not in sp

    def test_principles_before_role_content(self):
        """principles 在角色内容之前（primacy 位置）。"""
        sp = PromptBuilderFactory.get("orchestrator").build_system_prompt("orchestrator", {})
        p_idx = sp.find("共识原则")
        r_idx = sp.find("# Role:")
        assert p_idx >= 0 and r_idx >= 0
        assert p_idx < r_idx

    def test_all_role_md_files_reference_common(self):
        """所有 role .md 文件都引用 common 层。"""
        role_files = [f for f in _PROMPTS_DIR.glob("*.md") if not f.stem.startswith("_")]
        for f in role_files:
            text = f.read_text(encoding="utf-8")
            assert (
                "_common/principles.md" in text or "_common/rules/" in text
            ), f"{f.name} 未引用 common 层"

    def test_opt_out_role_skips_common(self):
        """注册一个 opt-out role → 跳过 principles。"""
        PromptBuilderFactory.register("test_opt_out", StaticFilePromptBuilder("test_opt_out"))
        PromptBuilderFactory._COMMON_OPT_OUT.add("test_opt_out")
        try:
            sp = PromptBuilderFactory.get("test_opt_out").build_system_prompt(
                "test_opt_out", {}
            )
            assert "共识原则" not in sp
        finally:
            PromptBuilderFactory._COMMON_OPT_OUT.discard("test_opt_out")


# ── chat.md ────────────────────────────────────────────────────────────


class TestChatInlining:
    def test_chat_inlines_high_frequency_rules(self):
        chat_text = (_PROMPTS_DIR / "chat.md").read_text(encoding="utf-8")
        assert "诚实模板" in chat_text
        assert "强制回测流程" in chat_text
        assert "可验证性约定" in chat_text
        assert "工具使用" in chat_text

    def test_chat_does_not_inline_low_frequency_rules(self):
        """chat.md 不应内联低频规则的完整内容（由 rules/ 按需提供）。

        注意：chat.md 顶部指针会"提及"低频规则名（作为引导），但不应
        内联它们的完整章节。测试检查的是完整章节标题是否存在。
        """
        chat_text = (_PROMPTS_DIR / "chat.md").read_text(encoding="utf-8")
        # 完整章节标题不应存在（这些是 rules/ 的文件，不是 chat.md 的章节）
        assert "## 小步迭代原则" not in chat_text
        assert "## 执行前自检" not in chat_text


# ── JSON roles ─────────────────────────────────────────────────────────


class TestJsonRoleInlining:
    def test_json_roles_have_inline_json_convention(self):
        for role in JSON_ROLES:
            role_file = _PROMPTS_DIR / f"{role}.md"
            assert role_file.exists(), f"{role}.md missing"
            text = role_file.read_text(encoding="utf-8")
            assert "必须返回纯 JSON" in text, f"{role} 缺 JSON 约定"

    def test_json_roles_point_to_json_output_rule(self):
        for role in JSON_ROLES:
            text = (_PROMPTS_DIR / f"{role}.md").read_text(encoding="utf-8")
            assert "json-output.md" in text, f"{role} 未引用 json-output.md"
