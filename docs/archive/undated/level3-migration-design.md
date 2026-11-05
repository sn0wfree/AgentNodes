# Migration Plan: opencode-aligned Message Model (Level 0 → 3)

> 日期：2026-07-31（初稿） / 2026-08-01（B-series 完成更新）
> 状态：Phase 1-3 (B1-B5) **已实施并生产验证**。Phase 4-6（revert/multi-agent）未做。
> 目标：从当前"单 messages 表 + role=tool 独立行"的数据模型，逐步迁移到 opencode 的"messages + parts + event sourcing"模型

## 0. 当前执行状态

### 0.1 B-series 完成情况

| 阶段 | 状态 | 提交数 | 测试 | 关键事件 |
|------|------|--------|------|----------|
| Phase 1 (Level 0 + 1) | ✅ 完成 | 4 | 47 | reorder + seq 列 |
| Phase 2 (Level 2) | ✅ 完成 | 6 | 52 | message_parts + 删 role=tool |
| Phase 3 B1 | ✅ 完成 | 11 | 257 | event_log + Projector + EventBusV2 |
| Phase 3 B2 | ✅ 完成 | 5 | 27 | Dual-write + Service 接线 |
| Phase 3 B3 | ✅ 完成 | 5 | 19 | 读路径切换 + Backfill |
| Phase 3 B4 | ✅ 完成 | 5 | 17 | 写路径切换 + 性能优化 |
| Phase 3 B5 | ✅ 完成 | 4 | 10 | Compact 事件化 + 终极 invariant |
| **Phase 3 B6 (cleanup)** | ✅ 完成 | 3 | 10 | L4 compaction + tech debt 文档 |
| **总计** | **✅** | **43** | **439** | **event_log 是唯一真源** |

### 0.2 核心不变量（生产验证通过）

```
1. event_log 是 messages + message_parts 的唯一真源
2. SessionStore.get_messages() 默认从 event_log 投影读取
3. 所有写入通过 EventBusV2.emit()（webui 主流程）
4. projector.flush() 在消息边界事件触发，自动维护 materialized view
5. 进程崩溃后可从 event_log 完全恢复 messages 表
```

### 0.3 性能

| 场景 | 延迟 |
|------|------|
| 700dc7f7 (39 msgs / 177 events) | 1.15ms / read |
| 3a63cdfe (5 msgs / 330 events) | 1.57ms / read |
| 写入事件流（1 msg + 20 text_delta + 1 final） | 27ms / 22 events |

---

---

## 1. 背景与动机

### 1.1 触发事件

Session `700dc7f7-95de-45e0-b568-d713fe05065f` 在 turn 3 触发 MiniMax HTTP 400 错误：

```
bad_request_error: invalid params, chat content is empty (2013)
```

调试后发现 `_convert_messages_to_history` 输出顺序违反 MiniMax 协议：
```
[0] user
[1] assistant              ← 上一轮的回复，无 tool_calls
[2] user                   ← 触发 tool 调用的 user
[3-7] tool x5              ← 🐛 在产生它们的 assistant 之前
[8] assistant + 5 tc       ← 🐛 在它生成的 tool 之后
[9] user
```

OpenAI 协议要求 `assistant(tool_calls) → tool → tool → ...`，反序时被 MiniMax 拒。

### 1.2 根本原因

当前数据模型有歧义：
- `role=tool` 是独立消息行（`messages.tool_call_id` 列关联到 assistant）
- `assistant` 终稿 `created_at` 晚于它生成的 `role=tool` 消息
- `_convert_messages_to_history` 按 `created_at` 升序遍历 → 顺序天然错位

### 1.3 opencode 的解决方案（参考）

```ts
// opencode 的 assistant() 函数 (to-llm-message.ts:108-112)
return [
  Message.make({ id, role: "assistant", content: meaningful }),  // 1) 整条 assistant
  ...results,                                                     // 2) 紧跟 tool results
]
```

物理保证：opencode 没有独立 `tool` 消息，tool 是 assistant 的子部分（PartTable），结构上不可能错位。

### 1.4 opencode 自己用了多久

```
2026-01-27  第一个 migration
2026-03-12  session_message_cursor
2026-03-23  ★ events 表创建
2026-04-04  ★ event_sourced_session_input
...
2026-06-22  simplify_session_input（reset_v2_session_state 整体重做）
38 个 migration，3+ 个月，1 次 reset
```

---

## 2. 现状盘点

### 2.1 数据规模

```
Total messages:    469
role=tool:         155 (33%)
tool_call_id NOT NULL: 155
Sessions with tools: 3 / 68
700dc7f7 alone:    137 tool messages
```

### 2.2 受影响文件

**Backend 生产代码**（14 个文件，~6611 LOC）：
- `src/strategy_research/api/session/service.py` (1217)
- `src/strategy_research/api/session/store.py` (257)
- `src/strategy_research/api/session/models.py` (211)
- `src/strategy_research/api/session/events.py` (239)
- `src/strategy_research/api/routers/chat.py` (985)
- `src/strategy_research/api/routers/web_session.py` (763)
- `src/strategy_research/api/sse_buffer.py` (145)
- `src/strategy_research/core/agent/loop.py` (1475)
- `src/strategy_research/core/agent/compact.py` (674)
- `src/strategy_research/core/agent/to_llm_message.py` (179)
- `src/strategy_research/core/agent/compaction_message.py` (219)
- `src/strategy_research/core/agent/context.py` (247)
- `src/strategy_research/core/workflow/worker.py` (~150)
- `src/strategy_research/cli/components/chat_log.py` (~80)

**Backend 测试**（33 个文件）：
- test_agent_loop, test_compact_*, test_tool_message_storage, test_history_compaction_filter, test_to_llm_message, test_compaction_message, test_compact_tool_pairs, test_agent_loop_extensions, test_compact_serialization, test_compact_full_pipeline, test_db_migration 等

