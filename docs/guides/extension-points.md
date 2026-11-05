# 扩展机制分工 — Extension Points

> 为什么有这份文档：「在 agent 生命周期的某个点插入行为」在本仓库有 **7 个入口**（steps / hooks / injectors / plugins / skills / mcp / toolsets），重叠处无成文分工——每个横切需求都要先做一次「放哪」的判断，而这个判断过去只存在于作者记忆里。
> 来源：`docs/archive/2026-10/architecture-harness-recommendations-20261008.md` §1.1（P1 落地项）
> 状态：现状描述 + 规则；**止血规则自本文档生效**

## 0. 30 秒决策树

```
我要给 agent 加一个横切能力 —
│
├─ 改变 loop 走哪条路 / 何时停 / 怎么执行某一步？
│    └─▶ strategy STEP（core/agent/strategy/steps/）
│
├─ 只读观察（打点、审计、metrics、旁路通知），绝不改流程？
│    └─▶ hook（core/hooks/composite.py::AgentHook）
│
├─ 往 messages 里补上下文（goal、todos、memory 检索结果）？
│    └─▶ injector（core/agent/context_injector.py）
│
├─ 让 agent 多一个能调用的能力（函数/工具）？
│    ├─ 内置且按场景分组 ──▶ toolset 注册（agent/toolsets.py::TOOLSETS）
│    ├─ 文档型能力（流程/知识）────▶ skill（core/skills/，SKILL.md）
│    └─ 外部服务协议 ────────────▶ MCP（core/mcp/）
│
└─ 以上都不是，想发明第 5 种编排级机制？
     └─▶ ✗ 停。先开 issue 讨论（见 §4 止血规则）
```

## 1. 四类分工（含红线）

### 1.1 编排 — Strategy Steps

| 项 | 值 |
|---|---|
| 位置 | `core/agent/strategy/steps/`（9 个） |
| 9 个 step | `pre_run` · `llm_call` · `tool_execution` · `compaction` · `resilience` · `continuation` · `stop` · `progress` · `finalization` |
| 装配 | `strategy/loop_strategy.py` dataclass 字段（`llm_call: LLMCallStep` …），策略工厂（`factory.py` / `minimal.py` / `explorer.py`）选型 |
| 职责 | 决定 loop **怎么做**：调不调 LLM、执行不执行工具、停不停、压缩不压缩 |

**红线**：step 可以改变控制流（它是编排）；一个行为只属于一个 step；新增 step 需要同时更新策略工厂与本文档。

### 1.2 观察 — Hooks

| 项 | 值 |
|---|---|
| 位置 | `core/hooks/composite.py`（`AgentHook` 基类 + `CompositeHook` 组合）；上下文 `hooks/context.py::AgentHookContext` |
| 生命周期点（11） | `before_run` · `after_run` · `before_iteration` · `after_iteration` · `on_stream` · `on_stream_end` · `before_execute_tools` · `after_tool_executed` · `on_tool_error` · `on_confirmation` · `on_error` |
| 触发 | `loop` 内 `_fire(name, ctx)` / `_afire_hooks(...)`；sync 路径经 `runtime/sync_bridge` |
| 职责 | 打点、审计、metrics、旁路通知 |

**红线**：**hook 不得改变控制流**——不许吞异常、不许改 messages、不许影响 stop/continue 判定。需要改变流程的需求 → 属于 step 或 injector，不是 hook。

**已知债**：`hooks/unified.py` + `hooks/adapter.py` 是 pre-P5 遗留，已标 DELETE-CANDIDATE（B5/B6），新代码只用 composite。

### 1.3 数据注入 — Injectors

| 项 | 值 |
|---|---|
| 位置 | `core/agent/context_injector.py`（`ContextInjector` Protocol + 4 实现） |
| 4 个实现 | `GoalContextInjector` · `TodosInjector` · `MemoryRetrieverInjector` · `GoalContinuationInjector` |
| 时机 | `order == 0` → `inject_per_iteration`（每轮 LLM 调用前，loop.py:877）；`order >= 100` → `inject_post_response`（无 tool_calls 时，返回 True 可让 loop 再转一轮，loop.py:928） |
| 职责 | 往 `messages` 追加内容（goal 状态、todos、memory 检索、目标续跑提示） |

