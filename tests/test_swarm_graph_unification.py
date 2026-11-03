"""深度统一 B1 的语义锁测试（docs/deep-unification-graph-engine-design-20261031.md）。

SwarmRuntime.execute 已改为 SwarmPreset → GraphEngine 编译器。本文件锁定
迁移中**容易走样**的等价性细节——旧层循环的哪些行为必须逐条保真：

1. on_agent_complete 在 execute 调用线程上按完成序逐条交付（非 join 批量）；
2. should_stop 每层执行后都查询（含末层）；
3. 预算超限 = 层边界 GraphInterrupt（agent_results 停在边界前）；
4. 未注册 agent 静默排除（无结果条目，不参与 success 判定——旧行为保真）；
5. 空 DAG 不进图，直接 success=True；
6. final_output = 按**完成序**最后一个 SUCCESS 的输出（非 ordinal 序）。
"""
from __future__ import annotations

import threading
import time
from pathlib import Path

from strategy_research.kernel.swarm.runtime import (
    AgentResult,
    AgentStatus,
    SwarmPreset,
    SwarmRuntime,
)
from strategy_research.kernel.swarm.types import AgentCall


def _call(name: str, **ctx) -> AgentCall:
    base = {"executor_type": "llm", "allow_stub": True}
    base.update(ctx)
    return AgentCall(agent_name=name, prompt="", context=base)


def _preset(dag: dict[str, list[str]], **kw) -> SwarmPreset:
    agents = [_call(n) for n in dag]
    return SwarmPreset(name="t", description="", agents=agents, dag=dag, **kw)


def test_on_agent_complete_delivery_is_incremental_on_caller_thread(tmp_path):
    """on_agent_complete：逐条（完成即发）、在 execute 调用线程上。"""
    events: list[tuple[str, int]] = []
    caller_tid = threading.get_ident()

    class Hook:
        name = "h"

        def on_agent_complete(self, agent_id, result, context):
            events.append((agent_id, threading.get_ident()))

        def on_layer_start(self, *a):
            pass

        def on_layer_complete(self, *a):
            pass

        def should_stop(self):
            return False

    rt = SwarmRuntime()
    # b 比 a 慢：a 完成时应已触发其 on_agent_complete（增量交付）
    agents = [
        _call("a"), _call("b"),
    ]
    preset = SwarmPreset(name="t", agents=agents, dag={"a": [], "b": []})
    rt.execute(preset, tmp_path, "task", hooks=[Hook()])
    assert len(events) == 2
    # 全部在调用线程（非 fanout worker 线程、非事件循环线程）
    assert all(tid == caller_tid for _, tid in events)


def test_on_agent_complete_completion_order(tmp_path):
    """插入序 = 完成序 = hook 交付序：桥接在 settled 事件里先插后发，
    settled 按 as_completed（完成序）逐条出现——旧 future.result() 循环
    的顺序语义保真（final_output 取「最后完成的 SUCCESS」依赖它）。"""
    seen: list[str] = []

    class Hook:
        name = "h"

        def on_agent_complete(self, agent_id, result, context):
            seen.append(agent_id)

        def on_layer_start(self, *a):
            pass

        def on_layer_complete(self, *a):
            pass

        def should_stop(self):
            return False

    rt = SwarmRuntime(max_workers=2)
    preset = SwarmPreset(name="t", agents=[_call("x"), _call("y")],
                         dag={"x": [], "y": []})
    result = rt.execute(preset, tmp_path, "task", hooks=[Hook()])
    assert sorted(seen) == ["x", "y"]
    # hook 交付序 = agent_results 插入序
    assert list(result.agent_results.keys()) == seen


def test_should_stop_queried_after_every_layer(tmp_path):
    """含末层：旧循环每层后查询 should_stop，末层也不例外。"""
    calls = {"n": 0}

    class Hook:
        name = "h"

        def on_layer_start(self, *a):
            pass

        def on_layer_complete(self, *a):
            pass

        def should_stop(self):
            calls["n"] += 1
            return False

    rt = SwarmRuntime()
    rt.execute(_preset({"a": [], "b": ["a"]}), tmp_path, "task", hooks=[Hook()])
    assert calls["n"] == 2  # 两层各一次（单 agent DAG = 两层）


