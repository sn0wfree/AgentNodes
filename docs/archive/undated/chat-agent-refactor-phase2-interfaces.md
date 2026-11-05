# Phase 2: 核心抽象与接口设计

> 11 个 Protocol 接口，9 层架构。所有模块依赖 Protocol 而非具体实现。

## 1. 设计原则

| 原则 | 说明 |
|---|---|
| **依赖倒置（DIP）** | 所有模块依赖 Protocol/ABC 接口，非具体实现 |
| **状态与行为分离** | 不可变 `AgentState`（Pydantic frozen） |
| **异常分流** | System 级重试 / Business 级转 message 回 history |
| **双 API 并存** | `emit` callback + `astream` iterator（Vercel AI SDK 模式） |

## 2. 接口家族（11 个，9 层）

### 2.1 层级 A: 核心数据契约

#### `AgentState` (Pydantic frozen)

不可变状态快照 — 所有模块只读不写，返回新 State。

字段：
- `messages: list[Message]` — OpenAI format 消息列表
- `system_prompt: str` — 当前 system prompt
- `current_iteration: int` — 当前迭代次数
- `max_iterations: int` — 最大迭代次数
- `tool_calls_made: int` — 工具调用累计
- `token_used: int` — Token 计数
- `token_limit: int | None` — 上限（None = 无限制）
- `compaction_count: int` — 压缩触发次数
- `error_count: int` — 错误累计
- `halted: bool` — 全局暂停标志
- `metadata: dict[str, Any]` — 自由元数据

### 2.2 层级 B: LLM 层

#### `BaseLLMClient` (Protocol)

统一厂商调用（OpenAI / Anthropic / local）。

方法：
- `achat(messages, tools=None, **kwargs) → LLMResponse` — 非流式调用
- `astream(messages, tools=None, **kwargs) → AsyncIterator[StreamChunk]` — 流式调用
- `config → LLMConfig` — 配置访问

**现有对接**：`core/llm/openai_client.py` 的 `OpenAICompatClient` 加 Protocol 装饰。

### 2.3 层级 C: 工具层

#### `BaseToolExecutor` (Protocol)

工具生命周期：`validate → execute → format`。

方法：
- `name → str` — 工具名
- `validate(arguments) → ValidationResult` — 参数验证
- `aexecute(arguments, context) → ToolResult` — 执行（**不抛业务异常**，错误封装进 ToolResult）
- `format_result(result) → dict` — 格式化结果

#### `BaseToolRegistry` (Protocol)

- `get(name) → BaseToolExecutor | None`
- `get_definitions() → list[ToolDefinition]`
- `with_whitelist(names) → BaseToolRegistry`

**现有对接**：`core/agent/tools.py:ToolRegistry`。

### 2.4 层级 D: 记忆层

#### `MemoryManager` (Protocol)

- `load(session_id) → list[Message]`
- `append(session_id, message)` / `save(session_id, messages)`
- `compact(messages, strategy) → list[Message]`
- `estimate_tokens(messages) → int`

#### `CompactionStrategy` (Protocol)

- `compact(messages, target_tokens) → tuple[list[Message], CompactionResult]`

**实现变体**：`TruncationStrategy` / `SummaryStrategy` / `SlidingWindowStrategy`。

**与现有对接**：三合一 `chat.py:_session_histories` / `session.py:ctx.history` / `web_session.py:store.py`。

### 2.5 层级 E: 提示词层

#### `PromptBuilder` (Protocol)

- `build_system_prompt(role, context) → str`
- `build_messages(user_query, history, context) → list[Message]`
- `estimate_tokens(messages) → int`
- `validate(messages) → ValidationResult`

**与现有对接**：三套并行 system_prompt 加载路径合一。变体：`ChatPromptBuilder` / `ResearcherPromptBuilder` / `StrategistPromptBuilder` / `DataQualityPromptBuilder` / `FactorAnalystPromptBuilder`。

### 2.6 层级 F: 输出解析层

#### `BaseOutputParser` (Protocol)

