"""契约独立定形测试 —— 中立协议 + 形状 fail-loud + 真引擎类型结构满足。"""
from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from strategy_research.kernel.orchestration.branch_contracts import (
    BranchContextLike,
    RoundOutcomeLike,
    assert_branch_context_shape,
    assert_round_outcome_shape,
)


@dataclass
class _StubOutcome:
    """与 phase_engine.RoundOutcome 同构的本地桩（协议测试不依赖引擎导入）。"""

    metrics: dict = field(default_factory=dict)
    verdict: str = "pass"
    eval_result: dict = field(default_factory=dict)
    agent_outputs: dict = field(default_factory=dict)
    strategist_output: dict = field(default_factory=dict)
    hypothesis: str = "h"
    predicted_affected: list = field(default_factory=list)
    captured_predictions: dict = field(default_factory=dict)
    backtest_error: str | None = None


@dataclass
class _StubContext:
    round_num: int = 1
    previous_summary: dict | None = None
    directive_text: str | None = None
    strategy: str = "s"
    session: str = "sess"
    sid: str = "sid-1"
    metric_targets: list = field(default_factory=list)
    # 引擎内部字段——不在跨层契约里，但存在于真实对象上
    runner: object = None
    run_dir: object = None


def test_stub_outcome_satisfies_protocol():
    assert isinstance(_StubOutcome(), RoundOutcomeLike)


def test_stub_context_satisfies_protocol():
    assert isinstance(_StubContext(), BranchContextLike)


def test_assert_round_outcome_shape_passes_on_valid():
    assert_round_outcome_shape(_StubOutcome())


def test_assert_round_outcome_shape_fails_on_missing_field():
    @dataclass
    class Broken:
        metrics: dict = field(default_factory=dict)
        verdict: str = "x"  # 其余 7 字段缺失

    with pytest.raises(TypeError, match="形状漂移"):
        assert_round_outcome_shape(Broken())


def test_assert_round_outcome_shape_fails_on_type_drift():
    broken = _StubOutcome(verdict=123)  # verdict 应为 str
    with pytest.raises(TypeError, match="verdict"):
        assert_round_outcome_shape(broken)


def test_assert_branch_context_shape_passes_on_valid():
    assert_branch_context_shape(_StubContext())


def test_assert_branch_context_shape_fails_on_missing():
    @dataclass
    class Broken:
        round_num: int = 1  # 其余稳定字段缺失

    with pytest.raises(TypeError, match="形状漂移"):
        assert_branch_context_shape(Broken())


def test_engine_internal_fields_not_required():
    """BranchContext 协议只查稳定子集——引擎内部字段缺失不报错。"""
    @dataclass
    class SlimContext:
        round_num: int = 2
        previous_summary: dict | None = None
        directive_text: str | None = None
        strategy: str = "s"
        session: str = "sess"
        sid: str = "sid"
        metric_targets: list = field(default_factory=list)
        # 无 runner/run_dir/graph 等引擎内部字段

    assert_branch_context_shape(SlimContext())


def test_real_phase_engine_types_satisfy_protocols():
    """集成确认：真引擎 dataclass 结构上满足中立协议（零改动引擎）。"""
    phase_engine = pytest.importorskip(
        "strategy_research.core.study.phase_engine", reason="引擎导入链较重"
    )
    outcome = phase_engine.RoundOutcome(
        metrics={}, verdict="pass", eval_result={}, agent_outputs={},
        strategist_output={}, hypothesis="h", predicted_affected=[],
        captured_predictions={}, backtest_error=None,
    )
    assert isinstance(outcome, RoundOutcomeLike)
    assert_round_outcome_shape(outcome)
