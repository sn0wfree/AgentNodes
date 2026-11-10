"""fanout preset（F3）—— 多 subagent 并行的通用执行形状。

设计：docs/archive/2026-10/stage-f3-fanout-preset-design-20261027.md。

落位说明：与 research preset（study 域，闭包持 BranchContext）相反，
fanout 是**纯平台件**——只依赖 GraphEngine / AgentLoop（V3-c 起唯一
执行引擎）/ RunBudget，不认识 study、不 import 业务模块。任何域都能
用它表达「拆分 → N 路并行 → 汇合」。

图形态（D1 复合节点，仍是两个普通节点 + 一条边）：

    split(fn) →[fanout 复合]→ join(fn)

每条分支是一个**独立子代理**：持自己的预算切片跑一次 AgentLoop 循环，
消息隔离、结果按 ordinal 保序回填到 `fanout_results`。

**V3-c 预算切片映射**（原 react ``RunBudget`` 注入的等价）：
- 入口：``budget.plan_call()``——0 切片（预标 exhausted）即连坐中断，
  不浪费一次 LLM 调用（INV-9：任何 LLM 调用前必须过 plan_call）；
- 运行中：切片 limits 注入 ``BudgetStopStep._limits``（三维
  tokens/cost/time，reason 格式 ``budget:*`` 与 react 同源）；
- 收尾：消耗 ``record`` 回切片对象——父线程 ``absorb`` 后回流父账本
  （settle 语义，与 react「同一实例直接记账」等价）。
"""
from __future__ import annotations

import queue
from typing import Any, Callable

from agentnodes.kernel.runtimes.graph_engine import (
    END,
    GraphEngine,
    GraphInterrupt,
)

EventSink = Callable[[str, dict], None]

FAILURE_POLICIES = ("abort_turn", "continue")


