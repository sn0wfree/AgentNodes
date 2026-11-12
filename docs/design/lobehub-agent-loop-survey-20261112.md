# LobeHub 产品调研 + Agent Loop / 任务调度源码对照（2026-11-12）

> **性质**：竞品调研（产品 + 源码级架构对照），供后续讨论
> **关联文档**：`loop-unification-design-20261111.md`（统一引擎实施设计）·
> `loop-engine-industry-survey-20261111.md`（业界证据）· `loop-engine-inventory-20261111.md`（现状实测）
> **方法说明**：git clone 与 tarball 在当前网络下均极慢/截断，改走 GitHub raw API
> 定点抓取 **12 个关键文件**（本地存于 `D:\zcode_data\.zcode\tmp\lh_*.ts`），
> 配合 `gh` 代码搜索定位调用链。覆盖从循环驱动器到分布式调度层的完整链路。
> 未逐文件覆盖的部分（如 `executors/callLlm.ts` 压缩管线、mecha 完整规则）标注于 §6。

---

## §1 产品概览（第一轮调研结论）

**一句话定位**："Super-Agents on Your Team"——从开源聊天 UI 起家，2025–2026 转型为
多 Agent 协作平台，自称「首席 Agent 运营官（CAO）」：用户定目标，LobeHub 组建 AI
团队 7×24 跑任务，经 IM 汇报。

### 1.1 演进时间线

| 阶段 | 时间 | 关键变化 |
|------|------|----------|
| LobeChat 诞生 | 2023 | 开源多模型聊天框架（Next.js），插件市场 + Agent 市场 |
| Agent 转向 | 2025 | 6 月完成数百万美元种子轮；产品转向 Agent 方向 |
| **2.0 发布** | 2026-01 | 从聊天框架转型**多 Agent 协作平台**，更名 LobeHub |
| 2.2+ | 2026-05 起 | Tasks、Agent Teams、结构化记忆、Pages、外部 Agent 接入 |

**公司**：创始人徐文彬（CEO），早期 2 人 → 约 20 人，AI 驱动研发。
⚠️ 信息冲突：有中文来源称种子轮由红杉中国/经纬参投，但另一轮检索**未找到公开证据**，
已披露的仅为种子轮本身，投资方存疑。

### 1.2 核心功能（2026 现状）

- **IM 渠道**：Discord / Slack / Telegram / 微信 / 飞书等 7 个适配器；Agent 自主"接活"
- **Agent 团队**：顺序 / 并行 / 迭代 / 辩论四模式；并发任务按档位 1→10
- **结构化记忆**（付费档）、Tasks 定时任务、Pages 长文写作、资源库
- **外部 Agent**：挂 Claude Code / Codex；GitHub 集成（**PR 合并即任务验收**）
- **生态市场**：Agents / MCP Servers / **Skills（SKILL.md 开放格式，称 332K 规模）** / 图标库；创作者激励
- 经典能力保留：多模型接入、RAG 知识库、多模态、Artifacts 站点

### 1.3 技术栈与开源数据

- Monorepo：Bun + pnpm workspace（`apps/` + `packages/` + `src/`），TypeScript
- Next.js 16 + React 19 + Vite SPA；PostgreSQL + Drizzle ORM、Redis、RustFS、SearXNG
- 部署：Docker Compose / Vercel
- GitHub **85.1k stars / 16.2k forks**，16k+ commits
- 许可证：自定义 "LobeHub Community License"（自托管/内部使用免费，**衍生商业产品需商业授权**）

### 1.4 商业化（Cloud 订阅）

| 档位 | 月付 | 年付 | 要点 |
|------|------|------|------|
| Free | $0 | $0 | 标准模型，50 万 credits/月，1 并发，无 Memory |
| Plus | $20 | $216 | 高级模型 + Agent Memory，2 并发，2GB 存储 |
| Pro | $100 | $1,080 | 5× 用量，Ultra 模型，5 并发 |
| Max | $200 | $2,160 | 20× 用量，10 并发 |

