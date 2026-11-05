# Chat SSE 事件流与 Role-Swap 修复

## 问题概述

**Role-Swap Bug**：用户消息和助手消息的内容互相错位——助手的回复显示在用户气泡里，或者用户的输入显示在 Agent 气泡里。

## 根因分析

### 原始 Bug（已修复）

旧的 `send_async` 流程：

1. 前端创建 `localAssistantId`（本地 assistant 占位符）
2. 调用 `/chat/send_async`，后端返回 `message_id`（实际是 user message id）
3. 前端把 `localAssistantId` rename 成 `message_id`
4. SSE `text_delta` 事件携带 `assistant_message_id`（与 `message_id` 不同）
5. 前端用 `assistant_message_id` 更新消息，但 store 中找不到 → 内容丢失到错误位置

**根本原因**：`message_id` 语义不明确，前端混淆了 user 和 assistant 的 ID。

### 第二个 Bug：消息消失

修复 role-swap 后，`message_received` 处理器用 `setState({ messages: next })` **替换整个 Map**。如果在 `getState()` 和 `setState()` 之间，Composer 的 `addMessage(tempUserId)` 执行了，temp 消息会被覆盖丢失。

**根本原因**：`getState()` + 修改 + `setState()` 不是原子操作。

## 最终架构

### 后端事件流

```
POST /chat/send_async
  → service.send_message()
    → emit "message_received" (user_message_id, assistant_message_id, content)
    → emit "session_meta_updated" (auto-title)
    → spawn _run_attempt()
      → emit "attempt.started"
      → AgentLoop.arun()
        → emit "thinking_start/thinking_delta/thinking_end"
        → emit "text_delta" (message_id = assistant_message_id)
        → emit "assistant_message" (message_id = assistant_message_id)
        → emit "iter_end"
      → emit "attempt.completed" or "attempt.failed"
      → emit "agent_done" (clears frontend streaming state)
  → return {user_message_id, assistant_message_id}
```

### 前端事件处理

| 事件 | 处理器 | 动作 |
|------|--------|------|
| `message_received` | useSSE | `addMessage`(user) + `addMessage`(assistant placeholder) |
| `thinking_start` | useSSE | `updateMessage`(add thinking part) |
| `thinking_delta` | useSSE | `updateMessage`(append delta) |
| `text_delta` | useSSE | `updateMessage`(append text) + `appendStreamingText` |
| `assistant_message` | useSSE | `updateMessage`(replace text) |
| `agent_done` | useSSE | `setStreamingMessage(null)` |
| `session_meta_updated` | useSSE | update session title in session store |

### 关键设计决策

#### 1. 双 ID 协议

`message_received` 事件携带两个 ID：
- `user_message_id`：用户消息 ID（DB 生成的 UUID）
- `assistant_message_id`：助手消息 ID（Attempt 的 `message_id` 字段）

SSE 事件（`text_delta`、`thinking_*` 等）携带 `assistant_message_id`。

#### 2. 增量更新，不替换 Map

`message_received` 处理器使用 `addMessage`（immer 的 `set`，增量更新），不使用 `setState({ messages: next })`（Map 替换）。

**为什么不能替换 Map**：
```
时间线：
T1: Composer addMessage(tempUserId) → store: [..., tempUser]
T2: SSE message_received → getState() → store: [..., tempUser]
T3: Composer addMessage(tempUserId2) → store: [..., tempUser, tempUser2]
T4: SSE handler setState({messages: next}) → store: [..., tempUser]  ← tempUser2 丢失！
```

使用 `addMessage` 后：
```
T1: Composer addMessage(tempUserId) → store: [..., tempUser]
T2: SSE message_received → addMessage(userId) → store: [..., tempUser, user]
T3: Composer addMessage(tempUserId2) → store: [..., tempUser, user, tempUser2]
T4: SSE handler addMessage(assistantMsgId) → store: [..., tempUser, user, tempUser2, assistant]
```

#### 3. Composer 不创建 assistant 占位符

旧方案：Composer 创建 `localAssistantId` 占位符，然后 rename。
新方案：Composer 只创建 optimistic user message，assistant 占位符由 SSE `message_received` 创建。

这消除了 rename 逻辑中的 ID 混淆。

#### 4. agent_done 事件

`service.py:_run_attempt()` 在完成/失败/取消后都发送 `agent_done` 事件。前端清除 `streamingMessageId`，结束"正在输入"状态。

## 文件变更清单

### 后端

| 文件 | 变更 |
|------|------|
| `api/session/service.py` | `send_message()` 返回 `user_message_id` + `assistant_message_id`；`_run_attempt()` 发送 `agent_done` |
| `api/routers/chat.py` | `SendMessageResponse` 加 `user_message_id` + `assistant_message_id` 字段 |

### 前端

| 文件 | 变更 |
|------|------|
| `components/chat/Composer.tsx` | 删除 `localAssistantId` 创建和 rename；只做 optimistic user message |
| `hooks/useSSE.ts` | `message_received` 用 `addMessage`（增量）替代 `setState({messages: next})`（替换）；新增 `agent_done` case |

## 测试

- `tests/test_e2e_chat.py`：Playwright E2E 测试，验证无 role-swap、历史上下文、streaming 清理
- `tests/test_e2e_persist.py`：Playwright E2E 测试，验证发送多条消息后所有消息保留
