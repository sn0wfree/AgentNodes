# Chat 消息排队设计

## 问题概述

当前 `POST /chat/send_async` 在用户**已发送但未完成**的状态下再发新消息时，会**隐式取消**正在运行的 AgentLoop（`chat.py` 中的 `_loop_tasks` cancel 逻辑）。这导致：

- 用户连续发多条消息时，前面的回复全部丢失
- 用户没有取消意图，仅是希望"下一条等这条跑完再发"

**隐式取消 ≠ 用户取消**。两者必须解耦：

| 操作 | 含义 | 当前行为 | 目标行为 |
|------|------|----------|----------|
| 发新消息 | 添加新任务 | **隐式取消**当前 | 进入队列，按序处理 |
| 点 Cancel 按钮 | 用户主动取消 | 取消当前（无队列） | 取消当前 + 暂停队列，等待用户确认后继续 |

## 设计目标

1. **FIFO 串行**：单 session 内消息严格按发送顺序处理
2. **跨 session 隔离**：不同用户/会话的队列互不影响
3. **取消语义清晰**：显式 Cancel 是唯一的中断途径，取消后队列暂停等用户确认
4. **硬限 10 条**：单 session 最多排队 10 条，超出返回 `429 Too Many Requests`
5. **UX 可见**：前端展示队列位置（`2/3` 格式）和暂停 banner

## 术语

- **隐式取消**：发新消息自动杀掉旧任务——本次设计**完全删除**
- **显式取消**：用户点 Cancel 按钮或调用 `POST /chat/cancel`——保留并改进
- **队列暂停**：显式取消后队列不再自动推进，等待 `POST /chat/queue/resume`
- **队列位置**：当前消息在队列中的序号（1-based） / 队列总长度

## 后端架构

### 数据结构（`SessionService`）

```python
class SessionService:
    def __init__(self, ...):
        ...
        self._session_queues: dict[str, asyncio.Queue] = {}
        self._processing_sessions: set[str] = set()
        self._paused_sessions: dict[str, asyncio.Event] = {}
```

三种状态互斥协同：

| 状态 | 含义 |
|------|------|
| `_session_queues[session_id]` | 该 session 的消息队列 |
| `_processing_sessions` | 有 consumer 协程正在跑的 session 集合 |
| `_paused_sessions[session_id]` | 显式取消后等待 resume 的 session Event |

### `send_message` 新流程

```
send_message(session_id, content, ...)
├── 1. 持久化用户消息（不变）
├── 2. 创建 Attempt（不变）
├── 3. 检查队列上限：qsize() >= 10 → 返回 {"error": "queue_full", "limit": 10}
├── 4. 计算 queue_position = qsize() + 1, queue_length = queue_position
├── 5. SSE message_received 携带 status + queue_position + queue_length
│      - status="processing" 当队首且未暂停
│      - status="queued" 当非队首或已暂停
├── 6. 入队：{"attempt_id", "model", "max_iterations", "system_prompt", "allow_shell_tools"}
└── 7. 若 session_id not in _processing_sessions → 启动 _process_session_queue
```

### `_process_session_queue` 新流程

```
async def _process_session_queue(session_id):
    while True:
        item = await queue.get()

        # 检查暂停（取消后等待 resume）
        if session_id in _paused_sessions:
            await _paused_sessions[session_id].wait()
            del _paused_sessions[session_id]

        emit("attempt.started", {"attempt_id", "message_id"})

        try:
            await _run_attempt(item)
        except asyncio.CancelledError:
            # 用户显式取消 → 暂停队列
            _paused_sessions[session_id] = asyncio.Event()
            emit("queue_paused", {"session_id", "next_attempt_id": item["attempt_id"]})
            # 不 task_done()，等 resume 后再 task_done
            # 但要重新让出 → 实际逻辑见下方"队列暂停实现"
        except Exception as exc:
            # 单条失败 → 记录 + 继续
            logger.error(...)
            queue.task_done()
            continue

        queue.task_done()

        if queue.empty():
            break

    # 清理
    _processing_sessions.discard(session_id)
    _session_queues.pop(session_id, None)
```

**队列暂停的实现细节**：

显式取消后，consumer 需要：
1. 把 session 标记为 paused
2. 发出 `queue_paused` 事件
3. **不 task_done**（保留 task_done 给 resume 后）
4. 进入"等 resume"状态

简化方案（避免 task_done 时机问题）：

```
CancelledError 处理块:
  _paused_sessions[session_id] = asyncio.Event()
  emit("queue_paused", ...)
  queue.task_done()  # 正常 task_done
  # 进入外层 while 循环，下次 await queue.get() 之前先 await pause event
```

这样 task_done 正常调用，pause 在下次取队列项之前生效。

### `/chat/cancel` 改进

现有逻辑：取消当前 attempt。改进点：**让消费者协程自然检测到 CancelledError**，由 `_process_session_queue` 内部处理 pause + emit queue_paused。

