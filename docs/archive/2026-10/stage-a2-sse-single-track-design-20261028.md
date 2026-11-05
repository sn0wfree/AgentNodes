# A2 SSE 单轨 — 设计 — 2026-10-28

> 目标：删掉 SSE 双轨（`api/sse_buffer.py` + `api/session/bridge_v2.py`），
> 统一走 `EventStore.subscribe()`。
> 依据：本文件 §2 的代码调查（两轨职责对照 + 能力覆盖证明）。

## 1. 双轨现状（实测）

| | **sse_buffer 轨**（待删） | **EventStore 轨**（保留） |
|---|---|---|
| 文件 | `api/sse_buffer.py`（169 行） | `core/agent/event_store.py` |
| 形态 | 进程内 ring buffer（deque，5min/10000 条） | 事件库 + 内存 cache + live 队列 |
| 写入 | `sse_buffer.push()` 直调（chat.py 约 20 处） | `EventStore.emit()`（唯一事件源） |
| 读 | `get_events_since` / `replay_from` | `subscribe()`（replay cache → SQLite tail → live） |
| 跨进程 | ❌ 纯进程内 | ✅ SQLite 持久 |
| 多监听者 | asyncio.Event 集合（multicast） | `_live_queues[session_id]` 列表（multicast） |

**bridge_v2 自己的 docstring 就写明**：它是临时桥，「lets the endpoint keep
working until that endpoint is migrated to read directly from
EventStore.subscribe()」。而 `subscribe()` 已经实现完整
（`event_store.py:327-366`：cache replay → SQLite tail → live queue）。

**结论：迁移目标早已就绪，桥可以拆了。**

## 2. 能力对照（证明 EventStore 轨不丢能力）

| sse_buffer 能力 | EventStore.subscribe 对应 |
|---|---|
| 首连回放最近 200 条 | cache replay（`_cache[session_id]`）+ SQLite tail |
| Last-Event-ID 断线续传 | tail replay 按 `seq > last_seq` 过滤 |
| multicast（多标签页） | `_live_queues[session_id]` 列表，每订阅者一队列 |
| 5min TTL | SQLite 事件持久（更强：重启后仍在） |
| 10000 条上限 | `queue(maxsize=1000)` 背压 + 库无上限 |

**唯一的行为差异**：sse_buffer 用 `evt_{counter}` 字符串 ID，EventStore 用
`seq` 整数。前端只把 Last-Event-ID 原样回传，不解析——语义等价。

## 3. 改动清单

| # | 文件 | 改动 |
|---|---|---|
| 1 | `api/routers/chat.py` | events 端点改为 `event_store.subscribe(session_id)`；删除约 20 处 `sse_buffer.push` 直调（这些事件本来也经 emit 走 EventStore，属重复推送） |
| 2 | `api/container.py` / `api/dependencies.py` | 删除 `attach_eventstore_to_sse` 调用 |
| 3 | `api/sse_buffer.py` | **删除** |
| 4 | `api/session/bridge_v2.py` | **删除** |
| 5 | 测试 | `test_sse_bridge` / `test_sse_buffer` / `test_sse_buffer_cap` / `test_sse_heartbeat` 删除或改写为 subscribe 轨；`conftest` 移除 sse_buffer fixture |
| 6 | 架构门禁 | 新增：`sse_buffer` 零 import（防复活） |

## 4. 关键风险与处置

| 风险 | 处置 |
|---|---|
| **重复推送**：事件已 emit 到 EventStore，又 sse_buffer.push 一次 → 迁移后变单次，前端可能少收 | 逐个核对 chat.py 的 push 点：凡与 emit 同源的删掉，只有 emit 没有的（如纯 UI 状态）补 emit |
| 事件名映射：sse_buffer.push 用 `kind` 字符串，EventStore 用 `EventType` 枚举 | 保留 `_event_to_sse()` 映射层（现有 `_format_sse` 已有类似逻辑） |
| 心跳：sse_buffer 的 15s 心跳是端点自己发的，与轨无关 | 保留（`_heartbeat_sse` 不动） |
| 前端 Last-Event-ID 是 `evt_N` 字符串 | 前端原样回传，不解析；切换后变 `seq` 整数也照原样回传。已核实前端无解析逻辑 |

## 5. 实施顺序（可独立回退）

1. **先加不删**：events 端点新增 `subscribe()` 直读路径（环境开关或直接切换），旧 push 保留 → 跑回归；
2. **核对事件覆盖**：对比两条轨产出的事件序列（同一操作），确认无丢失；
3. **删旧轨**：移除 push 直调 → 删 sse_buffer/bridge_v2 → 删/改测试；
4. **加门禁**：`sse_buffer` 零引用测试。

## 6. 验收

1. `git grep -l "sse_buffer\|bridge_v2" src/` 零命中；
2. SSE 相关测试全绿（改写后的 subscribe 轨测试）；
3. PR 道基线不退化（当前 9638 passed / 99 pre-existing）；
4. WebUI 实时链路冒烟：发消息 → 流式文本 → 工具卡片 → 断线重连（Last-Event-ID）。