计量制：Credits 折算 token，可 top-up；各档 BYOK。Enterprise 私有化另议。
**观察**：Memory 与并发是付费分层的主要杠杆 → 这两项是用户感知最强的 agent 基础设施
（与本仓库 U 批次 budget / pause-resume 方向吻合）。

### 1.5 竞品速览

| 维度 | LobeHub | Dify | Coze |
|------|---------|------|------|
| 定位 | 多 Agent 协作 / AI 员工团队 | 企业级 LLM 应用开发平台 | 零代码 Bot 开发（字节） |
| 上手门槛 | 对话体验优先，偏个人/小团队 | 需技术背景 | 拖拽式无代码 |
| 差异点 | IM 集成 + Agent 自主性 + 开源生态 | 企业基础设施 + 私有化 | C 端易用性 |

---

## §2 lobehub 源码架构拆解（四层）

### 2.1 L1 宿主无关循环驱动器 — `packages/agent-runtime/src/loop/index.ts`（仅 168 行）

```ts
while (!reason) {
  result = await step({ context, state, stepIndex })   // step 由宿主注入
  reason = stopCheck(state, nextContext)                // 可替换
}
```

- **循环体刻意不含任何业务**（原文注释）："A step means very different things per
  host — on the server it claims a distributed lock, persists state, dispatches hooks
  and records a trace; in the browser it is a direct `runtime.step`."
  → **薄循环 + 胖宿主 step**（对照本仓库：胖引擎 skeleton + 适配器）。
- `AgentLoopStopReason` 七值：`done / error / interrupted / parked / cost_limit /
  max_steps / no_next_context`。设计动机与本仓库 U1 出口漏斗**逐字同源**：
  "a stop reason that is computed once cannot drift between hosts the way the
  termination conditions did."
- `parked` 是**非终态**：人工审批或异步工具/子 agent 结果等待。

### 2.2 L2 Instruction 虚拟机 — `core/runtime.ts`（1135 行）

`AgentRuntime.step()` = **instruction-set 状态机**：

- `agent.runner(context, state) → AgentInstruction[]`（Brain 出指令）→ executor 执行
- 指令集：`call_llm / call_tool / call_tools_batch / compress_context / finish /
  request_human_approve|prompt|select / resolve_blocked_tools / exec_sub_agent(s)`
- executor 三层覆盖：builtin < config < agent
- **每步 `structuredClone(state)`**：不可变状态快照 = 可持久化、可跨进程恢复的根基
- **`forceFinish`**：超 `maxSteps` 不立即停——置标志，工具跑完，下一次 LLM 调用
  **剥 tools + 注入 summary prompt** 强制文字收尾
  （与本仓库 `wrap_up_nudge` 0.8×max_iter + `force_final_text` 末轮 tools=None **完全同构**）
- `costLimit` 三策略：`stop / interrupt(可恢复) / continue+警告事件`（本仓库
  StopPolicy 只有停，无策略选择）
- 批量工具 `pMap` 并发，各自从 `clone(baseState)` 起步再 merge（usage/cost 按
  byTool/byModel 细粒度合并）
- **parked 结算**：异步工具挂起时，其后指令不丢弃——`resolve_blocked_tools` 立即执行，
  `request_human_approve` 存 `deferredHumanApproval` 待恢复后补发
- `interrupt/resume`：状态级中断（`canResume` 标志 + `interrupted` 事件）与恢复

防 runaway：`utils/toolCallRepeatGuard.ts`——`identifier+apiName+规范化args` 签名
**累计**计数 ≥20 → 停 turn（为合法轮询留余量；本仓库 no_progress 是 md5 hash
**滑窗 3** + **审批闸可批准继续**，更激进但带人工通道）。

HITL 策略判定：`core/InterventionChecker.ts`——安全黑名单（硬闸，auto-run 也拦）+
规则匹配（exact/prefix/wildcard/regex → policy required/never…），**无配置默认
fail-safe `required`**。

### 2.3 L3 IO 端口 — `transport/host.ts`