**Frontend 生产代码**（7 个文件）：
- `webui/frontend/src/hooks/useSSE.ts`
- `webui/frontend/src/stores/chat.ts`
- `webui/frontend/src/components/chat/MessageList.tsx`
- `webui/frontend/src/components/chat/AssistantMessage.tsx`
- `webui/frontend/src/components/chat/ToolCallBlock.tsx`
- `webui/frontend/src/components/chat/ToolCallGroup.tsx`
- `webui/frontend/src/components/chat/ContextUsageBar.tsx`

**Frontend 测试**（5 个文件）：useSSE.test, ToolCallBlock.test, MessageList.test, agentsStore.test, ContextUsageBar.test

### 2.3 现有 git 集成（revert 的基础）

```python
# src/strategy_research/core/git.py (85 lines)
def git_commit(workspace_path, message) -> bool: ...  # 用于 backtest 提交
def git_commit_rich(workspace_path, strategy_name, run_name, status, metrics, ...): ...
def git_reset(workspace_path) -> bool: ...  # 硬重置到 HEAD~1
def git_get_hash(workspace_path) -> str: ...
```

**评估**：只能 commit/reset/get_hash，没有 tree snapshot 能力。opencode 的 snapshot 服务需要 `git.tree.capture` / `git.tree.restore` 这类 API。需扩展。

---

## 3. 设计原则

1. **分阶段可独立回滚**：每 Level 内部每个 commit 都独立 revertable
2. **双写期保护**：Schema 变更期间新旧两套同时写，验证后切读路径
3. **真实 session 验证**：每 Level 用 700dc7f7 turn N+1 端到端验证
4. **不破坏现有数据**：所有迁移幂等，可重复执行
5. **测试驱动**：每个 commit 必须有对应 test
6. **业务优先**：Phase 1 先修 700dc7f7，再考虑完美

---

## 4. Level 0 — 重排（核心修复）

### 4.1 目标

修 700dc7f7 turn 3 立刻可用。`assistant(tool_calls)` 必须在对应 `tool` 之前出现在 LLM 输入。

### 4.2 实现

**文件**：`src/strategy_research/api/session/service.py` `_convert_messages_to_history`

```python
# 第一遍：建 tool_call_id → assistant 索引
tool_to_assistant: dict[str, int] = {}
for i, msg in enumerate(history):
    if msg["role"] == "assistant" and msg.get("tool_calls"):
        for tc in msg["tool_calls"]:
            tool_to_assistant[tc["id"]] = i

# 第二遍：按 created_at 遍历
emitted: set[int] = set()
out: list[dict] = []
for i, msg in enumerate(history):
    role = msg["role"]
    if role == "tool":
        continue  # 已在 assistant 处理时紧跟 emit
    if role == "assistant" and msg.get("tool_calls"):
        if i in emitted:
            continue
        emitted.add(i)
        out.append(msg)
        # 紧跟它所有关联的 tool 消息
        for j, m in enumerate(history):
            if m["role"] == "tool" and tool_to_assistant.get(m.get("tool_call_id")) == i:
                out.append(m)
        continue
    out.append(msg)
```

**顺带修 trim 逻辑**（service.py:1086-1098）：保留 assistant 整组。

### 4.3 测试

**文件**：`tests/test_convert_messages_to_history_order.py`（新建，~150 行）

| 用例 | 验证 |
|------|------|
| `test_assistant_comes_before_tools` | 模拟 81102cc1 turn 2 数据 |
| `test_tool_call_id_groups_intact` | trim 不破坏 assistant-tool 配对 |
| `test_multiple_assistants_each_with_own_tools` | 多个 turn |
| `test_orphan_tool_dropped` | 找不到 assistant 的 tool 被丢弃 |
| `test_real_session_700dc7f7_replay` | DB fixture，验证 turn 3 输入顺序 |

### 4.4 验证

- pytest 新增 test 全过
- 所有 backend test 不退化
- 700dc7f7 turn 4 端到端成功（无 2013）

### 4.5 Commit

```
fix(service): reorder assistant before tools in LLM history (Level 0)

Opencode 模型的 reference 实现：assistant(tool_calls) 必须在对应 tool
result 之前。MiniMax 2013 "chat content is empty" 是因为 tool 在
assistant 之前。

重排逻辑：建 tool_call_id → assistant_idx 索引，assistant 出现时
立即紧跟它的所有 tool 消息。

修复 700dc7f7-95de-45e0-b568-d713fe05065f turn 3 崩溃。
```

### 4.6 风险

- **零**：纯函数内重排，不改 DB，不改前端
- 兜底：trim 整组逻辑可能漏 edge case → 5 个 test 覆盖

---

## 5. Level 1 — seq 列（消除歧义）

### 5.1 目标

`created_at` 是浮点时间戳，clock skew 可导致顺序不稳定。引入单调递增的 `seq` 列作为 LLM 历史投影的权威顺序键。

### 5.2 实现

**Schema 改动**（`web_session.py`）：
```python
_add_column(conn, "messages", "seq", "INTEGER NOT NULL DEFAULT 0")
conn.execute(
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_messages_session_seq "
    "ON messages(session_id, seq)"
)
```

**Seq 生成器**（`src/strategy_research/core/seq_generator.py`，新建 ~40 行）：
```python
class SeqGenerator:
    """Process-local monotonic seq counter per session_id."""
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[str, int] = {}

    def next(self, session_id: str) -> int:
        with self._lock:
            current = self._counters.get(session_id, 0)
            new = current + 1
            self._counters[session_id] = new
            return new
```

**Backfill 脚本**（`scripts/backfill_seq.py`，新建 ~60 行）：
- 按 `created_at` 升序为每条 message 分配 seq
- 事务性 UPDATE

**集成点**：
- `service.py:send_message` 调 `seq_gen.next(session_id)` 后传 `seq` 给 `append_message`
- `store.py:append_message` 加 `seq` 参数
- `service.py:_convert_messages_to_history` 改 `ORDER BY seq ASC`
- `web_session.py:list_messages` 改 `ORDER BY seq ASC`，加 `before_seq` 参数
- `compact.py` L4 路径传 seq

