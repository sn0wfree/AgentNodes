# Phase 3: 鲁棒性与边界条件审查

> 4 个保护机制，针对 Phase 1 的 7 个脆弱点。

## 1. 循环断路器（针对脆弱点 #6 工具调用重复）

### 1.1 当前现状

- `_check_no_progress` 仅检测最近 3 个 tool_call hash 相同
- 无 CircuitBreaker 抽象、无失败累计、无冷却

### 1.2 机制：`ToolLoopCircuitBreaker`

**三状态机**：`closed → open → half_open`

**触发条件**（任一满足即触发）：
1. 同 `tool_name` 连续失败 ≥ `failure_threshold`（默认 3）
2. 同 `tool_call_hash` 重复 ≥ `no_progress_window`（默认 3）
3. 总失败数 ≥ `max_total_failures`（默认 10）

**冷却机制**：OPEN 后 `cooldown_seconds`（默认 60s）进入 HALF_OPEN，放一次尝试决定是否恢复 CLOSED。

**行为**：OPEN 时转 tool message 回 history，让 LLM 自己纠正（**不抛异常**）。

**集成点**：`AgentLoop._aexecute_tool_batch` 中每次执行后。

### 1.3 与现有 `_check_no_progress` 的对比

| 维度 | 现有 `_check_no_progress` | 新 `ToolLoopCircuitBreaker` |
|---|---|---|
| 检测范围 | 仅 hash 相同 | hash 相同 + 同名连续失败 + 总失败数 |
| 状态机 | 无 | closed/open/half_open |
| 冷却恢复 | 无 | 60s 后自动半开 |
| 失败累计 | 无 | 按 tool_name 累计 + 总数 |
| 错误处理 | 仅 break | 转 tool message + break |

## 2. 上下文窗口溢出（针对脆弱点 #1 流式 chunk）

### 2.1 当前现状

- `_amaybe_compact` 触发条件 hard-coded
- 无预警水位线、无 abort 兜底

### 2.2 机制：`ContextWindowGuard` 三段水位线

| 水位 | 阈值 | 动作 |
|---|---|---|
| **Warning** | 80% | rate-limited 通知（每 60s 一次） |
| **Compress** | 90% | 触发 `_amaybe_compact` |
| **Abort** | 98% | 抛 `ContextOverflowError` + 友好提示（`/clear` 或 `/compact`） |

**集成点**：`AgentLoop.arun` 每个 iteration 开头。

### 2.3 错误消息设计

Abort 时返回的友好错误：
- "Context overflow (98%). Used 127K/128K tokens. Suggest /clear or /compact."
- 包含当前用量、限制、用户可执行的操作

## 3. 结构化输出兜底（针对脆弱点 #6 工具参数错误）

### 3.1 当前现状

- JSON 解析失败 → `_astream_chat:820` 降级到 `achat`
- 无修复重试、无正则提取

### 3.2 机制：`StructuredOutputParser` 四层降级

```
Layer 1: 严格 JSON 解析
   ↓ (失败)
Layer 2: 修复（trailing comma、single quote → double quote）
   ↓ (失败)
Layer 3: 正则按 schema 字段提取
   ↓ (失败)
Layer 4: 返回 None + 错误列表（永不抛 ParseError）
```

### 3.3 行为

- **可恢复**：使用 recovered data + emit `parse_recovered` 事件（标注 source: strict/repaired/regex）
- **不可恢复**：转 tool message 回 history（error="invalid_arguments" + message + expected_schema），让 LLM 重新生成

**集成点**：`AgentLoop._astream_chat` 处理 tool_call JSON 解析时。

### 3.4 修复规则（Layer 2）

| 规则 | 模式 | 修复 |
|---|---|---|
| 尾随逗号 | `,]` 或 `,}` | 移除 |
| 单引号键名 | `'key':` | `"key":`（小心撇号） |

## 4. 并发竞态（针对脆弱点 #3 history 散乱）

### 4.1 当前现状

- `chat.py:_session_histories` 模块级 dict → **并发请求同一 session 必 race condition**
- TUI 单线程安全，但 Goal 模式可能并发

### 4.2 机制：`ThreadSafeMemoryManager` 两层保护

| 层 | 机制 | 保护范围 |
|---|---|---|
| **内存层** | per-session `asyncio.Lock` | 保护 `_session_histories` 类状态 |
| **持久化层** | SQLite WAL 模式 + `check_same_thread=False` | 保护 SQLite 并发写 |

**同步转异步**：`asyncio.to_thread` 包装 SQLite 调用。

### 4.3 SQLite WAL 配置

```sql
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
```

### 4.4 与现有 `_session_histories` 对比

| 维度 | 现有 | 新 `ThreadSafeMemoryManager` |
|---|---|---|
| 并发安全 | ❌ race condition | ✅ per-session Lock + WAL |
| 持久化 | ❌ 模块级 dict（重启即失） | ✅ SQLite WAL |
| 内存泄漏 | ❌ 永不释放 | ✅ 可加 TTL / LRU |
| 跨进程 | ❌ | ✅ SQLite 共享 |

## 5. 4 个机制汇总

| 机制 | 接口 | 触发点 | 行为 |
|---|---|---|---|
| CircuitBreaker | `ToolLoopCircuitBreaker` | `_aexecute_tool_batch` | OPEN → 工具消息回 history |
| ContextWindowGuard | `ContextWindowGuard` | `arun` iteration 开头 | WARN/COMPACT/ABORT 三档 |
| StructuredOutputParser | `StructuredOutputParser` | `_astream_chat` tool_call 解析 | 4 层降级（strict/repair/regex/none） |
| ThreadSafeMemoryManager | `ThreadSafeMemoryManager` | MemoryManager 实现层 | per-session Lock + SQLite WAL |

## 6. 异常分级原则（贯穿所有机制）

| 异常类型 | 处理方式 | 例子 |
|---|---|---|
| **System** | 主循环重试 | 网络超时、LLM 5xx、流式解析失败 |
| **Business** | 转 message 回 history | 工具参数错误、JSON 解析不可恢复 |
| **Parse** | 永不抛 ParseError | 始终返回 `(None, [error])` |

## 7. 进入 Phase 4

PromptBuilder 作为首个重构目标，依赖 Phase 2 的 `PromptBuilder` Protocol 与 Phase 3 的 `ValidationResult` 概念。
