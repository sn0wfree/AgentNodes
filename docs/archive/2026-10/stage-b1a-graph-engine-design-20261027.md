# B1-a 设计 — GraphEngine 阶段图（条件边 / 环 / 中断）— 2026-10-27

> 依据：`replan-v2-capability-dependencies-20261026.md` §4「B1-a：GraphEngine 支持阶段图
> （多节点 + 条件边 + 中断），验收 = 跑通一个两阶段图」。
> 这是 B1-b（research preset）与 3c（scheduler 迁移）的前置能力。

---

## 1. 为什么需要（来自 phase_engine 的真实形态）

研究轮次的阶段图不是直线，旧 phase_engine 里已经有三种非线性形态：

| 旧代码里的形态 | 位置 | 图语义 |
|---|---|---|
| novelty 拒绝 → 跳过整轮 | `_run_phases_branch` 早退 `{"aborted": True}` | **条件边 → END** |
| 回测 parse_failed → 退避重试 | `_run_execution_with_parse_retry` 循环 | **环（回边）** |
| HITL 暂停 / 图中止 | `paused_for_approval` / `aborted` | **中断（带状态返回）** |

阶段 1a 的 GraphEngine 只有「add_node 线性串联」，三者都表达不了。

## 2. API（对现有调用方零破坏）

```python
engine = GraphEngine(sink=..., max_hops=50)

engine.add_node(name, fn)                 # 注册节点；默认边 = 指向下一个注册节点
engine.set_entry(name)                    # 显式入口（默认 = 第一个注册节点）
engine.add_edge(a, b)                     # 固定边，覆盖 a 的默认边
engine.add_conditional_edge(a, router, mapping)  # 条件边，覆盖 a 的默认边
engine.run(state, *, start=None)          # start: 指定入口节点（恢复用）
```

- **默认直线语义**：只调 `add_node` 的代码（1a 全部存量：SessionController 默认引擎、
  9 个既有测试）行为不变——第 i 个节点默认连第 i+1 个，末节点连 END。
- **声明即覆盖**：对某节点调用 `add_edge` / `add_conditional_edge` 后，其默认直线边被
  替换。每个节点只有一条出边（固定或条件），研究流程够用；langgraph 的「同源多出边」
  不开放（fanout 走 fanout preset，不在 B1-a）。
- `router(state) -> key`；`mapping: dict[key, 节点名 | END]`；mapping 省略时 router
  直接返回节点名或 END。router 返回未知 key → 立即抛错（fail-loud）。
- 出边引用未注册节点 → `add_edge` 时即抛错（准入校验，不等到 run）。

## 3. 中断语义（GraphInterrupt，协作式、无检查点）

```python
class GraphInterrupt(Exception):
    def __init__(self, reason: str, data: dict | None = None): ...
```

- 任何节点抛 `GraphInterrupt` → 引擎捕获：写 `state["__interrupt__"] = {node, reason, data}`，
  发 `graph.interrupted` 事件，该节点**不发** `node.completed`（与节点抛普通异常的事件
  语义一致：started 有、completed 无）。
- **全边守卫**：引擎内部把所有出边（含固定边）实现为条件边，路由前先查
  `state["__interrupt__"]`——命中即 END。中断在**节点边界**生效，已入 state 的部分结果
  不丢（这是相对「让异常直接冒出 invoke、state 全丢」的关键差别）。
- `run()` 返回带 `__interrupt__` 标记的最终 state；普通异常仍然冒出（引擎崩溃 ≠ 中断）。
- **恢复**：`engine.run(state, start="节点名")`，调用方先 `state.pop("__interrupt__")`。
  引擎不自动解除中断标记（防止静默重跑）；HITL 的完整暂停/恢复编排属 B1-b。
- **max_hops 防环失控**：引擎自带跃点计数（每个节点执行 +1），超过 `max_hops` 以
  `GraphInterrupt("max_hops")` 协作停止；langgraph `recursion_limit = max_hops + 5`
  只作硬兜底。

## 4. 保留字与事件

| 项 | 值 |
|---|---|
| state 保留键 | `__interrupt__`（节点 fn 不得写入该键） |
| 新事件 | `graph.interrupted {node, reason, data?}` |
| 既有事件 | `node.started` / `node.completed {node, seconds}` 不变 |

## 5. 明确不做（YAGNI）

- 子图节点（graph node）：fanout/research 需要时再加；
- 同源多出边、并行分支：fanout preset 的活；
- 检查点 / langgraph interrupt() 原生中断：需要 checkpointer 与持久化耦合，
  B1-b 若 HITL 需要再评估；
- 图数据 (JSON) 声明式构建：B1-b research preset 才引入。

## 6. 验收（两阶段图 + 全部既有行为不回退）

1. 两阶段图 `gen → judge`，judge 条件路由：ok → END / retry → gen（环），计数器控制
   第二次通过——gen 恰好执行 2 次；
2. 条件边 → END（novelty 形态）：下游节点从未执行；
3. 节点抛 `GraphInterrupt`：state 带原因返回、`graph.interrupted` 事件发出、
   下游不跑、部分结果保留；
4. `max_hops` 协作停止：状态保留 + reason=max_hops；
5. `start=` 恢复：pop 标记后从指定节点续跑；
6. 既有 9 个测试（线性语义、事件顺序、异常冒出、输入不被变异、引擎可复用）全绿，
   SessionController / llm / react 节点路径不回退。
