# 代码图 × 架构图差距审视 — 2026-10-27

> ⚠ **历史文档**：架构差距图。所列差距**已全部填平**（ContextHub 已存在、GraphEngine 为唯一编排 runtime）。

> 方法：`graphify update src/strategy_research`（纯 AST，22554 节点 / 36402 边）
> + 人工核实。**重要方法论警示**：graphify 的 `calls`/`references` 边会
> 按同名符号误连（实测 `core/study/langgraph_engine.py` 被标出 67 条指向
> `core/runtimes/graph_engine.py`，但该文件根本没有相关 import）。因此本报告
> **只采信 `imports_from`（1942 条）与人工 `git grep` 核实**。

---

## 1. 架构目标（v6.2 五层，已定案）

```
① 渠道 → ② Gateway（单进程唯一控制面）→ ③ 执行面（GraphEngine 唯一 runtime）
      → ④ 平台数据面（PlatformStore 唯一写入者 / 不变量 7）→ ⑤ 研究域（IP·独立）
```

关键定案：GraphEngine 唯一 runtime（wrap langgraph）；每 session 一自包含 SQLite
（event + blobs + **message** + goal/todo）；**PlatformStore 是消息与事件的唯一写入者**；
研究域不许私开存储通道。

## 2. 实测差距（imports_from 为准）

### 2.1 组件采用度（谁真的在用新组件）

| 新组件 | 真实采用者 | 判定 |
|---|---|---|
| `core/storage/platform_store.py` | **仅 `core/gateway/gateway.py`（构造时可选）** | ❌ 近乎未采用 |
| `core/storage/artifact_channel.py` | **无外部采用者** | ❌ 未接线 |
| `core/runtimes/graph_engine.py` | 仅自身包 `__init__` 导出 | ⚠ 只有 Gateway 的 SessionController 用到（`session_controller.py` 内 import，图未捕获该函数级 import） |
| `core/runtimes/nodes/react.py` | `gateway/invariants.py`（仅做结构检查） | ⚠ 生产链未用 |
| `core/orchestration/mailbox.py` | `entry_point.py` + 包导出 | ✅ 已用 |
| `core/gateway/gateway.py` | 包导出（`api/routers/chat.py` 经 singleton 用） | ✅ chat 已用 |

### 2.2 旧执行体仍然是大头（新 runtime 未取代）

| 旧组件 | 规模（连通边） | 谁在用 |
|---|---|---|
| `AgentLoop`（core/agent/loop.py） | **83**（第 5 大枢纽） | `core/study`（54）、`api`（109）、`core/mcp`（35）、`core/workflow`（19）、`core/swarm`（11）、**`core/runtimes`（11）** |
| `AutoresearchRunner` | 50 | study scheduler |
| `BaseEngine` | 47 | 回测（业务，正常） |
| `EventStore` | 45 | api / study.langgraph_engine / trace_projection |
| `MemoryManager` | 9（但文件内 99） | api/container、cli、goal |

**结论：GraphEngine 只在 chat 一条窄路径上生效；研究执行、MCP、workflow、swarm
仍全部走 AgentLoop。** 架构说的「唯一 runtime」目前只对 chat 成立。

### 2.3 数据面：新平台存储几乎没接，旧 sqlite 工具是事实标准

| 事实 | 数字 |
|---|---|
| `core/storage/sqlite.py`（旧的 json_dumps / new_id / write_transaction） | 被 study/store、goal/store、hypothesis/store、scheduled_research/store 等**6 个域模块** import |
| `PlatformStore` | 仅 gateway 构造点引用，**无任何域模块使用** |
| `messages` 表写入者 | `memory_manager`（INSERT）、`projection_store`（projector）、`web_session`（CRUD）——**三写者** |

### 2.4 反向依赖（架构禁止的平台 → 域）

架构门禁测试只覆盖 Python `import` 语句，实测到的违规是弱形式：
`core/storage → core/db.py`（根级 db 模块，非域）、`core/runtimes → core/hypothesis`（1 边，需核实）。

真正的问题是**方向性缺失**：域 → 平台有 92+86 边，但指向的是旧 `storage/sqlite.py`
工具函数，而不是新 `PlatformStore`。

### 2.5 ContextHub / RuntimeHub 不存在

| 架构要求 | 现状 |
|---|---|
| `core/context/hub.py`（collect→arbitrate→render→explain） | **目录不存在**；等价能力散在 `core/agent/context_injector.py`、`memory_manager`、`prompt_builder` |
| `core/orchestration/runtime_hub.py`（preset + provider 解析） | **不存在**；现状由 `SessionController._build_engine` + `LLMConfig.load()` 隐式完成 |
| `core/gateway/capacity.py`（容量组） | **不存在**；study 用 `SR_STUDY_MAX_CONCURRENT` 环境变量 |
| `core/gateway/scheduler.py`（唯一调度器） | **不存在**；study 自己的 `StudyScheduler` + `scheduled_research` 各一套 |
| `core/orchestration/run_context.py` | **不存在** |

