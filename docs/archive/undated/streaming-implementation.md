# 流式输出实现计划

**日期**: 2026-07-26
**状态**: 实施中

---

## 一、设计决策

| 决策 | 选择 |
|---|---|
| AgentLoop 改动 | 允许修改，支持流式 |
| 流式模式 | 默认开启 |
| Spinner 位置 | TranscriptView 中（内联） |
| 工具调用 UX | 开始 ⏳ → 完成 ✔/✘ |
| 错误处理 | 重试 3 次，超过显示失败 |
| 事件格式 | 与 vibe-trading 兼容 |

---

## 二、事件类型（与 vibe-trading 兼容）

| 事件类型 | 数据字段 | 来源 |
|---|---|---|
| `text_delta` | `{text: str}` | LLM streaming |
| `tool_call` | `{tool: str, args: dict, call_id: str}` | AgentLoop tool execution |
| `tool_result` | `{tool: str, call_id: str, ok: bool, elapsed_ms: int}` | AgentLoop tool completion |
| `llm_usage` | `{input_tokens: int, output_tokens: int}` | StreamChunk.usage |
| `thinking_start` | `{}` | LLM call started |
| `thinking_end` | `{}` | First token received |
| `error` | `{message: str, fatal: bool}` | Error occurred |
| `retry` | `{attempt: int, max: int}` | Retrying |

---

## 三、组件改动

### 1. AgentLoop (core/agent/loop.py)

- 添加 `on_event` 回调参数
- 添加 `_emit()` 方法
- 添加 `stream_mode` 参数（默认 True）
- 流式模式使用 `client.stream()`
- 工具调用发射 `tool_call` / `tool_result` 事件
- 错误重试 3 次

### 2. llm_streaming.py (cli/llm_streaming.py)

- 重写 `stream_chat_to_tui()` 为事件驱动
- 添加 `on_event` 回调路由事件到 widget
- 添加重试逻辑（3 次）
- 移除 `_consume_sync_stream`

### 3. ThinkingSpinner (cli/tui/widgets/thinking_spinner.py)

- 新增内联 spinner widget
- 实时计时（100ms 刷新）
- 首 token 到达后自动停止

### 4. ToolsRail (cli/tui/widgets/tools_rail.py)

- 添加 `handle_event()` 方法
- 处理 `tool_call` / `tool_result` 事件
- 实时更新工具状态和耗时

### 5. StatusHeader (cli/tui/widgets/status_header.py)

- 添加 `start_status_polling()` 方法
- 200ms 轮询连接状态
- 实时更新 token 计数

### 6. App (cli/tui/app.py)

- 添加 `route_agent_event()` 方法
- 路由事件到对应 widget

### 7. Session (cli/tui/session.py)

- 修改 `dispatch()` 使用流式调用
- 传递 `stream_mode=True`

---

## 四、事件流程

```
用户输入 → ChatSession.dispatch() → stream_chat_to_tui()
    ↓
AgentLoop.run(stream_mode=True)
    ↓
client.stream(messages) → 逐 token 发射事件
    ↓
on_event() → app.route_agent_event() → 更新 widget
    ↓
ThinkingSpinner / TranscriptView / ToolsRail / StatusHeader
```

---

## 五、错误处理

- 网络错误：重试 3 次，指数退避
- 超过 3 次：显示错误信息
- 工具调用失败：标记为 error 状态

---

## 六、工作量

| 步骤 | 工作量 |
|---|---|
| AgentLoop 流式 | 2h |
| llm_streaming 重写 | 1.5h |
| ThinkingSpinner | 0.5h |
| ToolsRail 事件 | 0.5h |
| StatusHeader 轮询 | 0.5h |
| App 事件路由 | 0.5h |
| Session 集成 | 0.5h |
| 测试 | 1h |
| **总计** | **7h** |
