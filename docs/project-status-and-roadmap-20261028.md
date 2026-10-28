# 项目现状梳理与路线图 — 2026-10-28

> 实测数据（本机 Python 3.12 环境），非估算。用于对齐「现在在哪、下一步做什么」。

---

## 1. 代码规模（实测）

| 区域 | 文件 | 行数 |
|---|---|---|
| **src 总计** | 969 | **116,622** |
| core/agent | 90 | 18,195 |
| api | 46 | 13,272 |
| core/study | 44 | 12,371 |
| cli | 70 | 10,906 |
| core/workflow | 15 | 2,309 |
| core/storage | 9 | 1,684 |
| core/runtimes | 9 | 1,172 |
| core/mcp | 2 | 782 |
| core/orchestration | 7 | 696 |
| core/session | 6 | 605 |
| core/gateway | 7 | 513 |
| core/context | 3 | 155 |
| **tests** | 638 | **119,323** |
| **docs** | 253 md + 3 html | — |

单文件最大：`agent/loop.py` 1161、`agent/tools.py` 785、`agent/memory_manager.py` 723、`agent/compact.py` 669、`agent/event_store.py` 568。

**读数**：测试与源码接近 1:1（119k vs 117k）。`core/agent` 一个包占源码 15.6%，是最大的单块——它就是 C1 要处理的对象。

---

## 2. 架构现状（v6.2 五层）

| 层 | 状态 | 证据 |
|---|---|---|
| 渠道 | ✅ 已统一 | mailbox 唯一准入（3a/3c） |
| Gateway 控制面 | ✅ 已建成 | 单例 + SessionController + 不变量门禁 |
| 执行面 Runtime | 🟡 **新旧并存** | 新 GraphEngine 1172 行（含 fanout）+ 旧 AgentLoop 18k 行 |
| 平台数据面 | 🟡 部分 | EventStore 门面化已完成（S1）；messages 仍多写者 |
| 研究域 | ✅ 单引擎 | langgraph 分支已退役（P5-2，-2782 行） |

**关键结论**：入口/控制面/研究域三层已收口；**执行面与数据面是剩余的两块硬骨头**，且互为依赖（执行体迁移必须先回答持久化归谁）。

### 2.1 执行面：新旧并存的具体形态

新引擎侧（已完成）：
- `GraphEngine`：阶段图 + 条件边 + 协作中断 + fanout 复合节点（T1/T2）
- `react` 节点：流式 + 工具链 + 预算注入
- `research` preset：study 轮次唯一执行体

旧执行体侧（待迁移）：
- `AgentLoop` 1161 行 + 4 个 mixin（aegis/knowledge/…）+ strategy 步管道
- 3 个真实构造点：`chat_loop.py:129`、`executor.py:347`、`role_factory.py:144`
- 4 条消费链：**chat**（gateway/CLI）、**study**（role_factory）、**workflow**（node_types 5 处）、**swarm**

### 2.2 数据面：messages 写者实况（本轮门禁已钉死）

| 写者 | 目标 | 定性 |
|---|---|---|
| `projection_store.py` | 统一库 | ✅ 设计唯一写者 |
| `web_session.py:persist_message` | 统一库 | ⚠ 兼容直写，已加幂等防护 |
| `memory_manager.py` | 自己的 SQLiteStore | 遗留，C1 处理 |
| `core/session/db.py` | 独立旧库（schema 不兼容） | 遗留，C1 退役 |

**本轮新发现的硬约束**：两套 messages schema 不可合并（`id INTEGER` vs `id TEXT`、`timestamp` vs `created_at`）。已落为门禁测试，防止后人误合。

---

## 3. 遗留面清单（实测）

