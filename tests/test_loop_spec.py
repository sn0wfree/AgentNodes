"""批次 12 S2 · LoopSpec 聚合与向后兼容测试。

**核心防线**：``AgentLoop.__init__`` 的旧参数集必须与
``loop_spec._LEGACY_MAP`` 完全一致——少一个就是「参数被静默丢弃」
（批次 12 S2 首轮实测：漏传 ``workspace`` / ``memory`` 导致所有工具报
"missing workspace context"，而 200+ 调用点不会报错）。
"""
from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from agentnodes.kernel.agent.loop import AgentLoop
from agentnodes.kernel.agent.loop_spec import (
    BudgetSpec,
    CapabilitySpec,
    ContextSpec,
    LoopSpec,
    PermissionSpec,
    RuntimeSpec,
    ScopeSpec,
    _LEGACY_MAP,
)


# ── 完整性防线（最重要）─────────────────────────────────────────────


class TestLegacyMapCompleteness:
    def test_every_init_param_is_mapped(self):
        """``__init__`` 的每个旧参数都必须在 _LEGACY_MAP 里。

        漏一个 = 该参数被静默丢弃（不报错，只在运行时表现为功能缺失）。
        """
        sig = inspect.signature(AgentLoop.__init__)
        # 排除：self / 第一必需参数 / spec 本身
        excluded = {"self", "config", "registry", "spec"}
        init_params = {
            name for name in sig.parameters
            if name not in excluded
        }
        mapped = set(_LEGACY_MAP) | {"on_event"}
        missing = init_params - mapped
        assert not missing, (
            f"这些 __init__ 参数没进 _LEGACY_MAP（会被静默丢弃）: "
            f"{sorted(missing)}"
        )

    def test_no_orphan_map_entries(self):
        """_LEGACY_MAP 里不应有 __init__ 已不存在的参数。"""
        sig = inspect.signature(AgentLoop.__init__)
        init_params = set(sig.parameters)
        orphans = set(_LEGACY_MAP) - init_params
        assert not orphans, f"_LEGACY_MAP 有孤儿条目: {sorted(orphans)}"


# ── 折叠 / 展开 roundtrip ───────────────────────────────────────────


class TestFoldUnfold:
    def test_fold_groups_values(self):
        spec = LoopSpec.fold_legacy_kwargs({
            "max_iterations": 3,
            "stream_mode": False,
            "workspace": Path("/tmp/ws"),
            "memory": "MEM",
            "system_prompt": "SYS",
        })
        assert spec.budget.max_iterations == 3
        assert spec.runtime.stream_mode is False
        assert spec.scope.workspace == Path("/tmp/ws")
        assert spec.capability.memory == "MEM"
        assert spec.context.system_prompt == "SYS"

    def test_permission_spec_exists_but_not_legacy_param(self):
        """PermissionSpec 保留供显式 spec 传参，但**不是 __init__ 参数**
        （chat_loop 用 setattr 挂载——getattr 消费点见 loop_tools）。"""
        spec = LoopSpec(permission=PermissionSpec(permission_evaluator="EV"))
        assert spec.permission.permission_evaluator == "EV"
        with pytest.raises(TypeError):
            LoopSpec.fold_legacy_kwargs({"permission_evaluator": "EV"})

    def test_unknown_key_raises(self):
        with pytest.raises(TypeError, match="unexpected keyword"):
            LoopSpec.fold_legacy_kwargs({"not_a_param": 1})

    def test_roundtrip_preserves_values(self):
        original = {"max_iterations": 7, "stream_mode": False,
                    "workspace": Path("/w"), "role": "r1"}
        spec = LoopSpec.fold_legacy_kwargs(original)
        back = spec.to_legacy_kwargs()
        for k, v in original.items():
            assert back[k] == v, k

    def test_defaults_match_init_defaults(self):
        """折叠空 kwargs 的默认值必须与 __init__ 签名默认一致。"""
        sig = inspect.signature(AgentLoop.__init__)
        defaults = LoopSpec.fold_legacy_kwargs({})
        pairs = {
            "max_iterations": defaults.budget.max_iterations,
            "stream_mode": defaults.runtime.stream_mode,
            "approval_timeout": defaults.budget.approval_timeout,
            "enable_goal_injection": defaults.context.enable_goal_injection,
        }
        for name, value in pairs.items():
            assert value == sig.parameters[name].default, name


# ── 端到端：旧构造 → 内部字段正确 ───────────────────────────────────


class TestAgentLoopConstruction:
    def _make(self, **kw):
        from agentnodes.foundation.llm import LLMConfig
        from agentnodes.kernel.agent.builtin_tools import build_default_registry

        base = dict(
            config=LLMConfig(api_key="sk-test"),
            registry=build_default_registry(),
        )
        base.update(kw)
        return AgentLoop(**base)

    def test_workspace_propagates_to_loop_and_tools(self, tmp_path):
        """批次 12 S2 回归：workspace 必须传到 self.workspace（工具读它）。"""
        loop = self._make(workspace=tmp_path)
        assert loop.workspace == tmp_path
        assert loop._spec.scope.workspace == tmp_path

    def test_memory_propagates(self):
        from types import SimpleNamespace

        mem = SimpleNamespace(snapshot="")
        loop = self._make(memory=mem)
        assert loop.memory is mem

    def test_all_groups_populated(self, tmp_path):
        loop = self._make(
            workspace=tmp_path,
            max_iterations=4,
            stream_mode=False,
            role="r",
            approval_timeout=99.0,
        )
        spec = loop._spec
        assert spec.scope.workspace == tmp_path
        assert spec.budget.max_iterations == 4
        assert spec.runtime.stream_mode is False
        assert spec.context.role == "r"
        assert spec.budget.approval_timeout == 99.0

    def test_explicit_spec_accepted(self, tmp_path):
        """显式 spec 入口可用（阶段 3 迁移的基础）。"""
        spec = LoopSpec(
            scope=ScopeSpec(workspace=tmp_path),
            budget=BudgetSpec(max_iterations=2),
        )
        loop = self._make(spec=spec)
        assert loop.workspace == tmp_path
        assert loop.max_iterations == 2

    def test_legacy_kwargs_override_explicit_spec_non_default(self, tmp_path):
        """旧参数非默认值时覆盖显式 spec（渐进迁移语义）。"""
        spec = LoopSpec(budget=BudgetSpec(max_iterations=2))
        loop = self._make(spec=spec, max_iterations=9)
        assert loop.max_iterations == 9

    def test_explicit_spec_wins_when_legacy_is_default(self):
        """旧参数保持默认时，显式 spec 的值生效。"""
        spec = LoopSpec(budget=BudgetSpec(max_iterations=2))
        loop = self._make(spec=spec)  # 不传 max_iterations（默认 10）
        assert loop.max_iterations == 2
