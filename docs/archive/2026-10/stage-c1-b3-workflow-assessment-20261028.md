# B3 评估：workflow 是否迁入 GraphEngine — 2026-10-28

> 上游：`docs/archive/2026-10/stage-c1-acceptance-20261028.md` §3.3（明确允许结论「不迁」）、
> `docs/archive/2026-10/stage-c1-b3-swarm-orchestration-design-20261028.md`（同批，结论也是不迁）
>
> **结论：不迁。** 另有 4 项空声明必须独立修复（与迁移解耦）。

---

## 0. 结论

| 判断 | 内容 |
|---|---|
| **建议** | **不迁移编排**。保留 `WorkflowRunner` 的段式执行 |
| 结构缺口 | 3 个，全部实测确认（§1） |
| 迁 vs 不迁的净收益 | 迁：给引擎加 3 个原语；不迁：0 引擎改动 |
| 必须独立做的 | 4 项空声明修复（§3），约 0.5d |
| 与 swarm 结论的一致性 | 一致：两条 DAG 链都判定「不迁」，理由同构（§4） |

---

## 1. 三个结构缺口（实测）

### 1.1 动态子图

`executor.py:257` `sid = f"plan_{sid}"`、`:270` `f"plan_{dep}"`：
planner 在**运行时**产出节点，边也运行时拼。

GraphEngine 的节点集在 `add_node` 阶段固定（`graph_engine.py:124-131`
`if name in self._fns: raise`；`_build` 时一次性 compile）。

**需要的引擎改动**：支持运行时增节点 + 重编译，或「子图嵌套」。
后者架构图上标为 `graph 子图嵌套`（节点类型之一），但**未实现**。

### 1.2 段门 + 重规划循环

`executor.py:96-121` `approve()`、`:153-158` `awaiting_approval` +
`return  # wait for approve()`、`:342-383` `_do_replan()`。

这是**命令式循环**：`run()` 在段门处 return，外部 `approve()` 再进来续跑。
GraphEngine 的 `GraphInterrupt` + `run(start=)` 能表达「暂停/恢复」，
但 `replan` 需要「回到 planner 节点重跑并替换已产出的 plan_* 节点」——
是**图变形**，不是续跑。

**需要的引擎改动**：state 键删除语义 + 回边 + 重编译。

### 1.3 扇入信封 + 逐节点持久化

`node_types.py:207-267` 的 `AgentResult` 归约、`store.py:253-267`
`save_node_output`。

GraphEngine 明确「引擎不落库——事件走 sink，持久化属数据面」
（`graph_engine.py:20`）。这不是缺口，是**有意的分层**。

**需要的改动**：preset 层自己落库 + 归约（不在引擎内）。

---

## 2. 迁移 vs 不迁移

| 维度 | 迁 | 不迁 |
|---|---|---|
| 引擎改动 | +3 原语（动态节点、state 删除、重编译） | 0 |
| workflow 改动 | 重写 491 行 executor | 0 |
| 净代码 | 约 -150 行 | 0 |
| 新风险 | 动态图 + 重编译是引擎最复杂的新增能力 | 0 |
| 收益 | 事件统一、预算统一、中断统一 | 无（见下） |

**收益评估**：三条「统一」在本项目里的实际价值：

| 收益 | 是否成立 | 依据 |
|---|---|---|
| 事件统一 | 部分 | workflow 的 LLM 节点当前**根本不发事件**（`node_types.py` 无 emit）。迁到引擎会自动获得 `node.started/completed`——这是真实收益 |
| 预算统一 | 弱 | workflow 只有全局 `budget.time_seconds`（`executor.py:414-419`）；接入 `RunBudget` 需要给每个节点切片，属新功能不是迁移 |
| 中断统一 | 弱 | 段门语义（外部 `approve()` 回调）与 `GraphInterrupt`（state 里带 payload）不同构，并非「统一」而是「改写」 |

**唯一真实收益是事件**。而它可以用**远小的改动**单独拿到：
给 `node_types.py` 的 LLM 节点加 `on_event` 透传（约 15 行），
不涉及任何引擎改动。按 less-is-more，**这是正确做法**。

---

## 3. 必须独立修复的四项空声明

这些与迁移无关，且 acceptance §3.3 要求「修或显式删」：

### 3.1 `node_timeout_seconds` / `node_max_retries` 零读取

`definition.py:51` 默认值里有，全仓**零读取**（已实测：除定义处外无引用）。

**处置**：接入。`node_max_retries` 可直接复用本轮为 swarm 加的
`AgentExecutor._exec_llm_with_retry`（B3 已实现）；`node_timeout_seconds`
映射到 `AgentNodeConfig.timeout` → `iteration_timeout_s` 推导
（`executor.py:337-345` 已有该逻辑）。
改动点：`node_types.py` 的 `_loop_kwargs` 补齐读取（约 10 行）。

