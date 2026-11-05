# SSE 心跳修复 - 防止"连接中 / 未连接"循环

## 问题

前端的 SSE 状态指示器（`SSEStatus` 组件）在新 session 上长时间在"连接中"和"未连接"之间循环。后端日志显示同一个 session 在短时间内收到了 50+ 次 `GET /api/chat/events` 请求。

## 根因

### 现象

`event_generator` 创建后：
1. 缓存里的事件先重放（如果有）
2. 进入 `while True` 循环等待新事件
3. 15 秒后无活动就发 heartbeat

**但**：FastAPI `StreamingResponse` 在 generator 第一次 `yield` 真正数据之前**不会发送 HTTP 响应头**。

**对新 session 来说**：
- SSE 缓冲里**没有缓存事件**（用户刚创建 session）
- `get_events_since` 返回空
- 直接进入 `while True`
- 浏览器要等 **15 秒**才能收到第一个字节

**结果**：浏览器 EventSource 在 15 秒等待期内触发 `onerror`，前端状态变成"未连接" → 立即重连 → 又等 15 秒 → 又断 → 死循环。

### 验证

对照业界参考（opencode `packages/server/src/handlers/event.ts:37`）：

```ts
const heartbeat = Stream.tick("15 seconds").pipe(Stream.map(() => ": heartbeat\n\n"))
```

opencode 也用 15 秒心跳，但它的 generator 在订阅事件总线时**立即拿到一个 `server.connected` 事件**作为第一个 yield，而我们当前的代码**没有立即 yield 任何东西**。

## 修复方案

### 修复 1：generator 立即 yield 注释行

`event_generator` 开头立即 yield `": connected\n\n"`：

```python
async def event_generator():
    # Send SSE comment immediately so the browser's EventSource
    # fires onopen without waiting for the first real event.
    yield ": connected\n\n"
    ...
```

**为什么用注释行**：
- SSE 注释行（`:` 开头）会被浏览器 **完全忽略**
- 不触发 `onerror`、`onmessage` 或任何事件
- 但能让 `StreamingResponse` 立即发出响应头，触发 `onopen`

### 修复 2：心跳改用注释行

`_heartbeat_sse` 之前输出 `event: heartbeat\ndata: {...}`，改为纯注释行 `": heartbeat\n\n"`：

```python
def _heartbeat_sse(count: int) -> str:
    return ": heartbeat\n\n"
```

**为什么**：
- 对齐 opencode 实践
- 注释行浏览器**主动忽略**，不触发任何事件
- 没法因为 named event 类型污染前端状态
- 保持 TCP 连接活跃，重新计时浏览器 3 分钟 idle timeout

### 修复 3：前端监听 heartbeat 事件

作为**双重保险**，前端 `useSSE` 监听 `heartbeat` 事件：

```ts
es.addEventListener('heartbeat', () => {
  useSSEStore.getState().setStatus('connected')
})
```

**为什么**：
- 万一后端未来切回 named event 形式，前端不需要同步改动
- 心跳到达 = 链路活着 = 强制标记"已连接"
- 防止浏览器因为短暂抖动误报"未连接"

## 测试

| 文件 | 测试 |
|------|------|
| `tests/test_sse_heartbeat.py` | `test_heartbeat_returns_comment_line`<br>`test_event_generator_yields_connected_first` |
| `webui/frontend/src/test/useSSE.heartbeat.test.ts` | `test_heartbeat_event_marks_connected` |

## 风险

| 改动 | 风险 | 回滚 |
|------|------|------|
| 1. 立即 yield 注释行 | 极低 | 删 1 行 |
| 2. 心跳改注释行 | 极低 | 改 1 行 |
| 3. 前端监听 heartbeat | 极低 | 删 3 行 |

所有改动都是**加性**的，最坏情况浏览器忽略新逻辑，恢复原行为。

## 经验教训

1. **SSE 第一个 yield 必须立即发生**，否则浏览器会超时断开
2. **注释行是 SSE keep-alive 的最佳实践**（比 `event: ping` 类事件更干净）
3. **前端 SSE 状态显示**应该有 stragger detection：心跳到达就强制标记 connected
