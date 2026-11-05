# 业界对标 — OpenClaw 架构模式与我们的差距

> 调研日期：2026-10 · 对象：OpenClaw（github.com/openclaw/openclaw，MIT，392k★，pnpm monorepo，TS）
> 资料：README · /concepts/architecture · /concepts/agent-loop · /concepts/agent-runtimes · /concepts/session-controller · /concepts/system-prompt · /plugins · /gateway/security · /start/why-openclaw
> 目的：回答「我们的设计怪在哪」，并给出**可执行**的差距清单。

---

## 0. 结论（三句话）

1. **我们的「怪」不是层次画错了，是把「控制面」和「执行面」放在同一层，而且控制面有 4 套。**
2. OpenClaw 的结构是：**1 个控制面（Gateway）+ N 个可换执行面（agent runtime/harness）**；它把「拥有状态/策略/调度/审计」与「跑一次模型循环」彻底分开。
3. **我们有两个不可替代的资产**（研究方法论层、70 题 golden + nightly 门禁），**不要为了像它而砍掉研究编排**；该借的是它的**结构纪律**，不是它的功能表。

---

## 1. OpenClaw 的分层（它自己的语言）

```
Gateway（单进程 · 本地控制面）
  拥有：session · 队列 · 策略 · 状态 · 审计 · 调度器（唯一一个 GatewayScheduler）
  ├── Agent Runtime（可换 harness，可缺省回退）
  │      openclaw(内置) / codex / copilot / claude-cli
  │      拥有「一次模型循环」；平台保留 channels/sessions/policy/state
  ├── Channels（插件：WhatsApp/Telegram/Discord/…20+）
  ├── Model Providers（插件）
  ├── Skills / Tools（插件）
  ├── Nodes（远端执行面：camera/screen/location）
  └── Session Controller（turn 准入的唯一 owner）
```

**它自己的架构命题**：「trusted gateway, untrusted execution, policy enforced by code」。

## 2. 与我们的关键结构差异

| 维度 | OpenClaw | 我们（实测） | 判读 |
|---|---|---|---|
| **控制面数量** | **1**（Gateway） | **4**（Session / Study / Swarm / Workflow+Goal） | ⚠️ **过重**。最「怪」的来源 |
| **控制面 vs 执行面** | 彻底分离 | **混在同一层** | ⚠️ **这是根因** |
| **模型循环** | **可换 runtime**（harness 插件） | AgentLoop 硬编码在引擎里（`role_factory.build_agent_loop` / `chat_loop.build_chat_agent_loop` 两套） | ⚠️ 我们是「两套固定」，它是「N 套可选 + 回退」 |
| **prompt 装配** | 平台**唯一所有者**；runtime 只贡献 3 个具名段或缓存边界前缀 | 4 个 PromptBuilder + injector 各自拼 | ⚠️ 应收敛（ContextHub 方向对） |
| **上下文预算** | 分层超时（per-attempt / provider HTTP / idle watchdog / cron / CLI backend），fallback 拿新预算 | 3 维（tokens/cost/time），单层 | ⚠️ 我们太粗 |
| **上下文预算归属** | 「谁产出谁拥有预算」 | 混 | ⚠️ |
| **缓存边界** | 稳定内容在缓存边界之上，目录在下；刷新不改变前缀字节 | **无此概念**（44 工具每次重算 tool_list） | 🔴 成本盲区 |
| **写者栅栏** | `activeWriterRunId` + `expectedWriterRunId`，提交事务校验；被取代的 run 不能提交陈旧数据 | `fence` 命中 35 处**全在 content_formatter/transcript/prompt_schema_extractor**（非并发栅栏） | 🔴 **缺一条真不变量** |
| **真值表达** | 6 个**正交**答案：slot owned / admissible / execution active / injectable / abortable / settled | 单一 `state` 字符串 | ⚠️ |
| **身份分离** | 逻辑 key ≠ 存储 incarnation ≠ 操作 ≠ 后端 attempt（4 个身份） | session_id/attempt_id/message_id 有 NewType ✅，但无 incarnation/operation 分层 | ⚠️ |
| **调度器** | **一个** kernel scheduler；插件注册任务；不持久化第二份调度状态 | scheduler.py 1050 + study runner + cron 三处 | ⚠️ 可能有重复调度状态 |
| **协议/能力版本** | `openclaw.compat.pluginApi` / `minHostVersion` / `activation.onCapabilities`；**不满足即拒绝，不降级** | `PROTOCOL_VERSION` **0 处** | 🔴 |
| **插件边界强制** | **CI import guards**（不是靠约定） | 我们刚提 0.5 CI 门禁 | ✅ 方向一致 |
| **插件信任** | 记录溯源决定（`provenance-missing` / `owner-ambiguous`）；`--force` 不授予信任 | 有 requires/provides，无溯源记录 | ⚠️ |
| **策略优先级** | deny > allow > 逐项启用；排他 allowlist 与排他 slot | toolsets 只是「名字→工具名列表」，无 deny/排他语义 | ⚠️ |
| **hook 语义** | 精确：`block:true` 终局、`block:false` 空操作、compaction hook 不可否决 | 我们也有「hook 不得改控制流」红线 | ✅ **一致** |
| **风险姿态** | 「身份 → 范围 → 模型（最后）」；风险在代码里执行，**不在提示词里请求** | InvariantLayer（Tier 0 冻结）是同一思想 | ✅ **我们更硬**（他们 sandbox 默认关） |
| **记忆清除** | 记忆可归因清除；forgotten-session 记录防重摄取；明确记录「删除不覆盖什么」 | 有 forget 20 处，无 retention/purge 概念 | ⚠️ |
| **标准** | MCP / A2A 1.0 / ACP / AgentSkills / OpenAI 兼容 API / OTel | MCP ✅，其余无 | 可选 |

