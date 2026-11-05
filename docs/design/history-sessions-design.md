# 历史会话 + 恢复 设计文档

## 背景

### 问题

Web UI 当前只能看到"当前打开"的会话，没有历史列表或恢复机制：

1. **后端消息不持久化**：`chat.py:27` 用 `_session_histories: dict[str, list]` **进程内字典**保存消息，进程重启即丢失。`web_session.py` 只持久化了 `sessions` 表（id/title/timestamps），没有 messages 表。
2. **前端无历史 UI**：`IconNav` 仅 3 图标（聊天/设置/登出），没有列出历史会话的入口。`CommandPalette.tsx:117, 126` 的"新建会话/刷新当前会话"是 TODO stubs。
3. **切换会话不清理消息**：`chat.ts:73-83` 的 `messages: Map<string, Message>` 是 flat 的，切到 session B 后 session A 的消息仍然在 Map 里。
4. **刷新丢失当前会话**：`session.ts` 没有 `persist` middleware，浏览器刷新后 `currentSessionId = null`，`AppShell.tsx:21-43` 永远选 `sessions[0]`。

### 目标

| 目标 | 验收 |
|---|---|
| 会话消息持久化 | 重启后端 → 刷新浏览器 → 切回旧 tab → 完整消息恢复 |
| Tab 形式切换（opencode 1.18+ 风格） | TopBar 下显示横向 tab bar，每个 tab 一个会话 |
| FTS5 搜索 | Ctrl/Cmd+K 输入关键词 → 高亮命中片段 → 点击跳到对应消息 |
| 标签 + 收藏 | 每个会话可加 tag + star；tab 上显示 star icon |
| 关闭 ≠ 删除 | 关 tab 仅从 tab bar 移除；session 仍在 DB，搜索可恢复 |
| 持久化 tab 状态 | 浏览器刷新后 openSessionIds + currentSessionId 恢复 |

---

## 设计方案

### 架构概览

```
┌───────────────────────────────────────────────────────────────┐
│  Browser                                                       │
│  ┌────────────────────────────────────────────────────────┐    │
│  │ TopBar: / Strategy Research                            │    │
│  ├────────────────────────────────────────────────────────┤    │
│  │ SessionTabs: [⭐Tab1] [Tab2*] [Tab3] ... [+] [🔍]     │    │
│  ├────────────────────────────────────────────────────────┤    │
│  │ Chat Area (currentSession)                             │    │
│  └────────────────────────────────────────────────────────┘    │
│  RightPanel: DAG | Goal | Agent                               │
│                                                                │
│  Stores:                                                       │
│    useSessionStore (persist: sr-sessions)                     │
│      - sessions: Session[]   (元数据缓存)                       │
│      - openSessionIds: string[]                              │
│      - currentSessionId: string | null                        │
│      - searchResults: SearchHit[]                             │
│    useChatStore                                               │
│      - messages: Map<id, Message>  (按 session_id 过滤渲染)    │
└───────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌───────────────────────────────────────────────────────────────┐
│  Backend (FastAPI)                                             │
│                                                                │
│  Routers:                                                      │
│    POST   /api/chat/session                                   │
│    GET    /api/chat/session                                   │
│    GET    /api/chat/session/{id}                              │
│    PATCH  /api/chat/session/{id}  ← NEW: title/starred/tags   │
│    DELETE /api/chat/session/{id}  ← UPDATE: cascade messages  │
│    GET    /api/chat/session/{id}/messages  ← NEW              │
│    POST   /api/chat/session/search  ← NEW: FTS5               │
│    POST   /api/chat/send_async   ← UPDATE: persist hooks      │
│    GET    /api/chat/events      ← unchanged                   │
│                                                                │
│  DB (SQLite, ~/.quantnodes/quantnodes_strategy_research_user.db):
│    sessions:                                                   │
│      id, user_id, title, created_at, updated_at,              │
│      starred, tags_json, message_count, archived              │
│    messages:                                                   │
│      id, session_id, role, content, parts_json,              │
│      created_at, metadata_json                                │
│      FK session_id → sessions.id ON DELETE CASCADE            │
│    messages_fts (FTS5 virtual):  ← NEW                        │
│      content, role                                            │
│      triggers ai/ad/au for sync                               │
└───────────────────────────────────────────────────────────────┘
```

