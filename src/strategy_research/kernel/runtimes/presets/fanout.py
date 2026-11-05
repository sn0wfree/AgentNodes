"""fanout preset（F3）—— 多 subagent 并行的通用执行形状。

设计：docs/archive/2026-10/stage-f3-fanout-preset-design-20261027.md。

落位说明：与 research preset（study 域，闭包持 BranchContext）相反，
fanout 是**纯平台件**——只依赖 GraphEngine / react 节点 / RunBudget，
不认识 study、不 import 业务模块。任何域都能用它表达「拆分 → N 路并行 → 汇合」。

图形态（D1 复合节点，仍是两个普通节点 + 一条边）：

    split(fn) →[fanout 复合]→ join(fn)

每条分支是一个**独立子代理**：持自己的预算切片跑一次 react 循环，
消息隔离、结果按 ordinal 保序回填到 `fanout_results`。
"""
from __future__ import annotations

import queue
from typing import Any, Callable

from strategy_research.kernel.runtimes.graph_engine import (
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

    def branch_sink(kind: str, data: dict) -> None:
        """分支线程只入队；父线程 join 后统一转发（sink 不跨线程）。"""
        branch_events.put((kind, dict(data)))

    def branch_worker(payload: Any, branch_state: dict) -> dict:
        from strategy_research.kernel.runtimes.nodes.react import (
            ReactConfig,
            make_react_node,
        )

        branch = branch_state["branch"]
        budget = branch.get("budget")
        node = make_react_node(
            client, registry, sink=branch_sink,
            config=ReactConfig(
                max_iterations=max_iterations, system=system, budget=budget,
            ),
        )
        out = node({
            "messages": [{"role": "user", "content": str(payload)}],
            "run_id": branch["id"],
        })
        stop_reason = str(out.get("stop_reason", ""))
        if stop_reason.startswith("budget:"):
            # 切片耗尽 = 系统档（预算），按 F1 语义连坐整棵 fanout
            raise GraphInterrupt(
                "branch_budget_exhausted",
                {"branch_id": branch["id"], "reason": stop_reason},
            )
        return {
            "answer": out.get("answer", ""),
            "stop_reason": stop_reason,
            "usage": out.get("usage", {}),
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