## 3. 最重要的两条结构启示

### 3.1 「控制面 1 + 执行面 N」——这才是我们该抄的

**我们的现状**（把控制面/执行面标出来）：

| 部件 | 角色 | 拥有的东西 |
|---|---|---|
| SessionService | 控制面 | 队列 · 状态 · 持久化 · 停 |
| StudyRunner | 控制面 | 轮循环 · 停因(9) · HITL · 预算 |
| AgentLoop | **执行面** | 一次 ReAct 循环 |
| phase_engine | **执行面** | 跑一轮（phases / langgraph） |
| SwarmRuntime | 半控制半执行 | DAG 批次 |

→ **控制面 3 套（Session/Study/Swarm）+ 执行面 2 套 + 1 混血**。每加一种驱动就多一套控制面 → 「怪」。

**OpenClaw 的收敛**：

```
编排层 = 1 个控制面（SessionController）
          7 阶段状态机：idle/admitting/preparing/running/finishing/settling/mutating
          队列模式：steer / followup / collect / interrupt
          停因 → 终态 + 续轮决策
          ├─ Director（策略）：下一步做什么
          └─ 保留：HITL 预留 · 预算 · 审计
能力层 = N 个可换执行面（AgentRuntime 协议：prepare → run → settle）
          AgentLoop · phase engine · swarm DAG …（同一协议，不同实现）
```

**关键推论（重要）**：OpenClaw 的 session controller 已经能表达「多轮研究」——
- **研究轮 = 一个 session 的第 N 个 turn**
- **directive（人给研究方向）= `steer`**（插队，不打断当前轮）
- **HITL pause = 预留在 admission barrier**
- **停因（TARGETS_MET / STAGNATION）= session 终态 + 是否续轮**
- **Swarm DAG = 另一种 turn 执行面（runtime）**

所以**我们的 StudyLoop 未必需要是独立的控制面**——它可以是「SessionController + Director 策略 + 一个 research runtime」。这比"编排层 4 格"更接近业界，也更省。

### 3.2 我们的「C. chat_loop / role_factory 收口」建议是错的

我们之前建议：把两套构造**合并成一套**。
OpenClaw 的做法：**不合并，做成 runtime 选择 + 声明式回退**：

> 选择序：model-scoped 策略 > provider-scoped > `auto`（注册方认领）> 内置 fallback
> 「一旦某个 harness 开始执行，它的失败不会通过另一个 runtime 重放」

→ 我们的正确动作是：**定义 `AgentRuntime` 协议 + 选择器**，让 `role_factory` / `chat_loop` / 未来的 research runtime 成为**可声明的 runtime 实现**，而不是两个互不相干的工厂函数。

## 4. 对既有计划的修正