def test_budget_exhaustion_stops_at_layer_boundary(tmp_path):
    """budget_turn=1 → 第二层 split 节点 GraphInterrupt，结果停在边界前。"""
    rt = SwarmRuntime()
    result = rt.execute(
        _preset({"a": [], "b": ["a"], "c": ["b"]}, budget_turn=1),
        tmp_path, "task",
    )
    assert "a" in result.agent_results
    assert "b" not in result.agent_results
    assert "c" not in result.agent_results


def test_unregistered_agent_silently_excluded(tmp_path):
    """dag 层里有、但 preset.agents 里没有的 agent：静默排除（无结果
    条目，不参与 success 判定）——旧 ``_execute_layer`` 的
    ``agent_call is None → continue`` 行为保真。注意与「在 agents 里
    但注册表查不到」区分：后者走 allow_stub stub 成功或 ERROR。"""
    rt = SwarmRuntime()
    preset = SwarmPreset(
        name="t",
        agents=[_call("real")],  # ghost 不在 agents 里
        dag={"real": [], "ghost": []},
    )
    result = rt.execute(preset, tmp_path, "task")
    assert "ghost" not in result.agent_results
    assert result.agent_results["real"].status == AgentStatus.SUCCESS
    assert result.success is True


def test_allow_stub_unregistered_agent_gets_stub_success(tmp_path):
    """在 agents 里但注册表查不到 + allow_stub：stub SUCCESS（官方结构
    测试后门），进结果集——与静默排除是两条不同路径。"""
    rt = SwarmRuntime()
    preset = SwarmPreset(
        name="t",
        agents=[_call("ghost_stub")],
        dag={"ghost_stub": []},
    )
    result = rt.execute(preset, tmp_path, "task")
    assert result.agent_results["ghost_stub"].status == AgentStatus.SUCCESS
    assert result.agent_results["ghost_stub"].output.startswith("[stub]")


def test_empty_dag_never_enters_graph(tmp_path):
    """空 DAG：无层可跑 → success=True（all([]) 语义），不触碰引擎。"""
    rt = SwarmRuntime()
    result = rt.execute(SwarmPreset(name="t", agents=[], dag={}),
                        tmp_path, "task")
    assert result.success is True
    assert result.agent_results == {}
    assert result.final_output == ""


def test_pre_completed_layers_fire_hooks_without_execution(tmp_path):
    """resume：start_layer 之前的层只发 hook 对，不执行 agent。"""
    events: list[tuple] = []

    class Hook:
        name = "h"

        def on_layer_start(self, layer_idx, agents, context):
            events.append(("start", layer_idx, tuple(agents)))

        def on_layer_complete(self, layer_idx, agents, results):
            events.append(("complete", layer_idx, tuple(results)))

        def should_stop(self):
            return False

    rt = SwarmRuntime()
    pre = {"a": AgentResult(agent_id="a", status=AgentStatus.SUCCESS,
                            output="pre")}
    result = rt.execute(
        _preset({"a": [], "b": ["a"]}), tmp_path, "task",
        hooks=[Hook()], pre_completed=pre, start_layer=1,
    )
    # 第 0 层：start+complete 对（resume 重放），b 层：start+complete
    starts = [e for e in events if e[0] == "start"]
    completes = [e for e in events if e[0] == "complete"]
    assert [e[1] for e in starts] == [0, 1]
    assert [e[1] for e in completes] == [0, 1]
    assert result.success
    assert result.agent_results["a"].output == "pre"


def test_layer_barrier_downstream_sees_upstream_output(tmp_path, monkeypatch):
    """层屏障 + 上游聚合：第 1 层 worker 通过 branch_state 拿到的
    agent_results 里能看到第 0 层的输出（_gather_upstream 读共享 dict）。"""
    captured: dict[str, str] = {}
    rt = SwarmRuntime()
    orig_execute_agent = rt._execute_agent

    def spy(call, ws, task_, upstream):
        captured.update(upstream or {})
        return orig_execute_agent(call, ws, task_, upstream)

    monkeypatch.setattr(rt, "_execute_agent", spy)
    result = rt.execute(_preset({"a": [], "b": ["a"]}), tmp_path, "task")
    assert result.success
    # b 的 upstream 聚合里有 a 的 stub 输出
    assert "a" in captured
    assert "[stub]" in captured["a"]
