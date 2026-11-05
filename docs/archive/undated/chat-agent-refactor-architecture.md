# Chat Agent 重构架构总览

> 4 阶段重构框架的架构总览。Phase 1-3 是诊断与设计，Phase 4 是首个落地模块（PromptBuilder）。

## 1. 重构背景

Chat Agent 前期未做完整规划，存在以下系统性问题：

- **职责散乱**：LLM 调用、工具调用、记忆、提示词、事件流等多个职责在多个文件中重复出现
- **DRY 违背**：AgentLoop 构造参数、事件 → parts 累加、system_prompt 加载等逻辑重复 2-3 次
- **状态分散**：会话历史有 3 个不同来源（chat.py 模块级 dict、TUI ctx、SQLite），无统一不可变快照
- **脆弱环节多**：7 个脆弱点，包括流式 chunk 解析失败、内存泄漏、同步工具阻塞等
- **缺乏鲁棒性机制**：无 CircuitBreaker、无 ContextWindowGuard、无 4 层 JSON 降级、无并发保护

## 2. 4 阶段框架

```
Phase 1: 诊断与抽象 —— 梳理现有逻辑，不改代码
Phase 2: 核心抽象与接口设计 —— 用 Protocol 固定契约
Phase 3: 鲁棒性与边界条件审查 —— 死循环/Token 溢出/JSON 解析/并发
Phase 4: 单模块高质量重构 —— 选 PromptBuilder 作为首个落地模块
```

### 2.1 Phase 1 关键发现

- **12 个核心职责**（LLM 调用、工具调用、记忆、提示词、事件流、解析、压缩、重试、SSE、TUI、持久化、Goal 适配）
- **5 类 DRY 违背**（AgentLoop 构造参数 12 行重复 / 事件累加 60 行 / system_prompt 3 套并行 / history 3 来源 / compact_config 来源不一致）
- **7 个脆弱环节**（流式解析失败 / on_event 异常 / 内存泄漏 / text_id 缺失 / 同步工具阻塞 / 工具无去重 / max_iter 边界）
- **9 项状态散乱**（会话历史、迭代计数、压缩计数、工具计数、Token 估算、goal_continuation、halt、mode、配置）

详细分析见 [`chat-agent-refactor-phase1-diagnosis.md`](./chat-agent-refactor-phase1-diagnosis.md)。

### 2.2 Phase 2 接口设计

定义 11 个 Protocol 接口，分 9 层。详细见 [`chat-agent-refactor-phase2-interfaces.md`](./chat-agent-refactor-phase2-interfaces.md)。

### 2.3 Phase 3 鲁棒性机制

4 个保护机制：CircuitBreaker、ContextWindowGuard、StructuredOutputParser、ThreadSafeMemoryManager。详细见 [`chat-agent-refactor-phase3-robustness.md`](./chat-agent-refactor-phase3-robustness.md)。

### 2.4 Phase 4 首个落地模块

选 `PromptBuilder` 作为首个重构目标。详细见 [`chat-agent-refactor-phase4-prompt-builder.md`](./chat-agent-refactor-phase4-prompt-builder.md)。

## 3. 核心设计原则

### 3.1 依赖倒置（DIP）

所有模块依赖 Protocol/ABC 接口，而非具体实现。模块间通过构造函数注入（DI），禁止内部 `import` 具体实现。

### 3.2 状态与行为分离

定义不可变 `AgentState`（Pydantic frozen），所有模块只操作 State 并返回新 State（`state.model_copy(update={...})`）。

### 3.3 异常分流

- **System 级**（网络超时、LLM 错误）：主循环重试
- **Business 级**（工具参数错误、JSON 解析失败）：捕获后转为 message 放回 history，让 LLM 自己纠正
- **Parse 级**：永不抛 ParseError，返回 `(None, [error])` 让上层决定

### 3.4 双 API 并存

事件层提供 `emit` callback（同步）+ `astream` iterator（异步）双 API（Vercel AI SDK 模式）。

## 4. 与现有架构的对接

| 现有组件 | 重构后对接 |
|---|---|
| `OpenAICompatClient` | 加 Protocol 装饰，对接 `BaseLLMClient` |
| `ToolRegistry` | 对接 `BaseToolRegistry` |
| `AgentLoop._emit` | 替换为 `BaseEventBus.emit` |
| `AgentRunner` (Protocol) | 顶层入口，新增 `ChatAgentRunner` 实现 |
| 3 套 system_prompt 路径 | 三合一为 `PromptBuilder` |

## 5. 实施路线图

| 阶段 | 任务 | 状态 |
|---|---|---|
| Phase 1 | 诊断与抽象 | ✅ 完成 |
| Phase 2 | 接口设计 | ✅ 完成 |
| Phase 3 | 鲁棒性审查 | ✅ 完成 |
| Phase 4 | PromptBuilder 落地 | 🚧 进行中 |
| Phase 5 | ChatAgentRunner 实施 | ⏳ 待启动 |
| Phase 6 | MemoryManager 三合一 | ⏳ 待启动 |
| Phase 7 | BaseEventBus 实施 | ⏳ 待启动 |
| Phase 8 | 鲁棒性机制落地 | ⏳ 待启动 |

## 6. 参考资料

- Vercel AI SDK: `streamText` + `result.stream` AsyncIterable + `onChunk` callback 双 API
- LangGraph: `astream_events()` 事件流模式
- Pydantic frozen=True: 不可变状态模式
- asyncio.to_thread: 同步 I/O 转异步