---

## 数据模型

### sessions 表（扩展）

```sql
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '新会话',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    starred INTEGER NOT NULL DEFAULT 0,        -- NEW
    tags_json TEXT NOT NULL DEFAULT '[]',       -- NEW (JSON array of strings)
    message_count INTEGER NOT NULL DEFAULT 0,   -- NEW (denormalized for badge)
    archived INTEGER NOT NULL DEFAULT 0         -- NEW
);

-- 幂等迁移（已存在时不报错）：
ALTER TABLE sessions ADD COLUMN starred INTEGER NOT NULL DEFAULT 0;
ALTER TABLE sessions ADD COLUMN tags_json TEXT NOT NULL DEFAULT '[]';
ALTER TABLE sessions ADD COLUMN message_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE sessions ADD COLUMN archived INTEGER NOT NULL DEFAULT 0;
```

### messages 表（新建）

```sql
CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL,                          -- 'user' | 'assistant' | 'system' | 'tool'
    content TEXT NOT NULL DEFAULT '',
    parts_json TEXT,                              -- 工具调用 / 思考过程
    created_at REAL NOT NULL,
    metadata_json TEXT,
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_messages_session_created
    ON messages(session_id, created_at);
```

### messages_fts 表（新建，FTS5 搜索）

```sql
CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
    content,
    role,
    content='messages',
    content_rowid='rowid'
);

-- 三触发器同步 FTS 索引
CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages BEGIN
    INSERT INTO messages_fts(rowid, content, role)
    VALUES (new.rowid, new.content, new.role);
END;
CREATE TRIGGER IF NOT EXISTS messages_ad AFTER DELETE ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, content, role)
    VALUES ('delete', old.rowid, old.content, old.role);
END;
CREATE TRIGGER IF NOT EXISTS messages_au AFTER UPDATE ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, content, role)
    VALUES ('delete', old.rowid, old.content, old.role);
    INSERT INTO messages_fts(rowid, content, role)
    VALUES (new.rowid, new.content, new.role);
END;
```

### TS 类型

```typescript
// stores/session.ts
export interface Session {
  id: string
  title: string
  created_at: number
  updated_at: number
  starred: boolean
  tags: string[]
  message_count: number
  archived: boolean
}

export interface SearchHit {
  session_id: string
  session_title: string
  message_id: string
  role: 'user' | 'assistant' | 'system' | 'tool'
  snippet: string                  // 含 <mark> 标签的命中片段
  score: number
  created_at: number
}
```

---

## API 契约

### POST `/api/chat/send_async` （扩展：持久化）

请求不变。**新增副作用**：

1. 启动前 INSERT user message（同步）：`INSERT INTO messages (id, session_id, role, content, parts_json, created_at) VALUES (...)`
2. `UPDATE sessions SET message_count = message_count + 1, updated_at = ?`
3. 若 `title == '新会话' AND message_count == 1`：截取 content 前 30 字符更新 title
4. AgentLoop 完成后 INSERT assistant message（异步）：包含完整 parts（text/tool_call/thinking）
5. `UPDATE sessions SET message_count = message_count + 1, updated_at = ?`

### GET `/api/chat/session/{id}/messages` （新建）

```http
GET /api/chat/session/abc123/messages?limit=200&before=1234567890
```

响应：
```json
{
  "messages": [
    {
      "id": "msg-uuid",
      "session_id": "abc123",
      "role": "user",
      "content": "你好",
      "parts": [],
      "created_at": 1234567890.5,
      "metadata": null
    }
  ],
  "has_more": false,
  "total": 1
}
```

