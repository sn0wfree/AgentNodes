# SSE Multicast + 连接状态 Store 设计文档

## 背景

### 问题

`SSEStatus` 组件和 `useSSE` hook 各自打开独立的 `EventSource` 连接到同一个 `/api/chat/events?session_id=xxx` 端点。后端 `sse_buffer.register_session()` 用 `dict[str, asyncio.Event]` 存储，第二个连接**覆盖**第一个连接的 notification event。结果：

1. `useSSE` 的 generator 等待的 `asyncio.Event` 永远不会被 `set()`
2. 用户发送消息后看不到任何回复（SSE 事件丢失）
3. `SSEStatus` 显示"已断开"但实际后端正常

### 根因

```python
# sse_buffer.py — 单消费者设计
def register_session(self, session_id: str) -> asyncio.Event:
    evt = asyncio.Event()
    self._session_events[session_id] = evt  # ← 第二次调用覆盖第一次
    return evt
```

两个前端组件（`useSSE` + `SSEStatus`）各调用一次 `register_session`，后者覆盖前者。

---

## 设计方案：混合方案（后端 multicast + 前端 store）

### 架构概览

```
┌─────────────────────────────────────────────────────┐
│  Browser Tab                                         │
│                                                      │
│  useSSE(sessionId)          SSEStatus                │
│    │                           │                     │
│    │  EventSource #1           │  读 store（无连接） │
│    │  ↓                        │                     │
│  ┌─┴──────────────────────────┴─┐                   │
│  │  useSSEStore:                 │                   │
│  │    status: 'connected'        │                   │
│  │    sessionId: 'xxx'           │                   │
│  └──────────────┬────────────────┘                   │
│                 │                                     │
└─────────────────┼───────────────────────────────────┘
                  │
    ┌─────────────▼─────────────┐
    │  Backend                  │
    │  sse_buffer               │
    │  _session_events:         │
    │    { session_id:          │
    │      set(Event1, Event2)  │  ← multicast
    │    }                      │
    └──────────┬────────────────┘
               │ push()
    ┌──────────▼────────────────┐
    │  AgentLoop background     │
    │  sse_buffer.push(...)     │
    │  → 遍历 set，set() 所有  │
    └───────────────────────────┘
```

### 改动清单

| # | 文件 | 改动类型 | 行数 | 说明 |
|---|------|---------|------|------|
| 1 | `sse_buffer.py` | 后端 | +5 | `dict[str, Event]` → `dict[str, set[Event]]` |
| 2 | `chat.py` | 后端 | +1 | `unregister_session` 传入具体 event |
| 3 | `stores/sse.ts` | 新增 | ~10 | 连接状态 store |
| 4 | `useSSE.ts` | 前端 | +3 | `onopen`/`onerror` 写入 store |
| 5 | `SSEStatus.tsx` | 前端 | 重写 | 删除 EventSource，读 store |

**总计：5 个文件，~35 行改动，删除 1 个重复连接。**

---

## 详细设计

### 1. sse_buffer.py — Multicast 改造

```python
# 数据结构变化
# BEFORE:
self._session_events: dict[str, asyncio.Event] = {}

# AFTER:
from collections import defaultdict
self._session_events: dict[str, set[asyncio.Event]] = defaultdict(set)
```

**register_session** — 追加而非覆盖：
```python
def register_session(self, session_id: str) -> asyncio.Event:
    evt = asyncio.Event()
    self._session_events[session_id].add(evt)
    return evt
```

**unregister_session** — 移除具体 event（非整个 session）：
```python
def unregister_session(self, session_id: str, event: asyncio.Event):
    events = self._session_events.get(session_id)
    if events:
        events.discard(event)
        if not events:
            del self._session_events[session_id]
```

**push** — 通知所有注册的 listener：
```python
def push(self, event: str, data: str, session_id: str) -> str:
    # ... existing code ...
    for evt in self._session_events.get(session_id, set()):
        try:
            evt.set()
        except RuntimeError:
            pass
    return event_id
```

### 2. chat.py — unregister 签名更新

```python
# BEFORE:
finally:
    sse_buffer.unregister_session(session_id)

# AFTER:
finally:
    sse_buffer.unregister_session(session_id, notification_event)
```

### 3. stores/sse.ts — 连接状态 Store

```typescript
import { create } from 'zustand'

type SSEStatus = 'connecting' | 'connected' | 'disconnected'

interface SSEState {
  status: SSEStatus
  sessionId: string | null
  setStatus: (status: SSEStatus) => void
  setSessionId: (id: string | null) => void
}

export const useSSEStore = create<SSEState>()((set) => ({
  status: 'disconnected',
  sessionId: null,
  setStatus: (status) => set({ status }),
  setSessionId: (sessionId) => set({ sessionId }),
}))
```

### 4. useSSE.ts — 写入连接状态

```typescript
// 在 connect() 函数中：
import { useSSEStore } from '../stores/sse'

es.onopen = () => {
  reconnectCount.current = 0
  useSSEStore.getState().setStatus('connected')  // ← 新增
}

es.onerror = () => {
  es.close()
  useSSEStore.getState().setStatus('disconnected')  // ← 新增
  // ... existing backoff logic
}
```

### 5. SSEStatus.tsx — 读 store（删除 EventSource）

```typescript
import { Wifi, WifiOff, Loader2 } from 'lucide-react'
import { useSSEStore } from '../../stores/sse'

export function SSEStatus() {
  const status = useSSEStore((s) => s.status)

  const config = {
    connected: { icon: Wifi, color: 'text-emerald-400', label: '已连接', pulse: false },
    connecting: { icon: Loader2, color: 'text-amber-400', label: '连接中', pulse: true },
    disconnected: { icon: WifiOff, color: 'text-red-400', label: '已断开', pulse: false },
  }[status]

  const Icon = config.icon
  return (
    <div className={`flex items-center gap-1.5 text-[10px] ${config.color}`}>
      <Icon className={`h-3 w-3 ${config.pulse ? 'animate-spin' : ''}`} />
      <span>{config.label}</span>
    </div>
  )
}
```

**删除：** 整个 `useEffect` + `EventSource` + `connect()` + `retryCount` + `timeout` 逻辑。

---

## 验证

### 功能验证

1. 登录后 SSEStatus 显示"已连接"
2. 发送消息 → 收到流式回复
3. SSEStatus 保持"已连接"
4. 手动断网 → SSEStatus 显示"已断开"
5. 恢复网络 → 自动重连 → SSEStatus 恢复"已连接"

### 多 Tab 验证

1. 打开两个浏览器 tab
2. 在 tab 1 发送消息
3. Tab 2 也能收到 SSE 事件（如果 tab 2 也连接了同一个 session）

### 测试

- `sse_buffer` 单测：register 多个 event → push 通知所有
- `sse_buffer` 单测：unregister 特定 event → 其他不受影响
- 前端 `useSSE` 测试：连接/断开时 store 状态正确
- E2E：SSEStatus 显示正确状态
