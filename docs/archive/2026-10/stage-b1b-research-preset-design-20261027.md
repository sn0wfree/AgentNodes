# B1-b 设计 — research preset（phase_engine → 阶段图）— 2026-10-27

> 依据：`replan-v2-capability-dependencies-20261026.md` §4「B1-b：research preset，
> 验收 = study 走新路径能真正执行轮次」。前置 B1-a 已交付（GraphEngine 条件边/环/中断）。

---

## 1. 范围切分：轮次级（本阶段） vs 提交级（3c）

study 执行链有两层，B1-b 只做第一层：

| 层 | 内容 | 归属 |
|---|---|---|
| **轮次级** | 一轮研究内部：researcher → novelty → execution → evaluation 用什么执行器跑 | **B1-b**（本阶段） |
| 提交级 | scheduler 提交 → Gateway mailbox → 控制器 → AutoresearchRunner 驱动多轮 | 3c（下一阶段） |

切分理由：BranchContext（study/路径/state/graph 等运行时上下文）只在
`run_round_phases` 前言里才构建得出来——preset 的节点工厂需要它做闭包，
所以 preset 天然是「轮次级」对象。提交级的引擎选择（AUTOMATION 工单 →
study runner）等 B1-b 验收后在 3c 接线。

## 2. 落位：preset 放 study 域，不放 runtimes

图引擎（runtimes）是平台件；研究阶段图是业务件。分层规则「域 → 平台，
绝不反向」决定了：

```
core/runtimes/            GraphEngine + 通用节点（llm/react）——不认识 study
core/study/graph_preset.py  build_research_engine(ctx) + run_phases_via_graph(ctx)
                            ——import GraphEngine，闭包里包 phase 函数
```

「preset」在 runtimes 层只是一个约定（一个返回配置好 GraphEngine 的函数），
具体的研究 preset 属于域。fanout preset（未来）同理。

## 3. 图拓扑：旧 phases 分支的直译

`_run_phases_branch` 的每一步原样搬进节点（**不是重写**——直接 import
phase_engine 的 `_novelty_gate` / `_capture_round_claims` /
`_run_execution_with_parse_retry`，行为等价由构造保证）：

| 旧代码 | 图元素 | 说明 |
|---|---|---|
| run_researcher_phase + emit + 非dict防御 | 节点 `researcher` | |
| `_novelty_gate` 判定 | 节点 `novelty` + **条件边** | novel → claims；dup → END |
| `_capture_round_claims`（回测前存证） | 节点 `claims` | 顺序保持：dup 时不存证 |
| `_run_execution_with_parse_retry`（内部退避重试） | 节点 `execution` | 重试是节点内细节，不进图边 |
| run_evaluation_phase + emit | 节点 `evaluation` | |
| 返回 abort dict / RoundOutcome | 适配器 `run_phases_via_graph` | 输出契约与旧分支逐字段一致 |

**abort 语义**：novelty 拒绝是**正常业务终态**（归档 + 事件 + 轮次跳过），
不是中断——走「数据标记 + 条件边 → END」（`state["novelty_ok"]=False`），
不用 GraphInterrupt。中断保留给 HITL/预算这类「异常暂停」。
parse 重试环同理留在节点内部（它是同阶段重入，不是阶段间跳转）。

域事件（study_phase）仍走 `runner._emit`（与旧路径同源同序）；
引擎的 node.started/completed 是平台观测事件，默认不接（sink=None），
避免双重事件。

## 4. 灰度门（第三面门，默认关）

`QNODES_RESEARCH_GRAPH`（`research_graph_enabled()`，默认关）——
「引擎路径」门，与两个入口门正交：入口门管「请求从哪进来」，
这个门管「phases 引擎分支内部用旧函数序还是阶段图」。
接线点在 `run_round_phases` 的分支解析处（`_resolve_branch_fn`）：

```
engine=phases + 门关 → _run_phases_branch（旧行为，零变化）
engine=phases + 门开 → run_phases_via_graph（新路径）
engine=langgraph    → 不受影响（langgraph 分支到阶段 4 清理时退役）
```

3c 用 scheduler 全链路验证后翻默认；翻默认前旧行为是唯一默认。

## 5. 执行等价验证（replan v2 §5 方法论的落地）

**等价由构造保证 + 测试钉死**：

1. 构造保证：preset 直接 import phase_engine 的门/存证/重试函数，
   阶段序、参数、emit 时点逐行对照旧分支——唯一的「新代码」是编排本身；
2. 测试钉死：monkeypatch autoresearch 的三个 phase 函数（两条路径都在
   函数体内延迟 import，同一个 fake 同时命中两边），同一 BranchContext
   分别喂旧分支与图适配器，**RoundOutcome 逐字段相等**；abort 场景下
   abort dict 相等且存证未发生、evaluation 未执行、事件序列相等。

「study 走新路径能真正执行轮次」在本阶段 = 上述等价证明；
全链路（scheduler → mailbox → 真实轮次）留到 3c 验收。

## 6. 明确不做（YAGNI）

- 提交级接线 / engine_factory 按 kind 分派：3c；
- preset 注册表：一个 preset 不需要注册表，函数即契约；
- langgraph 分支迁移：阶段 4 退役，不迁移；
- fanout preset：多 subagent 落地时再加；
- JSON 声明式图数据：等第二个 preset 出现再抽象（rule of three）。
