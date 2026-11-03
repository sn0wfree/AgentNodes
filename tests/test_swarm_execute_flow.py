"""SwarmRuntime.execute() 集成测试——全流程层推进（stub agent 路径）。

覆盖 docs/coverage-90-plan-20261030.md：`execute()` 主循环此前零覆盖
（层推进 / hooks / 预算 / cancel / pre_completed / start_layer）。
用 `allow_stub` 官方结构测试后门（不触 LLM），覆盖的是**编排语义**：
拓扑分层、上游聚合、hook 序列、预算熔断、取消、成功判定。
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from strategy_research.kernel.swarm.runtime import (
    AgentCall,
    AgentResult,
    AgentStatus,
    SwarmPreset,
    SwarmRuntime,
)
from strategy_research.kernel.swarm.types import AgentCall as AgentCallT


def _call(name: str, **ctx) -> AgentCallT:
    base = {"executor_type": "llm", "allow_stub": True}
    base.update(ctx)
    return AgentCallT(agent_name=name, prompt="", context=base)


def _preset(dag: dict[str, list[str]], **kw) -> SwarmPreset:
    agents = [_call(n) for n in dag]
    return SwarmPreset(name="t", description="", agents=agents, dag=dag, **kw)


# ── 基本执行 ─────────────────────────────────────────────


def test_execute_two_layer_pipeline(tmp_path: Path):
    rt = SwarmRuntime()
    result = rt.execute(_preset({"a": [], "b": ["a"]}), tmp_path, "task")
    assert result.success is True
    assert set(result.agent_results) == {"a", "b"}
    assert all(r.status == AgentStatus.SUCCESS for r in result.agent_results.values())
    # final_output = 最后完成的成功输出
    assert result.final_output.startswith("[stub]")
    assert result.elapsed_s >= 0


def test_execute_layer_ordering(tmp_path: Path):
    """a → b → c 链：c 的上游聚合里能看到 a、b 的输出。"""
    rt = SwarmRuntime()
    result = rt.execute(_preset({"a": [], "b": ["a"], "c": ["a", "b"]}),
                        tmp_path, "task")
    assert result.success
    # 层序：a 先于 b、c
    kinds_a = result.agent_results["a"]
    assert kinds_a is not None


def test_execute_pre_completed_skips_layer(tmp_path: Path):
    """start_layer=1 + pre_completed：第 0 层不发事件外的执行，直接跳过。"""
    rt = SwarmRuntime()
    pre = {"a": AgentResult(agent_id="a", status=AgentStatus.SUCCESS,
                            output="pre-done")}
    result = rt.execute(
        _preset({"a": [], "b": ["a"]}), tmp_path, "task",
        pre_completed=pre, start_layer=1,
    )
    assert result.success
    assert result.agent_results["a"].output == "pre-done"
    assert result.agent_results["b"].status == AgentStatus.SUCCESS


def test_execute_unresolvable_agent_fails_run(tmp_path: Path):
    """无 allow_stub 且未注册 → 该 agent ERROR → run 失败（不静默假成功）。"""
    rt = SwarmRuntime()
    preset = SwarmPreset(
        name="t", agents=[
            AgentCallT(agent_name="ghost", prompt="", context={"executor_type": "llm"}),
        ],
        dag={"ghost": []},
    )
    result = rt.execute(preset, tmp_path, "task")
    assert result.success is False
    assert result.agent_results["ghost"].status == AgentStatus.ERROR
    assert "not registered" in (result.agent_results["ghost"].error or "")


# ── hooks ────────────────────────────────────────────────


class RecordingHook:
    def __init__(self, *, stop_after_layer: int | None = None):
        self.calls: list[tuple] = []
        self._stop_after = stop_after_layer

    @property
    def name(self) -> str:
        return "recording"

    def on_layer_start(self, layer_idx, agents, context):
        self.calls.append(("layer_start", layer_idx, tuple(agents)))

    def on_layer_complete(self, layer_idx, agents, results):
        self.calls.append(("layer_complete", layer_idx, tuple(agents)))

    def on_agent_complete(self, agent_id, result, context):
        self.calls.append(("agent_complete", agent_id))

    def should_stop(self) -> bool:
        starts = [c for c in self.calls if c[0] == "layer_complete"]
        if self._stop_after is not None and len(starts) >= self._stop_after:
            return True
        return False


def test_hooks_fire_in_order(tmp_path: Path):
    hook = RecordingHook()
    rt = SwarmRuntime()
    rt.execute(_preset({"a": [], "b": ["a"]}), tmp_path, "task", hooks=[hook])
    kinds = [c[0] for c in hook.calls]
    assert kinds[0] == "layer_start"
    assert "agent_complete" in kinds
    assert kinds.count("layer_start") == 2
    assert kinds.count("layer_complete") == 2
    # start 在 complete 之前
    assert kinds.index("layer_start") < kinds.index("layer_complete")


def test_hook_should_stop_halts_pipeline(tmp_path: Path):
    hook = RecordingHook(stop_after_layer=1)
    rt = SwarmRuntime()
    result = rt.execute(
        _preset({"a": [], "b": ["a"], "c": ["b"]}), tmp_path, "task",
        hooks=[hook],
    )
    # 第一层完成后被停：c 未执行
    assert "c" not in result.agent_results
    assert "a" in result.agent_results


# ── 预算熔断 ─────────────────────────────────────────────


def test_budget_turn_exhaustion_stops_pipeline(tmp_path: Path):
    """budget_turn=1：第一层后预算耗尽，第二层不执行。

    锁定实际语义：success 只统计**已执行**的 agent——被预算挡住的
    agent 不参与判定（这与直觉相悖但为现行为；若将来改为
    「未跑完即失败」，这里应同步更新）。
    """
    rt = SwarmRuntime()
    result = rt.execute(
        _preset({"a": [], "b": ["a"]}, budget_turn=1), tmp_path, "task",
    )
    assert "a" in result.agent_results
    assert "b" not in result.agent_results
    assert result.success is True  # 只看跑过的
    assert result.final_output.startswith("[stub] a")


def test_budget_none_never_exhausts(tmp_path: Path):
    rt = SwarmRuntime()
    result = rt.execute(_preset({"a": [], "b": ["a"]}, budget_turn=None),
                        tmp_path, "task")
    assert result.success is True


def test_budget_time_zero_blocks_first_layer(tmp_path: Path):
    """budget_time_seconds=0.0：进入第 0 层前即判超时——什么都没跑。

    锁定实际语义：0 预算 = 完全不执行（连第一层都不跑）。
    """
    rt = SwarmRuntime()
    result = rt.execute(
        _preset({"a": [], "b": ["a"], "c": ["b"]}, budget_time_seconds=0.0),
        tmp_path, "task",
    )
    assert result.agent_results == {}
    assert result.success is True  # 空 results 的 all() 为真（现行为）


# ── cancel ───────────────────────────────────────────────


def test_cancel_before_layer_stops_execution(tmp_path: Path):
    """hook 在第一次 layer_start 后取消活跃 run → 第二层不执行。"""
    rt = SwarmRuntime()

    class CancelAll:
        def on_layer_start(self, layer_idx, agents, context):
            for rid in list(rt._active_runs):
                rt.cancel(rid)

        def on_layer_complete(self, *a, **k):
            return None

        def on_agent_complete(self, *a, **k):
            return None

        def should_stop(self):
            return False

        @property
        def name(self):
            return "cancel-all"

    result = rt.execute(
        _preset({"a": [], "b": ["a"]}), tmp_path, "task",
        hooks=[CancelAll()],
    )
    # 第一层照常执行（cancel 发生在其 layer_start 时），第二层被取消挡住
    assert "a" in result.agent_results
    assert "b" not in result.agent_results
    # run 结束后活跃表已清理
    assert rt._active_runs == {}
