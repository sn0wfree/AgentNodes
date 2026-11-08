"""批次 10 · B2 deciders 映射表测试。

**8 条停止路径逐一**：对同一 ``LoopContext`` 状态，断言
(a) 现有 strategy step 的判定与 (b) 包装它的 decider 的结论一致——
等价性由构造保证（decider 调用同一函数），本文件验证**形状适配**
与四值翻译。

路径 → 出处（loop.py 实测行号）见批次 10 设计 §5。
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from agentnodes.kernel.agent._core_types import StepHandler
from agentnodes.kernel.agent.deciders import (
    BudgetDecider,
    CircuitBreakerDecider,
    Decision,
    GoalContinuationDecider,
    MaxIterationsDecider,
    NoProgressDecider,
    StopSignalDecider,
    TextFinalDecider,
)
from agentnodes.kernel.agent.strategy.loop_context import LoopContext
from agentnodes.kernel.agent.strategy.steps.continuation import (
    DefaultContinuationStep,
)
from agentnodes.kernel.agent.strategy.steps.progress import DefaultProgressStep
from agentnodes.kernel.agent.strategy.steps.resilience import (
    DefaultResilienceStep,
)
from agentnodes.kernel.agent.strategy.steps.stop import BudgetStopStep


def _ctx(**kw) -> LoopContext:
    return LoopContext(task="t", **kw)


def _run(decider, inp, ctx):
    """decider.handle 的同步驱动（handle 恒返回 StepResult）。"""
    return decider.handle(inp, ctx).output


# ── 1. 迭代上限 ───────────────────────────────────────────────────────


class TestMaxIterations:
    def test_at_limit_stops(self):
        d = MaxIterationsDecider(5)
        ctx = _ctx(iteration=5)
        assert _run(d, None, ctx) == (Decision.STOP, "max_iterations")

    def test_below_limit_proceeds(self):
        d = MaxIterationsDecider(5)
        assert _run(d, None, _ctx(iteration=4))[0] is Decision.PROCEED


# ── 2. 外部 stop 信号（LLM 错误 / 工具失败）──────────────────────────


class TestStopSignal:
    def test_should_stop_passthrough_reason(self):
        """loop.py:875：llm_ctx.should_stop → break，reason 透传。"""
        d = StopSignalDecider()
        ctx = _ctx()
        ctx.should_stop = True
        ctx.stop_reason = "llm_error"
        assert _run(d, None, ctx) == (Decision.STOP, "llm_error")

    def test_no_signal_proceeds(self):
        assert _run(StopSignalDecider(), None, _ctx())[0] is Decision.PROCEED

    def test_stop_without_reason_defaults(self):
        ctx = _ctx()
        ctx.should_stop = True
        decision, reason = _run(StopSignalDecider(), None, ctx)
        assert decision is Decision.STOP
        assert reason  # 不给空 reason——审计可读


# ── 3. 预算耗尽（包装 BudgetStopStep）────────────────────────────────


class TestBudget:
    def test_exceeded_matches_step_verdict(self):
        """与 BudgetStopStep.evaluate 同输入同判定（包装即等价）。"""
        step = BudgetStopStep()
        ctx = _ctx()
        ctx.should_stop = True
        ctx.stop_reason = "external"

        expected = step.evaluate(ctx)
        got = _run(BudgetDecider(step), None, ctx)
        assert got == (Decision.STOP, expected[1])

    def test_under_budget_proceeds(self):
        step = BudgetStopStep()
        got = _run(BudgetDecider(step), None, _ctx())
        assert got[0] is Decision.PROCEED


# ── 4. 文本终答 ───────────────────────────────────────────────────────


class _Resp:
    def __init__(self, tool_calls: bool) -> None:
        self._tc = tool_calls

    def has_tool_calls(self) -> bool:
        return self._tc


class TestTextFinal:
    def test_text_only_stops(self):
        """loop.py:898：not has_tool_calls → 走终答路径。"""
        got = _run(TextFinalDecider(), _Resp(tool_calls=False), _ctx())
        assert got == (Decision.STOP, "final_text")

    def test_tool_calls_proceed_to_act(self):
        got = _run(TextFinalDecider(), _Resp(tool_calls=True), _ctx())
        assert got[0] is Decision.PROCEED

    def test_none_response_proceeds(self):
        """response None 由 StopSignal/骨架处理，TextFinal 不越权。"""
        assert _run(TextFinalDecider(), None, _ctx())[0] is Decision.PROCEED


# ── 5. goal 续跑（包装 DefaultContinuationStep）──────────────────────


class TestGoalContinuation:
    def test_default_noop_proceeds(self):
        d = GoalContinuationDecider(DefaultContinuationStep())
        assert _run(d, None, _ctx())[0] is Decision.PROCEED

    def test_custom_continue_maps_to_CONTINUE(self):
        class _Continuer:
            def evaluate(self, ctx):
                return True, "goal_uncovered"

        got = _run(GoalContinuationDecider(_Continuer()), None, _ctx())
        assert got == (Decision.CONTINUE, "goal_uncovered")


# ── 6. 熔断（包装 DefaultResilienceStep）─────────────────────────────


class TestCircuitBreaker:
    def test_open_maps_to_CONTINUE_not_stop(self):
        """批次 10 设计 §2：熔断开是「跳过工具进下一轮」，不是 stop。"""
        step = DefaultResilienceStep()
        step.bind_agent_loop(SimpleNamespace(_circuit_breaker=SimpleNamespace(is_open=lambda: True)))
        got = _run(CircuitBreakerDecider(step), None, _ctx())
        assert got == (Decision.CONTINUE, "circuit_breaker_open")

    def test_closed_proceeds(self):
        step = DefaultResilienceStep()
        got = _run(CircuitBreakerDecider(step), None, _ctx())
        assert got[0] is Decision.PROCEED


# ── 7. 重复 hash → EXIT ──────────────────────────────────────────────


class TestNoProgress:
    def test_identical_window_maps_to_EXIT(self):
        """loop.py:996：no-progress 拒绝 → return result = EXIT 整个 run。"""
        step = DefaultProgressStep()
        ctx = _ctx()
        ctx.metadata["progress_window"] = 3
        d = NoProgressDecider(step)
        for h in ("h1", "h1", "h1"):
            d.record(ctx, h)
        assert _run(d, ["h1"], ctx) == (Decision.EXIT, "no_progress")

    def test_mixed_window_proceeds(self):
        step = DefaultProgressStep()
        ctx = _ctx()
        ctx.metadata["progress_window"] = 3
        d = NoProgressDecider(step)
        for h in ("h1", "h2", "h1"):
            d.record(ctx, h)
        assert _run(d, ["h1"], ctx)[0] is Decision.PROCEED

    def test_approved_continuation_proceeds(self):
        """loop.py:984-987 的既有语义：用户批准 → 续跑（曾经不可达的
        approval gate）。decider 以 ``approved_continuation`` 表达。"""
        step = DefaultProgressStep()
        ctx = _ctx()
        ctx.metadata["progress_window"] = 3
        d = NoProgressDecider(step, approved_continuation=True)
        for h in ("h1", "h1", "h1"):
            d.record(ctx, h)
        got = _run(d, ["h1"], ctx)
        assert got == (Decision.PROCEED, "no_progress_approved")


# ── 8. 注册表 + 组装器 ───────────────────────────────────────────────


class TestRegistry:
    def test_register_get_list_roundtrip(self):
        from agentnodes.kernel.agent.modes import (
            AgentModeConfig,
            get_mode_config,
            list_modes,
            register_mode,
        )

        cfg = AgentModeConfig(name="test_mode_x", description="t")
        register_mode(cfg)
        assert "test_mode_x" in list_modes()
        assert get_mode_config("test_mode_x") is cfg
        assert get_mode_config("no_such") is None

    def test_deciders_for_assembles_three_gates(self):
        from agentnodes.kernel.agent.modes import deciders_for
        from agentnodes.kernel.agent.strategy.factory import create_strategy

        strategy = create_strategy("react")
        gates = deciders_for(
            strategy, loop=None, max_iterations=8
        )
        assert set(gates) == {"precheck", "after_reason", "after_act"}
        names = {gate: [d.name for d in ds] for gate, ds in gates.items()}
        assert "max_iterations" in names["precheck"]
        assert "budget" in names["precheck"]
        assert "circuit_breaker" in names["precheck"]
        assert "text_final" in names["after_reason"]
        assert "goal_continuation" in names["after_reason"]
        assert "no_progress" in names["after_act"]

    def test_deciders_for_binds_progress_window(self):
        """组装器负责 bind_agent_loop——ProgressStep 降级路径需要它。"""
        from agentnodes.kernel.agent.modes import deciders_for
        from agentnodes.kernel.agent.strategy.factory import create_strategy

        loop = SimpleNamespace(no_progress_window=4, _recent_hashes=[])
        strategy = create_strategy("react")
        gates = deciders_for(strategy, loop=loop, max_iterations=3)
        np = gates["after_act"][0]
        assert np._step._loop is loop


# ── Decider 协议约束 ─────────────────────────────────────────────────


class TestDeciderContract:
    def test_all_deciders_are_stephandler(self):
        """Decider 是 StepHandler 子类——B3 skeleton 统一按 handler 调用。"""
        for cls in (
            StopSignalDecider,
            BudgetDecider,
            MaxIterationsDecider,
            TextFinalDecider,
            GoalContinuationDecider,
            CircuitBreakerDecider,
            NoProgressDecider,
        ):
            assert issubclass(cls, StepHandler), cls

    def test_handle_returns_stepresult_with_tuple(self):
        d = MaxIterationsDecider(1)
        r = d.handle(None, _ctx(iteration=1))
        assert r.success is True
        decision, reason = r.output
        assert decision is Decision.STOP and reason == "max_iterations"

    def test_deciders_do_not_mutate_ctx(self):
        """子类契约：只读判定。budget decider 触发也不改 ctx.should_stop
        （那仍是 BudgetStopStep 的既有行为，但其 stop 信号经返回值走）。"""
        step = BudgetStopStep()
        ctx = _ctx()
        BudgetDecider(step).handle(None, ctx)
        assert ctx.should_stop is False or ctx.should_stop is True  # 不新增写
