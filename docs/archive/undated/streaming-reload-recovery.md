# 刷新后流式状态恢复（reload recovery）

> 关联: `docs/archive/undated/streaming-space-recovery.md`（流式空格根因）、`docs/design/projector-incremental.md`（增量投影）

## 问题

页面刷新（或会话切换重载）后，进行中的 agent 输出状态丢失：

1. **running 场景** — agent 正在执行（LLM 思考 / 工具执行的事件间隙），刷新后：
   - 消息已由 P0（iter_start 边界 flush）物化到 DB，刷新后可见；
   - 但 `streamingMessageId = null`（store 初始态），`MessageList.tsx` 的
     `isStreaming = message.id === streamingMessageId` 恒为 false，
     StreamingText 光标 / ThinkingBlock 展开动画不显示（“输出标识消失”）。
2. **queued 场景** — 排队等待的消息：
   - 占位 assistant 消息**只在前端内存**（`queueHandlers.messageReceived` →
     `addMessage`），不落 DB（projector 的 `_on_message_received` 只物化
     user 消息）；刷新即消失；
   - queued → running 的切换事件（`attempt.started`）在刷新前已被浏览器
     消费，不在 SSE 重放窗口（`chat.py::_replay_missed` 只重放
     `Last-Event-ID` 之后的事件）→ 刷新后无法得知状态已流转。

### 为什么 SSE 重放救不了

浏览器 EventSource 重连带 `Last-Event-ID`，后端重放其**之后**的缓冲事件。
`message_received` / `attempt.started` 都发生在刷新前，不在重放窗口内。
agent 处于事件间隙（等待下一次 LLM 调用 / 工具执行中）时没有任何新事件，
前端因此无从得知 attempt 仍在运行——直到下一个 `text_delta` /
`thinking_delta` / `tool_call` 事件到达。

## 方案（B: 端点重建，不持久化占位）

占位消息保持纯前端内存态（与现状一致），由后端新增的权威端点告知
“当前 session 有哪些未终结 attempt”，前端据此恢复流式状态。

```
前端 loadMessages ──► GET /api/chat/attempts?session_id=X
                          │
                          ▼
                 service.list_active_attempts()
                   ├─ running: _active_loops 命中（僵尸守卫）
                   └─ queued:   session 有活跃消费队列（僵尸守卫）
```

### 后端

1. `store.list_attempts_by_status(session_id, statuses)` — attempts 表按
   `created_at` 升序查询，返回非终态 attempt 列表。
2. `service.list_active_attempts(session_id)`：
   - 查询 attempts 表 `status IN ('pending','running')`；
   - **僵尸守卫**（服务重启后内存状态清空，DB 残留不能误导前端）：
     - `running` 必须命中 `self._active_loops`（service.py:385 注册的
       attempt task）；
     - `pending` 仅当 `session_id in self._session_queues`（活跃消费队列，
       service.py:427 队列空即 pop）；
   - 返回 `[{attempt_id, message_id, status, prompt, created_at}]`，
     status 归一为 `running` / `queued`。
3. `GET /api/chat/attempts?session_id=...`（`chat.py`，prefix `/api/chat`，
   复用 `_fetch_session_owned` 权限守卫，与 `/events` 一致）。

### 前端（`stores/chat.ts`）

`loadMessages` 末尾追加 `fetchSessionAttempts(sessionId)`（沿用
`loadMessagesSeq` 竞态守卫，防止会话切换后旧响应覆盖）：

- `running` 且本地已有该 message_id 的消息 → `setActiveAttempt(attempt_id)`
  + `setStreamingMessage(message_id)` → 光标 / thinking 动画立即恢复；
- `running` 但本地**没有**该消息（第一迭代内刷新，P0 尚未物化）→ 先
  `addMessage` 创建空占位（id = message_id，无 parts，无 queue_status），
  再设置 streaming → 消息条 + 标识立即出现，后续 flush / SSE 事件接管；
- `queued` → 本地无则 `addMessage` 重建占位（metadata.queue_status =
  'queued' + queue_position / queue_length，created_at = attempt.created_at），
  已有则仅更新位置信息。

### 为什么不做 A（持久化占位到 DB）

- 占位转正（真实流开始）时需清理 metadata，`_upsert_message` 的
  ON CONFLICT DO UPDATE 保留旧 metadata 逻辑会残留 `queue_status`，完成后
  误显示“等待中”；
- 服务重启后 DB 僵尸占位永久残留（队列是内存的，重启即丢，但占位在 DB）；
- projector 语义改动面大，收益（少一次 API 往返）不值得。

### 已知边界

- 单 worker 前提：`_active_loops` / `_session_queues` 是进程内存；多 worker
  部署时 running 检测失效（当前 `serve --reload` 为 reloader + 1 worker）。
- 微竞态：端点返回 running 时 attempt 恰已完成 → SSE 重放 `agent_done`
  （在重放窗口内）自愈。
- 服务重启后，DB 里残留的部分物化消息仍会显示（无标识）——agent 已死，
  这是正确行为。
- 排队中的 attempt 若在刷新期间自然跑完：占位消失、DB 有最终消息，
  无脏数据。

## 测试

- 后端 pytest：`list_active_attempts` 三态（running 在跑 / queued 排队 /
  重启僵尸全排除）+ 端点路由。
- 前端 vitest：mock api → running 恢复 streamingMessageId / queued 重建占位
  / 空响应不动作。
- e2e：发 2 条消息排队 → 中途刷新 → 第 1 条光标 + 第 2 条“等待中 2/2”
  → 完成后刷新显示最终结果。
