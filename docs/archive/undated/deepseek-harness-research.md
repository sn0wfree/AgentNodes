# DeepSeek Harness (dsh) 调研报告

> 调研时间: 2026-08-15（首次）; 2026-08-15（补充：上下文系统 + 插件架构 + Trace/Observability）
> GitHub: https://github.com/deepseek-ai/deepseek-harness
> 官网: https://deepseek.com/harness
> 开发者文档: https://deepseek-harness.github.io/deepseek-harness/guide/quickstart
> Cordis 框架: https://github.com/cordiverse/cordis
> 许可: MIT | 语言: TypeScript (pnpm monorepo)
> Stars: ~96,800（发布 2 天）

---

## 1. 概述

DeepSeek Harness（dsh）是 DeepSeek AI 开源的 **agent 运行时框架**。核心理念：

> **Agent = Model + Harness**

Harness 是赋予模型理解环境、使用工具、持续工作的运行时调度层。与传统框架不同，dsh 的口号是 **"Everything is a Plugin"** — 没有特权核心，包括 agent loop 本身也是可替换的插件。

### 1.1 核心定位

| 维度 | 说明 |
|------|------|
| 类型 | Agent harness / 运行时框架 |
| 语言 | TypeScript（pnpm monorepo，~50+ packages） |
| 内核 | Cordis — 通用插件运行时（非 agent 专用） |
| 运行模式 | Standard / PTC（代码生成） / Minimal（双工具基准） / Creative（自省） |
| 目标用户 | 编码 Agent 开发者 |
| 发布日期 | 2026-08-13（2 天内 96.8k stars） |
| 状态 | Developer Preview，快速迭代中 |

### 1.2 生态系统（发布 2 天）

| 项目 | Stars | 说明 |
|------|-------|------|
| deepseek-harness-desktop | 2,085 | 桌面应用 |
| awesome-dsh-plugin | 1,190 | 插件目录 |
| penguin-harness | 1,324 | RSI/自演化变体 |
| deepseek-harness-orange-book | 605 | 深度解析书 |
| awesome-deepseek-harness | 404 | 精选插件列表 |

---

## 2. 核心架构

### 2.1 Cordis 内核

Cordis 是 dsh 的底层框架，论文《A Programming Paradigm for Spatiotemporal Composability》描述的插件运行时。它只做三件事：

1. 插件加载/卸载
2. 插件间依赖管理
3. 服务/事件协作

**Cordis 本身不含任何 agent 逻辑**。所有 agent 行为来自插件。

#### Plugin 模型

```ts
import type { Context } from '@deepseek-ai/cordis'

export const name = 'my-plugin'
export const inject = ['tools']  // 声明依赖

export function apply(ctx: Context) {
  // ctx.tools 已就绪
  ctx.tools.register(/* ... */)
}
```

三个形式：function / object / class（service provider）。通过 `ctx` 注册的一切在插件卸载时自动清理（effect-based lifecycle）。

#### Service 模式

Service 是 Cordis 的 DI 原语。继承 `Service` 的 class 自动挂载为 `ctx` 上的命名服务：

```ts
import { Service, type Context } from '@deepseek-ai/cordis'

export default class MyService extends Service {
  static inject = ['tools']

  constructor(ctx: Context) {
    super(ctx, 'myService')  // 成为 ctx.myService
  }
}
```

核心服务及其 `ctx` 键：

| 包 | `ctx` 键 | 职责 |
|---|---|---|
| `core/session` | `ctx.sessions` | append-only `SessionEvent` 日志 + 内存存储 |
| `core/system-prompt` | `ctx.systemPrompt` | prompt 段落 + 工具 schema 组装 |
| `core/tools` | `ctx.tools` | 作用域工具注册表 + 守卫执行管道 |
| `core/agent` | `ctx.agents` | `Agent` 接口、实时注册表、`agent/*` 事件 |
| `core/agent-loop` | `ctx.agentLoop` | 默认驱动器，实现 Agent 契约 |
| `llm/llm` | `ctx.llm` | 消息/流词汇 + adapter seam |

### 2.2 Capability Seam（能力接缝）

**Seam** 是 dsh 最核心的设计模式。每个能力由三角色组成：

```
Service Definition (接口声明)
  → Service Provider (实现)
  → Consumer (模型面向的工具)
```

替换一个 Provider 就能改变整个产品的行为。例如把 filesystem 和 subprocess provider 指向远程沙箱，Bash、PTY 和 LSP 自动跟着走，无需修改任何工具代码。

**Seam 的威力在于解耦**：Provider 之间不知道彼此的存在，Consumer 只依赖 Definition 接口。

### 2.3 Profile / Bundle / Patch（组合系统）

```
Profile（命名组合）
  → Bundle（可分发的插件包）
    → Patch（配置层，可覆盖任意行）
```

**Layering 顺序**：
1. Profile 中每个 Bundle 按顺序加载
2. Profile 的 `cordis.patch.yml`
3. Home 目录的 `cordis.patch.yml`
4. `--patch` 命令行覆盖

**关键洞察**：任何配置行都可以通过 patch 替换，无需修改源码。

---

## 3. 上下文系统（Context System）

### 3.1 System Prompt Assembly（四输入组装）

DSH 的 system prompt 不是静态字符串，而是由 `SystemPrompt` 服务（`ctx.systemPrompt`）按步组装：

```
Prompt Sections (排序文本段，按 order 排序)
  + Dynamic Context (运行时评估 → user-role 消息)
  + Tool Schemas (函数定义)
  + Variables ({{name}} 插值)
  → renderPrompt() → 最终 system prompt
```

#### Prompt Section

```ts
interface PromptSection {
  readonly name: string
  readonly order: number          // -100=identity, 0=persona, 100-199=tool guidance
  readonly text: string | ((context: AssembleContext) => string)
  readonly complete?: boolean     // if true, REPLACES all other sections
}
```

Order bands：
- `-100`：`harness:identity`（"You are an AI agent powered by DeepSeek Harness."）
- `0`：`deployment:persona`（可配置 persona 模板，含 `{{model}}`, `{{cwd}}` 变量）
- `100-199`：Tool guidance（per-tool: `tool:bash`, `tool:read` 等）

#### Assembly 算法

```ts
async assemble(context: AssembleContext = {}): Promise<PromptAssembly> {
  // 1. 检查 runtime context 是否被抑制
  // 2. 解析变量（scoped shadow globals）
  // 3. 合并 sections 和 contexts（scoped shadow globals）
  // 4. 从所有 provider 收集 tool schemas
  // 5. 排序 sections，检测 complete section
  // 6. 构建 assembly
  // 7. 运行 system-prompt/assemble waterfall（插件可修改）
  // 8. 强制 complete section 和 runtime-context suppressor
}
```

