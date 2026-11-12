"""U4：deciders 可组合 + 外部终止（统一引擎第 4 批）。

**业界形状**（survey §2 / §4 建议 1）：
- AutoGen 把终止条件做成可组合对象（`MaxMessageTermination & TextMention`）；
- Vercel AI SDK 的 `stopWhen` 是条件数组；
- AutoGen 官方 pitfall #5869：max 计数条件必须与语义条件组合使用当安全网。

U4 给 `Decider` 加 `and_()` / `or_()` 组合子 + `ExternalTerminationDecider`
（读全局 halt），并在 `modes.deciders_for` 登记第四闸 `after_observe`（空）。

**本批约束：运行时行为零变化**——组合子与外部终止是纯增量，默认装配里
ExternalTerminationDecider 直读 `is_halted()`（生产语义），单测注入替身
（不实测 0.5s 节流窗口，见 design doc §3.4）。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from agentnodes.kernel.agent.deciders import (
    Decision,
    Decider,
    ExternalTerminationDecider,
)


class _Fixed(Decider):
    """测试用 decider：固定返回 (decision, reason)，记录调用次数。"""

    def __init__(self, decision: Decision, reason: str | None = None, name: str = "fixed"):
        self._decision = decision
        self._reason = reason
        self._name = name
        self.calls = 0

    @property
    def name(self) -> str:
        return self._name

    def decide(self, input, ctx):  # noqa: ANN001
        self.calls += 1
        return self._decision, self._reason


# ── and_：两者都 PROCEED 才 PROCEED；否则取第一个非 PROCEED ──


def test_and_short_circuits_on_first_non_proceed():
    first = _Fixed(Decision.STOP, "first_stop", "a")
    second = _Fixed(Decision.STOP, "second_stop", "b")
    d = first.and_(second)
    assert d.decide(None, None) == (Decision.STOP, "first_stop")
    assert first.calls == 1
    assert second.calls == 0, "and_ 应在第一个非 PROCEED 处短路"


def test_and_proceeds_only_when_both_proceed():
    first = _Fixed(Decision.PROCEED, None, "a")
    second = _Fixed(Decision.PROCEED, None, "b")
    d = first.and_(second)
    assert d.decide(None, None) == (Decision.PROCEED, None)
    assert first.calls == 1 and second.calls == 1


def test_and_second_decides_when_first_proceeds():
    first = _Fixed(Decision.PROCEED, None, "a")
    second = _Fixed(Decision.CONTINUE, "cont", "b")
    d = first.and_(second)
    assert d.decide(None, None) == (Decision.CONTINUE, "cont")
    assert second.calls == 1


# ── or_：第一个非 PROCEED 即返回 ──


def test_or_short_circuits_on_first_non_proceed():
    first = _Fixed(Decision.STOP, "stop1", "a")
    second = _Fixed(Decision.EXIT, "exit2", "b")
    d = first.or_(second)
    assert d.decide(None, None) == (Decision.STOP, "stop1")
    assert second.calls == 0


def test_or_proceeds_when_both_proceed():
    first = _Fixed(Decision.PROCEED, None, "a")
    second = _Fixed(Decision.PROCEED, None, "b")
    d = first.or_(second)
    assert d.decide(None, None) == (Decision.PROCEED, None)


# ── 组合子 name（审计可读性）──


def test_combination_name_is_readable():
    d = _Fixed(Decision.PROCEED, None, "max_iter").and_(
        _Fixed(Decision.PROCEED, None, "budget"))
    assert d.name == "(max_iter and budget)"
    d2 = _Fixed(Decision.PROCEED, None, "x").or_(_Fixed(Decision.PROCEED, None, "y"))
    assert d2.name == "(x or y)"


# ── ExternalTerminationDecider：halt 命中 → EXIT ──


def test_external_termination_exits_when_halted():
    d = ExternalTerminationDecider(halt_check=lambda: True)
    assert d.decide(None, None) == (Decision.EXIT, "halt")


def test_external_termination_proceeds_when_not_halted():
    d = ExternalTerminationDecider(halt_check=lambda: False)
    assert d.decide(None, None) == (Decision.PROCEED, None)


def test_external_termination_no_source_means_proceed():
    """默认 None = 无外部终止源 → 恒 PROCEED（运行时零变化）。"""
    assert ExternalTerminationDecider().decide(None, None) == (
        Decision.PROCEED, None)


def test_external_termination_swallows_source_errors():
    """外部信号故障不阻断循环（fail-open，与既有 injector 容错一致）。"""

    def _boom():
        raise RuntimeError("halt source broken")

    assert ExternalTerminationDecider(halt_check=_boom).decide(None, None) == (
        Decision.PROCEED, None)


def test_external_termination_wired_via_modes_injection():
    """装配层注入：deciders_for(halt_check=...) 时生效。"""
    from agentnodes.kernel.agent.modes import deciders_for
    from agentnodes.kernel.agent.strategy.factory import create_strategy

    strategy = create_strategy("react")
    gates = deciders_for(strategy, None, max_iterations=8,
                         halt_check=lambda: True)
    pre = gates["precheck"]
    names = [d.name for d in pre]
    assert "external_termination" in names
    idx = names.index("external_termination")
    decision, reason = pre[idx].decide(None, None)
    assert (decision, reason) == (Decision.EXIT, "halt")


# ── 分层纪律（R-001）：kernel 不得 import interfaces/cli ──


def test_deciders_module_does_not_import_interfaces():
    """U4 首版曾直读 is_halted 触发 L3>L1 违规——钉住不回归。"""
    from pathlib import Path

    src = Path(__file__).resolve().parents[1] / (
        "src/agentnodes/kernel/agent/deciders.py")
    text = src.read_text(encoding="utf-8")
    assert "interfaces.cli" not in text, (
        "kernel 层不得 import interfaces/cli（R-001 四层单向依赖）")
    assert "from agentnodes.interfaces" not in text


# ── 安全网组合惯例（AutoGen pitfall #5869 的落地形态）──


def test_max_iteration_and_budget_combined_safety_net():
    """max 计数与语义条件组合：任一触发即停（典型安全网写法）。"""
    from agentnodes.kernel.agent.deciders import MaxIterationsDecider

    class _Ctx:
        iteration = 5
        stop_reason = None
        should_stop = False
        tokens_used = 0
        cost_usd = 0.0
        t0 = 0.0

    # MaxIterationsDecider 用 for-else 语义（iteration > max）
    combined = MaxIterationsDecider(3).and_(
        ExternalTerminationDecider(halt_check=lambda: False))
    decision, reason = combined.decide(None, _Ctx())
    assert decision is Decision.STOP, f"5 > 3 应 STOP，实得 {decision}"