### PATCH `/api/chat/session/{id}` （扩展）

请求体（部分）：
```json
{ "title": "新标题" }
{ "starred": true }
{ "tags": ["工作", "重要"] }
{ "archived": true }
```
支持多个字段同时更新。

### POST `/api/chat/session/search` （新建）

```http
POST /api/chat/session/search
Content-Type: application/json

{ "query": "alpha 策略", "limit": 20 }
```

响应：
```json
{
  "hits": [
    {
      "session_id": "abc",
      "session_title": "alpha 探索",
      "message_id": "msg-xyz",
      "role": "user",
      "snippet": "我想做一个 <mark>alpha</mark> 策略...",
      "score": -1.5,
      "created_at": 1234567890
    }
  ]
}
```

**snippet 生成**：取匹配词前后 30 字符，转义 HTML，包裹 `<mark>`。

### DELETE `/api/chat/session/{id}` （扩展：级联）

```python
conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
```

FTS 触发器自动清理索引。

---

## 前端 Tab UI 设计

### 视觉

```
┌──────────────────────────────────────────────────────────────────────┐
│ /  Strategy Research            [⟳]  [⚙]  [⏻]                       │ ← TopBar
├──────────────────────────────────────────────────────────────────────┤
│ ⭐ alpha 探索 × │   双均线回测 × │  风控优化 ×*│   +   │   🔍         │ ← SessionTabs
├──────────────────────────────────────────────────────────────────────┤
│                                                                       │
│   💬 当前会话（双均线回测）                                            │
│                                                                       │
│   User: 帮我设计一个双均线策略                                         │
│   Agent: ...                                                          │
│                                                                       │
├──────────────────────────────────────────────────────────────────────┤
│ [Composer: type message...                                  ] [Send] │
└──────────────────────────────────────────────────────────────────────┘
```

- **Active tab**（`*` 标记）：底部 2px 高亮线 + 稍亮背景
- **Hover tab**：显示关闭 `×` 按钮（普通状态隐藏，星标常驻）
- **`+` 按钮**：最右，点击创建新 tab
- **`🔍` 按钮**：打开 SearchModal（也可 Ctrl/Cmd+K）
- **溢出**：超过 6 个 tab 时横向滚动（overflow-x-auto）

### 交互

| 操作 | 效果 |
|---|---|
| 单击 tab | `switchSession(id)`：clear chat + loadMessages + reconnect SSE |
| 单击 `×` | `closeSession(id)`：从 openSessionIds 移除 + 切换到邻 tab；**不删除** |
| 单击 `+` | `createNewSession()`：POST + 立即打开为 active tab |
| 单击 `⭐` | toggle `starred`（PATCH） |
| 右键 tab | 上下文菜单：⭐ 收藏 / 🏷 编辑标签 / 📁 归档 / 🗑 删除 |
| Ctrl/Cmd+K | 打开 SearchModal |
| Ctrl/Cmd+T | 等同于单击 `+` |
| Ctrl/Cmd+W | 等同于单击 active tab 的 `×` |
| Ctrl/Cmd+1-9 | 切到 openSessionIds 的第 n 个 |

### 右键菜单

```
┌─────────────────┐
│ ⭐ 收藏 / 取消   │
│ 🏷 编辑标签...   │
│ 📁 归档          │
│ ✏️ 重命名        │
│ ─────────────  │
│ 🗑 删除会话     │ ← 二次确认
└─────────────────┘
```

---

## 关键流程

### 1. 启动加载（AppShell.tsx）