#### Variable 插值（严格模式）

```ts
// {{name}} 通过 Object.hasOwn 查找（无原型链）
// 未知引用、未定义值、格式错误 → 抛异常
// 孤立的 {{ 不匹配 }} → 作为字面文本传递
// 替换值不再被重新扫描
```

#### Scoped Registration

每个 agent 通过 `agent.ctx` 获得独立 scope。Scoped sections/variables shadow globals with the same name。Disposing agent fiber 自动回滚所有 scoped 注册。

### 3.2 Dynamic Context Snapshots

动态上下文与 system-prompt sections 分离。它们成为模型历史中的 **sourced user-role 消息**：

```ts
// Dynamic context 变为带来源标注的 user-role 消息
export function renderContextSnapshot(assembly: PromptAssembly): string {
  return joinContextSections(renderContextSections(assembly))
}
```

这些被记录为 `user/message` 事件（带类型化 source），使其持久化且可重建。

### 3.3 Context Injection（三种输入原语）

```ts
agent.followup(message)  // next-turn FIFO，立即唤醒 driver
agent.steer(message)     // next-step inbox，立即唤醒 driver
agent.inject(message)    // next-step inbox，不唤醒 driver
```

**`agent.inject()`** 是关键上下文注入原语：
- 排队 **非唤醒** 的 next-step 上下文
- 运行中的 driver 在**最近的 later pre-step 边界**认领
- 空闲 driver 留待 `followup()` 或 `steer()` 唤醒
- 可能**错过** pre-step 已认领的请求

#### Inbox Model

```ts
interface InboxTarget = 'next-turn' | 'next-step'

// 持久化 inbox 变异事件
'agent/inbox/spliced': {
  target: InboxTarget
  start: number
  removedCount?: number
  inserted: UserMessage[]
  outcome?: 'canceled'
}
```

在每个 turn 边界：
1. 打开持久化 `turn/start`
2. 原子认领待处理 **next-step input** 加一个排队的 **next-turn 消息**
3. 在 steps 之间：只认领 **next-step input**
4. `agent/pre-step` 返回 rejection 或进入 step 的完整消息
5. Rejection 关闭 turn，无 step

### 3.4 Surface（核心创新）

Surface 是 raw append-only log 之上的**有序投影层**。它是模型历史的**唯一来源**。

```ts
interface SessionSurface {
  readonly nodes: readonly number[]           // 当前 surface event seqs（有序）
  readonly replaceGeneration: number         // 每次 positional replacement 递增
}
```

只有 3 种事件类型产生 LLM 消息（`SurfaceEventType`）：

| 事件 | 投影规则 |
|------|----------|
| `user/message` | → user 消息（精确内容） |
| `assistant/message` | → assistant 消息（provider, model, replay state）；空内容跳过 |
| `tool/result` | → user 消息（tool-result 块） |

其他事件（`turn/*`, `step/*`, `chunks`, 插件事件）**不投影**。

`deriveMessages()` 是**缓存+冻结**的：
- 每个 surface 节点只投影一次
- 返回深冻结消息数组
- Surface rewrite（replace）触发投影完全重建

### 3.5 Request Envelope（请求信封）

每次 LLM 调用前记录 `request/header` 事件：

```ts
interface EpochHeader {
  config: LlmCallConfig                    // provider, model, reasoning effort, sampling scalars
  adapterDefaults?: LlmCallConfigAdapterDefaults
  system?: string                          // 渲染后的 system prompt 文本
  tools?: ToolSchema[]                     // 组装后的 tool schemas
}

type RequestHeaderReason = 'initial' | 'resume' | 'change'
```

`foldRequestHeader(events)` 从日志重建任意历史请求的完整快照。

**`request/context`** 事件记录路由元数据（provider, model, contextWindow）。

**关键优势**：System prompt 变化、tool 重排序、model 切换都可审计。KV cache 失效可从 header 差异预测。崩溃恢复可重建精确请求信封。

### 3.6 上下文压力检测

| 触发类型 | 时机 | 行为 |
|----------|------|------|
| `pressure` | `agent/pre-step` 时 token meter 检测阈值 | 阈值 + 保留尾部策略；可能跳过 |
| `context-overflow` | `agent/request-error` 时模型拒绝上下文长度 | 强制压缩，平衡缩减 |

#### Tool-Result Pruning（预压缩裁剪）

可选的 `ctx.toolResultPruner` 在 compaction 范围选择**之前**裁剪超预算工具结果。替换文本中部同时保留 rich-block 顺序，并发出 `compaction/prune` shadow-price 事件用于 token 计账。

#### Tool-Pairing Boundary Helpers

```ts
toolPairingBalancedBefore(session: Session, seq: number): number
toolPairingBalancedAfter(session: Session, seq: number): number
```

安全边界 = 无未回答的 assistant tool call 跨越该边界。

### 3.7 Compaction 锁

Compaction 通过**一个日志记录锁**（`compaction/start` ... `compaction/end`）序列化。尾部检查找到最新的未匹配 `compaction/start`：
- 在 `session/end-seed` 边界之后 → 活跃锁，报告 `busy`
- 在 `session/end-seed` 边界之前 → 来自先前生命周期的过期锁，不阻塞

### 3.8 Crash Repair（崩溃修复）

`session/end-seed` 边界区分 seed 历史和实时工作。崩溃恢复时：
- 无 `tool/call` 的 assistant tool request → 合成 `TOOL_NOT_STARTED`
- 无 `tool/result` 的 `tool/call` → 合成 `TOOL_OUTCOME_UNKNOWN`

### 3.9 KV Cache Awareness

DSH 每个组件文档化其 KV cache 影响：
- 追加 surface 条目保留可重用前缀
- `replace` 操作从第一个被遮蔽的 token 开始使重用失效
- Registration, restriction, 或 reordering 可能从第一个变化的 token 开始使重用失效
- System prompt identity + persona + variables + sections 必须字节相同才能重用 cache

---

## 4. 会话日志系统（Event-Sourced Session）

### 4.1 Append-Only Session Log

这是 dsh 的 **单事实来源**。LLM 历史从日志 **派生**（`Session.deriveMessages()`），永远不单独存储。

#### 事件词汇表（SessionEventMap）

事件类型通过 TypeScript 的 `declare module` 合并扩展，无需修改源包：

```ts
interface SessionEventMap {
  // Turn 生命周期
  'turn/start':          { turn: number }
  'turn/end':            { turn: number; reason: TurnEndReason }
  
  // Step 生命周期
  'step/start':          { turn: number; step: number }
  'step/end':            { turn: number; step: number }
  
  // 消息事件（Surface 事件 — 产生 LLM 消息）
  'user/message':        UserMessage
  'assistant/chunk':     { turn; step; chunk: StreamChunk }  // token 级流
  'assistant/message':   { turn; step; message; usage? }
  
  // 工具事件
  'tool/call':           { turn; step; callId; name; arguments }
  'tool/result':         { turn; step; message; error?; meta? }
  
  // 请求信封
  'request/header':      { header: EpochHeader; reason }
  'request/context':     RequestContext
  
  // 种子结束标记
  'session/end-seed':    Record<string, never>
}
```