### 5.3 测试

**文件**：`tests/test_seq_generator.py`（~50 行）
- 线程安全（多线程并发）
- 单调递增
- 多 session 隔离
- 重启后从 0 重新分配（backfill 兜底）

### 5.4 验证

- pytest 新增 test 全过
- 所有 backend test 不退化
- backfill 后旧数据 seq 正确（按 created_at 顺序）
- 700dc7f7 turn N+1 仍成功

### 5.5 Commit 链（3 个）

```
1. feat(schema): add seq column to messages table
2. feat(seq): single-process seq generator + backfill script
3. refactor(history): ORDER BY seq instead of created_at
```

### 5.6 风险

- **低**：新增列默认 0 不破坏现有
- **中**：commit 3 切换 ORDER BY 前必须先跑 backfill（否则旧数据 seq=0 顺序错乱）
- 缓解：commit 3 README 明确 backfill 步骤

---

## 6. Level 2 — PartTable 拆表（数据模型靠拢）

### 6.1 目标

将 `parts_json` 字段从 `messages` 表拆出到独立的 `message_parts` 表。**不再有 `role=tool` 独立行**，tool result 物理上只能是 assistant 的 part。

### 6.2 Schema 改造

```sql
-- 现状
CREATE TABLE messages (
  id, session_id, role, content, parts_json, tool_call_id,
  message_type, created_at, metadata_json, seq
)

-- 改后
CREATE TABLE messages (
  id, session_id, role, message_type, time_created, metadata_json, seq,
  -- 删 content, parts_json, tool_call_id
)

CREATE TABLE message_parts (
  id TEXT PRIMARY KEY,
  message_id TEXT NOT NULL,
  session_id TEXT NOT NULL,
  type TEXT NOT NULL,            -- text | tool_call | tool_result | reasoning | file_edit | table | chart | image | thinking
  data_json TEXT NOT NULL,        -- 实际 part 数据
  seq INTEGER NOT NULL,
  time_created REAL NOT NULL,
  FOREIGN KEY (message_id) REFERENCES messages(id) ON DELETE CASCADE
)
CREATE INDEX idx_message_parts_message ON message_parts(message_id, seq);
CREATE INDEX idx_message_parts_session ON message_parts(session_id, seq);
```

### 6.3 改造策略：双写期

```
Week N+1: commit 1 — 新表 message_parts 创建，不写不读
Week N+1: commit 2 — append_message 改为双写（同时写 messages + message_parts）
Week N+2: commit 3 — backfill: 从旧 parts_json 重建 message_parts 行
Week N+2: commit 4 — _row_to_message 返回 (message, parts) 元组
Week N+2: commit 5 — 读路径（list_messages、_convert_messages_to_history）从 message_parts 读
Week N+3: commit 6 — 删 role=tool 行（migration），删 parts_json/tool_call_id 列
```

**双写期目的**：让 commit 1-5 任意 revert 都不会丢数据。

### 6.4 实现细节

**`persist_message` 改动**：
```python
def persist_message(
    session_id, role, content, parts=None,
    message_id=None, created_at=None, metadata=None, seq=None,
    tool_call_id=None, message_type=None
):
    msg_id = message_id or str(uuid.uuid4())
    ts = created_at or time.time()

    # 写入 messages 表
    conn.execute(
        "INSERT INTO messages (id, session_id, role, content, parts_json, "
        "tool_call_id, created_at, metadata_json, message_type, seq) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (msg_id, session_id, role, content, parts_json, tool_call_id,
         ts, metadata_json, message_type, seq)
    )

    # 双写：part 行也写入 message_parts
    if parts:
        for i, p in enumerate(parts):
            part_id = str(uuid.uuid4())
            conn.execute(
                "INSERT INTO message_parts (id, message_id, session_id, type, "
                "data_json, seq, time_created) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (part_id, msg_id, session_id, p.get("type"),
                 json.dumps(p, ensure_ascii=False), seq * 1000 + i, ts)
            )
    return msg_id
```

**`_row_to_message` 改动**（commit 4 之后）：
```python
def _row_to_message(row):
    # 读 message_parts 替代 parts_json
    parts_rows = conn.execute(
        "SELECT * FROM message_parts WHERE message_id = ? ORDER BY seq",
        (row["id"],)
    ).fetchall()
    parts = [json.loads(r["data_json"]) for r in parts_rows]
    return {
        "id": row["id"],
        "session_id": row["session_id"],
        "role": row["role"],
        "content": row["content"],
        "parts": parts,
        "created_at": row["created_at"],
        "metadata": metadata,
        "message_type": row["message_type"],
    }
```

### 6.5 关键路径改造清单

| 路径 | 改动 |
|------|------|
| `service.py:650-667` (event_callback 写 tool_result) | 改 append part 到对应 assistant message |
| `service.py:1043-1052` (_convert_messages_to_history 读 tool) | 删：tool 已经是 part，不再独立读 |
| `compact.py:213,278,511,531` (tool 特殊处理) | 删 |
| `to_llm_message.py:96-104` (MESSAGE_TYPE_TOOL) | 删 |
| `compaction_message.py:29,41` (MESSAGE_TYPE_TOOL 常量) | 删 |
| `models.py:102` (tool_call_id 字段) | 删 |
| `context.py:53`、`workflow/worker.py:96`、`cli/components/chat_log.py:36` | 删 tool 特殊处理 |
| `useSSE.ts:tool_result` handler | 改：更新 assistant message 的 parts，不再 addMessage |
| `useSSE.ts:message_received` 路径 | 改：assistant_message_id 总是新建 message_id |
| `MessageList.tsx:96` (`if role === 'tool' return null`) | 删：不再有 tool 消息 |

### 6.6 关键迁移脚本（commit 3）

`scripts/migrate_role_tool_to_parts.py`（~200 行）：