`AgentRuntimeHost = { operation, transports, lifecycle }`，9 个传输端口
（`messages/stream` 必需；`tools/llm/compression/subAgent/blob/operationStore` 可选，
executor 用时断言）。包与宿主的唯一接缝——等价于本仓库 capability 注入 + L0-L4
分层纪律：目标相同（**核心不反向依赖宿主**）。本仓库 U4 halt_check 注入的教训，
这里对应 `AgentRuntimeHostResolver`（可静态可 per-step 解析）。

### 2.4 L4 服务端宿主 — `apps/server`（durable 调度层，最重的部分）

**`AgentRuntimeService/executeStep`（392 行）——调度单元是"单步"，不是"一次 run"**：

```
lockFree 快路径 → tryClaimStep(分布式锁 TTL+ownerId) → touchRunning(租约)
→ 心跳 → 加载 AgentState(Redis/DB) → 延迟 init → step_start 事件(uiMessages SoT)
→ beforeStep hooks → abort 轮询(Redis 中断哨兵→AbortSignal) → 创建 runtime
→ runtime.step() → 保存状态+发流事件 → afterStep hooks → trace 追加
→ scheduleNextStep：QStash 队列下发下一步 或 inline 连跑(先 saveInlineResume 信封再交接)
→ !shouldContinue → finishOperation
finally: trace flush / 流 drain / 释放锁
```

- **每步一跳**：下一 step 作为消息进 QStash 队列（`stepPolicy` 定延迟：基础 50ms、
  工具后 +50ms、错误退避 ≤1s；优先级 waiting_for_human=high）
- `inlineContinuation` 优化允许同进程连跑，但**必须先落 inline resume 信封**
  （`executeStep/continuation.ts`）——进程死在半步，重投消息能从已到达步骤恢复；
  信封落盘失败 → 回退队列往返（慢一步但保恢复路径）
- **挂起语义分裂**（Coordinator 注释）：`waiting_for_human` = **流终态但状态可恢复**，
  恢复走**新 operationId**（新事件流）；`waiting_for_async_tool` = **同 operationId** 恢复
- **`StaleOperationReaper`**（495 行）：cron 扫租约冻结的 running 操作 → 优先
  **redrive**（重排队下一步，Redis 状态存活 2h TTL）→ 状态没了或 redrive 预算
  （3 次）耗尽才 **abandon**（用户可见错误）。生产教训（注释原文）：
  "production carries ~21k running rows whose lease froze more than a day ago"。
  安全三件套：`claimStaleRedrive` 原子认领（select+UPDATE 同语句）、`tryClaimStep`
  锁竞态仲裁、终态 ACK
- OTel `invoke_agent` span 全程包裹；trace partial 在跨进程边界强制 flush
  （Redis 10MB 限制教训：context-engine 重载荷 2.7MB/step 改走 trace 旁路，减 500×）

### 2.5 多 agent 团队 — `groupOrchestration/`

同一模式的第二次应用：**Supervisor（LLM 结构化决策）→ 确定性翻译状态机 → executor 执行**。

- 决策动词：`speak / broadcast(并行) / delegate(委托) / execute_task(s)(异步任务) / finish`
- 护栏：`maxRounds` 计数 + `skipCallSupervisor`
- 产品宣传的"顺序/并行/迭代/辩论"落在这 176（Runtime）+ 164（Supervisor）行
- Runtime 自身也是同款 step 循环（`structuredClone` + maxSteps + abort 检查）

### 2.6 运行配置 — `packages/mecha`

`AgentConfigSnapshot` 是纯数据输入：浏览器从 Zustand 缓存读、服务器从 DB 读、设备另取，
**规则只有一份**供三宿主共用（原文："Hoisting the reads into one value is what lets
a single implementation serve the browser, the server, and a device"）。
又是同一个"一次计算，处处不漂移"哲学。

---

## §3 逐维度对照表

