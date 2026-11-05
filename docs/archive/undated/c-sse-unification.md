# C: SSE 双路径统一 — 清理死代码

> **Status:** Applied (branch `c-sse-unification`)
> **承接:** SSE 双路径（EventStore + legacy EventBus → SSEEventBuffer）实际已统一——
> `bridge.py`（legacy EventBus → SSEEventBuffer）在生产环境从未导入，是死代码。
> `bridge_v2.py`（EventStore → SSEEventBuffer）是唯一活跃路径。

## 背景

SSE 交付管道结构：

```
EventStore.emit()  ──→  bridge_v2.py  ──→  SSEEventBuffer  ──→  /api/chat/events
                    （活跃，container.py + dependencies.py 调用）

EventBus.publish() ──→  bridge.py      ──→  SSEEventBuffer  ──→  /api/chat/events
                    （死代码，生产从未导入）
```

## 清理内容

1. **删除 `api/session/bridge.py`**：生产从未导入（`grep -rn "attach_eventbus_to_sse" src/` = 0 命中）；测试仅注释引用。
2. **`api/session/events.py` 标记 deprecated**：`EventBus` 仍被 7 个测试文件使用，但生产已无调用方。docstring 加 deprecated 说明。
3. **`api/session/bridge_v2.py` 更新注释**：移除"legacy EventBus"引用，明确它是唯一路径。

## 不改动

- `sse_buffer.py`（SSEEventBuffer 本身）—— 它是目标缓冲，保持不变
- `bridge_v2.py`（EventStore → SSEEventBuffer）—— 保持活跃
- `events.py` / `event_bus_v2.py`（保留供测试用，不删除）