### 3.2 `decorators.py` 的 `with_retry`/`with_timeout`/`with_validation` 零调用

**处置**：**删除整个 `decorators.py`（163 行）**。
理由：功能已被 3.1 覆盖（重试/超时），validation 由 `validator.py` 承担。
保留零调用方的装饰器只会误导。

### 3.3 `NodeContext._failures` 从未 set

`node_types.py:357` 读取，全仓无写入。

**处置**：删除该属性与读取处（约 5 行）。

### 3.4 LLM 节点不发事件

**处置**：加 `on_event` 透传（约 15 行），复用 `AgentExecutor` 已有的
`on_event` 参数（`executors.py` 已支持 python 节点发事件，LLM 节点漏了）。

---

## 4. 与 swarm 结论的一致性

| 维度 | swarm | workflow |
|---|---|---|
| 结论 | 不迁 | 不迁 |
| 层调度 vs 图语义 | 层屏障 ≠ 图 | 动态图 + 重规划 ≠ 图 |
| 执行层 | 已走 `AgentExecutor` ✅ | 已走 `AgentExecutor` ✅ |
| 真实缺陷 | 4 项（已修） | 4 项（本文 §3） |
| 引擎改动 | 0 | 0 |

**共性判断**：两条链的**执行层都已统一**（选项 B），
需要迁移的只是各自的**调度循环**，而调度循环的语义与图不同构。
这正是选项 B 的价值：它让「执行统一」这一半先落地，
剩下的调度迁移可以逐个按收益评估，而不是被迫一次性重写。

---

## 5. 重新评估的触发条件

1. **需要跨段/跨轮的可视化**：段门重规划的过程要展示成 DAG 图给前端。
   那时图语义才变成需求，不是实现手段。
2. **需要与 study 共享编排语义**：若 study 的 HITL 门与 workflow 的段门
   最终合并成一套审批模型，届时统一到一个引擎才有价值。
3. **`WorkflowRunner` 出现无法修复的正确性 bug**，且 GraphEngine 的
   已验证语义能避开。
4. **动态子图成为通用需求**（其他链也需要运行时建图）——那说明
   「图 = 纯数据」的预设注册表模型需要升级，是独立的大工程。

---

## 6. 改动面（本轮）

| 文件 | 改动 | 预估 |
|---|---|---|
| `core/workflow/node_types.py` | ① `_loop_kwargs` 读 `node_max_retries` / `node_timeout_seconds`<br>② LLM 节点加 `on_event` 透传<br>③ 删 `_failures` | ~30 行 |
| `core/workflow/decorators.py` | 删除整个文件 | -163 行 |
| `tests/test_workflow_node_types.py` | 补 3 项断言 | 已有 22 条基础上 |
| `tests/test_workflow_decorators.py` | 删除（若存在） | — |

---

## 7. less-is-more 调查表

| 需求 | 查过的现有入口 | 结论 |
|---|---|---|
| 节点级重试 | `AgentExecutor._exec_llm_with_retry`（B3 本轮新增） | **直接复用**，不写第二套 |
| 节点级超时 | `AgentExecutor._exec_llm` 内 `iteration_timeout_s` 推导（executor.py:337-345） | 复用，只补配置读取 |
| 事件发射 | `AgentExecutor.execute` 的 `on_event` 参数 | 透传即可，不新增机制 |
| DAG 拓扑 | `workflow/dag.py` `topological_layers` | swarm 已复用，正确 |
| 校验 | `workflow/validator.py`（130 行，已在用） | 保留 |
| 重试装饰器 | `workflow/decorators.py`（零调用） | **删除**，不修 |

---

## 8. 验证计划

| # | 证据 | 测试 |
|---|---|---|
| E1 | `node_max_retries` 生效（瞬时失败重试） | `test_workflow_node_types.py` |
| E2 | `node_timeout_seconds` 传到 loop | 同上 |
| E3 | LLM 节点发 `node.started/completed` | 同上 |
| E4 | `decorators.py` 删除后无 import 断裂 | 全量收集 |
| E5 | `_failures` 删除后 `test_workflow_segment_loop` 20 条仍绿 | 已有 |
| E6 | 节点信封 `AgentExecutionResult` 不变 | `test_workflow_node_types.py` 22 条 |

回归门：
```
D:\tools\python312\python.exe -m pytest tests/test_workflow*.py -q --tb=no > b3wf.log 2>&1
D:\tools\python312\python.exe scripts/c1_gate.py b3wf.log --files test_workflow
```