```python
"""Migrate role=tool rows to assistant message_parts.

For each role=tool row:
1. Find the assistant message that generated this tool_call_id
2. Look in the assistant's parts_json for the matching tool_call part
3. Add result + status='done' to that part
4. Delete the role=tool row
5. If no matching assistant found, log as orphan (skip)
"""
def main():
    # 读所有 role=tool 行
    tools = SELECT * FROM messages WHERE role='tool' ORDER BY created_at

    migrated = 0
    orphans = 0
    for tool_msg in tools:
        # 找产生它的 assistant
        assistant = SELECT * FROM messages
                     WHERE id IN (
                       SELECT message_id FROM message_parts
                       WHERE data_json LIKE '%' || tool_msg.tool_call_id || '%'
                       AND type = 'tool_call'
                     )
                     AND session_id = tool_msg.session_id
                     ORDER BY created_at DESC LIMIT 1

        if not assistant:
            logger.warning(f"orphan tool: {tool_msg.id} tc={tool_msg.tool_call_id}")
            orphans += 1
            continue

        # 找到对应 tool_call part，更新 result
        UPDATE message_parts
        SET data_json = json_set(data_json, '$.result', ?,
                                 '$.status', 'done')
        WHERE message_id = assistant.id
          AND json_extract(data_json, '$.id') = tool_msg.tool_call_id
          AND type = 'tool_call'

        # 删 role=tool 行
        DELETE FROM messages WHERE id = tool_msg.id
        migrated += 1

    print(f"migrated={migrated}, orphans={orphans}")
```

**Dry-run 模式**：先 `--dry-run` 报告，review 后再 `--apply`。

### 6.7 SSE 协议破坏与前端适配

**破坏面**：
- 旧 SSE 事件：`tool_result { message_id, id, result, status }`
- 新 SSE 事件：同字段名，但**前端不再 addMessage 一个 tool 消息**，而是 updateMessage(assistant_message_id) 改 parts

**前端改动**（`useSSE.ts:tool_result handler`）：
```ts
// 旧
case 'tool_result': {
  const mid = data.message_id as string  // tool message_id
  updateMessage(mid, msg => {
    msg.parts = msg.parts.map(p =>
      p.type === 'tool_call' && p.id === data.id
        ? { ...p, result: data.result, status: data.status }
        : p
    )
  })
  break
}

// 新：message_id 改为 assistant_message_id
case 'tool_result': {
  const mid = data.message_id as string  // ← 现在是 assistant message_id
  updateMessage(mid, msg => {
    msg.parts = msg.parts.map(p =>
      p.type === 'tool_call' && p.id === data.id
        ? { ...p, result: data.result, status: data.status }
        : p
    )
  })
  break
}
```

**SSE 事件名不变**，data 字段不变，只是 message_id 含义变了。前端适配很小。

### 6.8 Commit 链（~6 个）

```
1. feat(schema): add message_parts table (no data migration yet)
2. feat(persistence): dual-write parts to message_parts
3. feat(migration): migrate role=tool → message_parts + backfill
4. refactor(read): _row_to_message returns (message, parts) tuple
5. refactor(llm): _convert_messages_to_history reads from message_parts
6. chore(cleanup): drop role=tool, parts_json, tool_call_id columns
```

### 6.9 风险

| 风险 | 概率 | 影响 | 缓解 |
|------|------|------|------|
| role=tool 找不到 assistant | 中 | 数据丢失 | dry-run + orphan 报告 + 手动 review |
| 700dc7f7 137 条 tool 迁移耗时 | 高 | 中 | 脚本用 bulk UPDATE，分批提交 |
| 双写期性能下降 | 中 | 低 | 短期（commit 1-5 期间） |
| SSE 协议破坏前端 | 中 | 高 | commit 5 后跑全量 frontend test + 端到端 |

---

## 7. Level 3 — 事件溯源（最高风险）

### 7.1 目标

引入 event sourcing：所有 AgentLoop 事件先持久化到 event_log 表，projector 监听事件并更新 messages + message_parts。L4 compaction / revert / undo 等未来功能都基于 event log。

### 7.2 Schema 改造

```sql
CREATE TABLE event_log (
  id TEXT PRIMARY KEY,
  aggregate_id TEXT NOT NULL,    -- session_id
  seq INTEGER NOT NULL,          -- 全局单调递增
  type TEXT NOT NULL,             -- message_received, text.started, tool.call, tool.result, ...
  data_json TEXT NOT NULL,
  time_created REAL NOT NULL,
  FOREIGN KEY (aggregate_id) REFERENCES sessions(id) ON DELETE CASCADE,
  UNIQUE (aggregate_id, seq)
);

CREATE INDEX idx_event_log_type ON event_log(type, time_created);
```

### 7.3 事件类型

```python
# src/strategy_research/api/session/event_v2.py
class EventV2:
    """所有事件类型（discriminated union）"""
    MessageReceived = "message_received"          # 用户消息已持久化
    AssistantStarted = "assistant.started"        # assistant message 创建
    TextStarted = "text.started"                  # text part 开始
    TextDelta = "text.delta"                       # text part 增量
    TextEnded = "text.ended"                       # text part 结束
    ThinkingStarted = "thinking.started"
    ThinkingDelta = "thinking.delta"
    ThinkingEnded = "thinking.ended"
    ToolCall = "tool.call"                         # tool_call part 创建
    ToolInput = "tool.input"                       # tool 输入
    ToolResult = "tool.result"                     # tool result 到达
    ToolProgress = "tool.progress"
    FileEdit = "file.edit"
    Table = "table"
    Chart = "chart"
    Image = "image"
    CompactStarted = "compact.started"
    CompactEnded = "compact.ended"
    AgentStatus = "agent.status"
    AgentLoop = "agent.loop"
    AgentDone = "agent.done"
    Error = "error"
    SessionMetaUpdated = "session_meta_updated"
    GoalUpdated = "goal.updated"
```

