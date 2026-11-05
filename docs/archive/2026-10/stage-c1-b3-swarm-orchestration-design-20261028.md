# B3 设计：swarm 编排迁移 — 2026-10-28

> 上游：`docs/archive/2026-10/stage-c1-team-contract-20261028.md`、
> `docs/archive/2026-10/stage-c1-acceptance-20261028.md` §3.2、
> `docs/archive/2026-10/stage-c1-wp-agent-node-design-20261028.md`（选项 B）
>
> 本文是 B3 swarm 部分的设计。**结论与立项假设不同**：实测显示
> swarm 的编排迁移收益远低于预期，建议改为「补齐四项缺陷 + 不迁移编排」。

---

## 0. 结论先行

| 判断 | 内容 |
|---|---|
| **建议** | **不迁移编排**。保留 `SwarmRuntime` 的分层调度，只修四项缺陷 |
| 理由 | swarm 的编排能力（层屏障、hooks、skip/retry、断点续跑）与 GraphEngine 的图语义**不是同一层抽象**，强行迁移需要给引擎加 3 个原语，净增代码 |
| 与选项 B 一致 | 是。选项 B 说「AgentLoop 是执行原语」；swarm 的 `_execute_agent` 已经**经 `AgentExecutor` 调 AgentLoop**（`runtime.py:376-380`），执行层早已统一 |
| 收益 | 四项缺陷修复（0.5d）比编排迁移（2d+）便宜 4 倍 |

**这条结论满足验收标准 §5**：「workflow 决定不迁可以是」的同一条款适用于 swarm：
允许不迁，但必须文档化 + 给触发条件。

---

## 1. 实测事实（推翻立项假设的三条）

### 1.1 全部 37 个 preset 都没有 branches

```
src/agentnodes/core/swarm/presets/*.yaml  → 37 个文件
branches=False                                     → 37/37
```

`skip` / `retry` / `redirect` 那套机器（`runtime.py:635-706`，约 70 行）
**零生产使用**。测试里也零引用（grep `branches|skip|retry|redirect`
在 `tests/test_swarm*.py` 中命中 0）。

**推论**：迁移时要保留的 skip/retry 语义，实际上是**不存在的语义**。
原设计文档把它列为「必须保的 5 项证据之一」，是错的——没有生产路径依赖它。

### 1.2 执行层已经统一

```
SwarmRuntime._execute_agent (runtime.py:288)
  → AgentExecutor(reg).execute(plugin, task, workspace, ...)  (runtime.py:376-380)
    → AgentLoop(...)                                          (executor.py:347)
```

swarm 的 agent 执行**已经**走选项 B 的路径。所谓「C1 阶段三：swarm 链迁移」
在我的原计划里被理解成「重写执行」，实际执行层无事可做。

### 1.3 budget_token 是死字段

```
runtime.py:56   budget_token: int | None = None
runtime.py:237  _budget_exceeded(...)  只检查 budget_turn / budget_time_seconds
```

`budget_token` 声明了但从未被读。37 个 preset 中无人设置
（grep 命中的 `risk_budgeting` 是 agent id，不是预算字段）。

---

## 2. 为什么迁移不值得（逐项）

| 引擎缺口 | swarm 现状 | 迁 GraphEngine 需要 | 判定 |
|---|---|---|---|
| **层内并行 + 层间屏障** | `ThreadPoolExecutor` + `as_completed`（`runtime.py:506-561`），天然按层循环 | GraphEngine 无「N 个独立节点并发 + 全等」原语。fanout 是「单节点拆分支→汇合」，`graph_engine.py:164-214`。需新增 `add_parallel_layer` | **净增代码** |
| **hooks** | `SwarmHook` 4 个回调（`types.py:50-77`） | 需桥接成 sink 事件 + `should_stop` → `GraphInterrupt` | 新增胶水 |
| **skip/retry** | 运行时改层列表（`runtime.py:635-706`） | 需条件边回边 + state 删除语义 | 新增胶水，且**无人使用** |
| **断点续跑** | `pre_completed` + `start_layer`（`runtime.py:144-167`） | 映射到 `engine.run(start=)` | 可映射，但需重放层号→节点名 |
| **DAG → 节点** | `topological_layers(preset.dag)` | 直接 `add_node`/`add_edge` | ✅ 这部分确实简单 |
| **结果信封** | `SwarmResult.agent_results` | 需保持一致 | ✅ |

