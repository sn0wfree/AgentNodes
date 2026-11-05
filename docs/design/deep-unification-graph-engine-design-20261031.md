# 深度统一设计：SwarmRuntime 层循环退役，执行核收敛到 GraphEngine（2026-10-31）

> 背景：checkpoint 盘点发现三套机制两套执行核。用户拍板**方案 B（深度统一）**：
> goal workflow 的层 DAG 整体迁到 GraphEngine，从此字面上「一个引擎一套恢复原语」。
> 前置勘查（本文依据）：GoalWorkflowRunner / SwarmRuntime / GraphEngine / graph_preset 全文精读。

---

## 0. 结论

**GoalWorkflowRunner 不是第三套执行器——真正的层执行器是 SwarmRuntime，且它有三个消费面。**
所以本刀不是「goal workflow 迁引擎」，而是：

> **SwarmRuntime 的自研层循环（ThreadPoolExecutor + layer for + _active_runs 轮询）
> 退役；execute() 变成「SwarmPreset → GraphEngine 图」的编译器。**
> 契约（SwarmPreset / AgentResult / SwarmHook / cancel(run_id)）原样保留，三个消费面零改动。

完成后，全系统执行核分布：

| 路径 | 执行核（现状） | 执行核（统一后） |
|---|---|---|
| chat turn | GraphEngine（session_controller） | GraphEngine（不变） |
| study 轮 | GraphEngine（graph_preset） | GraphEngine（不变） |
| fanout preset | GraphEngine（presets/fanout） | GraphEngine（不变） |
| **goal workflow / CLI swarm / MCP swarm** | **SwarmRuntime 自研层循环** | **GraphEngine（经 SwarmRuntime 编译）** |

---

## 1. 勘查：现状执行语义（迁移必须逐条保真）

### 1.1 SwarmRuntime.execute 的层循环（runtime.py:114-230）

```
layers = topological_layers(dag)
for layer_idx, layer in enumerate(layers):
    ├ layer_idx < start_layer → 只发 hook 不执行（resume 语义）
    ├ run_id not in _active_runs → break（cancel 语义，层边界生效）
    ├ _budget_exceeded(turns, elapsed) → break（turn/time 两维；token 有意不强制）
    ├ on_layer_start hook
    ├ _execute_layer：ThreadPoolExecutor(max_workers) 并行跑 agent
    │   └ 每 agent：_execute_agent（AgentExecutor：llm / python_executor / evaluator）
    │      完成后在**主线程**按完成顺序发 on_agent_complete hook
    ├ 成功数计入 budget_turns；budget_time 累计
    ├ on_layer_complete hook
    ├ branches 评估（P3.7：对剩余层做 skip/retry 改写）
    └ 任一 hook should_stop() → break
result.success = 全员 SUCCESS；final_output = 按**插入序**最后一个 SUCCESS 的 output
```

### 1.2 恢复语义（resume_and_continue，workflow.py:573-706）

- P3.6 CheckpointStore 存 `state + layer_results + meta`；
- 恢复 = layer_results → `pre_completed: dict[agent_id, AgentResult]` +
  `start_layer = state.current_layer`（1-based 直接当 0-based 下一层索引用）；
- `runtime.execute(preset, workspace, objective, [hook], pre_completed, start_layer)`：
  pre_completed 灌进 result.agent_results，`_execute_layer` 跳过已完成 agent，
  下游 `_gather_upstream` 照常看到上游输出。

### 1.3 对照：study 已在引擎上的恢复（先例）

`graph_preset.build_research_engine` → `run(state)`；HITL 门 = 构建期条件节点；
中断 = `__interrupt__` 标记；恢复 = interrupt payload 重建 state + `run(start=claims)`。
**目标：goal workflow 的恢复建立在与 study 完全相同的引擎原语上。**

---

## 2. 目标形状

