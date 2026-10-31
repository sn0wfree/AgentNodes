# 规划：retry 空转修复（R-A）+ core/workflow 定义系统迁引擎（R-B）（2026-10-31）

> 输入：深度统一 B1 的遗留项两项，用户已拍板两者都做。
> 前置勘查：`core/swarm/runtime.py`（split 过滤 / `_apply_branch_action`）、
> `core/workflow/`（executor 段循环 / node_types / definition / store）、
> `api/routers/workflow.py`（22 个端点）、24 个测试文件。
> 验收门沿用：行为等价 / 测试不减 / 失败集不扩大 + `cov_gate --min 90`。

> **修订（2026-10-31 二次评估）**：初版有三处判断经复核后修正——
> ① R-B 的「state 落库」方案不成立（WorkflowStore 无 output 列，且引擎
> state 含不可序列化对象）→ 改为「run 级重建」；② R-A 优先级下调
> （全库零 branches 配置，属"有承诺无用户"）；③ R-B3 工作量上调
> （段循环测试 20 例紧密耦合待删私有方法）。详见 §5「二次评估勘误」。

---

## 0. 两个任务的性质不同（决定顺序与刀法）

| | R-A retry 空转 | R-B workflow 迁引擎 |
|---|---|---|
| 性质 | **语义缺陷修复**（行为会变，且是变好） | **架构迁移**（行为必须逐字节等价） |
| 半径 | `runtime.py` 两处（split 过滤 + retry 动作）+ 测试 | 6 模块 / 22 端点 / 230 用例 |
| 状态机 | 无（层内过滤条件） | **有**（段切分 + 审批门 + replan 环 + 断点续跑） |
| 用户 | **零**（全库无 branches 配置） | webui workflow 特性（4 个出厂模板） |
| 工期 | 0.5d | **5d**（修订：原 3–4d） |
| 风险 | 低（局部） | 中高（有状态编排 + 审批暂停恢复） |

**顺序（修订）：R-B1 先行，R-A 降为 P2 并行插入。**
理由：R-B 是架构主线（消灭最后一个自研调度器），R-A 是"有承诺无用户"
的旁支；先做主线能在影子编译阶段尽早暴露真实障碍。

---

## 1. R-A · retry 空转修复（0.5d，P2）

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

**决定 B · 审批门的暂停/恢复（修订：run 级重建，不落 state）**

现状：`approve()` 在**同一 runner 实例**上继续（runner 存在
`_get_run_registry()` 里跨请求保活，routers/workflow.py:635-637：实例不在
即 404）。**全库无任何 resume 路径**——进程重启后 awaiting 的 run 永久卡死。

初版设想「state 序列化入 WorkflowStore」经复核**不成立**：

| 障碍 | 实测证据 |
|---|---|
| store 存不下执行产物 | `node_outputs` 列只有 `status/summary/artifacts/metrics/error/elapsed_s`——**没有 `output`**（store.py:253-266）；下游节点靠 `AgentResult.output` 做 upstream |
| 引擎 state 不可序列化 | state 含 `AgentResult` 对象、节点闭包（捕获 `NodeContext`/`loop_factory`） |
| 需要的是极小集合 | 审批恢复真正只缺 `segment_idx` + `pre_completed` 的 output |

**修订方案 · 两层**：

1. **前置小刀 R-B0（0.5d）**：`node_outputs` 增列 `output TEXT`（SQLite
   `ALTER TABLE ADD COLUMN` 是 O(1) 且向后兼容），`save_node_output` 写入、
   `list_node_outputs` 读出。
2. **R-B2 实现 `resume_from_store(run_id)`**：从 `runs.segment_idx` +
   `node_outputs` 重建 `pre_completed: dict[str, AgentResult]` → 编译图 →
   `run(state, start=f"segment_{n}")`。**不引入任何序列化格式**，只用现有
   关系数据；`approve()` 走同一函数。

**净增益保留**：进程重启后 approve 仍可续跑（现状 404 卡死）。但表述改为
「run 级重建」，而非「state 落库」。

**决定 C · 事件流契约不变**

现状 `_emit(event_type, data)` → `store.append_event` + `emit_event`（SSE）。
引擎化后 sink 桥接：`node.started/node.completed` → `node_started/node_completed`
事件（名字映射表固定）；`segment_started/segment_completed` 在段首尾节点发；
`awaiting_approval/run_completed/run_failed` 保持不变。**22 个端点和前端
依赖事件名，映射表要逐条比对现有测试**。

**决定 D · `loop_factory` / `llm_config` / `params_override` 透传**

`NodeContext` 原样保留（`dispatch_node` 签名不变），只是构造点从"段循环里"
移到"节点 fn 闭包里"。零语义变化。

### 2.3 分刀（每刀独立可合入；工时已按二次评估修订）