```
POST /chat/cancel
├── 找到 session 的 active task
├── task.cancel()  # 触发 CancelledError
└── 返回 {ok: true}  # 不直接处理 pause，交给 consumer
```

### 新增 `/chat/queue/resume`

```
POST /chat/queue/resume
  body: {session_id}
  → 从 _paused_sessions 取 event，event.set()
  → consumer 在下一次循环中检测到，自动继续
  → 返回 {ok: true}
```

### `_run_attempt` 微调

`attempt.started` 事件 payload 增加 `message_id`，供前端 queued → processing 过渡。

```python
self.event_bus.emit(
    session_id,
    "attempt.started",
    {
        "attempt_id": attempt.attempt_id,
        "message_id": attempt.message_id,  # 新增
    },
)
```

### `chat.py:send_async` 删除隐式取消

```diff
- # Cancel any existing loop for this session (new message replaces old)
- if body.session_id in _loop_tasks:
-     _loop_tasks[body.session_id].cancel()

  service = _get_session_service()
  result = await service.send_message(...)
- # Track the spawned attempt task so we can cancel future messages
- for task in service._active_loops.values():
-     _loop_tasks[body.session_id] = task
-     break
+ # 处理 queue_full 错误
+ if "error" in result and result["error"] == "queue_full":
+     raise HTTPException(
+         status_code=429,
+         detail={"error": "queue_full", "limit": result["limit"]},
+     )
```

### 硬限 10 条

`send_message` 入队前检查 `_session_queues[session_id].qsize() >= 10`。并发情况下可能有 1 条超出（race），可接受。

## 前端架构

### SSE 事件流更新

#### `message_received` payload（扩展）

```typescript
{
  message_id: string,
  user_message_id: string,
  assistant_message_id: string,
  content: string,
  attempt_id: string,
  status: "processing" | "queued",     // 新增
  queue_position: number,              // 新增：1-based
  queue_length: number,                // 新增
  created_at: number,
}
```

#### `attempt.started` payload（扩展）

```typescript
{
  attempt_id: string,
  message_id: string,                  // 新增（前端用来切换 streamingMessageId）
}
```

#### 新增 `queue_paused` 事件

```typescript
{
  session_id: string,
  next_attempt_id: string,             // 队列里下一条要处理的 attempt
}
```

### chat store 新增状态

```typescript
interface ChatState {
  ...
  queuePaused: Map<string, boolean>     // session_id → 是否暂停
  queueLengths: Map<string, number>     // session_id → 队列总长度
  setQueuePaused: (sessionId: string, paused: boolean) => void
  setQueueLength: (sessionId: string, length: number) => void
}
```

### `useSSE.ts` 改动

`message_received` 分支：

```typescript
const { status, queue_position, queue_length } = data

const createdAt = data.created_at ?? Date.now() / 1000
const isQueued = status === "queued"

// 创建 user message（不变）
// 创建 assistant placeholder
addMessage({
  id: assistantMsgId,
  ...
  created_at: createdAt,
  metadata: { queue_position, queue_length, status: status ?? "processing" },
})

if (!isQueued) {
  // 当前行为：开始流式
  setStreamingMessage(assistantMsgId)
  setStreamingText("")
} else {
  // queued：不启动 streaming，等 attempt.started
  setQueueLength(sessionId, queue_length)
}
```

新增 `attempt.started`：

```typescript
case 'attempt.started': {
  const messageId = data.message_id
  if (messageId) {
    setStreamingMessage(messageId)
    setStreamingText("")
  }
  break
}
```

新增 `queue_paused`：

```typescript
case 'queue_paused': {
  setQueuePaused(sessionId, true)
  break
}
```

### `AssistantMessage.tsx` 排队态 UI

新增 props：`isQueued`、`queue_position`、`queue_length`。

排队态渲染：

```
[Bot] Agent · waiting...
       等待中... 2/3  ●●●
```

非排队态渲染不变。

`isQueued` 计算（由 `MessageList` 传入）：

```
role === "assistant"
  && parts.length === 0
  && message.id !== streamingMessageId
  && !queuePaused
```

### `MessageList.tsx` 暂停 banner

顶部 banner（消息列表上方）：

```
┌─────────────────────────────────────────┐
│ 队列已暂停，下一条等待确认 [继续下一条] │
└─────────────────────────────────────────┘
```

条件：`queuePaused.get(currentSessionId) === true`

按钮：`POST /chat/queue/resume`

### `Composer.tsx` 处理 429

```typescript
try {
  const res = await api.post(...)
} catch (err: any) {
  if (err.response?.status === 429 && err.response.data?.error === "queue_full") {
    showToast(`队列已满（最多 ${err.response.data.limit} 条），请等待完成或取消当前轮次`)
  }
}
```

## 数据流时序

### 场景 1：连续发 3 条