```
SwarmRuntime.execute(preset, workspace, task, hooks, pre_completed, start_layer)
  │ 编译（纯函数，无 IO）
  ▼
GraphEngine：
  layer_0(split, fanout) → join_0 → layer_1(split, fanout) → join_1 → … → END
  ├ split_i 节点：cancel/budget 检查（→ GraphInterrupt）+ on_layer_start
  │               + {"branches": [未完成 agent_id…]}
  ├ fanout(t1, max_parallel=max_workers)：worker = 现 _execute_agent 原样
  │               + on_agent_complete（见 §3 决定 C 的桥接）
  ├ join_i 节点：fanout_results → agent_results、budget 计数、on_layer_complete、
  │               branches 评估（改写 state 剩余层）
  ├ join_i 出边：条件路由（should_stop / 无剩余层 → END；否则 → layer_{i+1}）
  └ run(state, start=f"layer_{start_layer}") —— resume 走引擎原生 start=
  ▼
SwarmResult 组装（agent_results / success / final_output / elapsed_s 语义不变）
```

要点：

- **层屏障语义原样保留**（等整层完成再进下层）——与现行为严格等价；
  层内并行 = fanout t1（同 ThreadPoolExecutor 粒度）。
- **pre_completed 语义原样保留**：seed 进 state["agent_results"]，split 节点
  把已完成 agent 排除出 branches，join 合并时原序灌回。
- **不引入 RunBudget**：SwarmPreset 的 turn/time 预算逻辑照搬到 split/join 节点
  （同样的计数器进 state），token 预算继续「不强制 + preset_loader 告警」
  （B3 已定稿）。fanout 的 budget 切片机制本刀不用（传 None，零打扰）。

---

## 3. 关键设计决定

### 决定 A · 编译器不换契约

SwarmRuntime 的名字、`execute()` 签名、SwarmPreset/AgentResult/SwarmHook、
`cancel(run_id)` 全部保留——三个消费面（goal/workflow.py ×3、cli/swarm ×2、
mcp/server ×1）**一行不改**。退役的是实现：层 for 循环、
ThreadPoolExecutor 层执行、`_active_runs` 轮询点。

### 决定 B · DAG → 层 → fanout 链（不做忠实 DAG 边）

GraphEngine 每节点单出边（引擎约束，B1-a 定稿）；忠实 DAG 边需要引擎支持
多后继 + 改变调度语义（依赖就绪即起跑 vs 层屏障）。**选层屏障**：
topological_layers 编译成 (split→join) 链，行为与现状严格等价，验收门一可判。
忠实 DAG 作为将来引擎演进项，不在本刀。

### 决定 C · on_agent_complete 保序保线程

现状：on_agent_complete 在 execute 的调用线程按**完成顺序**逐个发
（as_completed 循环里，runtime.py:540-550）。fanout 的 `branch.settled`
sink 事件恰好在 as_completed 循环内发出（engine._run_fanout:409）——
**引擎加一个 opt-in 字段 `include_result: bool = False`**：
为 True 时 branch.settled 的 data 携带 `result`（worker 返回值）。
SwarmRuntime 编译时置 True，sink 桥接到 on_agent_complete。
- 交付时序（逐 agent 增量）、线程（execute 调用线程）、顺序（完成序）全部不变；
- 默认 False 保证 fanout preset / t2 用户的事件负载不变（agent 输出可能很大）。

### 决定 D · cancel 与 should_stop 映射到引擎中断

| 现状 | 图上 | 中断后 |
|---|---|---|
| `cancel(run_id)` → `_active_runs` 删除 → 层边界 break | split 节点查 `_active_runs`（run_id 在 state）→ `GraphInterrupt("cancelled")` | execute() 从 partial state 组装 SwarmResult（success=False），与今天 break 同形 |
| hook `should_stop()` → break | join 路由返回 END | 同上 |
| budget 超限 → break | split 节点检查 → `GraphInterrupt("budget")` | 同上 |
| 引擎 max_hops（现状无此概念） | 兜底，设高（层数上界） | 视为 error（诚实：不该发生） |

### 决定 E · 恢复原语统一，持久化仍分 profile

统一的是**引擎原语**：`__interrupt__` 标记 + state + `run(start=)`
（study 的 plan A 与 goal 的新 resume 共用同一原语）。
持久化 profile 不强行合一（10-31 盘点结论维持）：

| | study 轮（不动） | goal workflow（本刀后） |
|---|---|---|
| 原语 | GraphInterrupt + run(start=) | **同左** |
| 持久化 | study_interrupts + run_dir 工件（重建式） | CheckpointStore 快照（layer_results）|
| 恢复点 | 图节点 `start=claims` | 图节点 `start=layer_k` |

