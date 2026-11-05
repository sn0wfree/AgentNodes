# P5-1 设计 v2 — Gateway chat 补齐持久化与历史 — 2026-10-27

> 前一版（`p5-1-chat-gateway-parity-design-20261027.md`）已作废：它假设要复用
> PlatformStore 的多库形态，而分库方案已回退。本版基于**单库现状**重写。
>
> 前置结论：单库（`.quantnodes_strategy_research_session.db`）保持不变；搜索已改 LIKE。

---

## 1. 现状差距（实测）

`api/routers/chat.py::_send_via_gateway` 当前行为：

| 能力 | 旧路径（SessionService） | 新路径（Gateway） |
|---|---|---|
| 读会话历史注入上下文 | ✅ `store.get_messages(limit=100)` + `_convert_messages_to_history` | ❌ **只发当前一条**（`SessionController` 硬编码 `[{"role":"user",...}]`） |
| 写用户消息 | ✅ `persist_message` | ❌ 不写 |
| 写助手消息 | ✅ `persist_message` | ❌ 不写 |
| 事件落库 | ✅ `event_bus.emit` → `event_log` → projector | ⚠ 仅 Gateway 内部缓冲（未持久化，且 `data_dir` 未配置时不写） |
| `message_id` | ✅ | ❌ `None` |
| SSE 推送 | ✅ | ❌ 无 |

**后果**：走新路径的对话**刷新后消失**，且**没有多轮上下文**（每轮都是全新对话）。

## 2. 设计原则：最小复用，不新建存储

单库里已有全部所需能力，直接复用：

| 需求 | 复用件 | 位置 |
|---|---|---|
| 读历史 | `SessionStore.get_messages` + `_convert_messages_to_history` | `api/session/store.py`、`service.py` |
| 写消息 | `persist_message()` | `api/routers/web_session.py` |
| 事件 | `Service.event_bus.emit`（= EventStore） | `api/session/service.py` |
| 会话归属 | `_fetch_session_owned`（已有） | `api/routers/web_session.py` |

**不新建表、不加字段、不动 schema。**

## 3. 实现（约 60 行，改 2 个文件）

### 3.1 `SessionController` 支持历史注入

`WorkRequest.payload` 增加可选 `history`（协议白名单同步放行）：

```python
state = {
    "messages": [
        *(request.payload.get("history") or []),
        {"role": "user", "content": _payload_text(request)},
    ],
    ...
}
```

**校验**：`history` 必须是 `list[dict]`，每项含 `role`/`content`；非法则忽略（不阻断）。

### 3.2 `_send_via_gateway` 补齐四步

```python
# ① 读历史（失败降级为空，不阻断）
service = _get_session_service()
messages = service.store.get_messages(session_id, limit=100)
history = service._convert_messages_to_history(
    messages,
    keep_all_compactions=bool(cfg and cfg.compact_config and
                              cfg.compact_config.keep_all_compactions_in_history),
)

# ② 提交时带历史
receipt = gw.entry.submit(api_chat_request(session_id, content, history=history))

# ③ 执行（现有 run_once）

# ④ 成功后写库（只此一条路径，不发事件——见 §4）
try:
    user_mid = persist_message(session_id=session_id, role="user", content=content)
    asst_mid = persist_message(session_id=session_id, role="assistant", content=run.answer)
    warning = None
except Exception as exc:
    logger.warning("send_via_gateway persist failed: %s", exc)
    user_mid = asst_mid = None
    warning = "answer_not_persisted"

# ⑤ 返回
return {"message_id": asst_mid, "attempt_id": run.run_id,
        "reply": run.answer, "status": "completed",
        **({"warning": warning} if warning else {})}
```

**失败语义**（保持不变）：引擎失败 → 502，**不写任何消息**。

**持久化失败**：回答仍返回 200，响应加 `warning` 字段说明记录未落库——不让存储抖动吞掉用户看到的内容。

### 3.3 `api_chat_request` 增加 history 参数

```python
def api_chat_request(session_id: str, content: str,
                     *, history: list[dict] | None = None) -> WorkRequest:
    payload = {"text": content}
    if history:
        payload["history"] = history
    return WorkRequest(..., payload=payload)
```

**协议白名单**：`contracts.py` 的 payload 允许键加 `history`（原有的 `text` 保留）。

## 4. 持久化路径的选择（实测后的定案）