```
发 A
  → SSE msg_rcvd(status="processing", q=1/1)  → setStreamingMessage(A)
  → push queue[0] → consumer 启动
  → dequeue A → attempt.started → 流式开始

发 B（流式进行中）
  → SSE msg_rcvd(status="queued", q=2/2)     → 不启动流式，显示"等待中 2/2"
  → push queue[1]

发 C（流式进行中）
  → SSE msg_rcvd(status="queued", q=3/3)     → 显示"等待中 3/3"
  → push queue[2]

A 完成（agent_done）
  → consumer 继续循环 → dequeue B
  → attempt.started(B) → 前端切 streamingMessageId=B → 流式开始
```

### 场景 2：取消当前 + 手动 resume

```
流式 B 进行中
用户点 Cancel
  → POST /chat/cancel → task.cancel()
  → CancelledError → consumer 进入 pause 分支
  → SSE queue_paused(sessionId, next_attempt_id=C)
  → 前端 banner 出现"队列已暂停，下一条等待确认"

用户点"继续下一条"
  → POST /chat/queue/resume
  → event.set() → consumer 醒来
  → dequeue C → attempt.started(C) → 流式开始
```

### 场景 3：超过 10 条

```
队列已有 10 条
用户发第 11 条
  → send_message 检测 qsize() >= 10
  → 返回 {"error": "queue_full", "limit": 10}
  → chat.py 抛 HTTPException(429)
  → 前端 toast 提示
```

## 与 opencode 对照

参考 opencode（PR #35008）的实现：

| 行为 | opencode | 本设计 |
|------|----------|--------|
| 新消息排队 | ✅ FIFO | ✅ FIFO |
| 取消独立操作 | ✅ Escape 键 | ✅ Cancel 按钮 / `/chat/cancel` |
| 取消后是否自动继续 | opencode 默认会继续 | 本设计**不自动**，等显式 resume（用户决策） |
| 队列长度上限 | 无显式限制 | 硬限 10 条 |
| 队列位置显示 | footer 显示总数 | 前端显示 `q/n` 格式 |

差异原因：本系统是单用户聊天工具，用户主动控制感更强，所以选择"取消后等确认"而非 opencode 默认的"自动推进"。

## 改动清单

### 后端

| 文件 | 改动 |
|------|------|
| `api/session/service.py` | 加 `_session_queues` / `_processing_sessions` / `_paused_sessions`；改 `send_message`；新增 `_process_session_queue`；`attempt.started` 加 `message_id` |
| `api/routers/chat.py` | 删隐式 cancel；处理 queue_full → 429；新增 `/chat/queue/resume` 端点 |

### 前端

| 文件 | 改动 |
|------|------|
| `stores/chat.ts` | 加 `queuePaused` / `queueLengths` map 和 actions |
| `hooks/useSSE.ts` | `message_received` 解析 `status` / `queue_position` / `queue_length`；新增 `attempt.started` / `queue_paused` 处理 |
| `components/chat/AssistantMessage.tsx` | 加 `isQueued` / `queue_position` / `queue_length` props 和排队态渲染 |
| `components/chat/MessageList.tsx` | 计算 `isQueued` 传入；顶部暂停 banner |
| `components/chat/Composer.tsx` | 处理 429 + toast |

### 测试

| 文件 | 改动 |
|------|------|
| `tests/test_session_queue.py`（新） | 单 session 排队、跨 session 隔离、上限 10、cancel pause、resume 推进、异常隔离 |
| `webui/frontend/src/test/useSSE.test.ts` | queued 不启动 streaming、`attempt.started` 切 streaming、`queue_paused` 更新 store |
| `webui/frontend/src/test/MessageList.test.tsx` | 排队态渲染、banner 渲染 |

## 风险与缓解

| 风险 | 等级 | 缓解 |
|------|------|------|
| consumer 协程异常 | 中 | 内部 try/except 单条隔离；外层 while 用 queue.empty() 自然退出 |
| `_paused_sessions` 状态泄漏 | 中 | resume 后 `del`；consumer 退出前清理；`finally` 兜底 |
| SSE `queue_paused` 事件丢失 | 低 | 在 CancelledError 处理块直接 `emit`，与 cancel 同步 |
| 队列上限 race（并发 11 条） | 低 | 可接受；前端 toast 提示 |
| 前端 `streamingMessageId` 切换闪烁 | 低 | queued 态 placeholder 已存在，切换是"placeholder → 流式"过渡，UI 平滑 |
| Cancel 后用户不点 resume 导致队列卡住 | 中 | banner 一直显示直到 resume；用户可点 Cancel 取消队列中的某条 |

## 实施顺序

1. 写文档（本文）
2. 后端：SessionService 队列机制（任务 1）
3. 后端：chat.py 删除隐式 cancel + 429（任务 2）
4. 后端：`/chat/cancel` + `/chat/queue/resume`（任务 3）
5. 后端：`attempt.started` message_id（任务 4）
6. 前端：useSSE.ts 事件处理（任务 5）
7. 前端：chat store 状态（任务 6）
8. 前端：AssistantMessage 排队 UI（任务 7）
9. 前端：MessageList banner（任务 8）
10. 前端：Composer 429 处理（任务 9）
11. 测试（任务 10）
12. 手动回归 + 全量后端测试（任务 11）