**顺带收口两个已知缺口**（10-31 盘点 §二）：
1. **层间自动存档**：join 节点 on_layer_complete 之后调 `checkpoint()`
   （JSON 极小；测试经 `STRATEGY_RESEARCH_CHECKPOINT_BASE_DIR` 隔离，
   三层防线已保证不触真实 `~/.agentnodes`）；
2. **CLI 真续跑**：`/goal checkpoint resume` 改调
   `GoalWorkflowRunner.resume_from_checkpoint` + `resume_and_continue`
   （两个方法已存在且有测试锁语义，CLI 接线即可），替换「只展示」。

---

## 4. 边界（本刀不做）

| 不做 | 理由 |
|---|---|
| core/workflow 定义系统（definition/executors/orchestrator）迁引擎 | 独立用户特性（webui workflow），同模式迁移可将来另开一刀；本刀不扩大爆炸半径 |
| study 侧任何改动 | plan A 是有文档的已决决策，且已在引擎上 |
| 忠实 DAG 边 / 多后继引擎能力 | 见决定 B |
| SwarmPreset 预算接 RunBudget 切片 | 语义不同（turn/time 计数 vs token 池），等价优先 |
| 压缩 checkpoint（`<conversation-checkpoint>`） | 类别不同（压缩边界标记，非恢复机制） |

---

## 5. 分阶段实施（估 ~3d）

| 阶段 | 内容 | 验收 |
|---|---|---|
| **B1** | SwarmRuntime.execute 改编译器：层循环 → (split→fanout→join) 链；决定 A-D 全落地；预编译（B2 的 resume 改 run(start=)） | 既有 goal workflow 测试（v051-v053/phase3/phase3b/hook/checkpoint_resume/final_band_push）+ swarm 测试 + MCP/CLI swarm 测试**零修改全绿**；c1_inventory 不减；c1_gate 失败集不扩大 |
| **B2** | checkpoint 收口：join 节点层间自动存档；resume_and_continue 改 `run(start=)`；CLI `/goal checkpoint resume` 真续跑 | 新增测试：自动存档逐层可查、崩溃模拟→resume 续跑完成、CLI resume 端到端 |
| **B3** | 清尾：删退役的层循环/线程池代码（less-is-more 零调用方验证）；架构图 SwarmRuntime 格翻 ✅「引擎统一」；三道验收门 + cov_gate | 图上执行面单引擎成立；覆盖率三档 ≥90% 不回退 |

---

## 6. 风险与诚实说明

1. **hook 交付时序**：决定 C 保证 on_agent_complete 增量/顺序/线程不变；
   但 fanout 分支异常路径（worker 抛异常）今天与将来都走 ERROR AgentResult
   + on_agent_complete，桥接处需覆盖（engine 已记 error entry，sink 拿得到 result）。
2. **final_output 插入序**：今天按完成序插入 agent_results，final_output 取
   「最后一个完成的 SUCCESS」；fanout_results 是 ordinal 序——join 合并时
   按 entry 完成时间戳排序灌入，逐字节保序。实现时加单测锁。
3. **langgraph 双跳开销**：每层两个节点（split/join），max_hops 需 ≥ 2×层数+余量。
4. **事件量**：SwarmRuntime 默认 sink=None（今天也无引擎事件），hook 之外
   不新增事件面；要接事件流属后续增强，不在本刀。
5. **retry 分支空转（现状保真）**：旧 `_execute_layer` 对「已有结果的
   agent」一律跳过（`if agent_id in result.agent_results: continue`），
   因此 P3.7 的 retry 动作实际从未真正重跑过 agent。本刀按行为等价
   原样保留（split 节点同样排除已有结果者）；要不要让 retry 真重跑
   属语义变更，不在等价刀内做——若做，改 split 过滤条件即可。

---

## 7. 一句话

> SwarmRuntime 从「第二套执行核」降级为「一张图的编译器」——
> 契约不动、消费面不动、行为逐条等价；从此全系统一个引擎、
> 一套恢复原语（`__interrupt__` + state + `run(start=)`），
> checkpoint 的「能存不能续」缺口顺带收口。