| 维度 | AgentNodes（kernel/agent） | lobehub（agent-runtime + server） |
|---|---|---|
| **循环体** | `ReActSkeleton`：PRECHECK→REASON→①→ACT→②→OBSERVE→③ 四闸状态机（in-process） | `runAgentLoop` 薄 while + `AgentRuntime.step()` instruction VM |
| **决策点** | `deciders.decide(input,ctx)→Decision`（STOP/EXIT/CONTINUE/PROCEED）+ `and_/or_` + ExternalTermination | `resolveStop` 可替换回调 + runtime status 机 + `shouldContinueExecution` |
| **退出漏斗** | 9 出口 → `LoopFinalize` 幂等（`_ending_emitted`） | finish 指令/终态 status → Coordinator `hasEnteredStreamEndState` 恰一次 `agent_runtime_end` |
| **事件流** | `run_stream` 双通道（事件桥 drain）+ `on_event` 回调 | events 数组累积 + StreamEventManager Redis pub + gateway 推送 + uiMessages SoT 快照 |
| **HITL** | H1 `PauseHandle`：**in-loop** pause→await→**重跑闸位**，红线=无副作用重放 | `parked` status：**流结束**，审批后**新 operationId** 恢复；resume=separate entry point |
| **审批触发面** | no-progress 闸（一个闸位）；另有 PermissionGateway（工具级）、halt（进程级） | 三种**指令类型**：approve/prompt/select——比"no-progress 才问"通用 |
| **预算** | 三维 tokens/cost/time + `budget_exhausted` 事件 | 仅 cost，但三策略 stop/interrupt/warn + maxSteps→forceFinish |
| **wrap-up** | 0.8×max_iter nudge + 末轮 tools=None | forceFinish：剥 tools + summary prompt —— **同构** |
| **no-progress** | hash 滑窗 3 + **审批闸可批准继续** | 签名累计 20 直接停，无审批通道 |
| **每步状态** | 进程内 messages list + trace JSONL | `structuredClone` 快照 → Redis(2h TTL)+DB 每步持久化 |
| **调度单元** | 一次 run = 一个 asyncio task | **一个 step = 一条 QStash 队列消息**（可 inline 连跑省往返） |
| **崩溃恢复** | 无跨进程恢复（H1 是内存 Future） | 分布式 step 锁 + inline resume 信封 + Reaper redrive/abandon |
| **工具执行** | 同步/异步双路 + circuit breaker + retry policy + 只读工具并行 gather | pMap 并发批次 + InterventionChecker 审批策略 + resolve_blocked/aborted_tools |
| **多 agent** | SwarmWorker parity（wrap-up 对齐） | Supervisor 状态机（speak/broadcast/delegate/execute_task + maxRounds） |
| **分层纪律** | L0-L4 import 门禁 + capability resolve | transport 端口接口 + host resolver 注入 |
| **配置解析** | LoopSpec 分组折叠（budget/runtime/capability/context/permission/scope） | mecha `AgentConfigSnapshot` 纯数据输入，规则单份多宿主共用 |

---

## §4 三个值得带走的发现

**1. 两边独立收敛出了同一组设计，说明这些是"对的东西"。**
出口 reason 单点计算防漂移（本仓库 U1 出口漏斗 vs `resolveStopReason`）、决策/执行分离
（deciders vs supervisor.decide + instruction VM）、wrap-up 双件套、循环防护
（hash 窗口 vs 签名计数）。loop-unification 的方向在业界不是孤例；
`loop/index.ts` 的 "computed once cannot drift" 注释可直接引进 survey 文档作证据。

**2. 根本分歧点：durable execution 做没做进循环层。**
lobehub 把「每步持久化 + 队列调度 + 分布式锁 + 僵尸回收」做成循环的宿主层——代价是
server 端 2000+ 行协调代码与挂起语义分裂（`waiting_for_human` 走新 operationId）。
本仓库 §9 明确**不做**内建 checkpoint/durable（编排已有 workflow 层）——合理的范围裁剪。
但 H1 pause/resume 若将来要跨进程/跨会话恢复，lobehub 的
**「信封先落盘再 inline 连跑」**（`continuation.ts`）是现成的最小实现参考——
恰好覆盖 H1 单次挂起不变量未敢碰的嵌套/跨进程场景。