```typescript
useEffect(() => {
  const init = async () => {
    // 1. 从 localStorage 恢复
    const persisted = sessionStore.persist.getOptions()
    const openIds = useSessionStore.getState().openSessionIds
    const currentId = useSessionStore.getState().currentSessionId

    // 2. 并行加载 open sessions 元数据
    const validIds: string[] = []
    await Promise.all(openIds.map(async (id) => {
      try {
        const s = await api.get(`/chat/session/${id}`)
        useSessionStore.getState().addSession(s)
        validIds.push(id)
      } catch (404) {
        // session 已被删除，跳过
      }
    }))
    useSessionStore.setState({ openSessionIds: validIds })

    // 3. 加载 current session 的消息
    if (currentId && validIds.includes(currentId)) {
      await useChatStore.getState().loadMessages(currentId)
    } else if (validIds.length > 0) {
      await useSessionStore.getState().switchSession(validIds[0])
    } else {
      // 4. 完全空：创建新 session
      await useSessionStore.getState().createNewSession()
    }
  }
  init()
}, [])
```

### 2. 切换会话（switchSession）

```typescript
switchSession: async (id: string) => {
  const { clearMessages, loadMessages } = useChatStore.getState()
  setCurrentSession(id)
  if (!openSessionIds.includes(id)) {
    setOpenSessionIds([...openSessionIds, id])
  }
  clearMessages()                                // 清空旧消息
  stopStreaming()                                 // 取消旧 SSE（useSSE 内部处理）
  await loadMessages(id)                          // 拉取新消息
  // useSSE.ts 监听 sessionId 变化自动重连
}
```

### 3. 持久化（chat.py）

```python
async def _run_agent_loop_background(session_id, message_id, user_content):
    user_msg_id = str(uuid.uuid4())
    now = time.time()

    # 1. 持久化 user message
    _persist_message(user_msg_id, session_id, "user", user_content, parts=None, created_at=now)

    # 2. Auto-title
    _auto_title_if_needed(session_id, user_content)

    # 3. 运行 AgentLoop（产生 parts 累积）
    accumulated_parts: list[dict] = []
    async for event in agent_loop.run(...):
        accumulated_parts.append(event_to_part(event))
        sse_buffer.push(event["type"], json.dumps(event), session_id)

    # 4. 持久化 assistant message
    assistant_content = "".join(p.get("text", "") for p in accumulated_parts if p["type"] == "text")
    _persist_message(
        str(uuid.uuid4()), session_id, "assistant", assistant_content,
        parts=accumulated_parts, created_at=time.time()
    )

    # 5. message_count 已在 _persist_message 内部 +1
```

### 4. 搜索（Ctrl/Cmd+K）

```typescript
async function runSearch(query: string) {
  const res = await api.post<{ hits: SearchHit[] }>('/chat/session/search', { query })
  setSearchResults(res.hits)
}
```