| 项 | 规模 | 归属 |
|---|---|---|
| 旧 chat 分支 + `new_entry_enabled` 门 | chat.py:875、tui/session.py:236 + 2 导出点 | **P5-1c（已挂起，业务稳定后）** |
| SSE 双轨（`sse_buffer` + `bridge_v2`） | 8 生产 + 7 测试文件 | P5-3（独立工程） |
| AgentLoop 18k 行 | 3 构造点 / 4 消费链 | C1（最大项） |
| MemoryManager 直写 messages | 723 行文件内 2 处 | C1 顺带 |
| `core/session/db.py` 旧会话栈 | 605 行包内 | C1 后退役 |
| `_archived/` + `tests/attic/` | 13 + 29 文件 | 保留（零运行时成本） |
| `to_llm_message` 等历史别名 | 少量 | 随消费方迁移清 |

---

## 4. 测试健康度（实测基线）

**PR 道实测基线（2026-10-28，排除 6 个已知无限阻塞的 live/e2e 套件）**：

```
9632 passed, 127 failed, 61 skipped, 7 errors — 363s
```

**归因结论（已用历史 commit 对比验证）**：这 127 个失败**全部预存**，与本轮所有改动无关。

验证方法：在 `616085ee`（安全改动前的提交）上跑同一批失败最集中的 5 个文件，
失败集合与当前 HEAD **逐条完全相同**（24/25 的差异来自随机序波动）。
抽样确认的失败族：

| 失败族 | 数量 | 性质 |
|---|---|---|
| `test_shell_tool` | 6 | POSIX 命令（pwd/ls/python -c）在 Windows 上跑不了——**环境性** |
| `test_runner_dag_helpers` | 13 | 遗留 runner DAG 助手，旧执行体耦合 |
| `test_route_agent_event_*` | 11 | 事件路由，同属旧执行体 |
| `test_compact_*` | 13 | 压缩管道，同属旧执行体 |
| `test_study_runner_integration` | 7 | study runner 集成 |
| `test_webui_e2e` / `test_webui_api` / 其余 | ~77 | 分散，多为旧路径/环境断言 |

**这些失败高度集中在旧 AgentLoop 生态**（runner/route/compact/tools）——
即 C1 要迁移的对象。预期它们随 C1 迁移自然消失，而不是逐个修。

**已知无限阻塞（必须先分道）**：
`test_e2e_full_flow`（approval 1800s）、`test_sse_e2e`、`test_e2e_chat`、
`test_e2e_comprehensive`、`test_webui_e2e`、`test_webui_e2e_playwright`、
`test_chat_e2e_playwright`——真实 uvicorn + socket 长等待。

---

## 5. 本轮（2026-10-27/28）已交付

| 批次 | 内容 | 提交 |
|---|---|---|
| B1-a/b | GraphEngine 阶段图 + research preset | 早期 |
| F1-F4 | fanout 多 subagent（T1/T2 + 预算树） | 早期 |
| 3c + 阶段 4 | scheduler 迁移 + 旧队列/门删除 + 测试隔离 | 早期 |
| P5-1 / P5-1b | Gateway chat 历史/持久化/流式/工具 + MiniMax think 修复 | `af4cb235` `0d25b702` `3d03ed65` |
| 搜索 | LIKE 替代 FTS trigram | `d73054d1` |
| HITL 门 | 审批门 + resume（方案 A）+ 评审修复 | `71d5cf34` `8623d42d` `f639ee2d` |
| P5-2 | langgraph 分支退役（-2782 行） | `5c4c544` |
| P5-4 | ContextHub W0 + pre-run 迁入 + 多 agent 评审修复 | `7e6e183d` `2f17a1da` |
| **P5-5** | **安全债 ×4 清零** | `174ecea1` `4d6696da` `0c02c346` `87802d5e` |
| **0.4 S3** | messages 白名单门禁 + 幂等防护 | `ed883eae` |

---

## 6. 下一步路线（按依赖排序）

