# B4 止血：D7（/clear 外键失败）与 schema 双主 — 2026-10-29

> 上游：`docs/archive/2026-10/stage-c1-b4-dataplane-design-20261028.md`（完整事件化方案，其
> Step 1 需协调者拍板 §9 的 7 个未决项后实施）。
> 本文是**先行止血刀**：只修两个用户可感知的活 bug，不碰语义决策。

---

## 0. 范围

| Bug | 用户可见症状 | 本文处置 |
|---|---|---|
| **D7** | `/clear` 之后所有消息落不了库（`FOREIGN KEY constraint failed`），直到有人重新 append 重建 session 行 | ✅ 止血 |
| **§1.4c schema 双主** | CLI 先启动建库 → 只有 2 张表 → API projector flush 报 `no such table: message_parts`，消息落不了库 | ✅ 止血 |
| D8 | `message_count` 恒 0（93/93 会话） | ❌ 后议（归属未决，见 B4 §9） |
| seq 口径不一 | `backfill_seq --apply` 后 mm 写入静默降级 | ❌ 后议（事件化时一并解决） |

---

## 1. D7 止血方案

### 1.1 根因（B4 §1.4b 实测）

```
/clear → mm.clear(sid) → SQLiteStore.delete_session(sid)
  → DELETE FROM messages; DELETE FROM sessions   (memory_manager.py:310-314)
→ /clear 的确认消息 emit → projector flush
  → INSERT INTO messages ... FK(session_id) → sessions 行已删 → 全部失败
```

### 1.2 修法：`delete_session` 不再删 sessions 行

`SQLiteStore.delete_session` 改为只删 `messages`，保留 `sessions` 行。

**为什么这是对的**：
- sessions 行是后续一切写入的 FK 锚点（`event_log.aggregate_id` 与
  `messages.session_id` 两处 FK 依赖它）——删除它没有任何收益，只有破坏。
- Web `/clear` 的文档语义本来就是「清空上下文、保留会话」（`slash_commands.py:821-829`）。
- 完整语义（改发 `compact.ended` 标记、消息是否保留）属 B4 §9 未决项，本文不动。

**InMemoryStore.delete_session 同步改**（测试后端，语义一致）。

### 1.3 不改的

- `compact_messages`（`memory_manager.py:324-363`）：它不删 sessions 行，
  不触发 D7；事件化时整体退役（B4 §3），本轮不动。
- `/clear` 是否保留消息：**未决项 D1**，本文保持现状行为（清空 messages）。

---

## 2. schema 双主止血方案

### 2.1 根因（B4 §1.4c 实测）

`SQLiteStore._init_schema`（`memory_manager.py:145-191`）只建
`sessions`/`messages` 两张表；`web_session_db._ensure_schema` 建全套
（含 `message_parts`/`attempts`/FTS/迁移）。谁先建库谁说了算——
CLI 先跑 → 缺表 → projector flush `no such table`。

### 2.2 修法：单一 DDL 主人

两个候选（B4 §2.4）：

| 方案 | 内容 | 代价 |
|---|---|---|
| A. 代理 | `_init_schema` 懒导入 `web_session_db._ensure_schema` | 新增 core→api 边（有先例：`event_store.py:569-578` 同样懒导入 api.session） |
| B. 删除 | 删掉 MemoryManager 的建表 SQL，要求调用方先建库 | 需要改全部 CLI/TUI 启动路径，触碰面大 |

**选 A**。理由：一行代理消除双主，调用方零改动；先例已存在；
B 的「更干净依赖方向」收益要等 B4 Step 5 删文件时自然兑现，现在做 B
是为未来收益付今天的复杂度——违反 less-is-more。

实现：`_init_schema` 保留（EventStore 依赖它存在），方法体改为
懒导入 + 调 `_ensure_schema(conn)`，原有 48 行 DDL 删除。

---

## 3. 改动面

| 文件 | 改动 |
|---|---|
| `core/agent/memory_manager.py` | ① `SQLiteStore.delete_session` 只删 messages<br>② `_init_schema` 代理 `_ensure_schema`<br>③ `InMemoryStore.delete_session` 语义同步（只清消息映射） |
| `api/session/web_session_db.py` | ④ `_ensure_schema` 修复两个预存缺陷（代理改动暴露，见 §5） |
| `tests/test_memory_dataplane_hotfix.py`（新） | D7 回归：clear 后 flush 不再 FK 失败；schema 双主：先 SQLiteStore 建库后 projector 可写 |

---

## 4. 验证

| # | 证据 | 测试 |
|---|---|---|
| E1 | `clear` 后立即 emit 消息事件，flush 成功 | 新增（复现 B4 §1.4b 序列） |
| E2 | sessions 行在 clear 后仍存在 | 新增 |
| E3 | `SQLiteStore` 先建库的库，`web_session_db` 全家桶表齐全 | 新增（复现 §1.4c 序列） |
| E4 | 现有 `/clear` 行为（清空消息）不变 | `test_chat_slash_commands.py` 既有断言 |
| E5 | EventStore 依赖面不破 | `test_event_store_comprehensive.py` 抽查 |

回归门：`test_memory*` + `test_session*` + `test_chat_slash_commands*`
→ `c1_gate.py`。

---

## 5. 代理改动暴露的两个 `_ensure_schema` 预存缺陷（已修）

`_init_schema` 代理 `_ensure_schema` 后，SQLiteStore 的共享连接第一次
真正走过这段代码，两个一直存在的缺陷随即在全量回归中爆出：

| 缺陷 | 机制 | 修法 |
|---|---|---|
| **PRAGMA 泄漏** | `_ensure_schema:45` `PRAGMA foreign_keys=ON` 是**连接级**设置且从不还原——schema 函数永久改了调用方连接。SQLiteStore 的连接因此开始对 event_log 执法 FK，裸插 legacy 行失败 | 进入时记 `_fk_before`，函数尾部还原。schema 函数不应改变调用方的连接状态 |
| **FTS trigger 顺序** | 同步 trigger（messages_ai/ad/au）引用 `messages_fts`，但清理旧 trigger 的代码在函数后段（`needs_fts_rebuild` 块内）。任何「FTS 表被删而 trigger 残留」的库，中段的 `UPDATE messages`（message_type 回填）就会被残留 trigger 打爆：`no such table: main.messages_fts` | trigger 清理提到函数头部、任何 DML 之前；后段重建时幂等 |

两处都是 `_ensure_schema` 自身的正确性问题（不修，任何外部调用方
以相同顺序触达都会炸），不是测试兼容性妥协。