- `parse(raw) → ParseResult` — 返回 `(parsed, errors)`，**永不抛 ParseError**
- `repair(raw, errors) → str` — 修复常见 JSON 错误

### 2.7 层级 G: 事件层

#### `BaseEventBus` (Protocol)

**双 API**：callback（同步）+ iterator（异步）。

- `subscribe(event_type, callback)` / `unsubscribe(event_type, callback)`
- `emit(event_type, data)` — 同步分发
- `astream() → AsyncIterator[tuple[str, dict]]` — 异步流
- `transform(transformer)` — 流转换（filter / smooth / upper）

**与现有对接**：替代 `_emit` 同步回调 + 未来 `astream` 迭代器。

### 2.8 层级 H: 重试 / 鲁棒性层

#### `RetryPolicy` (Protocol)

- `should_retry(error, attempt) → bool`
- `delay(attempt) → float`
- `classify(error) → ErrorCategory` — SYSTEM (retry) / BUSINESS (don't retry)

**实现变体**：`ExponentialBackoff` / `FixedDelay` / `NoRetry`。

#### `CircuitBreaker` (Protocol)

- `record_attempt(tool_call_hash, success)` — 记录尝试
- `should_trip() → bool` — 是否触发熔断
- `status() → BreakerStatus` — closed / open / half_open

### 2.9 层级 I: Runner 层

#### `AgentRunner` (Protocol, 已存在)

位于 `core/workflow/agent_runner.py:30`。

```python
async def run(
    self, agent_id: str, prompt: str, tools: list[str], context: dict
) -> dict[str, Any]:
    """Returns {"answer": str, "summary": str, "parts": [...], ...}."""
```

**实现变体**：
- `StubAgentRunner`（已存在，测试用）
- `ChatAgentRunner`（Phase 5 待新增）
- `AgentLoopRunner`（已存在 dormant，Goal 模式后续）
- `SwarmRunnerAdapter`（Goal 模式后续）

## 3. 接口依赖关系图

```
                     ┌──────────────────────┐
                     │     AgentRunner      │ ← 顶层入口
                     └──────────┬───────────┘
                                │ 注入
        ┌───────────────────────┼───────────────────────┐
        ▼                       ▼                       ▼
┌───────────────┐    ┌──────────────────┐    ┌──────────────────┐
│  PromptBuilder│    │  MemoryManager   │    │   BaseLLMClient  │
└───────────────┘    └──────────────────┘    └──────────────────┘
        │                       │                       │
        └───────────┬───────────┴───────────┬───────────┘
                    ▼                       ▼
           ┌─────────────────┐    ┌──────────────────┐
           │   AgentState    │    │   BaseEventBus   │
           │   (immutable)   │    └──────────────────┘
           └─────────────────┘             │
                    ▲                       ▼
        ┌───────────┴──┐           ┌──────────────────┐
        │              │           │ BaseOutputParser │
┌───────────────┐  ┌──────────────┐         │
│  RetryPolicy  │  │CircuitBreaker│         ▼
└───────────────┘  └──────────────┘  ┌──────────────────┐
        │              │             │ BaseToolExecutor │
        └──────┬───────┴─────────────┴──────────────────┘
               ▼
      ┌─────────────────┐
      │   Exceptions    │ ← SystemError / BusinessError / ParseError
      └─────────────────┘
```

## 4. 核心约束

- 所有接口**只依赖 `AgentState` + `Message`** 等值对象
- **不互相依赖具体实现**（PromptBuilder 不知道 MemoryManager 是哪种实现）
- 注入通过构造函数：`ChatAgentRunner(prompt_builder=..., memory_manager=..., llm_client=..., tool_registry=..., event_bus=..., retry_policy=..., circuit_breaker=...)`
- 禁止内部 `import` 具体实现

## 5. 业界参考

| 框架 | 借鉴点 |
|---|---|
| Vercel AI SDK | `streamText` + `result.stream` AsyncIterable + `onChunk` callback 双 API |
| LangGraph | `astream_events()` 事件流模式 |
| Pydantic frozen | 不可变状态模式 |
| asyncio.to_thread | 同步 I/O 转异步 |
