# LLM 重试 + 错误气泡 + SSE 重连修复方案

日期：2026-07-31
状态：已批准，实施中
相关会话：`700dc7f7-95d`

---

## 问题概述

会话 `700dc7f7-95d` 不可用由两个耦合问题造成：

1. **MiniMax 429 限流 → 空白回复**：`stream()`/`astream()` 完全没有重试机制，429 立即失败，错误被吞进 `status=empty` 的空 assistant 消息，前端无任何提示。

2. **SSE 无限重连风暴**：前端手动重连不发 `Last-Event-ID`，后端 `get_events_since("")` 全量重放含 `agent_done` 导致 generator 立即退出；`onopen` 重置计数器使 backoff 恒为 1000ms，形成每秒一次的重连风暴。

---

## 根因分析

### 429 路径（P-A 至 P-F）

| 编号 | 问题 | 位置 |
|------|------|------|
| P-A | `stream()`/`astream()` 无重试 | `openai_client.py` |
| P-B | 不读 `Retry-After` header | `openai_client.py` |
| P-C | 重试计数 bug（attempt 3/2） | `openai_client.py` |
| P-D | 429 误映射为 `LLMServerError` | `openai_client.py` |
| P-E | backoff 太短（1/2/4s 无 jitter | `openai_client.py` |
| P-F | 错误吞成 `status=empty` 空白回复 | `service.py _run_attempt` |

### SSE 重连路径（P4/P5/P6/P8）

| 编号 | 问题 | 位置 |
|------|------|------|
| P4 | 前端手动重连不发 Last-Event-ID | `useSSE.ts` |
| P5 | 后端 `get_events_since("")` 全量重放 | `sse_buffer.py` |
| P6 | SSEEventBuffer 无消费游标 | `sse_buffer.py` |
| P8 | `onopen` 重置 `reconnectCount` | `useSSE.ts` |
| 关键 | `chat.py` 用 `Query(alias="Last-Event-ID")` 读 query param，但浏览器原生重连发送的是 HTTP Header | `chat.py:871` |

---

## 实施方案

### Phase 1：LLM 重试健壮性

**目标**：`stream()`/`astream()` 增加重试；所有路径正确错误映射 + `Retry-After` + jitter + 总尝试次数

**改动文件**：
- `src/strategy_research/core/llm/openai_client.py`
- `src/strategy_research/core/llm/config.py`

**改动要点**：

1. **`__init__` 加可选 `transport`**：`transport: httpx.BaseTransport | None = None`，透传给 `httpx.Client` / `httpx.AsyncClient`，用于测试注入 MockTransport。

2. **提取共享 helper：
   - `_compute_retry_delay(status, headers, attempt, config)` → 读 `Retry-After`（秒数 + HTTP-date via `email.utils.parsedate_to_datetime`），base backoff `2^attempt`，jitter ±30%，上限 60s，`delay = max(backoff, retry_after)`
   - `_raise_final_error(status, provider, model)` → 按状态码抛 `LLMRateLimitError(429)` / `LLMServerError(5xx)` / `LLMTimeoutError`

3. **`chat()` / `achat()` 现有重试循环修正**：
   - `for attempt in range(config.max_retries)`（总尝试次数）
   - 日志 `attempt {n+1}/{config.max_retries}`
   - 耗尽调用 `_raise_final_error`
   - 读 `Retry-After`
   - 加 jitter + 60s cap

4. **`stream()` / `astream()` 新增重试循环**：
   - 仅**首 chunk 产出前**重试（HTTP 状态错 / 连接建立失败）
   - 已产出 chunk 后网络中断 → 直接抛 `LLMError` 不重试（避免前端收到半截重复文本）
   - 复用 `_compute_retry_delay` + `_raise_final_error`

5. **`config.py`**：`max_retries` 注释改为"总尝试次数（默认 3）"

---

### Phase 2：`message_type='error'` 错误气泡

**目标**：LLM 失败不再是空白回复，展示带折叠详情的错误气泡，且消息正常进入历史

#### 后端

**`src/strategy_research/api/session/service.py`**
- `_run_with_agent` return dict 加 `"error": loop_result.error`
- `_run_attempt`：`loop_result.finished_reason == "error"` 时：
  - 友好文案：`⚠️ 模型请求失败（429 限流），请稍后再试`
  - 完整错误存 `metadata.details`
  - `append_message(role="assistant", message_type="error", content=友好文案, metadata={details})`
  - `attempt.mark_failed(error=error_msg)`
  - 发 `assistant_message` SSE 事件

**`src/strategy_research/api/session/models.py`**
- `Message.message_type` doc 加 `'error'`

**`src/strategy_research/api/routers/web_session.py`**
- `persist_message` doc 加 `'error'`
- `_row_to_message`：`message_type == "error"` 且无 parts 时，从 content 建 text part