插件可以添加额外事件类型（如 compaction 添加 `compaction/start`、`compaction/summary`、`compaction/end`），这些是 **log-only** — 不产生 LLM 消息。

#### 事件信封

```ts
type SessionEvent<T> = {
  type: T
  seq: number          // 单调递增位置（seq = log.length）
  time: number         // epoch ms
  data: SessionEventMap[T]
  ignorable?: true     // 无法识别时安全跳过
} & (T extends SurfaceEventType ? {
  sourceEventSeqs?: number[]
  surfaceOp?: SurfaceOp  // 'append' | { op: 'replace', start, end }
} : object)
```

### 4.2 持久性保证

**核心不变式**：到达模型请求的一切必须可从日志重建。

- `assistant/chunk` 事件存储用于 replay 保真
- seq 必须保持连续，所以 chunk 不能被过滤
- 后端无损持久化每个事件
- `Session.append` 在源端强制 JSON 可序列化

---

## 5. Compaction（上下文压缩）

### 5.1 架构

Compaction 是 **可选能力 seam**：Definition（`ctx.compaction`）→ Provider（如 `dsh-compaction-basic`）→ Consumer（`dsh-command-compact`）。

### 5.2 会话事件扩展

Compaction 向 `SessionEventMap` 添加三个 log-only 事件：

```ts
// compaction/start — 获取锁
{ turn: number | null }  // number = 自动触发, null = 手动触发

// compaction/summary — 结果
{ summary: ContentBlock[], rawOutput?, llmStreamCall?,
  shadowedRange: { start: number; end: number },  // surface 位置跨度
  shadowedSeqs: number[],                          // 被移除的 surface 节点
  shadowedTokenCount: number,
  provider, model, maxTokens?, usage? }

// compaction/end — 释放锁
{ turn: number | null, error? }
```

### 5.3 Surface Mutation via Replace

**这是 dsh compaction 最优雅的地方**。

替换用的 `user/message` 使用 `surfaceOp: { op: 'replace', start, end }` 遮蔽旧 surface 条目。summary 本身承载在一个独立的 `user/message` 事件上，使用 replace 操作符 — 这是 compaction 执行的唯一 surface 变异。

```
compaction/start          (获取锁)
... 摘要生成 ...
compaction/summary        (记录摘要元数据)
user/message [replace]    (summary 替换旧 surface entries)
compaction/end            (释放锁)
```

**关键优势**：不删除日志，只遮蔽。旧数据仍然存在但对 LLM 不可见。

### 5.4 Service API

```ts
abstract class CompactionEngine {
  // 自动：在 agent/pre-step 时检测压力或溢出触发
  compactIfNeeded(agent, trigger: 'pressure' | 'context-overflow', signal): Promise<CompactionResult | null>

  // 手动：即使低于压力阈值也压缩有用历史
  compactNow(agent, signal, sourceCommandId?): Promise<CompactionResult | null>

  // 显式：压缩特定 surface 范围
  compactRegion(start, end, agent, signal?): Promise<CompactionResult>
}
```

---

## 6. 工具系统（Tool Registry）

### 6.1 注册

```ts
import { defineTool } from '@deepseek-ai/dsh-tools'

ctx.tools.register(defineTool({
  name: 'read_file',
  description: 'Read a file from disk.',
  parameters: {
    path: { type: 'string', required: true, description: 'Absolute path' },
    limit: { type: 'number' },
  },
  output: {
    schema: { type: 'string' },
    render: (_args, value) => [{ type: 'text', text: value }],
  },
  async execute(args, exec) {
    return readFile(args.path, { encoding: 'utf8', signal: exec.signal })
  },
}))
```

注册是 effect-based 的 — 释放插件 fiber 自动注销工具。Schema 自动流入 system-prompt 组装。

### 6.2 Guard（单调拒绝守卫）

```ts
const dispose = ctx.tools.guard((execution) => {
  if (execution.name === 'dangerous_tool' && isUntrusted(execution)) {
    return 'Not allowed in untrusted context'
  }
  return undefined  // allow
})
```

**关键约束**：guard 只能拒绝，不能强制放行 — 排序不能撤销拒绝。这保证了安全性是单调的。

### 6.3 执行管道

```
ctx.tools.execute(exec)
  → tools/pre-execute        (allow/deny/ask 瀑布)
  → ToolGuard[]              (单调拒绝守卫)
  → tools/execute            (around-dispatch 包装：timeout, retry, metrics)
  → tool body                (实际 execute() 函数)
  → tools/post-execute       (accept/replace/block 瀑布)
  → finalizeContent          (定义属主的最后变换)
  → tools/result             (观察冻结结果)
```

### 6.4 作用域工具（Scoped Tools）

Scoped 注册自动 shadow globals；限制过滤继承的工具：

```ts
ctx.tools.restrict({
  deny: ['shell', 'subprocess'],   // 移除危险工具
  // 或：
  allow: ['read_file', 'grep'],    // 只保留这些
})
```

### 6.5 执行模式

```ts
// isConcurrencySafe 声明工具是否能与兄弟并行运行
isConcurrencySafe?(args: unknown): boolean
```

Agent loop 使用 `executionMode()` 形成独占屏障和滚动池并行运行。

### 6.6 Tool Ordering

```ts
// 配置：显式模型面向工具排序 + rest entry
toolOrder?: string[]  // 必须包含 exactly one '<unlisted-tools>' marker
```

Listed tools 取其列出位置；Unlisted tools 按字母序放在 rest entry。

---

## 7. Sub-Agent（子代理委托）

### 7.1 Provider 模型

Subagent 是 **多 provider seam**（`ctx.subagents`）。多个命名 provider 共存：

| Provider | 说明 |
|----------|------|
| `spawn` | 进程内全新子 agent |
| `fork` | 子 agent 用父的已完成 turn 前缀种子化 |
| `acp` | 远程 agent client protocol |
| `codex`, `claude-code` | 外部产品后端 |

### 7.2 一次性委托（One-Shot）

```ts
const run: SubagentRun = await ctx.subagents.start({
  provider: 'spawn',
  parent: agent,
  prompt: [{ type: 'text', text: 'Research X' }],
  signal: exec.signal,
  outputSchema: { type: 'object', properties: { findings: { type: 'string' } } }
})

const result: SubagentResult = await run.result
// result.output: ContentBlock[]
// result.structured?: unknown
// result.stopReason: 'completed' | 'error' | 'max-tokens' | ...
```

