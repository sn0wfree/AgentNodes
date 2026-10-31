# 规划：retry 空转修复（R-A）+ core/workflow 定义系统迁引擎（R-B）（2026-10-31）

> 输入：深度统一 B1 的遗留项两项，用户已拍板两者都做。
> 前置勘查：`core/swarm/runtime.py`（split 过滤 / `_apply_branch_action`）、
> `core/workflow/`（executor 段循环 / node_types / definition / store）、
> `api/routers/workflow.py`（22 个端点）、24 个测试文件 280 个用例。
> 验收门沿用：行为等价 / 测试不减 / 失败集不扩大 + `cov_gate --min 90`。

---

## 0. 两个任务的性质不同（决定顺序与刀法）

| | R-A retry 空转 | R-B workflow 迁引擎 |
|---|---|---|
| 性质 | **语义缺陷修复**（行为会变，且是变好） | **架构迁移**（行为必须逐字节等价） |
| 半径 | `runtime.py` 两处（split 过滤 + retry 动作）+ 测试 | 6 模块 / 22 端点 / 280 用例 |
| 状态机 | 无（层内过滤条件） | **有**（段切分 + 审批门 + replan 环 + 断点续跑） |
| 工期 | 0.5d | 3–4d |
| 风险 | 低（局部） | 中高（有状态编排 + 审批暂停恢复） |

**顺序：R-A 先做**（独立可合入、无冲突），再做 R-B。

---

## 1. R-A · retry 空转修复（0.5d）

### 1.1 缺陷确认（代码级）

`_apply_branch_action("retry", target, remaining, ...)` 把 `target` 追加回
`remaining[0]`，但 `_make_split_node` 的待跑集合是：

```python
pending = [aid for aid in layer if aid not in state["agent_results"] and ...]
```

被 retry 的 agent **必定已有结果**（它在之前层跑过，否则 retry 前置条件
`target in layer_results` 不成立）→ 永远被过滤掉 → 空转。
B1 已按「行为等价」原样保留并在设计文档 §6 注明，现在修。

### 1.2 修复方案（两条路，选 a）

| 方案 | 做法 | 评价 |
|---|---|---|
| **a. 显式重跑标记（推荐）** | join 节点应用 retry 时把 target 记入 `state["retry_queue"]`（集合）；split 的 pending 过滤改为「已有结果 **且不在 retry_queue**」；该 agent 真正重跑时从 retry_queue 移除 | 语义清晰、可观测（state 里能看到待重跑集）、不影响 resume |
| b. 直接删除已有结果 | join 里 `del agent_results[target]` | 简单但危险：下游已读过的上游输出会消失，`_gather_upstream` 后续可能拿不到；且影响 final_output |

**选 a**。附加语义决定：
- **重跑次数上限**：同一 target 在同一 run 内最多重跑 N 次（默认 1，可配
  `exec.branch_max_retries`），防止「条件恒真 → 无限重跑」；
- **结果覆盖**：重跑产生的新 AgentResult 覆盖旧条目（`agent_results[target]`
  赋值），但插入序保留首次位置（不改变 final_output 的「最后完成」语义）；
- **hook 交付**：重跑也走 fanout → 桥接，`on_agent_complete` 会再次收到
  target（符合「真的又跑了一次」的事实）；
- **可观测**：重跑触发/完成各发一条日志 + sink 事件 `branch.retried`
  （kind 名引擎无关，由 SwarmRuntime sink 转 hook 日志）。

### 1.3 验收

| 项 | 要求 |
|---|---|
| 新增测试 | ① retry 真重跑（调用计数 = 2）；② 重跑结果覆盖旧值；③ 重跑上限生效（第 2 次条件命中不再跑）；④ retry 后下游看到的是新输出；⑤ skip 语义不受影响 |
| 既有测试 | `test_swarm_branches.py` 23 项中 `_apply_branch_action` 单元断言需同步（retry 后新增 retry_queue 语义）——**允许改这两个单测**（它们锁的是缺陷行为），其余全绿 |
| 门禁 | c1_gate 失败集不扩大；cov_gate 三档 ≥90 |

---

## 2. R-B · core/workflow 定义系统迁引擎（3–4d）

### 2.1 目标与边界

