"""Tier 1 四类组件的真实消费通道（prompt / skill / tool_set / compaction）。

与 ``test_harness_wiring.py`` 的分工：那边测的是**单个解析函数**的返回值；
本文件测的是**四类组件在真实装配路径上是否真的改变了输出**——
"解析出配置" 与 "配置真的生效" 是两件事，本轮的真实缺陷正是后者缺失
（``skill`` 无任何消费通道、``compaction`` 字段零写入）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import agentnodes.apps  # noqa: F401,E402 —— 触发能力注册
from agentnodes.foundation.compact_config import CompactConfig
from agentnodes.kernel.agent.harness_resolver import (  # noqa: E402
    apply_compaction_overrides,
    apply_skill_set,
    apply_tool_set,
    assemble,
    resolve_harness,
)
from agentnodes.kernel.agent.prompt_builder import (  # noqa: E402
    PromptBuilderFactory,
)
from agentnodes.kernel.orchestration.genome_runtime import (  # noqa: E402
    reset_active_genome,
    set_active_genome,
)
from agentnodes.kernel.orchestration.genome_version import (  # noqa: E402
    GenomeVersionStore,
)

SIG = "tier1-test-sig"
ROLE = "researcher"

TOOLS = ["read", "write", "run_backtest", "websearch"]


@pytest.fixture
def genome():
    """装一个干净的基因组并登记为活跃（三个装配点据此解析）。"""
    g = GenomeVersionStore()
    set_active_genome(g)
    yield g
    reset_active_genome()


@pytest.fixture(autouse=True)
def _clear_builder_state():
    """builder 是进程级缓存实例，测试之间必须清掉残留覆盖。"""
    yield
    for role in (ROLE, "chat"):
        b = PromptBuilderFactory.get(role)
        if hasattr(b, "apply_harness_override"):
            b.apply_harness_override(None)
        if hasattr(b, "apply_skill_block"):
            b.apply_skill_block(None)


# ── 零基线：未启用 RSI 时四类全部原样 ───────────────────────────────


class TestBaselineUnchanged:
    def test_empty_signature_returns_inputs_unchanged(self):
        assert resolve_harness("").is_empty()
        assert apply_tool_set(TOOLS, "") == TOOLS
        assert apply_skill_set("base prompt", "") == "base prompt"
        cc = CompactConfig()
        assert apply_compaction_overrides(cc, "") is cc

    def test_assemble_empty_signature_is_noop(self):
        cc = CompactConfig()
        h = assemble(role=ROLE, compact_config=cc, task_signature="")
        assert h.prompt_override == {}
        assert h.tool_set is None
        assert h.skill_set is None
        assert h.compact_config is cc


# ── ① prompt：正文替换 ──────────────────────────────────────────────


class TestPromptOverrideApplies:
    def test_prompt_override_changes_rendered_prompt(self, genome):
        base = PromptBuilderFactory.get_for_signature(ROLE, "").build_system_prompt(
            ROLE, {}
        )
        genome.activate("p1", {"prompt": {ROLE: {"text": "【替换后的提示词】"}}})
        after = PromptBuilderFactory.get_for_signature(
            ROLE, SIG
        ).build_system_prompt(ROLE, {})

        assert "【替换后的提示词】" in after
        assert after != base

    def test_override_cleared_when_signature_goes_empty(self, genome):
        base = PromptBuilderFactory.get_for_signature(ROLE, "").build_system_prompt(
            ROLE, {}
        )
        genome.activate("p1", {"prompt": {ROLE: {"text": "【临时】"}}})
        PromptBuilderFactory.get_for_signature(ROLE, SIG).build_system_prompt(ROLE, {})

        # 切回空 signature：必须清掉覆盖，否则是跨 signature 的状态泄漏
        back = PromptBuilderFactory.get_for_signature(
            ROLE, ""
        ).build_system_prompt(ROLE, {})
        assert back == base
        assert "【临时】" not in back


# ── ② tool_set：工具裁剪 ────────────────────────────────────────────


class TestToolSetApplies:
    def test_tool_set_filters(self, genome):
        genome.activate("p1", {"tool_set": ["read", "websearch"]})
        assert apply_tool_set(TOOLS, SIG) == ["read", "websearch"]

    def test_tool_set_none_means_unrestricted(self, genome):
        genome.activate("p1", {"tool_set": ["read"]})
        genome.activate("p2", {"tool_set": None})
        assert apply_tool_set(TOOLS, SIG) == TOOLS

    def test_tool_set_only_subtracts(self, genome):
        """只做减法：RSI 不能启用未注册的工具。"""
        genome.activate("p1", {"tool_set": ["read", "NOT_REGISTERED"]})
        out = apply_tool_set(TOOLS, SIG)
        assert out == ["read"]
        assert "NOT_REGISTERED" not in out


# ── ③ skill：方法论注入（本轮新建的通道） ──────────────────────────


class TestSkillInjection:
    def test_skill_block_appended(self, genome):
        genome.activate("p1", {"skill": ["factor-research"]})
        out = apply_skill_set("基础提示词", SIG)
        assert out is not None
        assert out.startswith("基础提示词")
        assert "方法论：factor-research" in out
        assert len(out) > len("基础提示词") + 500, "应注入真实技能正文"

    def test_skill_works_without_base_prompt(self, genome):
        genome.activate("p1", {"skill": ["risk-analysis"]})
        out = apply_skill_set(None, SIG)
        assert out is not None
        assert out.startswith("## 方法论")

    def test_unknown_skill_skipped(self, genome):
        genome.activate("p1", {"skill": ["definitely-not-a-skill"]})
        assert apply_skill_set("base", SIG) == "base"

    def test_multiple_skills(self, genome):
        genome.activate("p1", {"skill": ["factor-research", "risk-analysis"]})
        out = apply_skill_set("base", SIG)
        assert out.count("## 方法论") == 2

    def test_skill_none_means_no_injection(self, genome):
        genome.activate("p1", {"skill": None})
        assert apply_skill_set("base", SIG) == "base"

    def test_skill_reaches_real_prompt_builder(self, genome):
        """关键：skill 必须真的进入 build_system_prompt 的输出。"""
        base = PromptBuilderFactory.get_for_signature(ROLE, "").build_system_prompt(
            ROLE, {}
        )
        genome.activate("p1", {"skill": ["factor-research"]})
        after = PromptBuilderFactory.get_for_signature(
            ROLE, SIG
        ).build_system_prompt(ROLE, {})

        assert "方法论：factor-research" in after
        assert len(after) > len(base), "prompt 必须变长"

    def test_skill_cleared_on_empty_signature(self, genome):
        base = PromptBuilderFactory.get_for_signature(ROLE, "").build_system_prompt(
            ROLE, {}
        )
        genome.activate("p1", {"skill": ["factor-research"]})
        PromptBuilderFactory.get_for_signature(ROLE, SIG).build_system_prompt(ROLE, {})

        back = PromptBuilderFactory.get_for_signature(
            ROLE, ""
        ).build_system_prompt(ROLE, {})
        assert "方法论：factor-research" not in back
        assert back == base, "切回空 signature 必须与基线逐字一致"


# ── ④ compaction：参数覆盖 ──────────────────────────────────────────


class TestCompactionApplies:
    def test_compaction_overrides_applied(self, genome):
        genome.activate("p1", {"compaction": {"collapse_keep_recent": 7}})
        cc = CompactConfig()
        out = apply_compaction_overrides(cc, SIG)
        assert out is not cc, "有覆盖时必须返回新对象"
        assert out.collapse_keep_recent == 7

    def test_compaction_unknown_field_ignored(self, genome):
        """字段名写错时静默丢弃（不崩，但也不生效）——由 policy 侧保证正确。"""
        genome.activate("p1", {"compaction": {"no_such_field": 1}})
        cc = CompactConfig()
        assert apply_compaction_overrides(cc, SIG) is cc

    def test_compaction_empty_returns_same_object(self, genome):
        genome.activate("p1", {"compaction": {}})
        cc = CompactConfig()
        assert apply_compaction_overrides(cc, SIG) is cc


# ── assemble()：四合一 ──────────────────────────────────────────────


class TestAssembleAllFour:
    def test_all_four_resolved(self, genome):
        genome.activate("p1", {
            "prompt": {ROLE: {"text": "x"}},
            "tool_set": ["read"],
            "skill": ["factor-research"],
            "compaction": {"collapse_keep_recent": 5},
        })
        h = assemble(role=ROLE, compact_config=CompactConfig(), task_signature=SIG)

        assert h.prompt_override == {"text": "x"}
        assert h.tool_set == frozenset({"read"})
        assert h.skill_set == frozenset({"factor-research"})
        assert h.compact_config is not None
        assert h.compact_config.collapse_keep_recent == 5

    def test_partial_proposal_only_affects_its_component(self, genome):
        """只提 skill 时，其余三类保持原样。"""
        genome.activate("p1", {"skill": ["risk-analysis"]})
        cc = CompactConfig()
        h = assemble(role=ROLE, compact_config=cc, task_signature=SIG)

        assert h.skill_set == frozenset({"risk-analysis"})
        assert h.prompt_override == {}
        assert h.tool_set is None
        assert h.compact_config is cc


# ── 装配失败降级 ────────────────────────────────────────────────────


class TestGracefulDegradation:
    def test_no_genome_registered_degrades(self):
        reset_active_genome()
        assert resolve_harness(SIG).is_empty()
        assert apply_tool_set(TOOLS, SIG) == TOOLS
        assert apply_skill_set("base", SIG) == "base"

    def test_broken_genome_does_not_raise(self, monkeypatch):
        class Boom:
            def tier1_active(self):
                raise RuntimeError("genome exploded")

        set_active_genome(Boom())
        try:
            assert apply_tool_set(TOOLS, SIG) == TOOLS
            assert apply_skill_set("base", SIG) == "base"
        finally:
            reset_active_genome()
