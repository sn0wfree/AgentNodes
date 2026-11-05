# S3 消息写入归一（0.4 数据面收敛）— 2026-10-28

> 前置：S1（P-1 信封 + EventStore 门面化）已落地（`s1-event-protocol-unification-design`）；
> S2（拆 per-session 文件）**随单库定案作废**；S4（SSE 单轨）并入 P5-3 另行处理。
> 依据：`p04-data-plane-convergence-feasibility-20261027.md` 调查 + 本轮复核。

## 1. 复核后的写者实况（修正可行性报告的「四写者」）

| 写者 | 文件 | 写哪个库 | 定性 |
|---|---|---|---|
| ① projector | `api/session/projection_store.py:310` | **统一库** | ✅ 唯一合法写者（事件 fold） |
| ② 直写 | `api/routers/web_session.py:126`（`persist_message`） | **统一库** | ⚠ 活路径（chat slash/compaction 用）——撞 UNIQUE 风险 |
| ③ 直写 | `core/agent/memory_manager.py:270,355` | **统一库** | ⚠ MemoryManager 的 SQLiteStore 直写（CLI/goal 用） |
| ④ 独立库 | `core/session/db.py:188,297`（`SESSIONS_DB = ~/.quantnodes-research/sessions.db`） | **另一个文件** | 🔴 9/5「admin/TUI 读不同历史」根因 |

关键发现：④ 是**独立的第二套库**（不是同一张表的第四个写者），消费方 =
CLI `session` 命令族、TUI、MCP server、admin、`session_memory` hook。它自带
自己的 sessions/messages/FTS 表与触发器。

## 2. 本轮范围（保守、可独立回退）

### 2.0 实施中的关键修正（2026-10-28，如实记录）

原计划第 1 项「④ 并入统一库（默认路径改 `resolve_session_db_path()`）」
**实施后发现是错的，已回退**。论证：两套 messages schema 不兼容——

| 旧会话栈（core/session/db.py） | 统一库（web_session_db） |
|---|---|
| `id INTEGER PRIMARY KEY` | `id TEXT PRIMARY KEY` |
| `timestamp REAL` | `created_at REAL NOT NULL` |

同文件会让旧栈的 INSERT 写坏/写崩表。这本身就是 C1 之前不能合库的硬证据。
替代结论（并已落为门禁测试）：
- 旧会话栈继续写自己的文件（默认不变）；
- **两库不得指向同一文件**由测试钉死（`test_legacy_session_stack_stays_separate`）；
- 分叉视图问题留给 C1（MemoryManager 事件化 + 旧会话栈整体退役）。

**做：**
1. **~~④ 并入统一库~~ → 改为「旧栈保持独立 + 禁合并门禁」**（见 §2.0）。
2. **② 加唯一性防护**：`persist_message` 在 INSERT 前检查
   `messages.id` 是否已存在（projector 可能已投影同一事件），存在则跳过
   ——杜绝 UNIQUE 冲突与 message_count 双计。
3. **架构门禁 ×3**：
   - `INSERT INTO messages` 白名单（projector + persist_message + 两个
     已知遗留各自文件），只许缩短；
   - 旧会话栈与统一库路径不得相同；
   - messages_fts 索引写入不计入 messages 白名单（精确表名匹配）。

**不做（本轮）：**
- ③ MemoryManager 的 SQLiteStore 直写改事件化——服务 CLI/goal 同步回退
  路径，改动牵动全部读写语义，属 C1 范畴。
- 彻底删除 `persist_message` 直写——依赖 compaction persister 回调，
  与 P5-1c 耦合。

**结果**：本轮交付的是「写入者清单钉死 + 幂等防护 + 不可合并的硬边界」，
不是「四写者归一」。真实归一等 C1。

## 3. 兼容语义

| 场景 | 变化 |
|---|---|
| 统一库已有会话 | 不变（④ 改成读同一文件后，旧 `~/.quantnodes-research/sessions.db`
数据**不自动迁移**——旧数据仍在原文件；CLI `/history` 从此看到 WebUI 的会话） |
| `SR_SESSIONS_DB` 显式指定 | 照旧生效（优先级最高） |
| `slash_session` 自定义 db_path | 不受影响（显式参数优先于默认） |
| 测试 | 依赖旧库路径的测试需改为 tmp_path 或 env（逐个核对） |

## 4. 验收

1. `git grep "INSERT INTO messages" src/` 命中仅 `projection_store` + `web_session`；
2. 新增门禁测试全绿；`persist_message` 幂等测试（同 message_id 二次写不炸）；
3. 回归：session/chat 全家 + CLI session 命令 + MCP + admin + study；
4. lint 干净。