def build_fanout_preset(
    client: Any,
    registry: Any,
    *,
    split: Callable[[dict], list],
    system: str | None = None,
    max_iterations: int = 8,
    max_parallel: int = 8,
    failure_policy: str = "abort_turn",
    join_fn: Callable[[dict], dict] | None = None,
    sink: EventSink | None = None,
) -> GraphEngine:
    """构造 fanout 预设图（拆分 → N 路 react 子代理 → 汇合）。

    split: 父 state -> 分支载荷列表（每个载荷是一条独立子任务）。
    failure_policy: "abort_turn"（默认，D2：任一分支失败整轮标红）
                    / "continue"（跳过坏分支，其余照常合并）。
    join_fn: 自定义合并；省略走默认合并（读 fanout_results）。
    """
    if failure_policy not in FAILURE_POLICIES:
        raise ValueError(
            f"failure_policy 必须是 {FAILURE_POLICIES} 之一，收到 {failure_policy!r}"
        )
    emit = sink or (lambda kind, data: None)
    branch_events: "queue.Queue[tuple[str, dict]]" = queue.Queue()

    def branch_worker(payload: Any, branch_state: dict) -> dict:
        branch = branch_state["branch"]
        budget = branch.get("budget")

        # INV-9：分支入口过 plan_call——0 切片（预标 exhausted）直接连坐，
        # 不起 AgentLoop、不发一次 LLM 调用。
        if budget is not None:
            allowed, reason = budget.plan_call()
            if not allowed:
                raise GraphInterrupt(
                    "branch_budget_exhausted",
                    {"branch_id": branch["id"], "reason": reason or "exhausted"},
                )

        from agentnodes.kernel.agent.loop import AgentLoop
        from agentnodes.kernel.agent.runtime.sync_bridge import _run_coro_in_sync
        from agentnodes.foundation.llm.config import LLMConfig
        from agentnodes.kernel.runtimes.nodes.agent_loop_node import (
            make_loop_event_sink,
            wrap_async_capable_client,
        )

        def branch_sink(kind: str, data: dict) -> None:
            # AgentLoop 事件不带 run_id（loop 级发射）——分支身份在这里
            # 注入，join 回流后消费方可按 branch_id 归因。
            branch_events.put((kind, {"run_id": branch["id"], **dict(data)}))

        base_cfg = getattr(client, "config", None) or LLMConfig(api_key="")
        loop = AgentLoop(
            config=base_cfg,
            registry=registry,
            system_prompt=system,
            max_iterations=max_iterations,
            # 分支是独立子代理：无 goal/memory 注入、无审批闸
            # （web fanout 没有 loop 级提问通道；无进展按 continue 跑满）。
            enable_goal_injection=False,
            enable_memory_injection=False,
            injectors=[],
            approval_timeout=30.0,
            approval_on_timeout="continue",
            stream_mode=False,
            on_event=make_loop_event_sink(branch_sink),
        )
        loop.client = wrap_async_capable_client(client)
        # F2 预算切片：三维上限注入 BudgetStopStep（运行中判定）；
        # 消耗记账在 run 结束后回写切片对象（下方 record）。
        if budget is not None:
            stop_step = getattr(loop._strategy, "stop", None)
            if stop_step is not None:
                stop_step._limits = budget.limits

        result = _run_coro_in_sync(loop.arun(str(payload)))
        if result.error:
            # react 语义：分支业务失败不连坐（error 条目进 fanout_results，
            # 其余照跑）——恢复异常冒泡形状，GraphEngine 的 run_one 捕获。
            raise RuntimeError(result.error)

        usage = {
            "tokens": int(getattr(loop, "_budget_tokens", 0) or 0),
            "prompt_tokens": int(getattr(loop, "_budget_prompt_tokens", 0) or 0),
            "completion_tokens": int(getattr(loop, "_budget_completion_tokens", 0) or 0),
            "cost_usd": float(getattr(loop, "_budget_cost_usd", 0.0) or 0.0),
            "iterations": result.iterations,
        }
        if budget is not None:
            # settle：消耗记回切片（父线程 absorb 后回流父账本）。
            budget.record({
                "total_tokens": usage["tokens"],
                "prompt_tokens": usage["prompt_tokens"],
                "completion_tokens": usage["completion_tokens"],
            })
            if budget.exhausted:
                # 切片耗尽 = 系统档（预算），按 F1 语义连坐整棵 fanout
                raise GraphInterrupt(
                    "branch_budget_exhausted",
                    {"branch_id": branch["id"], "reason": budget.reason or "exhausted"},
                )
        stop_reason = result.stop_reason or result.finished_reason or "completed"
        return {
            "answer": result.answer,
            "stop_reason": stop_reason,
            "usage": usage,
        }

    def split_node(state: dict) -> dict:
        branches = split(dict(state))
        if not isinstance(branches, list):
            raise ValueError("fanout preset: split 必须返回 list")
        # 把分支线程的事件挂到 state，join 后统一转发
        state = {**state, "branches": branches, "_branch_events": branch_events}
        return state

    def join_node(state: dict) -> dict:
        results = list(state.get("fanout_results") or [])
        ok = [r for r in results if r["status"] == "ok"]
        failed = [r for r in results if r["status"] == "error"]

        # ── 分支事件回流（父线程）──
        drained = 0
        while True:
            try:
                kind, data = branch_events.get_nowait()
            except queue.Empty:
                break
            emit(kind, data)
            drained += 1

        # ── steer 承载点（D3）：join 后注入，供后续节点消费 ──
        steers = list(state.get("steer_after_join") or [])
        messages = list(state.get("messages") or [])
        if steers:
            messages = messages + [
                {"role": "user", "content": s} for s in steers
            ]
            emit("steer.injected_after_join", {"count": len(steers)})

        merged = {
            **state,
            "messages": messages,
            "steer_after_join": [],
            "fanout_answers": [r["result"].get("answer", "") for r in ok],
            "fanout_branch_events": drained,
        }
        if failed and failure_policy == "abort_turn":
            # D2 默认：任一分支业务失败 → 整轮标红（调用方看 fanout_ok）
            merged.update({
                "fanout_ok": False,
                "aborted": True,
                "reason": "branch_failed",
                "fanout_errors": [
                    {"branch_id": r["branch_id"], "error": r["error"]} for r in failed
                ],
            })
        else:
            errors = [
                {"branch_id": r["branch_id"], "error": r["error"]} for r in failed
            ]
            merged["fanout_ok"] = not errors
            if errors:
                merged["fanout_errors"] = errors
        merged.pop("_branch_events", None)
        if join_fn is not None:
            merged = join_fn(merged)
        return merged

    engine = GraphEngine(sink=emit)
    engine.add_node("split", split_node)
    engine.add_node("join", join_node)
    engine.add_fanout("split", branch_worker, "join", max_parallel=max_parallel)
    engine.add_edge("join", END)
    return engine


__all__ = ["build_fanout_preset", "FAILURE_POLICIES"]