### 7.3 可续活子代理（Continuable Children）

持久化子 `Session` + 每个进程最多一个 **Activation**（重建的子 Agent 驻留期间）。

```ts
// 启动
const { childId, messageId } = await ctx.subagents.startContinuable({...})
// 跟进
await ctx.subagents.followup(agent, childId, [{ type: 'text', text: 'Part B' }], {...})
// 中断
ctx.subagents.interrupt(childId, { kind: 'ancestor', agent: parentAgent })
// 子 → 父回报
await ctx.subagents.reportFrom(childAgent, content, { delivery: 'wakeup', signal })
```

#### Activation 生命周期

```
persisted Session
  → optional live Activation
       → one retained AgentHandle
       → Agent inbox as the only turn FIFO
       → zero or more owned child Activations
```

状态：`running`（活跃工作）→ `waiting`（静默但有子代）→ `settled`（子代已释放）

---

## 8. Agent Loop（Turn 流程）

### 8.1 Turn/Step 模型

一个 **step** = 一次模型请求 + 其 tool calls。一个 **turn** = 零或多个 steps：

```
turn/start → claim input → assemble prompt → agent/pre-step (reject or rewrite)
  → step/start → append user messages → derive model history → agent/request
  → llm/stream → assistant/chunk* → assistant/message → tool/call*
  → tools/pre-execute → tools/execute → tools/post-execute → tool/result*
  → step/end → (loop if more work) → agent/turn-stopping → turn/end
```

### 8.2 事件分类

| 事件 | 类型 | 持久化 |
|------|------|--------|
| `turn/*`, `step/*`, `user/message`, `assistant/*`, `tool/*` | Session 事件 | ✅ 写入日志 |
| `agent/pre-step`, `agent/request`, `llm/stream`, `tools/*` | Capability 事件 | ❌ 仅运行时 |

### 8.3 Agent 取消

```ts
agent.cancel(
  { kind: 'user' },           // cause: 'user' | 'parent' | 'hook' | 'disposed'
  { keepInbox: true }         // 保留队列中的工作供后续使用
)
```

---

## 9. Trace / Observability 系统

### 9.1 核心架构：Session Log IS the Trace

**DSH 没有独立的 telemetry/trace 包**。`packages/core/telemetry` 返回 404。取而代之的是一个根本不同的架构：**session event log 本身就是 trace 系统**。

每个关于 agent 交互的持久化事实都是 append-only `SessionEvent` 中的一条记录，所有 replay、telemetry 导出、session 查询、诊断检查都从这个单一事件流派生。

#### SessionEvent 信封（基础 "trace event"）

```typescript
type SessionEvent<T> = {
  type: T
  seq: number          // 单调序列号（替代传统 trace/span ID）
  time: number         // Unix epoch 毫秒
  data: SessionEventMap[T]
  ignorable?: true     // 无法识别时安全跳过
} & (T extends SurfaceEventType ? {
  sourceEventSeqs?: number[]   // 因果关联：哪些 chunk 产生了 message
  surfaceOp?: SurfaceOp        // 如何进入 surface
} : object)
```

关键设计属性：
- **单调 `seq`** — 序列号是 trace 关联机制（替代传统 trace/span ID）
- **`sourceEventSeqs`** — 事件间的显式因果链接
- **`ignorable`** — 前向兼容的未知事件跳过标记

### 9.2 完整事件目录

| 事件类型 | 类别 | 用途 |
|----------|------|------|
| `turn/start`, `turn/end` | log-only | Turn 生命周期 |
| `step/start`, `step/end` | log-only | Step 生命周期 |
| `user/message` | **surface** | 用户消息 |
| `assistant/chunk` | log-only | 原始流 chunk — token 级 replay 保真 |
| `assistant/message` | **surface** | 组装的 assistant 消息 + TokenUsage |
| `tool/call` | log-only | 模型请求工具调用（原始 JSON 参数） |
| `tool/result` | **surface** | 工具结果 + error + meta |
| `request/header` | log-only | 下次请求的完整 header 快照 |
| `request/context` | log-only | 路由元数据（仅变化时记录） |
| `compaction/start`, `/summary`, `/end` | log-only | Compaction 锁 + 摘要元数据 |
| `compaction/prune` | log-only | 裁剪替换的 shadow price |
| `agent/inbox/spliced` | log-only | Agent 待处理消息列表变异 |
| `hook/invoked`, `hook/result` | log-only | Hook 调用审计 + 结果 |
| `approval/asked`, `approval/decided` | log-only | 审批审计 |
| `llm/retry`, `llm/retry-started` | log-only | 重试调度 |
| `session/end-seed` | log-only | Seed 边界标记 |
| `subagent/descriptor` | log-only | 子代理身份 + 生命周期模式 |
| `todo/write` | log-only | 整列表快照 |
| `goal/change` | log-only | 目标状态变异 |
| `feedback/record` | log-only | 人工反馈 |
| `command/run`, `command/done` | log-only | Slash 命令审计 |

每个事件的 payload 完全类型化且 JSON 可序列化。

### 9.3 Replay 机制

Session log 天然就是 replay 机制：

```typescript
// 从持久化日志重建活跃 session
ctx.sessions.create(id, { seed })  // 验证、冻结连续日志、重建 surface

// Fork（分支）
ctx.sessions.fork(source, boundary?, childSessionId?)  // 选择 seed 到 inclusive boundary

// 派生消息历史
session.deriveMessages()  // 增量投射 surface entries → 新消息数组
```

### 9.4 Session Telemetry（出站报告）

独立于 session log 的**出站投影层**，位于 `packages/session/session-telemetry/`：

```typescript
interface SessionTelemetryRecord {
  channel: 'ledger' | 'ops'
  time: number
  severity: 'info' | 'warn' | 'error'
  attributes: Record<string, string | number>
  body: unknown
}
```

**两个通道**：
- **`ledger`** — 1:1 镜像 session log 事件（携带 session.id, event.type, event.seq）
- **`ops`** — 运营信号（agent-error, shutdown），无 log home

**三种投递模式**：`full`（实时）/ `feedback-only`（按需）/ `disabled`

**Redact waterfall**：`session-telemetry/record` waterfall 允许插件在导出前脱敏。脱敏只应用于导出副本 — 规范 session log 永远不被重写。

**OpenTelemetry 集成**：`session-telemetry-otel` 包将 telemetry records 投射为 OTel log records。

### 9.5 Session Query（读/导出层）

```
packages/session-query/
  session-query/           # 可信读取、关系查询、搜索操作
  session-query-sqlite/    # SQLite 全文搜索实现
  session-log-export/      # Web `/export` 命令、浏览器下载、ZIP 端点
  tool-session-query/      # 模型面向的 session 查询工具
```