**历史兼容**：`_convert_messages_to_history` 对 role=assistant、非 compaction/tool 的消息天然按 assistant 文本进历史 — 无需改动

#### 前端

**`webui/frontend/src/stores/chat.ts`**
- `MessageType` 加 `'error'`
- `MessageMetadata` 加 `details?: string`

**`webui/frontend/src/components/chat/MessageList.tsx`**
- 新增 `message_type === 'error'` 分支：警告样式气泡 + 友好文案 + `<details>` 折叠详情

---

### Phase 3：SSE 原生重连修复

**目标**：用浏览器 EventSource 原生重连（自动带 `Last-Event-ID` header），从根本上解决重连风暴

#### 前端

**`webui/frontend/src/hooks/useSSE.ts`**
- `onerror` 移除 `es.close()` + 手动 `setTimeout(connect)`，保留 status 更新
- 移除 `reconnectCount` / `reconnectTimer` refs
- `onopen` / `onerror` 加 `console.log(es.readyState)` 诊断日志

#### 后端

**`src/strategy_research/api/routers/chat.py`**
- `last_event_id` 增加 `Header(None, alias="Last-Event-ID")` 读取（与现有 Query 合并，Header 优先）
- generator 开头 yield `retry: 3000\n\n`（控制原生重连间隔 3s）

**`src/strategy_research/api/sse_buffer.py`**
- `get_events_since("")` 首连重放**上限 last 200 条**（防御性）

---

### Phase 4：测试 & 验证

#### 后端测试
- 新建 `tests/test_llm_retry.py`：`transport=MockTransport` 注入脚本化响应
  - 429 重试后成功 / 耗尽抛 `LLMRateLimitError`
  - 5xx 耗尽抛 `LLMServerError`
  - `Retry-After`（秒数 + HTTP-date）生效
  - 总尝试次数 == `max_retries`
  - `stream` + `astream` 有重试
  - `stream` mid-stream 断连不重试
- error 消息集成测试：persist 类型正确 / attempt `mark_failed` / 进历史 / `_row_to_message` 产出 text part
- `chat.py` `/events` 端点：`Last-Event-ID` Header 被读取并精确补发
- `pytest` 全量通过

#### 前端测试
- 改写 `useSSE.test.ts`："reconnects on error" → 断言 onerror 后不新建 EventSource、status → disconnected
- `npm test` + `npm run build`

#### 手动验证
- 发消息 → 429 时显示错误气泡（友好文案 + 折叠详情）
- 无重连风暴（F12 Network 看到稳定 SSE 连接）
- 日志正常出现 `[SSE] heartbeat`

---

## 用户决策记录

- `max_retries` = 总尝试次数（默认 3）
- LLM-caused empty reply → attempt `mark_failed`
- 错误消息进入历史（作为正常 assistant 文本）
- 错误气泡 = 友好文案 + 折叠详情
- Phase 3（SSE 重连修复）包含在本次实施中

---

## 执行顺序

Phase 1 → Phase 2（后端→前端）→ Phase 3（后端→前端）→ Phase 4

每阶段通过对应测试后再进入下一阶段。

---

## 后续发现（2026-07-31 追加）

部署后用户实际测试发现 session `700dc7f7-95de-45e0-b568-d713fe05065f` 仍然返回 400 错误，但**不是 429 而是 400**：
```
bad_request_error: invalid params, chat content is empty (2013)
```

### 真实根因（与 429 不同）

虽然上述修复让 429 重试工作正常（截图显示错误气泡 + 折叠详情），但用户累积了 100 条消息后，触发了**新的 400 bug**：

1. 会话累积 100 条消息（多次 L4 压缩）
2. 用户发"你好" → history 73 条（含 5 次旧 compaction）
3. Agent loop: `messages = [system] + history + [user("你好")]`
4. L4 触发：`_split_into_turns` 按 assistant 角色切分
5. 由于最新消息"你好"无 assistant 回复 → `len(turns) <= 2`
6. `tail_turns_list = []` → `recent = []`（空）
7. L4 把 74 条全部进 head，生成 3200 字符 summary
8. 返回 `new_messages = [system]`（**只剩 system 消息**）
9. 后续 LLM 调用发 `[system]` → MiniMax 400 `chat content is empty (2013)`

### 后续修复

详见 `docs/design/compaction-history-filter.md`，采用 opencode 方案：
- **只保留最近 1 个 compaction**进 LLM history（5 个 → 1 个）
- **L4 防御**：检查 new_messages 必须含 user role
- **MiniMax 2013 友好提示**："会话内容已压缩为空，请发送新消息或新建会话"
- **MiniMax 适配器**：2013 → `LLMConfigError`（避免 stream→achat 回退）
- **Kill switch + 监控**：admin API + 内存计数器

**结论**：429 重试 + 错误气泡正常工作（Phase 1-4 修复成功），但 400 错误暴露出历史压缩的**累积问题**——通过 compaction filter 修复。