| 刀 | 内容 | 工期 | 验收 |
|---|---|---|---|
| **R-B0** | `node_outputs` 增列 `output TEXT`（`ALTER TABLE ADD COLUMN`，向后兼容）+ save/read 接线 | 0.5d | 新旧库均可读写；列存得下 `AgentResult.output` |
| **R-B1** | 引入 `WorkflowGraphCompiler`（新模块，不改 executor）：`WorkflowDefinition → GraphEngine`（无审批、无 replan 的最简路径），用 **test_workflow_e2e / node_contracts** 做**影子验证**（parallel run 对比，不替换） | 1d | 影子对比：同 definition + 同 fake dispatch → 结果逐字段一致；事件序列逐条一致 |
| **R-B2** | 主路径切编译器：段子图 + approval gate 抛 `GraphInterrupt` + `resume_from_store(run_id)`（从 store 重建 pre_completed）；保留旧 `_run_loop` 为临时开关 | 1.5d | 230 例中除 segment_loop 内部实现断言外全绿；审批 e2e 通过；新增「跨实例 approve 续跑」测试 |
| **R-B3** | replan 环（evaluator → planner 条件边）+ `plan_steps` 内联节点 + 删旧 `_run_loop`/`_run_segment`/`_run_plan_steps`；**重写 `test_workflow_segment_loop.py` 20 例**（它们直接调待删私有方法） | **1.5d** | 全量绿；库存只增不减；失败集不扩大 |
| **R-B4** | 清尾：架构图 webui workflow 格翻 ✅；交付总结；cov_gate | 0.5d | 四道门全绿 |

**关键风险与对策**

| 风险 | 对策 |
|---|---|
| 事件名映射错位（前端依赖） | R-B1 影子模式下 dump 两边事件序列做逐条 diff，差异即失败 |
| 审批续跑跨进程 | 走 `resume_from_store` 重建（R-B0 补 output 列）；测试用「新实例 + 同 store」模拟重启 |
| **段循环测试耦合待删私有方法** | `test_workflow_segment_loop.py` 20 例直接调 `_run_loop`/`_run_segment`/`_execute_segment_nodes`/`_do_replan`，R-B3 必须整文件重写——已计入 1.5d |
| replan 图内环 + max_hops 冲突 | `max_hops` 按 `(段数+plan 步数) × (max_segments+1) × 2` 动态计算；业务上限仍在 replan_count |
| 段循环里的「连续 2 步失败即停」规则 | 搬到 join 节点 / plan_steps 节点内部，行为不变（保持原判定位置） |
| 旧 `legacy_runner` 开关长期滞留 | R-B3 删除，不允许长期双轨（less-is-more）；若 R-B2 出现回归才临时启用 |

### 2.4 明确不做

- 不改 `WorkflowDefinition` JSON schema / 前端协议（端点全保）；
- 不把 workflow 的 approval 语义并进 Gateway 的 mailbox/审批（那是另一层，
  属后续「统一审批面」话题）；
- 不迁 `core/workflow/dag.py`（纯拓扑工具，被 goal/swarm 共用，保持中立）；
- **不做引擎 state 序列化**（见决定 B：改 run 级重建）。

---

## 3. 总顺序与工期（修订版）

```
R-B0 store 补 output 列（0.5d）
  └→ R-B1 影子编译（1d）
       └→ R-B2 主路径切换 + resume_from_store（1.5d）
            └→ R-B3 replan + 重写段循环测试 + 删旧循环（1.5d）
                 └→ R-B4 清尾（0.5d）
R-A（0.5d，P2，与主线无文件冲突，可任意时点插入）
合计 ≈ 5d 单人（R-B 4.5d + R-A 0.5d）
```

R-A 与 R-B 无文件冲突（R-A 只碰 `swarm/runtime.py`；R-B 碰 `core/workflow/`
与 `api/routers/workflow.py`）。

## 4. 完成后全系统执行核分布

| 路径 | 执行核 |
|---|---|
| chat turn | GraphEngine |
| study 轮 | GraphEngine |
| fanout preset | GraphEngine |
| goal / CLI / MCP swarm | GraphEngine（B1 编译器） |
| **webui workflow（R-B）** | **GraphEngine（R-B 编译器）** |
| 恢复原语 | 全部 `__interrupt__` + state + `run(start=)`（goal/workflow 另加 store 级 run 重建） |

## 5. 二次评估勘误（2026-10-31 复核）

| # | 初版判断 | 复核结论 | 影响 |
|---|---|---|---|
| 1 | 「state 序列化入 WorkflowStore，**净增益**」 | **不成立**：`node_outputs` 无 `output` 列；引擎 state 含不可序列化对象 | 改为 R-B0 补列 + `resume_from_store` run 级重建；新增 0.5d |
| 2 | 「R-A 先做，0.5d」 | 方向对但**优先级过高**：全库 4 模板 + 6 preset **零 branches 配置**，属"有承诺无用户" | 降为 P2，不占关键路径；R-B1 先行 |
| 3 | 「workflow 测试 280 例，R-B3 除少数内部断言外全绿」 | **280 含 50 例 goal 域**；core 实为 230 例；且 `test_workflow_segment_loop.py` **20 例直接调待删私有方法**，需整文件重写 | R-B3 由 1d → 1.5d；总数 3–4d → 5d |
| 4 | 「重启即卡死是缺陷」 | **成立**：`registry.get(run_id)` 为 None 即 404（routers/workflow.py:635-637），全库零 resume 路径 | 保留为 R-B2 的核心增益，但解法改为 run 级重建 |

## 6. 一句话

> R-A 修一个「文档承诺了但从未生效」的语义缺陷（retry 空转），
> 用显式 retry_queue 让重跑真正发生；R-B 把最后一个自研调度器
> （workflow 段循环）按 B1 同模式迁到引擎——但它有状态，所以要分四刀、
> 先影子后切换，并把审批续跑的 state 落库（顺带修掉「进程重启即卡死」）。