### 7.4 改造策略：三阶段

**B1: 基础设施（~1 周）**
- commit 1: event_log 表 + EventV2 类型定义
- commit 2: event_bus_v2 双写（同时 emit 旧 event_bus + 持久化到 event_log）
- commit 3: projector 监听 event_log → 更新 messages + message_parts（与双写并行）

**B2: 主流程迁移（~2 周）**
- commit 4-8: AgentLoop 各类 emit 改 events.publish(EventV2.X)
- commit 9: SSE handler 改读 projector 状态
- commit 10: 删除旧 event_bus 的 SSE 直发逻辑

**B3: 清理（~1 周）**
- commit 11: 删除旧 event_bus.emit
- commit 12: 删双写逻辑，只走 projector
- commit 13: 测试 + 性能验证

### 7.5 关键模块

**event_v2.py**（新建 ~150 行）：
- EventType 枚举
- Event dataclass（id, type, data, time_created）
- EventPublisher 接口
- 单进程 seq 生成器（与 message seq 共享）

**projector.py**（新建 ~300 行）：
- 每个事件类型一个 handler
- 监听 event_log → 更新 messages + message_parts
- 处理 seq 冲突（UNIQUE INDEX）

**event_bus_v2.py**（新建 ~100 行）：
- publish(EventV2.X, data)
- 双写：emit SSE + 持久化到 event_log
- 提供 last_event_id 检索（reconnect）

### 7.6 风险（最高）

| 风险 | 概率 | 影响 | 缓解 |
|------|------|------|------|
| 事件类型遗漏 | 高 | 中 | 完整 SSE event 列表对照表 + 集成测试 |
| projector 状态不一致 | 中 | 高 | 与双写期对比验证，diff 检查 |
| seq 冲突（多线程） | 中 | 中 | 锁 + 测试 |
| 性能下降（每事件双写） | 中 | 中 | 双写期短（commit 2-10），可接受 |
| 设计错误需 reset | 中 | 高（2 周） | 每周末 review + 调整设计 |

**opencode 自己的教训**：他们 `reset_v2_session_state` 整体重做了一次。我们**应该预留 1-2 周 buffer**用于可能的 reset。

---

## 8. 实际执行 commit 链（43 个）

> **Status: ✅ COMPLETE** (Phase 1-3 B1-B6)
>
> 原计划 45 个 commit，实际 43 个（部分合并到 commit）

### 8.1 Phase 1 — Level 0 + 1（4 commits）

```
7c0e41b fix(service): reorder assistant before tools in LLM history
b580204 feat(schema): add seq column to messages table
c94442e feat(seq): single-process seq generator + backfill script
f30458b refactor(history): ORDER BY seq instead of created_at
```

### 8.2 Phase 2 — Level 2 PartTable（6 commits）

```
efde19e feat(schema): add message_parts table
2a0448c feat(persistence): dual-write parts to message_parts
31b7304 feat(migration): migrate role=tool to message_parts
4c71967 refactor(read): _row_to_message returns (message, parts)
c66e6d1 refactor(llm): _convert_messages_to_history from message_parts
2bd70a8 chore(cleanup): drop role=tool, parts_json, tool_call_id
ccc0cdd fix(store): handle None metadata in get_messages
```

### 8.3 Phase 3 B1 — Event Sourcing 基础设施（11 commits）

```
92ea792 feat(schema): event_log table for event sourcing
73b0623 feat(event): EventV2 dataclass + EventType registry (30+ types)
bf97b9e feat(event_bus): EventBusV2 dual-write (event_log + EventBus)
ce356da feat(projector): Projector reads event_log → ProjectedSession
c2fd515 feat(e2e): end-to-end Phase 3 B1 verification
52b0036 test(event_sourcing): edge case coverage (19 tests)
5a30dcd test(event_sourcing): more edge cases + 5 bug fixes (45 tests, 5 real bugs)
f0eadc1 test(event_sourcing): round 3 edge cases (25 tests)
f0ff3e8 test(event_sourcing): round 4 edge cases with hypothesis (22 tests)
cd58d27 test(event_sourcing): round 5 edge cases + DB consistency (21 tests)
bfa7bbf test(event_sourcing): round 6 edge cases (39 tests)
```

### 8.4 Phase 3 B2 — 接线到 SessionService（5 commits）

```
418a6e6 feat(projector): Projector.flush() writes to messages + message_parts
faff631 feat(chat): wire EventBusV2 into SessionService
f9b67a1 test(b2): dual-write consistency verification
f224caf test(b2): service wiring + backward compat verification
9a47ab7 test(b2): backward compatibility verification
```

### 8.5 Phase 3 B3 — 读路径切换（5 commits）

```
113a0b6 feat(projector): to_messages() + project_to_messages()
c5e4ea7 feat(store): dual read path - event_log via projector
f3b8582 test(b3): read path consistency verification + fix msg seq
2025736 feat(backfill): messages + message_parts → event_log
88eadee feat(b3): projector compaction support + switch default read path
```

### 8.6 Phase 3 B4 — 写路径切换（5 commits）

```
a6790ad feat(event_bus): flush_to_messages - projector auto-flush on publish
288bfea feat(chat): enable flush_to_messages in EventBusV2
ac10162 feat(b4): remove direct message writes from service.py
2d56626 test(b4): recovery + edge case verification
555cb97 perf(b4): boundary-only flush optimization
```

### 8.7 Phase 3 B5 — Compact 事件化 + 终极 invariant（4 commits）

```
50f0ef1 feat(b5): compact history event-sourced
b95d023 feat(b5): /goal and /compact commands use event-sourced writes
5993230 test+fix(b5): final invariant + projector flush handles deletions
(nothing to commit for B5.4 — already clean)
```

### 8.8 Phase 3 B6 — Cleanup + Legacy（3 commits）

```
4b5400a feat(b6): L4 auto-compaction event-sourced
4f6ad0f docs(b6): legacy SessionDB technical debt + migration roadmap
```