**目标**：`WorkflowRunner`（段循环执行器）的自研调度退役，与 B1 同模式——
`WorkflowDefinition` 编译为 GraphEngine 图，`dispatch_node` 作为节点执行体。
**不动的**：`WorkflowDefinition` / `WorkflowNode` 数据模型、JSON 持久化格式、
22 个 REST 端点契约、`WorkflowStore` 表结构、`node_types.dispatch_node` 五种
节点类型的内部实现、validator/builtin/definition 的加载逻辑。

**为什么不像 B1 那样「一次等价」**：本系统有**有状态编排**——
- 段切分（approval 节点是图的切割点，不在段内）；
- 审批门暂停（`status=awaiting`，`approve()` 从暂停处续跑，需跨 HTTP 请求
  保活 runner 实例）；
- replan 环（evaluator 判 replan → 重跑 planner → 执行新 plan_* 步骤，
  最多 max_segments 次）；
- 节点级重试（`exec.node_max_retries` + 永久错误分类）。

这些在 GraphEngine 上都能表达（条件边 + 环 + GraphInterrupt + run(start=)），
但**图的形状随 replan 动态变化**，需要「编译 + 重建 + 恢复」三段式。

### 2.2 目标形状（编译策略）

```
段 = 子图（segment_0 … segment_k），段间用 approval 节点连接：
  segment_i 内：节点按层 → llm_agent/planner/evaluator/python/tool 各自成节点
  approval 门：独立的 gate_i 节点，抛 GraphInterrupt("approval_required")
               → run 返回 state（含 __interrupt__），runner 置 awaiting 并落 store
  approve(True)：run(state, start=f"segment_{i+1}")   ← 与 B1/study 同一原语
  approve(False)：进入 replan 子图（planner → plan_* 动态节点 → evaluator）
  evaluator verdict=replan：条件边回 planner 子图（图内环，max_hops 兜底
                            + replan_count 业务上限）
  planner 展开的 plan_* 步骤：**动态加节点**——GraphEngine 目前不支持
                            run 期间加节点 → 见决定 A
```

**决定 A · planner 动态步骤的两种实现**

| 方案 | 做法 | 评价 |
|---|---|---|
| **a1. 子图内联（推荐）** | plan_* 步骤作为「一个 fanout/链 节点」的**内部循环**执行：单个 `plan_steps` 节点，其 fn 内部按 plan 的 depends_on 拓扑序逐层调 `dispatch_node` | 不需要引擎加节点 API；plan 规模小（max_steps 6）；与现状的 `_run_plan_steps` 逐层调用等价 |
| a2. 引擎动态加节点 | 给 GraphEngine 加 `extend(nodes)` + 重编译 | 改引擎核心，风险高；plan 步骤不值得 |

**选 a1**：`plan_steps` 节点 fn = 现 `_run_plan_steps` 原样搬入（含内部拓扑层、
`pre_completed` 跳过、失败计数、事件发射）。动态性是「节点内部循环」而非
「动态拓扑」——这与 study 的 phase 内部逻辑同构。

**决定 B · 审批门的暂停/恢复**

现状：`approve()` 在**同一 runner 实例**上继续（runner 存在 `_active_runners`
里跨请求保活）。引擎化后：
- gate 节点抛 `GraphInterrupt("approval_required", data={node_id, segment_idx})`
  → `run()` 返回带 `__interrupt__` 的 state；
- runner 把 **state 序列化入 WorkflowStore**（新表列或复用
  `params_snapshot`/`run_node_outputs`）→ 进程重启也能恢复（现状不能，
  因为 runner 实例在内存）；
- `approve()` → 反序列化 state → pop `__interrupt__` → `run(state, start=下一段)`
  → 逐步持久化。
- **这是净增益**：现状 runner 实例丢失即无法审批续跑（进程重启 = 卡死在
  awaiting），引擎化后 state 在库里。

**决定 C · 事件流契约不变**

现状 `_emit(event_type, data)` → `store.append_event` + `emit_event`（SSE）。
引擎化后 sink 桥接：`node.started/node.completed` → `node_started/node_completed`
事件（名字映射表固定）；`segment_started/segment_completed` 在段首尾节点发；
`awaiting_approval/run_completed/run_failed` 保持不变。**22 个端点和前端
依赖事件名，映射表要逐条比对现有测试**。

