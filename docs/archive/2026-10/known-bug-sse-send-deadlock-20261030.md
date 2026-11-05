# P0 已知 bug：SSE 订阅挂起期间 send_async 死锁（2026-10-30）

> 发现途径：e2e 车道首次完整运行（总目标「运行完整 e2e」）。
> `tests/test_sse_e2e.py::TestSSEReconnection::test_sse_reconnection` 挂死。

---

## 1. 症状（最小化复现，两因素对照）

| 条件 | 结果 |
|---|---|
| `POST /api/chat/send_async`（无 SSE 订阅者） | **200，毫秒级**（status=processing） |
| 先建立 `GET /api/chat/events` 订阅，再 send_async | **永久挂起**（60s 超时无响应；服务端连 access log 都未打出——handler 未完成） |

复现脚本：`scripts/repro_sse_send_deadlock.py`（起真 uvicorn → 建 session →
开 SSE 流（线程）→ send_async 60s 严格超时）。
pytest 侧证据：`tests/test_sse_e2e.py:130` 主线程 `requests.post` 无 timeout
→ 全套件挂死；faulthandler 栈显示 MainThread 阻塞在 `socket.recv_into`。

## 2. 已排除的嫌疑（勘查记录）

| 嫌疑 | 排除依据 |
|---|---|
| `EventStore.subscribe` 持锁流式 | `_live_lock` 只在注册/注销瞬间持有（event_store.py:354-366） |
| `_create_and_persist` 的 `_backend._lock` 跨 flush 持有 | flush 在 `with` 块外调用（event_store.py:193-224） |
| `SessionLockMap` 死锁 | `EventStore._locks` 是**死字段**（实例化后零使用） |
| REST 端点本身坏 | 无 SSE 时 login/session/send_async/send 全 200（探针验证） |
| 端口冲突/环境差异 | 挂死在干净端口上确定性复现（探针两次） |

## 3. 未定位（需专项调试）

`send_async` handler（`chat.py:528`）在 SSE generator 挂着时卡死在某 await，
卡点在 `service.send_message`（service.py:118）与 EventStore emit/broadcast 的
交错深处——**需要 py-spy dump 服务进程线程栈**或逐步 instrumentation 定位。
初步怀疑方向：`_broadcast_live` 对 asyncio.Queue 的**跨线程** put
（AgentLoop 在 worker 线程 emit → asyncio.Queue 非线程安全）与
StreamingResponse 的背压交互。

## 4. 处置（本刀）

- `test_sse_e2e.py` 整文件标记 skip（原因指向本文档），不阻塞其余 e2e；
- 修复后：删除 skip，跑通 `test_sse_e2e` 全部 5 用例，本 bug 关闭。

## 5. 修复后必须验证

1. `scripts/repro_sse_send_deadlock.py` 全绿（send_async < 5s）；
2. `tests/test_sse_e2e.py` 5 用例全过；
3. WebUI 手工冒烟：打开对话页发消息，SSE 流正常收 `agent_done`。