**总计：43 commits, 439 tests, 0 failures**

---

## 8.9 执行期间发现的关键 bug

| B 系列 | Bug | 影响 | 修复 |
|--------|-----|------|------|
| B1 round 7 | `_persist` 不处理非 JSON 数据 → OperationalError | 中 | try/except |
| B1 round 7 | `replay` 在 DB 不存在时崩 | 中 | try/except OperationalError |
| B1 round 7 | Projector tool_call 丢失 `function` field | 中 | preserve `function` verbatim |
| B1 round 7 | FK CASCADE PRAGMA 未开 | 低 | 在 projector flush 开 PRAGMA foreign_keys=ON |
| B5 | Projector flush 缺删除逻辑 (compact 后旧消息残留) | 中 | 加 DELETE FROM messages WHERE session_id=? AND id NOT IN (...) |
| B5 | 跨会话 PK 冲突（消息 id 全局唯一） | 低 | 测试用唯一 id per session（生产用 UUID 不冲突） |

---

## 9. 验证策略

### 9.1 每个 Level 必跑

| 验证 | 工具 / 命令 |
|------|-------------|
| 单元测试 | `pytest tests/test_<level>.py -v` |
| 全量测试 | `pytest tests/ -q`（ignoring pre-existing failures） |
| 类型检查 | `ruff check src/` |
| 端到端：700dc7f7 | 前端发新消息，看 SSE 事件流 |
| 端到端：81102cc1 | 前端发新消息 |
| 性能基准 | `python benchmarks/llm_history_projection.py` |

### 9.2 端到端回归测试

`tests/e2e/test_700dc7f7_replay.py`（新建）：

```python
def test_700dc7f7_turn_3_no_2013():
    """Reproduce session 700dc7f7 turn 3 conditions, verify no 2013."""
    # 1. Load session 700dc7f7 history from DB
    # 2. Simulate user sending "1A 2A 3B 4A 5A" (or any new message)
    # 3. Call _convert_messages_to_history
    # 4. Verify output: assistant(tool_calls) BEFORE its tool messages
    # 5. Send to mocked LLM provider, verify no 2013
```

### 9.3 L4 触发行为

每 Level 跑：
```python
def test_l4_still_works():
    """Verify L4 compaction still works after schema change."""
    # 1. Setup session with > threshold tokens
    # 2. Call compact_messages
    # 3. Verify compaction applied
    # 4. Verify CompactionMessage persisted (via event log in Level 3)
```

---

## 10. 回滚策略

### 10.1 单 commit 回滚

```bash
git revert <commit-sha>
# 或
git reset --hard <previous-commit-sha>  # 慎用
```

### 10.2 Level 级回滚

| Level | 回滚命令 | 数据风险 |
|-------|----------|----------|
| Level 0 | `git revert HEAD` | 0（纯函数） |
| Level 1 | `git revert HEAD~3..HEAD` + 删 seq 列 | 低 |
| Level 2 | `git revert HEAD~6..HEAD` + 回滚 message_parts 删 column | 中（role=tool 行已删，备份恢复） |
| Level 3 | `git revert HEAD~10..HEAD` + 停 projector 监听 | 高（event_log 与 messages 不同步） |

### 10.3 灾难恢复

```bash
# Level 2 之前：备份 DB
cp /home/ll/Public/qn-research/quantnodes_strategy_research_user.db /tmp/backup.db

# 灾难回滚
cp /tmp/backup.db /home/ll/Public/qn-research/quantnodes_strategy_research_user.db
git revert --no-commit HEAD~N..HEAD
```

---

## 11. 业务影响评估

### 11.1 各 Level 期间 chat 可用性

| Level | 期间 chat 可用？ | 备注 |
|-------|------------------|------|
| Level 0 | 是 | 纯函数改动 |
| Level 1 | 是 | 新列默认 0，不影响 |
| Level 2 | 是 | 双写期保证，commit 6 才删 column |
| Level 3 B1 | 是 | 监听 projector 但不切换主流程 |
| Level 3 B2 | 部分 | 切换 SSE 协议，前端适配期间可能有 bug |
| Level 3 B3 | 是 | 清理阶段，无功能影响 |

### 11.2 性能影响

| Level | 性能影响 | 缓解 |
|-------|----------|------|
| Level 0 | 0 | 纯函数 |
| Level 1 | 0（UNIQUE INDEX 开销 < 1ms） | — |
| Level 2 | -5%（双写 + JOIN） | 双写期短；读路径用 JOIN 优化 |
| Level 3 | -10%（事件持久化 + projector） | 异步 projector；event log 用 append-only 优化 |

### 11.3 数据风险

| 场景 | 风险等级 | 缓解 |
|------|----------|------|
| 700dc7f7 137 tool 消息迁移 | 中 | dry-run + 手动 review |
| orphan tool 消息 | 低 | log + 报告，不丢失 |
| 旧 parts_json 内容损坏 | 极低 | 完整读取后才迁移 |
| event_log 与 messages 漂移 | 中 | 定期 reconciliation job |

---

## 12. 业务价值

| Level | 解决的问题 | 业务价值 |
|-------|------------|----------|
| Level 0 | 700dc7f7 turn 3 立即可修复 | **高**：用户能继续对话 |
| Level 1 | 任何 session 都不会因时间戳歧义崩 | **高**：永久消除一类 bug |
| Level 2 | 数据模型与 opencode 一致 | **中**：未来 LLM 协议问题预防 |
| Level 3 | event sourcing，可实现 revert、undo、multi-agent | **未来**：revert 单独评估 |

**不做的代价**：
- 任何 MiniMax 协议层消息格式问题都可能再次出现
- 长 session（>100 消息）数据模型脆弱
- 永远不会有 revert / undo / multi-agent

**做的代价**：
- 12-20 周（一个 senior 全栈）
- 1-2 次设计迭代/reset
- 短期 chat 性能可能下降 5-10%

---