**决定 D · `loop_factory` / `llm_config` / `params_override` 透传**

`NodeContext` 原样保留（`dispatch_node` 签名不变），只是构造点从"段循环里"
移到"节点 fn 闭包里"。零语义变化。

### 2.3 分刀（每刀独立可合入）

| 刀 | 内容 | 工期 | 验收 |
|---|---|---|---|
| **R-B1** | 引入 `WorkflowGraphCompiler`（新模块，不改 executor）：`WorkflowDefinition → GraphEngine`（无审批、无 replan 的最简路径），并用它跑 **test_workflow_e2e / node_contracts** 这两组作为影子验证（parallel run 对比结果，不替换） | 1d | 影子对比：同一 definition + 同 fake dispatch → 结果逐字段一致 |
| **R-B2** | `WorkflowRunner` 主路径切到编译器（含审批门 GraphInterrupt + state 持久化 + approve 恢复）；保留旧 `_run_loop` 作为 `legacy_runner` 开关 | 1.5d | 280 用例中除 segment_loop 内部实现断言外全绿；审批 e2e 通过 |
| **R-B3** | replan 环（evaluator → planner 条件边）+ plan_steps 内联节点 + 删旧 `_run_loop`/`_run_segment`/`_run_plan_steps` 段循环；新增「重启后 approve 续跑」测试 | 1d | 全量绿；库存只增不减；失败集不扩大 |
| **R-B4** | 清尾：架构图 webui workflow 格翻 ✅；交付总结；cov_gate | 0.5d | 四道门全绿 |

**关键风险与对策**

| 风险 | 对策 |
|---|---|
| 事件名映射错位（前端依赖） | R-B1 影子模式下 dump 两边事件序列做逐条 diff，差异即失败 |
| 审批续跑跨进程 | R-B2 的 state 持久化是净增益，但要加「序列化往返」测试（含 AgentResult） |
| replan 图内环 + max_hops 冲突 | `max_hops` 按 `(段数+plan 步数) × (max_segments+1) × 2` 动态计算；业务上限仍在 replan_count |
| 段循环里的「连续 2 步失败即停」规则 | 搬到 join 节点 / plan_steps 节点内部，行为不变（保持原判定位置） |
| 旧 `legacy_runner` 开关长期滞留 | R-B3 删除，不允许长期双轨（less-is-more）；若 R-B2 出现回归才临时启用 |

### 2.4 明确不做

- 不改 `WorkflowDefinition` JSON schema / 前端协议（端点全保）；
- 不把 workflow 的 approval 语义并进 Gateway 的 mailbox/审批（那是另一层，
  属后续「统一审批面」话题）；
- 不迁 `core/workflow/dag.py`（纯拓扑工具，被 goal/swarm 共用，保持中立）。

---

## 3. 总顺序与工期

```
R-A（0.5d，先合）
  └→ R-B1 影子编译（1d）
       └→ R-B2 主路径切换（1.5d）
            └→ R-B3 replan + 删旧循环（1d）
                 └→ R-B4 清尾（0.5d）
合计 ≈ 4.5d 单人
```

R-A 与 R-B 无文件冲突（R-A 只碰 `swarm/runtime.py`；R-B 只碰 `core/workflow/`
与 `api/routers/workflow.py`），R-A 可先独立合入。

## 4. 完成后全系统执行核分布

| 路径 | 执行核 |
|---|---|
| chat turn | GraphEngine |
| study 轮 | GraphEngine |
| fanout preset | GraphEngine |
| goal / CLI / MCP swarm | GraphEngine（B1 编译器） |
| **webui workflow（R-B）** | **GraphEngine（R-B 编译器）** |
| 恢复原语 | 全部 `__interrupt__` + state + `run(start=)` |

## 5. 一句话

> R-A 修一个「文档承诺了但从未生效」的语义缺陷（retry 空转），
> 用显式 retry_queue 让重跑真正发生；R-B 把最后一个自研调度器
> （workflow 段循环）按 B1 同模式迁到引擎——但它有状态，所以要分四刀、
> 先影子后切换，并把审批续跑的 state 落库（顺带修掉「进程重启即卡死」）。