能力：
- 逻辑记录
- 精确事件范围读取
- **关系 trace**（跨事件的 trace 关联）
- 语义过滤/文档
- 全文搜索结果页
- Lineage 查询（parent/child session 关系）

### 9.6 Session Projection（实时派生状态）

```
packages/session/
  session-projection/          # 定义和驱动 session projection 单元
  session-projection-cache/    # 持久化/恢复 projection 检查点
  session-stats/               # 全 log 对话计数和 wall time
```

### 9.7 Runtime Diagnostics（不变量注册）

`packages/runtime-diagnostics/invariants/`：

```typescript
ctx.invariants.register(packageName: string, installer: InvariantInstaller): () => void
```

每个 workspace 包拥有一个 `./invariant` companion 插件。Session 包专门注册：
- 单调序列号检查
- Turn/step enclosure 不变量
- 同步 tool call/result 配对

### 9.8 JSON-RPC Trace Protocol（SDK）

SDK 通过 JSON-RPC 2.0 流式传输**完整的 session log 信封**：

```typescript
// Server → Client 通知
type HarnessSdkNotificationMap = {
  'session.event': SessionEventNotification  // 运行时中的每个 session，无过滤
  'session.status': SessionStatusNotification  // whole-agent running/idle 转换
  'subagent.started': SubagentStartedNotification
  'subagent.finished': SubagentFinishedNotification
}
```

`subscribeSessionTree(id)` 作用域到一个 session + 从 `subagent.started` lineage edges 发现的后代。

### 9.9 跨 Session Trace 关联

没有传统 trace/span ID 系统。DSH 使用：

1. **单调 `seq`** — 每个 session 内的排序和因果链接
2. **`sourceEventSeqs`** — surface 事件引用早期 source 事件
3. **`session.parentSession`** — parent-child session lineage
4. **`session.seedLength`** — 标记 forked/resumed session 的 seed 结束位置
5. **`session/end-seed`** — 持久化边界标记
6. **`subagent/descriptor`** — 子代理身份和生命周期模式
7. **Anonymous correlation ID** — 跨 session telemetry 关联

### 9.10 诊断/调试工具

| 工具 | 说明 |
|------|------|
| Runtime Invariants | 每个包注册不变量检查，失败带 `code: 'INVARIANT'` |
| `dsh --dump-config` | 显示实际组合树 |
| `dsh --profile web --dump-config` | 任意行可被 patch |
| Session dump | 完整 session 状态导出 |
| stdout 协议 | "stdout is the protocol. Diagnostics belong on stderr." |

### 9.11 不存在的概念

| 概念 | 状态 |
|------|------|
| `packages/core/telemetry` | **不存在**（404） |
| "Trajectory View" | **未找到**（作为命名概念） |
| "Replay from log" 调试工具 | **无独立工具** — replay 是 `Session.create(id, { seed })` 的原生能力 |
| "Devtools" / "inspector" 包 | **未找到** — `apps/` 只有 `cli/` 和 `web/` |
| 传统 APM/metrics 集成 | **无** — 被 `session-telemetry-otel` 替代 |

---

## 10. 其他能力模块

### 9.1 计划模式（Plan）
`plan/` 包实现 plan 模式作为 **logged state** — 计划的每一步都是会话事件，可审计、可回放。

### 9.2 目标系统（Goal）
`goal/` 包实现同一会话内的目标跟踪。Agent 可以设置和更新目标，目标状态作为会话事件记录。

### 9.3 Todo 写入
`todo/` 包提供 `todo_write` 工具。Todo 状态作为 `todo/write` 会话事件记录。

### 9.4 Web 搜索/获取
`web/` 包提供 web search 和 fetch 工具。

### 9.5 LSP（Language Server Protocol）
`lsp/` 包集成语言服务器，提供代码智能感知。

### 9.6 MCP（Model Context Protocol）
`mcp/` 包支持 Model Context Protocol，用于与外部工具服务器通信。

### 9.7 沙箱
三层沙箱 provider：本地沙箱 / Landlock（Linux 内核级）/ E2B（云端）。通过同一个 seam 切换。

---

## 11. "Everything is a Plugin" 架构哲学

### 10.1 核心原则

1. **无特权核心** — 连 agent loop 本身也是插件
2. **Effect-based cleanup** — 注册时返回 dispose 函数，卸载时自动清理
3. **Scoped isolation** — 每个 agent 有独立 ctx，shadow globals
4. **Merge-extensible maps** — 插件通过 `declare module` 扩展事件类型
5. **Capability seams** — Definition/Provider/Consumer 三元组

### 10.2 对 Python 的映射

| DSH 概念 | Python 对应 | 可行性 |
|----------|------------|--------|
| `@tool` decorator | `BaseTool` subclass + `registry.register()` | ✅ 已有 |
| `@hook` | `AgentHook` subclass + `CompositeHook.add()` | ✅ 已有 |
| `@event` | `EventBusV2.emit()` | ✅ 已有 |
| `@prompt` | `PromptBuilderFactory.register()` | ⚠️ 硬编码字典 |
| `@inject` | (缺失) | ❌ 4 处硬编码调用 |
| `@workflow` | `AgentRunnerRegistry.register()` | ✅ 已有 |
| `entry_points` | (缺失) | ❌ 无外部插件发现 |

### 10.3 最小可行方案（~280 行）

1. **`Registry[T]` 统一基类**（~50 行）— 替代 8 个 ad-hoc 注册表
2. **`ContextInjector` 协议**（~120 行）— 提取 loop.py 4 处硬编码
3. **`importlib.metadata` entry_points**（~60 行）— 外部工具插件发现
4. **`BaseTool.inject_kwargs()`**（~30 行）— 消除工具名分派
5. **Prompt 自动发现**（~20 行）— 扫描 `.prompts/*.md`

**不需要做的**：PluginManager 类、插件生命周期钩子、YAML/TOML 插件清单、替换 CompositeHook。

---

## 12. 与我们系统的对比分析

### 11.1 架构层面