**关键权衡**：只有最后两项是「天然契合」，前四项都要给引擎加东西。
迁移后代码量：删 `SwarmRuntime` 约 400 行（层调度 + hooks + branches），
加引擎原语约 150 行 + preset 适配约 120 行 + 事件桥约 60 行 ≈ **净减 70 行，
但新增 3 个引擎原语**。

按 less-is-more：**为净减 70 行而给唯一的编排引擎加 3 个原语，
且其中一个服务的功能无人使用，不值得。**

---

## 3. 真正该做的事：修四项缺陷（0.5d）

acceptance §3.2 要求四项必须处置，「保持原样」不可接受。逐项：

### 3.1 `max_retries` 从未执行

**现状**：`AgentNodeConfig.max_retries`（`dag_config.py:26-29`）声明；
`AgentExecutor` 从不读（`executor.py:321-345` 只读 timeout / max_iterations /
tools_override）。

**处置**：**实现**。位置 `AgentExecutor.execute` 外层（不改 AgentLoop）：
失败且 `node.max_retries > 0` 时重试整个 `_exec_llm`。
理由：这是声明了的契约，且重试对 LLM 瞬时失败有真实价值。

### 3.2 `budget_token` 从未检查

**现状**：`runtime.py:56` 声明，`_budget_exceeded` 不读（只检查 turn/time）。

**实施时的修正**：**不能删字段**。`goal/workflow.py:136` 从 study 配置
透传 `budget_token`，且 study 链的 token 预算**是真实生效的**
（`study/store.py` 持久化、`api/routers/study.py` 接收）。
删 `SwarmPreset.budget_token` 会切断 goal 的配置传递。

**最终处置**：保留字段 + 注释说明 swarm 不执行它 +
`preset_loader` 遇到 preset YAML 设置该键时 **warn**（§3.3 同一机制）。
理由：让缺口可见，比静默忽略或切断 goal 链路都好。

### 3.3 `redirect` 未实现

**现状**：`runtime.py:703-706` 只 log。

**处置**：**加载期 fail-loud**——`preset_loader._reject_unsupported` 抛
`ValueError`。理由：静默忽略是最坏的（用户以为生效）。

### 3.4 `max_retries` 泄漏进 prompt

**现状**：`_NON_PROMPT_KEYS`（`executor.py:38-41`）漏了 `max_retries`，
导致它被渲染进「Current Context」段。

**处置**：**修**——加入 `_NON_PROMPT_KEYS`。一行改动，有测试价值。

---

## 4. 不改的部分（明确）

| 项 | 理由 |
|---|---|
| `SwarmRuntime` 层调度 | 与 GraphEngine 图语义不同层；迁移净增原语 |
| `SwarmHook` | 现有 4 回调是 swarm 专有生命周期，非图事件 |
| `topological_layers` | 已在 `core/workflow/dag.py`，复用正常 |
| `_execute_agent` | 已走 `AgentExecutor`，符合选项 B |
| `branches` 机器 | 零使用，但删它会破坏 `SwarmPreset` 契约测试；保留，标注 dead |

---

## 5. 重新评估的触发条件

出现以下任一信号时，本结论需重审：

1. 有 preset 开始使用 `branches`（skip/retry/redirect 变成真实需求）——
   那时需要条件边，图语义才真正契合。
2. 需要 swarm 与 study/workflow 共享同一套观测事件（当前三套并行：
   `SwarmHook` / `study_phase` / sink）。
3. 需要跨 swarm run 的检查点恢复（当前 `pre_completed` 是内存态，不落盘）。
4. `SwarmRuntime` 的层调度出现正确性 bug 且难以修复，而 GraphEngine 的
   已验证语义能避开。