## 13. 决策点

### 13.1 范围

- **Phase 1 (Level 0+1)**：1-2 天，~430 LOC，4 commits。修 700dc7f7。
- **Phase 2 (Level 2)**：1-2 周，~1500 LOC，6 commits。数据模型靠拢。
- **Phase 3-5 (Level 3)**：8-12 周，~3500 LOC，~35 commits。事件溯源。

### 13.2 风险承受

- **保守**：只做 Phase 1
- **平衡**：Phase 1+2（推荐，3 周）
- **激进**：全 Level 0-3（12-20 周）

### 13.3 Revert

- **现在不做**：Level 3 后单独评估
- **不评估**：永远不做

---

## 14. 附录

### 14.1 opencode 参考文件

- `packages/core/src/session/compaction.ts` (241 lines)
- `packages/core/src/session/runner/to-llm-message.ts` (171 lines)
- `packages/core/src/session/history.ts` (101 lines)
- `packages/core/src/session/projector.ts` (458 lines)
- `packages/core/src/session/sql.ts` (176 lines)
- `packages/core/src/session/message-updater.ts` (397 lines)
- `packages/core/src/session/event.ts` (638 lines)
- `packages/core/src/session/revert.ts` (121 lines)
- `packages/core/src/snapshot.ts` (266 lines)
- `packages/llm/src/schema/messages.ts` (312 lines)
- `packages/schema/src/session-message.ts` (213 lines)

### 14.2 受影响 schema

**当前 messages 表**（src/strategy_research/api/routers/web_session.py:83-110）：
```sql
CREATE TABLE messages (
  id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL,
  role TEXT NOT NULL,
  content TEXT NOT NULL DEFAULT '',
  parts_json TEXT,
  tool_call_id TEXT,
  created_at REAL NOT NULL,
  metadata_json TEXT,
  message_type TEXT
)
```

**目标 messages 表**（Level 2 + 3）：
```sql
CREATE TABLE messages (
  id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL,
  role TEXT NOT NULL,
  message_type TEXT,
  time_created REAL NOT NULL,
  metadata_json TEXT,
  seq INTEGER NOT NULL
)

CREATE TABLE message_parts (
  id TEXT PRIMARY KEY,
  message_id TEXT NOT NULL,
  session_id TEXT NOT NULL,
  type TEXT NOT NULL,
  data_json TEXT NOT NULL,
  seq INTEGER NOT NULL,
  time_created REAL NOT NULL,
  FOREIGN KEY (message_id) REFERENCES messages(id) ON DELETE CASCADE
)

CREATE TABLE event_log (
  id TEXT PRIMARY KEY,
  aggregate_id TEXT NOT NULL,
  seq INTEGER NOT NULL,
  type TEXT NOT NULL,
  data_json TEXT NOT NULL,
  time_created REAL NOT NULL,
  UNIQUE (aggregate_id, seq)
)
```

### 14.3 受影响测试

| 测试文件 | 关联 Level |
|----------|-----------|
| tests/test_tool_message_storage.py | Level 0, 2 |
| tests/test_convert_messages_to_history_order.py | Level 0 (新建) |
| tests/test_seq_generator.py | Level 1 (新建) |
| tests/test_compact_*.py (10+ 文件) | Level 0, 1, 2 |
| tests/test_history_compaction_filter.py | Level 0, 2 |
| tests/test_to_llm_message.py | Level 0, 2, 3 |
| tests/test_compaction_message.py | Level 0, 2, 3 |
| tests/test_compact_tool_pairs.py | Level 2 |
| tests/test_compact_full_pipeline.py | Level 0, 1, 2 |
| tests/test_compact_serialization.py | Level 2 |
| tests/test_event_log_round_trip.py | Level 3 (新建) |
| tests/test_projector_consistency.py | Level 3 (新建) |
| tests/e2e/test_700dc7f7_replay.py | 所有 Level (新建) |
| webui/frontend/src/test/useSSE.test.ts | Level 2, 3 |
| webui/frontend/src/test/MessageList.test.tsx | Level 0, 2 |
| webui/frontend/src/test/ToolCallBlock.test.tsx | Level 2, 3 |

### 14.4 时间线（按 Phase 分）

```
Week 0 (Plan):
  Day 1-2:  设计文档 (本文档)
  Day 3:    User 评审 + 调整
  
Week 1 (Phase 1):
  Day 1 上午:  Task 1 — Level 0 重排
  Day 1 下午:  验证 700dc7f7
  Day 2 上午:  Task 2 — Level 1 seq
  Day 2 下午:  全量测试 + 端到端
  
Week 2 (Phase 2):
  Day 1-2:    Level 2 commit 1-2 (schema + dual-write)
  Day 3-4:    Level 2 commit 3 (migration script + dry-run)
  Day 5:      Level 2 commit 4-5 (read path)
  Day 6-7:    Level 2 commit 6 (cleanup) + 集成测试
  
Week 3-12 (Phase 3-5, Level 3):
  Week 3-4:   B1 基础设施（event_log + EventV2 + projector 基础）
  Week 5-8:   B2 主流程迁移
  Week 9-10:  B3 清理
  Week 11-12: 性能验证 + 文档 + reset 缓冲

Week 13+ (Future, separate decision):
  Revert feature: ~4-6 周
  业务评估后再决定
```

---

## 15. 等待用户确认

请讨论以下决策点：

1. **范围确认**：Phase 1 only / Phase 1+2 / 全 Level 0-3？
2. **风险承受**：是否接受 1-2 次 reset 风险？
3. **Revert 评估**：未来是否需要 revert ？（业务侧）
4. **数据迁移策略**：700dc7f7 137 tool 消息是否值得冒险迁移？
5. **验证标准**：除 700dc7f7 turn 3 成功外，还需要什么验收条件？

---

## 16. B-series Edge Cases 总结

> 实际执行 B1-B6 期间发现并处理的边界情况，供未来参考。

### 16.1 事件顺序保证