| 维度 | DeepSeek Harness | strategy-research | 差距分析 |
|------|-----------------|-------------------|----------|
| **事件架构** | 内存态（transient） | SQLite 持久化 event sourcing | **我们更强** |
| **会话模型** | Append-only + mutable session | Append-only event_log + mutable session/attempt | **平手** |
| **Compaction** | Surface replace 遮蔽 + 锁 | L4 LLM 摘要 + 增量更新 + 历史过滤（无锁） | **DSH 更优雅（锁）；我们更智能（摘要）** |
| **上下文系统** | 四层组装 + scoped + waterfall + inject | 静态模板 + str.replace + 4 处硬编码注入 | **结构性差距** |
| **Agent Loop** | Linear prompt-response | ReAct streaming + 无进展检测 + 断路器 | **我们更强** |
| **Hook/中间件** | 无正式 hook 系统 | 13 点 `AgentHook` + `CompositeHook` | **我们更强** |
| **工具系统** | Scoped + guard + ordering + pruning | ToolRegistry + BaseTool + effects | **DSH 更灵活** |
| **子代理** | Continuable + multi-provider | SubAgentTool（一次性） | **DSH 更强** |
| **长周期执行** | 无 | StudyRecord 状态机 + 轮次 + 指令 | **我们更强** |
| **多租户/预算** | 无 | per-user 并发 + budget 限额 + hanging 防护 | **我们更强** |
| **可观测性** | Session log IS trace + request/header + fold 重建 + session-telemetry-otel | JSONL trace.jsonl + ContextVar trace_id + hanging_events（无 request 日志，无 trace viewer UI） | **结构性差距** |
| **Trace 关联** | session parent-child + sourceEventSeqs + anonymous correlation ID | ContextVar trace_id 跨 round 共享（study 级）+ session_id | **我们更简单但够用** |
| **持久化** | 多文件或内存 | 单 SQLite DB | **我们更简单** |
| **崩溃修复** | session/end-seed + 合成恢复消息 | 无 | **DSH 更健壮** |

### 11.2 设计哲学对比

| 哲学 | DeepSeek Harness | strategy-research |
|------|-----------------|-------------------|
| 核心理念 | Everything is a Plugin | Agent = Model + Harness |
| 扩展方式 | Cordis 插件 + patch + entry_points | AgentHook + ToolRegistry + 硬编码 |
| 语言/生态 | TypeScript + pnpm | Python + React |
| 目标场景 | 通用编码 Agent | 策略研究/回测/长周期研究 |
| 优先级 | 灵活性 > 安全性 | 安全性 > 灵活性 |

### 11.3 我们已有的优势

1. **Event sourcing 更成熟** — SQLite 持久化 + Projector + 自动修复 + 降级模式
2. **Compaction 更智能** — L4 LLM 摘要 + 增量更新 + 结构化模板 + 历史过滤
3. **Study 状态机** — DSH 没有长周期执行框架
4. **多租户/预算** — per-user 并发、budget 限额、hanging 防护
5. **Agent hook 更丰富** — 13 个 hook 点 + error isolation
6. **Tool circuit breaker** — 自动重试 + no-progress 检测
7. **子代理隔离** — 独立 LLMClient + ToolRegistry + workspace

### 11.4 我们的真实缺陷（实证确认）

| 缺陷 | 证据 | DSH 对应 |
|------|------|----------|
| Compaction 无锁 | `compact.py` 无 Lock；auto + manual 可并发 | compaction/start-end 锁 |
| 工具结果无界进 history | loop.py:1280 传全文；50k cap 只管 SSE | ToolResultPruner |
| Request envelope 无日志 | trace 只记 200 字符 preview | request/header |
| Steering 缺失 | FIFO 严格排队，attempt 跑完才消费 | inbox targets |
| Session fork 无能力 | 无 fork/clone/copy | sessions.fork |
| Cancel orphan 风险 | cancel_session 取消 consumer 不取消 attempt | cancel + keepInbox |
| 上下文注入硬编码 | loop.py 4 处 if/else | ContextInjector 协议 |
| 工具构建硬编码 | build_default_registry() 85 行 | entry_points 发现 |

---

## 13. 可借鉴的模式（完整列表）

### 12.1 Surface Mutation（替换模式）⭐⭐⭐⭐⭐

**现状**：我们的 compaction 用 `compaction_indices` 过滤 + `keep_all_compactions` 开关，逻辑复杂。

**DSH 方案**：`surfaceOp: { op: 'replace', start, end }` 遮蔽旧条目，不删日志。

**可借鉴方案**：

给 `MessagePart` 加 `surface_op` 字段：

```python
@dataclass
class MessagePart:
    message_id: str
    seq: int
    part_type: str
    content: str
    surface_op: str = "append"  # "append" | "replace"
    surface_replace_start: int | None = None
    surface_replace_end: int | None = None
```

Compaction 时：
1. 旧条目的 `surface_op` 改为 `"replace"`
2. 新 summary 条目使用 `surface_op="append"`
3. `_convert_messages_to_history` 根据 `surface_op` 过滤，无需维护 `compaction_indices` 列表

**优势**：消除 `keep_all_compactions` 开关和 `hidden_until_seq` 计算逻辑。

**风险**：中等。需要修改 `MessagePart` 模型 + `Projector` + history 转换逻辑。

### 12.2 Scoped Tool Registry（作用域工具注册）⭐⭐⭐⭐

**现状**：`ToolRegistry` 有 `allowed_tools` 白名单 + `readonly` 模式，粒度不够。

**可借鉴方案**：

```python
class ToolRegistry:
    def restrict(self, *, deny: list[str] | None = None, allow: list[str] | None = None):
        if deny:
            self._denied.update(deny)
        if allow:
            self._allowed = set(allow)
```

**优势**：子 agent 继承父的工具集但可以裁剪。

**风险**：低。向后兼容。

### 12.3 Tool Guard Pipeline（工具守卫管道）⭐⭐⭐

**DSH 方案**：单调拒绝 — 后加的 guard 不能覆盖前面的拒绝。

**可借鉴方案**：

```python
class ToolGuard(Protocol):
    def check(self, execution: ToolExecution) -> str | None: ...

class ToolRegistry:
    _guards: list[ToolGuard]
    def guard(self, guard: ToolGuard): ...
    def _check_guards(self, execution: ToolExecution) -> str | None: ...
```

**优势**：支持审计、权限层叠加、安全策略组合。

**风险**：低。

### 12.4 Continuable Subagents（可续活子代理）⭐⭐⭐

**DSH 方案**：持久化子会话 + Activation 生命周期 + followup/interrupt/reportFrom。

**可借鉴场景**：Study 中的 sub-research、多 agent 协作、后台任务管理。

**风险**：高。需要 session 持久化 + activation 管理 + 父子所有权图。

### 12.5 Waterfall Events（瀑布事件）⭐⭐

**DSH 方案**：`agent/pre-step → tools/pre-execute → tools/execute → tools/post-execute`，`next()` 委托模式。

**可借鉴场景**：工具执行前的权限检查、工具执行后的审计日志、工具结果的后处理。

**风险**：中等。需要重构工具执行管道。

### 12.6 Compaction 并发锁 ⭐⭐⭐⭐⭐

**现状**：`compact.py` 无锁；手动 `/compact` 与 loop 自动 L4 可同 session 并发。

**DSH 方案**：`compaction/start` ... `compaction/end` 事件对锁。