---

## 6. 改动面（本轮）

| 文件 | 改动 | 归属 |
|---|---|---|
| `core/agent/executor.py` | ① `_NON_PROMPT_KEYS` 加 `max_retries`<br>② `execute` 外层实现 `max_retries` 重试 | W-P（请求代改，因 executor.py 属 W-W） |
| `core/swarm/runtime.py` | 删 `budget_token` 字段 | W-W |
| `core/swarm/preset_loader.py` | `redirect` fail-loud + `budget_token` warn | W-W |
| `tests/test_swarm_retry.py`（新） | 覆盖 3.1 与 3.4 | W-W |
| `tests/test_swarm_preset_validation.py`（新） | 覆盖 3.3 | W-W |

---

## 7. less-is-more 现有入口调查表

写新代码前查过并排除的现有入口：

| 需求 | 查过的现有入口 | 为何不新写 |
|---|---|---|
| agent 执行 | `AgentExecutor.execute`（executor.py:101） | 已完整，swarm 在用 |
| 层拓扑 | `topological_layers`（workflow/dag.py） | 已在用，正确 |
| 并行执行 | `ThreadPoolExecutor`（runtime.py:516） | fanout 的 T1 也是这个，无需第二套 |
| 结果信封 | `AgentResult` / `AgentExecutionResult` | 两者字段已对齐 |
| 预算判定 | `RunBudget.plan_call` | swarm 层预算与 run 预算不同粒度，不强行合并 |
| 重试 | **无现成**（`decorators.py` 的 `with_retry` 零调用方且语义不同） | 唯一需要新增的逻辑 |

**新代码只有一处**：`AgentExecutor.execute` 内的重试循环（约 12 行）。
`decorators.py` 的 `with_retry` 查过了——它是给 workflow 装饰器链路用的，
签名不匹配（装饰器 vs 内联），复用会引入间接层，不符合 less-is-more。

---

## 8. 验证计划

| # | 证据 | 测试 |
|---|---|---|
| E1 | `max_retries=2` 时瞬时失败重试后成功 | `test_swarm_retry.py` |
| E2 | `max_retries` 不再出现在 prompt | `test_swarm_retry.py` |
| E3 | `action: redirect` 在加载期报错 | `test_swarm_preset_validation.py` |
| E4 | `budget_token` 被忽略并 warn | `test_swarm_preset_validation.py` |
| E5 | 37 个 preset 仍全部可加载 | `test_swarm_presets_30.py`（已有 12 条） |
| E6 | `SwarmResult` 信封不变 | `test_swarm.py`（17 条） |
| E7 | agent 执行仍走 AgentExecutor | `test_agent_loop_swarm_parity.py`（16 条） |

回归门：
```
D:\tools\python312\python.exe -m pytest tests/test_swarm*.py \
    tests/test_agent_executor*.py tests/test_agent_loop_swarm_parity.py \
    tests/test_goal_*.py tests/test_v05*.py -q --tb=no > b3.log 2>&1
D:\tools\python312\python.exe scripts/c1_gate.py b3.log
```

---

## 9. 与验收标准的偏差说明

`docs/archive/2026-10/stage-c1-acceptance-20261028.md` §2.1 swarm 行列出 5 项「必须证明的
等价项」。本设计实施后：

| # | 项 | 状态 |
|---|---|---|
| ① | `SwarmResult.agent_results` 逐 agent 等价 | 不变（不迁移，天然满足） |
| ② | 分层屏障语义 | 不变（不迁移） |
| ③ | 预算三维记账（含 token） | **token 维度改为显式删除**（§3.2） |
| ④ | `skip`/`retry` 分支语义 | **实测零使用**，保留但不作为验收项 |
| ⑤ | 层钩子调用序 | 不变（不迁移） |

**第 ③ ④ 项需要在验收标准里同步修订**——原文把不存在的生产语义
当成了必须保的证据。