| 原建议 | 修正后 | 理由 |
|---|---|---|
| 0.11 AgentLoop **移入能力层** | 方向对，但**终点是「成为 runtime」**（实现 `AgentRuntime` 协议），不只是搬目录 | OpenClaw 的 harness 是插件；只搬目录解决不了「可换」 |
| C. chat_loop/role_factory **收口成一套** | 改为**做成 runtime 选择 + 声明式 fallback** | 同上 |
| 编排层 4 格（3 驱动 + Director） | 收敛为 **1 控制面（SessionController）+ N runtime**；StudyLoop 降为「Director 策略 + research runtime」 | OpenClaw 的 session controller 已能表达多轮研究 |
| RunStatus 单一 state | 改为**6 个正交事实**（owned/admissible/active/injectable/abortable/settled） | OpenClaw 明确「真值来自不同事实的组合，不是一个布尔」 |
| 上下文预算 3 维 | **分层预算**（per-attempt / provider / idle / 总）+「谁产出谁拥有」 | OpenClaw 的 timeout 表 |
| —（新增） | **write fence**：被取代的 run 不能提交陈旧数据 | 我们缺这条并发不变量（实测 `fence` 全在无关文件） |
| —（新增） | **prompt 缓存边界**：稳定内容在上、目录在下，刷新不改变前缀字节 | 44 工具每次重算 tool_list = 成本盲区 |
| —（新增） | **协议版本 + 不满足即拒绝**（不降级） | 我们 `PROTOCOL_VERSION` 0 处 |
| —（新增） | **记忆清除边界**（可归因清除 + 「删除不覆盖什么」明示） | 自主研究会产生大量记忆，可清除性是产品要求 |

## 5. 我们的优势（不要被对标改掉）

| 资产 | 业界对标 |
|---|---|
| **研究方法论层**：假设 → 派生 → 证伪(claims) → AEGIS 新颖性/回归门 → 两阶段 journal | **无直接对标物**。OpenClaw/DSH 都没有 |
| **70 题 golden eval + nightly 跟幅门禁 + pyright 回归门禁** | 多数项目没有可比的门禁资产 |
| **类型债治理机制**（分批 + triage + 门禁） | 罕见 |
| **多编排域**（会话 + 研究 + DAG + 目标账本） | OpenClaw 只有会话域。**我们不该为此自砍** |

## 6. 明确不照抄

| 不抄 | 理由 |
|---|---|
| 单 Gateway 极简（一个控制面打天下） | 我们是研究系统，多编排域是产品形态，不是复杂度冗余 |
| 20+ 渠道 / 设备节点 / 云 worker | 场景不同 |
| 「一次 turn」式 agent loop | 我们的核心是多轮研究 |
| 把「研究轮」降格成「session 的第 N 个 turn」而**不补**研究语义 | 可以统一控制面，但 claims/AEGIS/journal 仍是一等公民 |
| sandbox 默认关 | 我们的 InvariantLayer 更好，保留 |

## 7. 建议的下一步（按杠杆排序）

| 序 | 动作 | 估工 | 收益 |
|---|---|---|---|
| **1** | 写 `core/agent/runtime.py`：**`AgentRuntime` 协议**（prepare/run/settle 三段）+ **选择器**（model-scoped > provider-scoped > auto > 内置）+ 声明式 fallback；把 `role_factory` / `chat_loop` 收成两个 runtime 实现 | 2d | 打通「可换 harness」，并修掉我们此前判断错误的 C 刀 |
| **2** | 编排层收敛为 **1 控制面 + N runtime**（SessionController 状态机 + 队列模式 + Director 策略；StudyLoop → research runtime） | 3–4d | 消除「4 套编排」的怪异感，是最大结构性简化 |
| **3** | **write fence**：run 级 `activeRunId` + 提交校验（对齐 OpenClaw 的 writer claim） | 1d | 补一条真并发不变量（现在没有） |
| **4** | **prompt 缓存边界 + 分层预算**（ContextHub 扩展）：稳定段在上、目录在下；预算按来源归属 | 2d | 上下文成本可控（现在每次重算 tool_list） |
| **5** | 协议版本化 + 不匹配即拒绝；CI import 门禁（把 0.5 扩到协议面） | 1d | 接缝稳定性的地基 |

> 这 5 项的共同点：**都是「减结构 / 加纪律」，不是「加功能」**——与当前「已经很复杂」的判断一致。

## 8. 引用

- OpenClaw README（github.com/openclaw/openclaw）· 架构案例 /start/why-openclaw
- 架构 /concepts/architecture · Agent loop /concepts/agent-loop · Runtimes /concepts/agent-runtimes
- Session controller /concepts/session-controller · System prompt /concepts/system-prompt
- Plugins /plugins · 信任模型 /gateway/security/trust-model
- 对照 Hermes Agent（NousResearch）作为「单进程 harness」参照
