"""阶段三验收 ④：三个装配点真的消费 SkillMemoryResolver 的结果。

补 goal §3 阶段三的完成标准：
    「PromptBuilderFactory / ToolRegistry / CompactConfig 装配期调用
      SkillMemoryResolver.resolve(task_signature)，无 Tier 0 泄漏」

此前只断言了「查询接口存在」，没有断言「结果被消费」。本文件用
可观测的行为变化证明三处都真的接线了。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from strategy_research.foundation.compact_config import CompactConfig
from strategy_research.kernel.agent import harness_resolver
from strategy_research.kernel.agent.harness_resolver import (
    AssembledHarness,
    apply_compaction_overrides,
    assemble,
)
from strategy_research.kernel.agent.prompt_builder import (
    PromptBuilderFactory,
    StaticFilePromptBuilder,
)
from strategy_research.kernel.agent.tools import BaseTool, ToolContext, ToolRegistry


# ── 用注入的 harness 配置替换真实 RSI 来源 ───────────────────────────


class _FakeHarness:
    def __init__(self, prompt=None, tool_set=None, compaction=None):
        self.prompt_version = prompt or {}
        self.tool_set = tool_set
        self.compaction_config = compaction or {}

    def is_empty(self):
        return not (self.prompt_version or self.tool_set or self.compaction_config)


@pytest.fixture()
def harness(monkeypatch):
    """把 resolve_harness 换成可控替身（不依赖 apps/RSI 是否启用）。"""
    state = {"cfg": _FakeHarness()}

    def fake_resolve(task_signature):
        return state["cfg"]

    monkeypatch.setattr(harness_resolver, "resolve_harness", fake_resolve)
    return state


# ── ① PromptBuilderFactory 消费 ──────────────────────────────────────


class _EchoTool(BaseTool):
    name = "echo"
    description = "echo back"
    is_readonly = True

    def execute(self, ctx: ToolContext, **kwargs):
        return "ok"


class TestPromptBuilderConsumes:
    def test_override_replaces_role_prompt_text(self, harness):
        """覆盖项里的 text 真的替换了 role prompt 正文。"""
        harness["cfg"] = _FakeHarness(
            prompt={"researcher": {"text": "OVERRIDDEN PROMPT BODY"}}
        )
        builder = PromptBuilderFactory.get_for_signature("researcher", "sig-x")
        assert isinstance(builder, StaticFilePromptBuilder)
        out = builder.build_system_prompt("researcher", {})
        assert "OVERRIDDEN PROMPT BODY" in out
        # 原文件正文不应再出现（替换而非追加）
        assert "researcher.md" not in out

    def test_no_signature_keeps_original_behaviour(self, harness):
        """task_signature 为空 → 与 get() 完全等价（不装入任何覆盖）。"""
        harness["cfg"] = _FakeHarness(
            prompt={"researcher": {"text": "ZZ_OVERRIDE_MARKER_ZZ"}}
        )
        plain = PromptBuilderFactory.get("researcher")
        assert getattr(plain, "_harness_override", None) is None
        out = plain.build_system_prompt("researcher", {})
        assert "ZZ_OVERRIDE_MARKER_ZZ" not in out

    def test_override_cleared_between_signatures(self, harness):
        """同一 builder 实例被复用：切换到无覆盖 signature 时必须清残留。"""
        harness["cfg"] = _FakeHarness(
            prompt={"researcher": {"text": "ZZ_OVERRIDE_MARKER_ZZ"}}
        )
        with_override = PromptBuilderFactory.get_for_signature("researcher", "sig-1")
        assert "ZZ_OVERRIDE_MARKER_ZZ" in with_override.build_system_prompt(
            "researcher", {}
        )
        # 同一实例，换成空 signature
        harness["cfg"] = _FakeHarness()
        plain = PromptBuilderFactory.get_for_signature("researcher", "sig-2")
        assert plain.build_system_prompt("researcher", {}) == (
            PromptBuilderFactory.get("researcher").build_system_prompt("researcher", {})
        ), "跨 signature 的状态泄漏：前一次的覆盖未清除"

    def test_tier0_common_layer_not_replaced(self, harness):
        """覆盖只动 role 正文，principles（Tier 0 方法论）保持不变。"""
        original = PromptBuilderFactory.get("researcher").build_system_prompt(
            "researcher", {}
        )
        harness["cfg"] = _FakeHarness(
            prompt={"researcher": {"text": "ZZ_OVERRIDE_MARKER_ZZ"}}
        )
        overridden = PromptBuilderFactory.get_for_signature(
            "researcher", "sig"
        ).build_system_prompt("researcher", {})
        assert "ZZ_OVERRIDE_MARKER_ZZ" in overridden
        # common 层（principles）的前缀必须原样保留
        prefix = original[:200]
        assert overridden.startswith(prefix), "Tier 0 common 层被误改"

    def test_empty_override_is_noop(self, harness):
        harness["cfg"] = _FakeHarness()
        builder = PromptBuilderFactory.get_for_signature("researcher", "sig")
        assert builder._harness_override is None


# ── ② ToolRegistry 消费 ─────────────────────────────────────────────


class TestToolRegistryConsumes:
    def _registry(self):
        r = ToolRegistry()
        r.register(_EchoTool())
        return r

    def test_tool_set_filters_definitions(self, harness):
        harness["cfg"] = _FakeHarness(tool_set=frozenset({"nope"}))
        reg = self._registry()
        defs = reg.get_definitions_for("sig")
        assert defs == [], "tool_set 未包含 echo → 定义应被过滤掉"

    def test_tool_set_none_returns_all(self, harness):
        harness["cfg"] = _FakeHarness(tool_set=None)
        reg = self._registry()
        assert len(reg.get_definitions_for("sig")) == 1

    def test_tool_set_cannot_add_unregistered(self, harness):
        """只做减法：tool_set 里的未知名字不会凭空出现。"""
        harness["cfg"] = _FakeHarness(tool_set=frozenset({"echo", "ghost"}))
        reg = self._registry()
        names = [d["function"]["name"] for d in reg.get_definitions_for("sig")]
        assert names == ["echo"]

    def test_empty_signature_equals_plain(self, harness):
        harness["cfg"] = _FakeHarness(tool_set=frozenset({"nope"}))
        reg = self._registry()
        assert reg.get_definitions_for("") == reg.get_definitions()


# ── ③ CompactConfig 消费 ────────────────────────────────────────────


class TestCompactConfigConsumes:
    def test_override_applied_via_replace(self, harness):
        harness["cfg"] = _FakeHarness(compaction={"goal_evidence_truncate_chars": 7})
        base = CompactConfig()
        out = apply_compaction_overrides(base, "sig")
        assert out.goal_evidence_truncate_chars == 7
        assert base.goal_evidence_truncate_chars == 100, "原对象不得被改"

    def test_unknown_field_dropped(self, harness):
        harness["cfg"] = _FakeHarness(compaction={"not_a_field": 1})
        base = CompactConfig()
        assert apply_compaction_overrides(base, "sig") is base

    def test_no_override_returns_same_object(self, harness):
        harness["cfg"] = _FakeHarness()
        base = CompactConfig()
        assert apply_compaction_overrides(base, "sig") is base

    def test_disabled_path_unchanged(self, harness):
        """未给 compact_config 时 assemble 不产出覆盖。"""
        harness["cfg"] = _FakeHarness(compaction={"goal_evidence_truncate_chars": 7})
        h = assemble(role="researcher", compact_config=None, task_signature="sig")
        assert h.compact_config is None


# ── 统一装配入口 ─────────────────────────────────────────────────────


class TestAssemble:
    def test_returns_all_three(self, harness):
        harness["cfg"] = _FakeHarness(
            prompt={"r": {"text": "T"}},
            tool_set=frozenset({"a"}),
            compaction={"goal_evidence_truncate_chars": 3},
        )
        base = CompactConfig()
        h = assemble(role="r", compact_config=base, task_signature="sig")
        assert isinstance(h, AssembledHarness)
        assert h.prompt_override == {"text": "T"}
        assert h.tool_set == frozenset({"a"})
        assert h.compact_config.goal_evidence_truncate_chars == 3

    def test_empty_signature_short_circuits(self, harness):
        harness["cfg"] = _FakeHarness(
            prompt={"r": {"text": "T"}}, tool_set=frozenset({"a"})
        )
        base = CompactConfig()
        h = assemble(role="r", compact_config=base, task_signature="")
        assert h.prompt_override == {}
        assert h.tool_set is None
        assert h.compact_config is base

    def test_never_raises(self, monkeypatch):
        """resolve 抛异常时装配照常进行（RSI 是增强不是前置）。"""
        def boom(sig):
            raise RuntimeError("rsi down")

        monkeypatch.setattr(harness_resolver, "resolve_harness", boom)
        base = CompactConfig()
        h = assemble(role="r", compact_config=base, task_signature="sig")
        # resolve_harness 内部已 try/except，这里只要求不炸
        assert h.compact_config is base


# ── Tier 0 不泄漏 ────────────────────────────────────────────────────


class TestNoTier0Leak:
    def test_tier0_keys_dropped_before_consumption(self):
        """从 apps 侧来源出来的 tier0 键不会流入三个消费点。"""
        from strategy_research.apps.evolution import resolve_harness as real_resolve

        class _Src:
            def tier1_active(self):
                return {
                    "budget": {"max": 999999},          # Tier 0，必须丢
                    "prompt": {"r": {"text": "V2"}},     # Tier 1
                    "tool_set": ["echo"],
                }

        cfg = real_resolve("sig", source=_Src())
        assert "budget" not in cfg.prompt_version
        assert cfg.prompt_version == {"r": {"text": "V2"}}
        assert cfg.tool_set == frozenset({"echo"})