**问题**: 多个事件以非确定顺序到达（理论上不应该发生，但防御性处理）。

**实际处理**: event_log 的 UNIQUE (aggregate_id, seq) 约束 + first-wins
逻辑。如果 seq 冲突，projector 跳过（log warn）。生产中 seq 是
monotonic 的，理论上不会冲突。

### 16.2 部分事件流（crash 场景）

**问题**: 进程在 text.started 之后崩溃，没有 text_delta/ended 事件。

**实际处理**: projector 的 `_on_text_started` 创建带空 text 的 part。
下次进程启动后，projector 可以从 event_log 重建状态（空 part
可见）。下次 agent run 可以继续。

### 16.3 消息 id 冲突

**问题**: 跨会话使用相同 id（如 "u1"）会导致 PK 冲突。

**实际处理**: 生产用 UUID，理论不冲突。测试用唯一 id per session。
B5 invariant 测试验证 100+ 事件场景无冲突。

### 16.4 Compact 替换语义

**问题**: compact.ended 事件携带 `messages` 字段时的行为模糊。

**实际处理**:
- **/compact (manual)**: `messages` 字段是压缩后的消息列表 → 替换
- **L4 auto-compaction**: 不携带 `messages` 字段 → 只是 marker
- projector 的 `_on_compact` 通过 `messages` 是否存在来区分

### 16.5 性能 vs 一致性

**问题**: 每次 event 都 flush 会很慢（每事件 ~2.4ms）。

**实际处理**: 只在消息边界事件 flush（message_received、assistant_message、
compact.ended）。streaming 事件（text_delta、tool_progress）只写
event_log，不 flush。延迟 ~1.3ms/event，完全可接受。

### 16.6 DB 一致性边界

**问题**: messages 表是 materialized view，event_log 是真源。两者可能
暂时不一致（boundary 事件之间）。

**实际处理**: 测试覆盖了"flush 后"的不变性和"wipe + re-flush"恢复场景。
B5 invariant 测试 `test_invariant_after_table_wipe_and_re_flush` 验证
完全可恢复。

### 16.7 EventBusV2 + legacy EventBus 互操作

**问题**: bridge.py monkey-patches `event_bus.publish` 到 sse_buffer。
新 EventBusV2 转发到 legacy EventBus 后，bridge 还能工作吗？

**实际处理**: 验证 ✅。EventBusV2._forward 调用
`self.event_bus.publish(sse_event)`，触发 bridge 的 patched
`bridged_publish`，正确推送到 sse_buffer。SSE 前端零改动。

### 16.8 流式事件 + 崩溃恢复

**问题**: 假设 100 个 text_delta 事件都已持久化到 event_log，但
projector 没 flush（因为不是 boundary 事件）。进程崩溃。重启。

**实际行为**: event_log 有所有 100 个事件。重启后调用
`Projector.project(session_id)` 可以从 event_log 重建完整状态。
下次 boundary 事件会 flush 完整状态到 messages 表。

### 16.9 Tool result 时序

**问题**: 旧代码在 `event_callback` 里立即写 `role=tool` 消息到 DB
（line 664 in service.py 旧版），下一个 AgentLoop iteration 可以读到。
新架构下，这种"提前持久化"还需要吗？

**实际行为**: 不需要。event_log 已经持久化了所有事件。下一个
AgentLoop 调用 `SessionStore.get_messages()` 走 projector 路径，
从 event_log 投影。tool_result 事件合并到 tool_call part，
下次读取时可见。

### 16.10 Compaction 中的边界情况

**问题**: 手工 /compact 后，agent run 继续。后续 LLM call 的
history 中 compaction 消息如何投影？

**实际行为**: `compaction_message.to_llm_message()` 投影为
USER role（不是 assistant），with `<conversation-checkpoint>` 包装。
LLM 正确识别为历史 context，不当作新指令。已有
`test_compact_serialization` 验证此行为。

### 16.11 Auto-title 与事件流

**问题**: `_run_message` 里 auto-title 更新 `sessions.title`，
然后发 `session_meta_updated` 事件。这是 event-sourcing 范畴吗？

**实际处理**: sessions 表本身是**元数据**（不属于消息流）。
`session_meta_updated` 事件只用于 SSE 推送，**不**写 event_log
（因为它不是消息事件）。sessions.title 由 SQL 直接更新
（不是 message 写），仍在 B-series 范围外。

### 16.12 跨 B 系列阶段的事件兼容

**问题**: B3 之前的事件（无 event_log 记录）能否被 B3+ projector 读？

**实际行为**: 不能直接读。`backfill_event_log` 工具从 messages +
message_parts 表生成事件。B3 切换默认读路径**之前**已对生产 DB
完成 backfill（61 sessions / 2437 events）。未来 schema 变化时
backfill 工具可复用。

### 16.13 大事件的 JSON 序列化

**问题**: data_json 字段是 TEXT 类型，受 SQLite max length 限制
(默认 1GB)。但 Python json.dumps 可能有性能问题。

**实际行为**: 当前所有事件都 < 10KB JSON。性能测试 1000 events
< 100ms。无 size 问题。

### 16.14 Sequence 重置

**问题**: 如果 event_log 被清空（disaster recovery）然后重新写入，
seq 怎么算？

**实际行为**: `_next_seq(session_id) = last_seq(session_id) + 1`。
如果 last_seq 是 0，从 1 开始。messages 序号也重新从 1 开始。
这是有意的 — 灾难恢复后从干净状态重启。

### 16.15 Concurrent write 安全性

**问题**: 多个 AgentLoop 同时运行（罕见但可能），会冲突吗？

**实际行为**: 单 session 单 attempt 串行（per-session queue in
service.py）。跨 session 完全独立（不同 aggregate_id）。UNIQUE
(aggregate_id, seq) 约束保证 seq 不冲突。当前架构不处理
**同一个 session 两个 attempt** 的并发（理论上 service.py 的
queue 保证不发生）。
