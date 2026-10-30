# B4 Step 1 实施：MemoryManager 事件化 — 2026-10-30

> 上游：`docs/stage-c1-b4-dataplane-design-20261028.md` §2、§4.3（Step 1-5）
> 前置：`c161b0aa`（D7/schema 止血）、`249545ee`（七项决策，含 D1 clear 改标记）

---

## 0. 范围

Step 1 = **`append` 改发事件**，让 messages 表只由 projector 写。
Steps 2-5（历史回填、消费方迁移、删零调用体、删 `core/session/db.py`）
是后续独立提交，本刀只做 Step 1 并验证。

| 改动 | 内容 |
|---|---|
| `UnifiedMemoryManager._primary_append` | INSERT 直写 → `emit(message_received/assistant_message)` |
| `SQLiteStore.insert_message` | 保留但标记为「事件化后无调用方」（Step 4 删） |
| `UnifiedMemoryManager.compact` | 删除（零生产调用，设计 §2.2 已裁定） |
| `EventStore` | 新增 `close()`（设计 §2.3 明确需要） |

---

## 1. 现状与约束

### 1.1 调用方（实测）

| 调用方 | 位置 | 性质 |
|---|---|---|
| TUI 主路径 | `cli/tui/session.py:298-299` | **活**（唯一真实写入方） |
| TUI 另一路径 | `cli/tui/session.py:366-367` | 活 |
| chat.py 旧路径 | `api/routers/chat.py:445-446` | **死**（B5 调查确认 `_run_agent_loop_background` 全仓零调用） |
| `compact()` | 无 | 零生产调用 |

### 1.2 事件桥已就绪

`_should_flush` 已含 `message_received` / `assistant_message` / `compact.ended`
（`event_store.py:582-591`）。projector handler 已存在（`projection_handlers.py:90,116`）。
**无需改事件桥或新增事件类型。**

### 1.3 关键约束：message_id 由谁签发

`_on_message_received` 读 `data["message_id"]`（`projection_handlers.py:95`），
`_on_assistant_message` 同（`:130`）。所以 **`append` 必须自己生成 message_id
并放进事件 payload**，否则 projector 拒收（只 warning）。

设计 §2.2 已写明 payload 含 `message_id`。本刀照做：
`msg_id = str(uuid.uuid4())`，emit 后返回它（保持 `append` 的返回值契约）。

### 1.4 seq 归属

现 `_primary_append` 用 `seq = len(existing)`（`memory_manager.py:727`）。
projector 用 `seq = len(state.messages) + 1`（`projection_handlers.py:105`）。
事件化后 **seq 由 projector 唯一决定**——这正是 D2 提到的「seq 口径不一」
隐患的根治：mm 不再自己算 seq。

---

## 2. 实施

### 2.1 `_primary_append` 改为事件驱动

```python
async def _primary_append(self, session_id, role, content, metadata, message_type) -> str:
    msg_id = str(uuid.uuid4())
    if role == "user":
        event_type = "message_received"
        payload = {"message_id": msg_id, "user_message_id": msg_id,
                   "role": "user", "content": content}
    else:
        event_type = "assistant_message"
        payload = {"message_id": msg_id, "content": content,
                   "message_type": message_type}
    if metadata:
        payload["metadata"] = metadata           # D7 已让 projector 消费它
    self._emit_event(session_id, event_type, payload)
    self._cache.invalidate(session_id)           # 下次 get 从库重读
    return msg_id
```

`_emit_event` 复用 D1 已建的 `_emit_clear_marker` 同款机制（懒建 EventStore）。

### 2.2 缓存策略

现 `append` 在 WRITE_THROUGH 下重读全部消息并写缓存。事件化后 projector
是异步的（`emit` 内部同步 flush，但状态在 projector 侧），所以**改为
invalidate 而非 set**——下次 `get` 从库读，保证跨进程一致。

### 2.3 `compact()` 删除

零生产调用（实测）。设计 §2.2 裁定删除。连带删 5 条测试（设计 §2.3 已预告，
按契约 §2.2 同提交重冻清单）。

### 2.4 `EventStore.close()`

设计 §2.3 明确需要。6 行：关 backend 连接。

---

## 3. 风险与对策

| 风险 | 对策 |
|---|---|
| projector 未启用（`flush_to_messages=False`）→ 事件发了但 messages 不落 | 懒建时显式传 `flush_to_messages=True`；测试断言实写 |
| emit 失败 → 消息丢失（现直写失败有 emergency buffer） | 保留 emergency buffer 兜底；emit 异常走同一路径 |
| TUI 依赖 `append` 返回值做 emergency 前缀检测 | 保持返回值契约（正常返回 uuid，降级返回 `emergency_*`） |
| seq 冲突（设计 §1.2 记录的隐患） | 事件化后 mm 不再算 seq，由 projector 唯一决定 |
| 已有直写数据缺对应事件 | Step 2 回填处理；本刀不动存量 |

---

## 4. 验证

| # | 断言 |
|---|---|
| T1 | `append` 后 `get` 读得到（经 projector 落库） |
| T2 | 消息的 `message_type` / `seq` 由 projector 决定（非 mm） |
| T3 | metadata 透传（D7 链路端到端） |
| T4 | emit 失败 → emergency buffer 兜底 + 返回 `emergency_*` |
| T5 | `compact()` 已删除 |
| T6 | TUI 路径契约不变（返回值前缀语义） |
| T7 | 现有 memory/history 测试零改动通过 |

回归门：`test_memory* + test_session* + test_chat_slash* + test_event_store* +
test_projection*` → `c1_gate.py`。