**可借鉴方案**：复用 `SessionLockMap` 模式新建 per-session `asyncio.Lock`。

**优势**：消除真实 bug 风险。

**风险**：低。

### 12.7 工具结果入史前统一截断 ⭐⭐⭐⭐⭐

**现状**：loop 把工具输出全文塞进 history；50k cap 只管 SSE。

**DSH 方案**：`ctx.toolResultPruner` 统一 pre-compaction 裁剪。

**可借鉴方案**：`_append_tool_results` 前加 universal cap（默认 30k 字符，中间截断保留头尾）。

**优势**：消除无界输出风险。

**风险**：低。

### 12.8 Request Envelope 日志 ⭐⭐⭐⭐

**现状**：trace 只记 200 字符 preview；system prompt / tools schema / history hash 从不落盘。

**DSH 方案**：`request/header` 完整快照 + fold 重建。

**可借鉴方案**：loop 在 LLM 调用前发 `llm_request` trace 事件。

**优势**：为 Trajectory View 打地基。

**风险**：低。

### 12.9 ContextInjector 协议 ⭐⭐⭐⭐

**现状**：loop.py 4 处硬编码注入点。

**DSH 方案**：`ContextInjector` 协议 + 注册表。

**可借鉴方案**：

```python
class ContextInjector(Protocol):
    name: str
    def inject(self, loop: "AgentLoop", task: str, messages: list[dict]) -> str: ...
```

**优势**：消除 loop.py 硬编码，新增上下文类型无需改 loop。

**风险**：低。

### 12.10 Typed IDs ⭐⭐⭐

**现状**：全部裸 `str`；attempt_id 传错位置无编译期保护。

**DSH 方案**：Branded IDs（`SessionId`, `CallId` 等）。

**可借鉴方案**：`NewType("SessionId", str)` 等 3 个；关键函数签名替换。

**优势**：零运行时成本，编译期保护。

**风险**：低。

### 12.11 Session Fork ⭐⭐

**现状**：完全没有。但我们有 event sourcing + `replay()`，fork-by-replay 天然可行。

**可借鉴场景**：Study 变体（同一假设前缀分叉对比）、A-B 测试。

**风险**：中等。

### 12.12 Plugin-First Architecture（"Everything is a Plugin"）⭐⭐⭐

**现状**：Python 无原生插件系统；我们用 `AgentHook` + `ToolRegistry` 做扩展。已有 10 个注册表但无统一基础设施。

**可借鉴方案**：~280 行的最小插件系统（Registry[T] + ContextInjector + entry_points + inject_kwargs + prompt 自动发现）。

**优势**：消除硬编码，支持外部插件。

**风险**：低（增量重构）。

### 12.13 Request Envelope 日志（Trace 升级）⭐⭐⭐⭐⭐

**现状**：trace.jsonl 只记 200 字符 preview；system prompt / tools schema / history hash 从不落盘。TraceWriter 的 `write_text_entry()` sidecar offload 机制已实现但未使用。

**DSH 方案**：`request/header` 完整快照 + fold 重建。每次 LLM 调用前记录 system prompt + tools schema + history hash。

**可借鉴方案**：

```python
# loop.py LLM 调用前新增
self._trace({
    "type": "llm_request",
    "iteration": iteration,
    "system_prompt_hash": sha256(system_prompt),
    "tools_hash": sha256(json.dumps(tools)),
    "history_count": len(messages),
    "history_chars": sum(len(m.get("content", "")) for m in messages),
    "context_window": self.context_window,
})
```

配合 `write_text_entry()` sidecar offload 记录完整 system prompt 和 tools schema（大字段自动 offload 到 `trace-blobs/`）。

**优势**：为 Trajectory View / Trace Viewer 打地基；支持 replay-from-trace 调试。

**风险**：低。

### 12.14 Session Log 作为 Trace 单事实来源 ⭐⭐⭐

**现状**：我们有两套并行的持久化：
- `event_log` 表（append-only session events，monotonic seq）
- `trace.jsonl`（独立 JSONL 文件，trace event）

两者存储不同格式、不同位置、不同查询 API。

**DSH 方案**：session event log IS the trace。没有独立 trace 文件。所有查询、导出、replay 都从单一事件流派生。

**可借鉴方案（渐进式）**：

Phase 1：让 trace.jsonl 的关键事件也写入 event_log（或作为 EventV2 类型）
Phase 2：将 TraceWriter 的 sidecar offload 统一到 event_log 的大字段存储
Phase 3：提供 Trace Viewer UI，从 event_log 派生显示

**优势**：消除双源不一致；统一查询；支持 replay。

**风险**：中等（需要修改 event_log schema + 迁移逻辑）。

### 12.15 Trace Viewer UI ⭐⭐⭐⭐

**现状**：前端只有 trace_id 复制按钮（StudyProgress.tsx:245-253），无 trace 查看器。

**DSH 方案**：Web UI 直接消费 `session.event` 通知流，每个 session event 就是一条 trace 记录。

**可借鉴方案**：

新建 `TraceViewer` 组件：
1. 从 event_log 读取指定 session 的所有事件
2. 按 seq 排序显示为 timeline
3. Surface 事件高亮（user/message, assistant/message, tool/result）
4. 点击展开 event data（JSON 折叠）
5. Request envelope 对比（两次 LLM 调用的 system prompt/tools 差异）
6. Token usage 累计图表

**优势**：开发者调试的核心工具；支持 "agent 看到了什么" 的精确回放。

**风险**：中等（前端工作量）。

### 12.16 Telemetry Redact Waterfall ⭐⭐

**现状**：我们的 event_log 和 trace.jsonl 都是原始数据导出，无脱敏机制。

**DSH 方案**：`session-telemetry/record` waterfall 允许插件在导出前脱敏，只应用于导出副本。

**可借鉴方案**：在 EventBusV2 的 export 路径加 redact pipeline（可选）。

**优势**：安全合规；支持共享 trace 而不泄露敏感信息。

**风险**：低。

### 12.17 Runtime Invariant 检查 ⭐⭐

**现状**：我们用 hanging_events 跟踪异常事件，但无运行时不变量检查。

**DSH 方案**：每个包注册 invariant 检查，失败带 `code: 'INVARIANT'` 和 `packageName`。

**可借鉴方案**：

```python
class InvariantRegistry:
    def check(self, name: str, condition: bool, message: str):
        if not condition:
            logger.error("INVARIANT [%s]: %s", name, message)
            # 可选：记录到 hanging_events
```

**优势**：早期发现数据不一致；可审计。

**风险**：低。

---

## 14. 不建议借鉴的模式

