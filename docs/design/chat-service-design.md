# ChatService 统一架构设计

## 目标

将 API 路径（FastAPI + SSE）和 TUI 路径（Textual）的聊天功能统一到同一个核心服务，同时保留 strategy-research 现有的关键优势。

## 背景

### 现状问题

1. **API 和 TUI 重复实现**：`_run_agent_loop_background`（chat.py）和 `_run_agent_loop`（session.py）有大量相同代码（~80 行）
2. **上下文管理缺失**：AgentLoop 完全不接收历史消息，每条消息都是无状态调用
3. **SSE 无重连重放**：当前 `sse_buffer` 断开后无法恢复
4. **没有 Attempt 概念**：每次 AgentLoop 执行没有可追踪的记录（metrics, react_trace）

### vibe_trading 的可借鉴设计

通过调研 `/home/ll/vibe_env/lib/python3.11/site-packages/src/session/`，发现 vibe_trading 有以下成熟设计可直接复用：

- **`EventBus`**：线程安全 pub/sub，支持 Last-Event-ID 重连重放 + 30s 心跳
- **`SessionService`**：统一的消息流处理（持久化 + AgentLoop + 事件分发）
- **`SessionStore`**：文件系统持久化（我们改造为复用 SQLite）
- **`_convert_messages_to_history`**：按字符预算裁剪历史（~3000 tokens）
- **`Attempt` 模型**：跟踪每次 AgentLoop 执行

### strategy-research 已有优势

| 功能 | 位置 |
|------|------|
| SQLite 持久化（`SessionDB`） | `core/session/db.py` |
| 自动标题生成 | `web_session.py:auto_title_session()` |
| 多模型路由 | `LLMConfig` + `_build_llm_config()` |
| 用户消息 ID 双轨制（修复 SSE 误覆盖 bug） | `chat.py:send_async` |

## 架构

### 分层设计

```
┌─────────────────────────────────────────────────────────┐
│                  Transport Layer                        │
│   ┌──────────────────┐    ┌──────────────────────────┐ │
│   │ SSE Transport    │    │ Textual Transport        │ │
│   │ (api/routers)    │    │ (cli/tui)                │ │
│   └──────────────────┘    └──────────────────────────┘ │
├─────────────────────────────────────────────────────────┤
│                  Service Layer (共享)                    │
│   ┌──────────────────────────────────────────────────┐ │
│   │  SessionService                                  │ │
│   │  - send_message()                                │ │
│   │  - _run_attempt()                                │ │
│   │  - _convert_messages_to_history()                │ │
│   └──────────────────────────────────────────────────┘ │
│   ┌──────────────┐  ┌──────────────┐  ┌─────────────┐ │
│   │ SessionStore │  │  EventBus    │  │ Models      │ │
│   │ (SQLite)     │  │ (重连重放)    │  │             │ │
│   └──────────────┘  └──────────────┘  └─────────────┘ │
├─────────────────────────────────────────────────────────┤
│                  Agent Layer (共享)                     │
│   ┌──────────────────────────────────────────────────┐ │
│   │  AgentLoop + ContextBuilder                      │ │
│   │  - arun(task, history=...)                       │ │
│   │  - build_initial_messages(task, history=...)     │ │
│   └──────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────┘
```

### 数据流

```
用户发送消息
  ↓
[API] POST /chat/send_async        [TUI] InteractiveContext
  ↓                                  ↓
SessionService.send_message()
  ↓
  ├─→ Message 持久化 (SQLite，user msg 不传 message_id)
  ├─→ auto_title_session() (保留优势)
  ├─→ EventBus.emit("message.received", ...)
  └─→ Attempt 创建 + asyncio.create_task(_run_attempt)
        ↓
       _run_attempt()
        ↓
        ├─→ SessionStore.get_messages() (SQLite)
        ├─→ _convert_messages_to_history() (字符预算裁剪)
        ├─→ AgentLoop.arun(task, history=...)
        │     ↓
        │    ContextBuilder.build_initial_messages(task, history)
        │     ↓
        │    [system, ...history, current_user_message]
        │     ↓
        │    LLM 调用 + 流式响应
        │     ↓
        │    event_callback → EventBus.emit(...)
        │                     ├─→ SSE Transport (前端)
        │                     └─→ Textual Transport (终端)
        ├─→ Message 持久化 (assistant, message_id=attempt.message_id)
        └─→ EventBus.emit("attempt.completed", ...)
```

## 文件改动清单

### 新建文件

| 文件 | 来源 | 说明 |
|------|------|------|
| `src/agentnodes/api/session/events.py` | 复制 `vibe_trading/src/session/events.py` | EventBus + SSEEvent，直接复用 |
| `src/agentnodes/api/session/models.py` | 复制 `vibe_trading/src/session/models.py` | Session + Message + Attempt dataclass |
| `src/agentnodes/api/session/store.py` | 复制 + 适配 | 复用 SessionDB，新增加 attempts 表 |
| `src/agentnodes/api/session/service.py` | 复制 + 适配 | SessionService 核心 |
| `docs/design/chat-service-design.md` | 新建 | 本文档 |