## 3. 差距矩阵（架构项 → 状态）

| # | 架构项 | 状态 | 证据 | 归属 |
|---|---|---|---|---|
| 1 | Gateway 单进程唯一控制面 | 🟡 部分 | chat 已走；study 走自己的 scheduler 消费者（准入已统一，执行体未统一） | 3c 已完成准入；执行体归一属 C1 |
| 2 | GraphEngine 唯一 runtime | ❌ 未达成 | AgentLoop 83 边仍是第 5 枢纽；study/mcp/workflow/swarm 全走 AgentLoop | **最大差距** |
| 3 | PlatformStore 唯一写入者（INV-7） | ❌ 未达成 | 仅 gateway 引用；messages 三写者；域模块用旧 sqlite 工具 | **第二大差距** |
| 4 | 每 session 一自包含 SQLite | 🟡 部分 | PlatformStore 实现了；但 WebUI 仍用单一 `.session.db` 分表 | 0.4 |
| 5 | ContextHub | ❌ 不存在 | 无 core/context/；能力散落 3 处 | A4 |
| 6 | RuntimeHub | ❌ 不存在 | 无 runtime_hub.py | A1 剩余 |
| 7 | 唯一 scheduler | ❌ 未达成 | StudyScheduler + scheduled_research 两套 | C5 |
| 8 | 容量组 gate | ❌ 不存在 | 环境变量硬编码 | A2 |
| 9 | RunContext（per-run 预算/事件/取消） | 🟡 部分 | RunBudget 有（F2）；CancelScope/TraceView 无 | A4 |
| 10 | research preset 图化 | ✅ 已达成 | graph_preset.py + 等价性测试 | B1-b |
| 11 | fanout 多 subagent | ✅ 已达成（引擎层） | F1-F4，真 LLM 验证 | F1-F4 |
| 12 | artifact 通道（P-3） | 🟡 实现未接线 | ArtifactChannel 无采用者 | 0.4 |
| 13 | 入口统一（唯一准入） | ✅ 已达成 | chat/cli/scheduler 全走 mailbox | 3a/3c |
| 14 | SSE 单轨 | ❌ 双轨仍在 | sse_buffer + bridge_v2 | 3b-2 |

## 4. 结论：路线需要修正

**原 P5 计划（P5-1 chat 补齐 → P5-2 langgraph 退役 → P5-3 SSE）建立在「差距不大」的假设上。实测不是。**

真实版图是：**入口层已统一（13 项 ✅），但执行层与数据层基本没动（2/3/5/6/7/8 全 ❌）**。
这解释了为什么 P5-1 会越做越别扭——chat 要持久化，就必须先回答「持久化归谁」，
而这正是 0.4 数据面收敛要解决的问题。

### 修正后的优先级

| 序 | 事项 | 理由 | 工期 |
|---|---|---|---|
| **1** | **数据面收敛（0.4）**：PlatformStore 接管 session 消息/事件；projector 改读新库；messages 三写者归一 | 不解决它，P5-1 只能做半套；且它是 INV-7 的正式落地 | 3-5d |
| **2** | **P5-1 chat 补齐 + 删旧分支** | 依赖 1（持久化有了归属） | 1-1.5d |
| 3 | **执行体收敛（C1）**：AgentLoop → react 节点迁移（study/mcp/workflow/swarm 四条链） | 最大差距，但依赖面最广 | 5-8d |
| 4 | P5-2 langgraph 退役 | 可随 3 一并做 | 2d |
| 5 | P5-3 SSE 单轨 | 独立 | 2d |
| 6 | ContextHub / RuntimeHub / 容量组 / 唯一 scheduler | 架构完整性 | 5-6d |
| 7 | order-dependence 隔离 + 审计门② | 收官 | 4-5d |

**与原计划的差异**：把「数据面收敛」提到最前（原计划在 P5-1 之后），把「执行体收敛」
从被忽略提升为第二大项。原 P5-1 的前提（只补 chat）被证明不成立。

## 5. 建议的下一步动作

先做**数据面收敛的可行性验证**（1d，不写生产代码）：

1. 清点 `web_session._get_db_path()` 库的表/行数/依赖方（api/SSE/study 面板）；
2. 验证 projector 能否只依赖 `event_log`（它本来就是 fold）；
3. 验证「旧库只读兼容 + 惰性迁移」路径（架构已定，需确认工作量）；
4. 产出 0.4 的实施设计，再决定工期。

在此之前**不做 P5-1 的代码**——避免在错误前提上写一版要推翻的实现。