结果点击：
```typescript
function hitClick(hit: SearchHit) {
  // 1. 关闭搜索
  setSearchOpen(false)
  // 2. 打开/激活会话
  switchSession(hit.session_id)
  // 3. 滚动到消息
  setTimeout(() => {
    document.getElementById(`msg-${hit.message_id}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' })
  }, 300)
}
```

`MessageBubble.tsx` 添加 `id={`msg-${message.id}`}`。

---

## 改动清单

### 后端（2 文件）

| # | 文件 | 类型 | 说明 |
|---|---|---|---|
| 1 | `src/strategy_research/api/routers/web_session.py` | 扩展 | schema migration + 2 个新 endpoint + PATCH 扩展 + DELETE 级联 |
| 2 | `src/strategy_research/api/routers/chat.py` | 扩展 | `_run_agent_loop_background` 增加持久化 + auto-title |

### 前端（新增 5 / 修改 7）

| # | 文件 | 类型 | 说明 |
|---|---|---|---|
| 1 | `src/components/chat/SessionTabs.tsx` | 新增 | Tab bar + 右键菜单 |
| 2 | `src/components/common/SearchModal.tsx` | 新增 | Ctrl/Cmd+K 搜索面板 |
| 3 | `src/hooks/useKeyboardShortcuts.ts` | 新增 | 全局快捷键 |
| 4 | `src/stores/session.ts` | 重构 | 加 `persist` middleware + 新 actions |
| 5 | `src/stores/chat.ts` | 扩展 | `loadMessages`, `clearMessages`, `stopStreaming` |
| 6 | `src/components/layout/AppShell.tsx` | 修改 | 启动加载 + 持久化恢复 |
| 7 | `src/components/layout/MainSplit.tsx` | 修改 | 在 TopBar 下插入 `<SessionTabs />` |
| 8 | `src/components/chat/MessageBubble.tsx` | 修改 | 加 `id={`msg-${id}`}` 锚点 |
| 9 | `src/components/common/CommandPalette.tsx` | 修改 | 实现新建/刷新 stubs |
| 10 | `src/api/client.ts` | 扩展 | 加 `api.patch()` 方法 |
| 11 | `src/components/layout/TopBar.tsx` | 扩展 | 标题 inline edit 调用 PATCH |
| 12 | `src/test/SessionTabs.test.tsx` | 新增 | 渲染/切换/关闭/+按钮/快捷键 |
| 13 | `src/test/SearchModal.test.tsx` | 新增 | 输入/结果/点击切换 |
| 14 | `src/test/sessionStore.test.ts` | 扩展 | switchSession/closeSession/persist/search |

### 后端测试（1 文件）

| # | 文件 | 说明 |
|---|---|---|
| 1 | `tests/test_webui_api.py` | 扩展：messages CRUD、PATCH meta、FTS5 search、cascade DELETE、auto-title |

---

## 风险点 & 缓解

| 风险 | 缓解 |
|---|---|
| 老 `sessions` 行没有新列（starred/tags/message_count/archived） | `CREATE TABLE IF NOT EXISTS` 后用 `try/except` 包裹 `ALTER TABLE ADD COLUMN`（幂等） |
| FTS5 在某些 SQLite 编译版不可用 | 用 `try/except` 包 `CREATE VIRTUAL TABLE`，失败时 search endpoint 返回 503 + 前端降级（不显示搜索框） |
| 持久化失败导致 AgentLoop 中断 | 持久化在 try/except 中，失败仅 log，不影响 SSE 流 |
| persisted `openSessionIds` 引用已被删除 session | AppShell 启动时过滤 404 |
| Auto-title 在并发写入时竞争 | 用 `asyncio.Lock` 序列化同一 session 的 title 写入 |
| 大量消息时 `loadMessages` 慢 | 加 `limit=200` 默认 + `before=` 分页参数 |
| 关闭 tab 误以为删除 | UI 上 `×` hover-tooltip："关闭（保留历史）"，删除走右键菜单 + 二次确认 |
| 标签输入框 XSS | `<` `>` 转义 + 长度限制 32 字符 |

---

## 验收

| # | 步骤 | 通过标准 |
|---|---|---|
| 1 | `tsc -b` | exit 0 |
| 2 | `vitest run` | 全 pass，+15 tests |
| 3 | `vite build` | 成功 |
| 4 | 后端启动后 reload | 端点 `/api/chat/session/{id}/messages` 返回 200 |
| 5 | 浏览器 E2E：登录 → 发消息 → reload → tab bar 显示会话 + 消息恢复 | ✓ |
| 6 | E2E：新建 tab + 切回旧 tab + 关闭 tab（不删） + Ctrl+K 搜索 | ✓ |
| 7 | `git commit + push` | commit hash 返回 |

---

## 实施顺序

1. **后端 A1-A2**：schema + 持久化（不动现有 API 形状）
2. **后端 A3-A7**：新 endpoint + PATCH + search + 级联
3. **前端 stores B3-B5**：sessionStore 重构 + chatStore loadMessages + persist
4. **前端 components B1-B2, B6**：SessionTabs + SearchModal + TopBar edit
5. **前端 keyboard B10**：useKeyboardShortcuts
6. **测试 C1-C5**：前后端测试
7. **D1-D5**：验证 + commit + push