**3. 审批语义粗细互补。**
lobehub 的 `request_human_approve` 停整个 turn、恢复走新 operationId（粗）；
本仓库审批闸是循环内闸位、批准后原地继续（细，且修过"批准仍退出"bug）。
反观 lobehub 把审批做成**指令类型**（approve/prompt/select 三种），触发面比
"no-progress 才问"通用。若将来把审批逻辑迁到 H1 pause/resume 上
（设计文档列为 U6 后可选），三种人工介入指令是可参考的功能面。

---

## §5 附：证据链（抓取文件清单）

| 文件 | 本地路径（`D:\zcode_data\.zcode\tmp\`） | 行数 | 关键内容 |
|---|---|---|---|
| `agent-runtime/src/loop/index.ts` | `lh_loop_index.ts` | 168 | 薄循环驱动器 + 七值 stop reason |
| `agent-runtime/src/core/runtime.ts` | `lh_runtime.ts` | 1135 | instruction VM + forceFinish + costLimit 三策略 |
| `agent-runtime/src/core/InterventionChecker.ts` | `lh_intervention.ts` | 247 | 安全黑名单 + 审批规则匹配（fail-safe required） |
| `agent-runtime/src/executors/registry.ts` | `lh_registry.ts` | 55 | 服务端 executor 工厂（host 绑定） |
| `agent-runtime/src/transport/host.ts` | `lh_host.ts` | 51 | AgentRuntimeHost 端口接口 |
| `agent-runtime/src/transport/operation.ts` | `lh_operation.ts` | 49 | OperationStore + RuntimeOperationContext |
| `agent-runtime/src/utils/toolCallRepeatGuard.ts` | `lh_repeatGuard.ts` | 60 | 签名累计 20 次熔断 |
| `agent-runtime/src/groupOrchestration/GroupOrchestrationRuntime.ts` | `lh_groupOrch.ts` | 193 | 团队编排 step 循环 |
| `agent-runtime/src/groupOrchestration/GroupOrchestrationSupervisor.ts` | `lh_groupSupervisor.ts` | 185 | 决策动词翻译状态机 + maxRounds |
| `apps/server/.../AgentRuntimeCoordinator.ts` | `lh_coordinator.ts` | 441 | Redis 状态/流事件管理 + inline resume + 终态语义 |
| `apps/server/.../executeStep/index.ts` | `lh_executeStep.ts` | 392 | 分布式 step 锁 + 调度全链路 |
| `apps/server/.../executeStep/continuation.ts` | `lh_continuation.ts` | 119 | 信封先落盘再 inline 连跑 |
| `apps/server/.../stepPolicy.ts` | `lh_stepPolicy.ts` | 95 | 延迟/优先级/完成原因判定 |
| `apps/server/.../StaleOperationReaper.ts` | `lh_reaper.ts` | 495 | redrive/abandon + 生产 21k 僵尸行教训 |
| `packages/mecha/src/resolveAgentConfig.ts` | `lh_mecha.ts` | 566 | 多宿主共用配置解析（仅读前 120 行） |

**产品调研来源**（网络）：
[LobeHub 官网](https://lobehub.com/zh) · [定价页](https://lobehub.com/pricing) ·
[GitHub 仓库](https://github.com/lobehub/lobehub) ·
[百度百科：Lobe Chat](https://baike.baidu.com/item/Lobe%20Chat/67326368) ·
36 氪《一家 8 万 Star 的 AI 公司，决定让 AI 接管自己》及知乎/腾讯云横评文章。

## §6 未覆盖 / 可继续深挖

- `executors/callLlm.ts` + `compressContext.ts`：服务端上下文压缩管线（对照本仓库 compact 三层）
- `mecha` 完整解析规则（本调研只读了前 120 行）与 `context-engine` 包
- `packages/agent-manager-runtime`（AgentManagerRuntime：插件安装/OAuth）
- `apps/server` 的 Tasks 产品调度入口（`builtin-tool-task` 是 agent 侧工具，
  服务端触发链路未完整追——cron 相关：`services/agentSignal/.../selfIteration/review/schedule.ts`）
- `heterogeneous-agents`（Claude Code / Codex 外接）的桥接协议