**红线**：injector **只加数据不改流程**——除了 `inject_post_response` 返回 True 的「要求再转一轮」这一个受控出口。副作用必须幂等（每轮都会跑）。

### 1.4 能力面 — Toolsets / Skills / MCP / Plugins

| 机制 | 位置 | 形态 | 何时用 |
|---|---|---|---|
| **toolsets** | `agent/toolsets.py::TOOLSETS: dict[str, list[str]]` | 工具名分组注册表（16 组），按角色/场景裁剪可见工具 | 同一批 builtin 工具的**按场景打包** |
| **skills** | `core/skills/`（4 py） | SKILL.md 文档型能力，按需载入 prompt | 流程性知识（怎么做 X），不是代码 |
| **MCP** | `core/mcp/`（2 py） | 外部进程协议接入 | 第三方工具服务 |
| **plugins** | `agent/builtin_plugins.py` 等 | plugin 注册（工具实现本体） | 工具的**实现/发现**层 |

**红线**：能力面提供「可调用的东西」，**不参与编排决策**。工具按场景裁剪用 toolset，不要为每个角色复制一套工具实现。

## 2. 怎么选：三个真实场景

| 需求 | 选择 | 反例（不要用） |
|---|---|---|
| 「LLM 调用前注入本研究的目标上下文」 | injector（`GoalContextInjector` 已存在——先找现成的） | ✗ 新建 step |
| 「每次工具执行后把耗时上报 metrics」 | hook `after_tool_executed` | ✗ 改 ToolExecutionStep |
| 「回测失败 3 次就停止本轮」 | step `resilience`（或 loop 内既有 circuit breaker） | ✗ hook 里 raise |
| 「给研究员角色多一个爬虫工具」 | toolset 分组 + plugin 注册工具实现 | ✗ 新建机制 |
| 「压缩 prompt 时保留结构化摘要」 | step `compaction` | ✗ injector 里删改历史 |

## 3. 机制对比速查

| | steps | hooks | injectors | 能力面 |
|---|---|---|---|---|
| 改控制流 | ✅ | ❌ | ❌（仅「再转一轮」例外） | ❌ |
| 改 messages | 间接（compaction） | ❌ | ✅ 唯一入口 | ❌ |
| 触发频率 | 每轮固定槽位 | 11 个生命周期点 | 每轮 / 每次响应后 | 按调用 |
| 失败影响 | 影响本轮成败 | 必须自吞（best-effort） | 必须幂等 | 按工具语义 |

## 3.5 评分单源红线（2026-10-08 补）

因子质检评分（IC/IR/6 维/overall）**只出自** `core/factor_validate.py`——不内联复制公式、需要分数就调用 `compute_overall_score`。排除：组合构造评分（`config_runner._compute_scores`）。详见 `docs/archive/undated/phase1-research-discipline.md`。

## 4. 止血规则（自 2026-10-08 生效）

1. **不新增第 5 种编排级机制**。需求落在上表之外 → 先开 issue，评审是否扩展本表。
2. **新 hook / injector / step 必须补登记到本文档**（CI 有 doc 存在性检查的可加；目前靠 PR review）。
3. 存量重叠（unified hooks、chat_loop/role_factory 双入口）按既有 DELETE-CANDIDATE / P2 清单处理，**不新增同类**。

## 5. 事实核对（可复验）

```bash
# steps 清单
ls src/strategy_research/core/agent/strategy/steps/

# hooks 生命周期
rg "async def (before_|after_|on_)" src/strategy_research/core/hooks/composite.py

# injectors 时机
rg "inject_per_iteration|inject_post_response" src/strategy_research/core/agent/loop.py

# toolsets 注册表
rg "^TOOLSETS" src/strategy_research/core/agent/toolsets.py

# 已标待删
rg "DELETE-CANDIDATE" src/strategy_research/core/hooks/
```