| 模式 | 原因 |
|------|------|
| Cordis 插件内核 | Python 生态无对应；`Registry[T]` 足够 |
| Effect-based cleanup | 当前 "import 时注册" 模式够用 |
| Scope shadow（prompt sections） | 我们没有 per-agent scope 的 prompt 需求 |
| Runtime Context Suppression | 不需要禁用运行时上下文的场景 |
| Complete Section Override | 我们没有 "接管全部 prompt" 的需求 |
| Profile/Bundle 组合 | 过度工程化，我们的 study/persona 已覆盖 |
| Creative Mode | 实验性功能，不适合生产系统 |
| PTC 模式（代码生成组合工具） | 风险高，我们的 ReAct 已够用 |
| Landlock 沙箱 | 我们不需要进程级沙箱 |
| KV Cache Awareness | LLM 提供商已处理；我们不需要手动管理 |

---

## 15. 实施方案

### 15.1 优先级排序

| 优先级 | 模式 | 工作量 | 风险 | 收益 |
|--------|------|--------|------|------|
| **P0** | Compaction 并发锁 | 半天 | 低 | 高 — 修复真实 bug |
| **P0** | 工具结果统一截断 | 半天 | 低 | 高 — 修复真实风险 |
| **P0** | Cancel orphan 修复 | 1 天 | 低 | 高 — 修复真实 bug |
| **P1** | Typed IDs | 1 天 | 低 | 中 — 编译期保护 |
| **P1** | ContextInjector 协议 | 2 天 | 低 | 高 — 解耦 loop.py |
| **P1** | Request Envelope 日志 | 2 天 | 低 | 高 — Trace 升级 |
| **P2** | Steering 中途纠偏 | 1-2 周 | 中 | 高 — 交互升级 |
| **P2** | Scoped Tools + Guard | 2 天 | 低 | 中 — 安全增强 |
| **P3** | Registry[T] 统一基类 | 2 天 | 低 | 中 — 代码质量 |
| **P3** | Tool 插件发现 | 2 天 | 低 | 中 — 外部插件 |
| **P3** | Prompt 自动发现 | 1 天 | 低 | 低 — 代码质量 |
| **P4** | Surface Mutation | 1 周 | 中 | 高 — 简化 compaction |
| **P4** | Session Fork | 1 周 | 中 | 中 — study 变体 |
| **P4** | Trace Viewer UI | 1 周 | 中 | 高 — 开发者调试 |
| **P5** | Continuable Subagents | 2 周 | 高 | 高 — 多 agent |
| **P5** | Waterfall Events | 1 周 | 中 | 中 — 工具管道 |

### 15.2 推荐实施路径

```
Phase 1 (1 周): 快赢修复
  ├── QW-1: Compaction 并发锁
  ├── QW-2: 工具结果统一截断
  ├── QW-3: Cancel orphan 修复
  └── QW-4: Typed IDs

Phase 2 (2 周): 上下文系统升级 + Trace 升级 + "Everything is a Plugin"
  ├── C-1: ContextInjector 协议（loop.py 解耦）
  ├── C-2: Request Envelope 日志（llm_request trace 事件 + sidecar offload）
  ├── C-3: Scoped Tools + Guard Pipeline
  └── C-4: Registry[T] + Tool 插件发现 + Prompt 自动发现

Phase 3 (2 周): 交互升级
  ├── C-5: Steering 中途纠偏
  └── C-6: Session Fork

Phase 4 (4 周): 架构演进 + Trace Viewer
  ├── A-1: Surface Mutation
  ├── A-2: Trace Viewer UI（从 event_log 派生 timeline + token 图表）
  ├── A-3: Continuable Subagents
  └── A-4: Waterfall Events
```

### 15.3 风险缓解

1. **Compaction 锁**：先在 feature branch 实现，跑通所有 compaction 测试后合并
2. **ContextInjector**：向后兼容，现有 4 个注入逻辑直接迁移为 injector 类
3. **Request Envelope**：利用已有 `write_text_entry()` sidecar offload，不新增存储
4. **Trace Viewer**：先做只读 timeline，不做实时 streaming（Phase 4 后期）
5. **Surface Mutation**：先在 feature branch 实现，跑通所有 compaction 测试后合并
6. **Continuable Subagents**：先实现简单版（无 activation 生命周期），后续迭代

---

## 16. 关键 URL 速查

| 资源 | URL |
|------|-----|
| GitHub Repo | https://github.com/deepseek-ai/deepseek-harness |
| 官网 | https://deepseek.com/harness |
| 开发者文档 | https://deepseek-harness.github.io/deepseek-harness/guide/quickstart |
| 架构文档 | https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/architecture.md |
| Cordis 框架 | https://github.com/cordiverse/cordis |
| Cordis 论文 | https://github.com/cordiverse/paper |
| Awesome 插件 | https://github.com/awesome-dsh-plugin/awesome-dsh-plugin |
| 桌面应用 | https://github.com/anywhere-labs/deepseek-harness-desktop |
| Discord 社区 | https://discord.gg/Ycq5dCaS4 |

---

## 17. 总结

DeepSeek Harness 是一个设计精良的 agent 运行时框架，其核心创新在于：

1. **Everything is a Plugin** — 无特权核心，所有能力可替换
2. **Capability Seam** — Definition/Provider/Consumer 三元组，替换 Provider 即改变产品
3. **Append-Only Session Log + Surface** — 事件溯源 + Surface Replace 遮蔽，优雅的 compaction
4. **四层 Context Assembly** — Sections + Dynamic Context + Tools + Variables，scoped merge + waterfall
5. **Scoped Tool Registration** — 作用域工具 shadow globals + 单调拒绝 guard
6. **Continuable Subagents** — 持久化子会话 + activation 生命周期
7. **Request Envelope** — 完整请求信封日志 + fold 重建

**对我们的核心价值**：

- **Compaction 锁 + 工具结果截断** — 修复真实 bug 风险（P0）
- **ContextInjector 协议** — 解耦 loop.py 硬编码（P1）
- **Request Envelope 日志** — trace 升级，为 Trajectory View 打地基（P1）
- **"Everything is a Plugin" 最小方案** — ~280 行统一注册表 + 插件发现（P1-P3）
- **Surface Mutation** — 简化 compaction 逻辑（P4）
- **Continuable Subagents** — 支撑多 agent 协作（P5）
- **Trace Viewer UI** — 开发者调试核心工具（P4）

**我们的优势**（不需要羡慕 DSH 的地方）：

- 更成熟的 event sourcing（SQLite 持久化 vs 内存态）
- 更智能的 compaction（LLM 摘要 vs 简单截断）
- Study 长周期执行框架（DSH 无此概念）
- 多租户/预算控制（DSH 无此概念）
- 更丰富的 agent hook 系统（13 点 vs 无）
- Tool circuit breaker + no-progress 检测（DSH 无）