### 修改文件

| 文件 | 改动 |
|------|------|
| `core/session/db.py` | 新增 `attempts` 表 CRUD |
| `core/agent/context.py` | `build_initial_messages(task, history=None)` |
| `core/agent/loop.py` | `arun(task, *, context=None, history=None)` |
| `api/routers/chat.py` | 简化为 `SessionService` 包装 |
| `api/routers/web_session.py` | `persist_message` 委托给 `SessionStore` |
| `cli/tui/session.py` | 也用 `SessionService` + Textual EventBus |
| `api/sse_buffer.py` | 标记 deprecated（由 EventBus 取代） |

## 关键设计决策

### 1. SessionStore 复用 SessionDB（保留 SQLite 优势）

vibe_trading 用文件系统 JSONL 持久化消息，strategy-research 已有 SQLite `SessionDB`（`core/session/db.py`），方案：

- **保留 SessionDB 作为底层**：避免引入第二种持久化机制
- **新增 attempts 表**：在 SessionDB 同库内新建，确保事务一致性
- **接口对齐**：SessionStore 的 `append_message()` / `get_messages()` 内部调用 `SessionDB.add_message()` / `SessionDB.search_messages()`

### 2. 用户消息 ID 不控制（保留双 ID 修复）

刚才修复的 SSE 角色互换 bug，核心是 `message_id` 必须专属助手消息。SessionService 中：

```python
# 用户消息：不传 message_id，让 DB 自动生成 UUID
self.store.append_message(Message(session_id=..., role="user", content=...))

# 助手消息：使用 attempt.message_id（与 SSE 事件关联）
self.store.append_message(Message(
    session_id=..., role="assistant", content=...,
    linked_attempt_id=attempt.attempt_id,
), message_id=attempt.message_id)
```

### 3. 历史消息按字符预算裁剪（直接复制 vibe_trading）

```python
MAX_HISTORY_CHARS = 12000  # ~3000 tokens

# 从最新往回裁剪
total_chars = 0
trimmed = []
for msg in reversed(history):
    if total_chars + len(msg["content"]) > MAX_HISTORY_CHARS:
        break
    trimmed.append(msg)
    total_chars += len(msg["content"])
return list(reversed(trimmed))
```

### 4. EventBus 直接复制（保留 SSE 重连能力）

`vibe_trading/src/session/events.py` 的 EventBus 设计完整：
- 线程安全（`call_soon_threadsafe`）
- 缓冲最近 500 事件
- Last-Event-ID 重放
- 30s 心跳

零修改复制到 `api/session/events.py`。

### 5. ContextBuilder.history 参数（修复上下文管理）

```python
# core/agent/context.py
def build_initial_messages(
    self, task: str,
    history: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    messages = [{"role": "system", "content": self.build_system_prompt()}]
    if history:
        messages.extend(history)
    messages.append(self._build_user_message(task))
    return messages

# core/agent/loop.py
async def arun(self, task: str, *, context: str | None = None,
               history: list[dict[str, Any]] | None = None) -> LoopResult:
    ...
    messages = self.context_builder.build_initial_messages(full_task, history=history)
```

## 实施步骤

### Phase 1: 基础设施（零风险）

1. 复制 `events.py` → `api/session/events.py`
2. 复制 `models.py` → `api/session/models.py`
3. 修改 `core/agent/context.py` + `core/agent/loop.py` 加 `history` 参数

### Phase 2: 持久化层

4. 修改 `core/session/db.py` 新增 `attempts` 表
5. 新建 `api/session/store.py`（复用 SessionDB）

### Phase 3: 核心服务

6. 新建 `api/session/service.py`（SessionService）
7. 改造 `api/routers/chat.py` 使用 SessionService
8. 改造 `cli/tui/session.py` 使用 SessionService

### Phase 4: 验证

9. 测试：发送消息 → 切换会话 → 切回（验证上下文管理 + 角色正确）
10. 测试：断网重连 SSE（验证 EventBus 重放）

## 风险与缓解

| 风险 | 缓解 |
|------|------|
| EventBus 复制可能引入 vibe_trading 特有依赖 | events.py 仅依赖 stdlib，零依赖 |
| SessionService 重构可能破坏现有 API | 保留原有路由，新代码作为内部实现 |
| AgentLoop history 参数可能影响其他调用点 | `history` 默认 `None`，向后兼容 |

## 兼容性

- `SessionService.send_message()` 返回的字段包含 `message_id` 和 `attempt_id`，前端 SSE event 仍用 `message_id` 关联
- `SessionDB` 接口不变，`web_session.py` 的 REST 端点继续工作
- `_build_messages()` in `cli/llm_streaming.py` 可改为调用 `_convert_messages_to_history()`