**问题**：消息有两条落库路径，不能都调。

| 路径 | 机制 |
|---|---|
| `persist_message()` | 直接 `INSERT INTO messages`（无 ON CONFLICT） |
| `event_bus.emit` + projector | 事件 → `_on_message_received`/`_on_assistant_message` → `Projector.flush` → `_upsert_message`（**`ON CONFLICT(id) DO UPDATE`**） |

**实测结论**：
- 同一 `message_id` 两路都写 → `persist_message` 抛 `UNIQUE constraint failed`（被 except 吞掉）、projector 的 UPSERT 胜出，内容为事件里那份；
- **不同 `message_id` → 两行**（真双写，重复消息）。

**定案：只走 `persist_message`，不发事件。**

理由：
1. 单库场景下 `persist_message` 是现成的、被 chat/compaction/slash_commands 共用的唯一写入口；
2. 发事件会额外触发 projector 全量投影（`project_incremental` + `flush`），成本远高于一次 INSERT；
3. 避免两套 id 生成逻辑需要对齐（projector 从 `event.data["message_id"]` 取，`persist_message` 自己生成 uuid）。

**代价（如实声明）**：`event_log` 里不会有这两条消息的事件，即**该轮对话不可通过事件回放重建**。当前单库下这不是问题（`messages` 表是真源，SSE 走 `sse_buffer` 另推）。若将来要求全事件溯源，再切到「只发事件、projector 投影」路径——那时 `persist_message` 退役即可。

## 5. 一个必须说清的取舍：同步 vs 异步

旧路径是「入队即返回 attempt_id，后台跑，结果走 SSE」；新路径是**同步阻塞到出答案**。

| | 旧路径 | 新路径 |
|---|---|---|
| `/send` 返回时机 | 立即（几百 ms） | 等 LLM 完成（数秒） |
| 前端如何拿结果 | SSE `agent_done` | HTTP 响应体 |
| 超时 | 无（后台跑） | HTTP 超时（默认 60s+） |

**这不是本次要改的**——`/send` 本就是同步端点（另一条 `/send_async` 才是异步）。文档记录：新路径行为 = 「同步等待」，与端点契约一致，不是回归。

## 5. 验收（执行等价，按 replan v2 方法论）

| 维度 | 验证 |
|---|---|
| 历史条数 | 一轮对话后 `messages` 表 +2（user/assistant） |
| 多轮上下文 | 第二轮请求的 prompt 含第一轮内容（fake client 捕获） |
| `message_id` | 非空且能在 `messages` 表找到 |
| 事件 | `event_log` **无新增**（见 §4：只走 persist_message） |
| 消息不重复 | 一轮后 `messages` 表恰好 +2 行（不是 +4） |
| 失败路径 | 引擎失败 → 502 且 `messages` 表无新行 |
| 跨路径连续性 | 先旧路径写 2 轮，再新路径写 1 轮 → 历史 6 条且新轮看到前 4 条 |
| 契约 | 响应字段与旧路径一致 |

## 6. 删旧分支的条件（不在本次）

补齐后，**理论上可以**删除 `send_sync` 的旧分支和 `new_entry_enabled` 门。但：

| 待确认 | 说明 |
|---|---|
| 流式行为 | 旧路径支持 SSE 流式（`loop_stream` 逐 token 推）；新路径同步返回，**流式能力缺失** |
| 工具调用 | 旧路径 AgentLoop 有完整工具链；新路径 react 节点已具备（1b），但 registry 装配未接 |

**结论：本次只补齐，不删旧分支。** 删除需等流式与工具链在新路径就位（属后续阶段）。

## 7. 明确不做

- 流式输出（后续）
- 工具链装配到 Gateway chat（后续）
- PlatformStore 接线（单库不回退，也不需要）
- 删除旧分支与 `new_entry_enabled`（条件未满足）

## 8. 风险

| 风险 | 缓解 |
|---|---|
| 历史注入改变 prompt → 回答行为变化 | 这正是目标（旧路径也注入）；用 fake client 断言 prompt 结构 |
| `get_messages` 读 100 条可能慢 | 与旧路径同源同参数，不是新风险 |
| 写库失败污染响应 | 失败只加 `warning`，回答照常返回 |
| 消息重复（persist_message 与 projector 双写） | **已实测并规避**：只走 `persist_message`，不发事件（§4） |