> **2026-10-28 修订（选项 B + less-is-more）**：B2/B3 的原始前提「执行体迁 react」
> 已被推翻——实测 react 节点缺 AgentLoop 四项无条件能力（工具结果 30k 截断、
> 瞬时错误重试、deny guard、只读并行），替换会造成真回归。架构目标精化为
> 「GraphEngine 是唯一**编排** runtime，AgentLoop 是执行原语」。
> 依据：`docs/stage-c1-wp-agent-node-design-20261028.md`。
> **注意 B2 一行：study 链已经符合选项 B，无需迁移。**

### 立即可做（无需前置）

| # | 事项 | 工期 | 说明 |
|---|---|---|---|
| A1 | **测试分道 + 预存阻塞清理** | ✅ 已完成 | 分道白名单 + 180s 兜底 + 失败基线（100→101 条，见 `tests/baselines/`） |
| A2 | P5-3 SSE 单轨 | ✅ 已完成 | `_event_generator_v2` 走 `EventStore.subscribe`（`0d25b702`） |
| A3 | P5-1c 旧 chat 分支 | 0.5-1d | 已挂起，业务稳定后 |

### 依赖链（执行面 + 数据面）

| # | 事项 | 工期 | 依赖 | less-is-more 审视 |
|---|---|---|---|---|
| B1 | C1 阶段一：chat 链 | ✅ 已完成 | — | react 承接 chat 单轮，B1 八项等价证据在 `test_b1f_equivalence.py` |
| B2 | ~~study 链迁 react~~ | **0d** | — | **已符合选项 B**：编排在 `graph_preset.py`，执行是 AgentLoop。原计划纯重复 |
| B3 | C1 阶段二：swarm + workflow 的**编排**接入 GraphEngine | 1-2d / 评估后定 | B1 | 执行体不动。swarm：`SwarmRuntime` 层调度换成 GraphEngine 节点，agent 执行继续走 `AgentExecutor`（已有适配器，勿新写）。workflow：dynamic subgraph / 段门重规划仍是真问题，允许结论「不迁」（须文档化触发条件） |
| B4 | MemoryManager 事件化 + 旧会话栈退役 | 1-2d | B3 | 沿用 `projection_store.py` 现有写入路径，不新增写者 |
| B5 | ContextHub W1/W2 | 2-3d | — | provider 协议已冻结（`core/context/protocol.py`），只加实现不加协议 |

### 收尾

| # | 事项 | 工期 |
|---|---|---|
| C1 | 审计门② 五项（清单 §6） | 1d |
| C2 | 文档对齐（253 份 md 中去重/归档过期项） | 1-2d |

---

## 7. 建议顺序

```
B3（swarm 编排迁移，1-2d；workflow 评估是否迁或文档化不迁）
   ↓
B4（数据面收尾）→ B5（ContextHub 尾项）
   ↓
A3（P5-1c，可随时插入）→ C1/C2（审计门 + 文档）
```

**为什么 B3 只剩 1-2 天**：原估 6-10d 建立在「重写执行体」上。选项 B 确立后，
agent 执行复用 `AgentLoop`（study 已如此）与 `AgentExecutor`（swarm/workflow
已有适配器），要迁移的只有各链自己的**调度循环**。

**A3（P5-1c）可以随时插入**：它不阻塞任何东西，纯粹是「入口只剩一条路」的整洁性收益。

---

## 8. less-is-more 约束（2026-10-28 起，约束所有 worker）

> **能用现有代码就不新写；能改造完成就不重写。**

执行细则（写入契约 `docs/stage-c1-team-contract-20261028.md` §11）：

1. **写新函数前，先列出查过的现有入口**（类/函数名 + 为什么不能复用），
   写进设计文档。查不到 = 禁止新写。
2. **新代码零生产调用方 = 死代码**，下一次提交必须删除
   （实例：W-P 首版的 `agent_node`，零调用方，已删）。
3. **同包内纯函数禁止复制**，直接 import
   （实例：`_first_two_sentences` 与 `executor.first_two_sentences`，已删）。
4. **改造优先于新建**：扩展参数/注入点算改造；平行的第二套实现算新建。


