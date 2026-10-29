# C1 · B4 — MemoryManager 事件化 + 旧会话栈退役（数据面设计）

> 归属：W-D（`core/agent/memory_manager.py`、`core/session/**`、`core/storage/**`，
> 另有契约 §5 授予的 `core/context/**`、`core/agent/context_providers.py`）。
> 上游：`docs/stage-c1-team-contract-20261028.md`（§1 能力矩阵 / §5 所有权 / §6 验收 / §6.5 less-is-more）、
> `docs/stage-c1-acceptance-20261028.md`（§2 三道门 / §7 交付物）、
> `docs/stage-s3-message-writer-convergence-20261028.md`（四写者清单与 §2.0 修订）。
> 基线：分支 `refactor/v6-gateway`，HEAD `755d9051`。所有事实为本机实测，带 `file:line`；
> 无法从源码直接阅读得到的结论均标注「实测」并给出复验方式（§10）。估算一律标注为估算。
>
> **本文只读调查产物：未修改任何 `src/` / `tests/` 文件，未提交 git。**

---

## 0. 结论先行

| # | 结论 | 影响 |
|---|---|---|
| **D1** | `memory_manager` 的 messages 直写只有两处 SQL：`memory_manager.py:270`（`SQLiteStore.insert_message`）与 `memory_manager.py:355`（`SQLiteStore.compact_messages` 的 summary 行）；另有 `memory_manager.py:312/350` 两处 DELETE。**它写的不是"自己的库"，而是统一库同一个文件**——`UnifiedMemoryManager` 走 `resolve_db_path()` → `resolve_session_db_path()`（`memory_manager.py:579`、`71-100`），实测与 `web_session._get_db_path()` 同文件。S3 文档里"MemoryManager 自有 SQLiteStore"指的是**自有连接 + 自有 seq 方案**，不是自有文件 | 事件化 = 换写法，不是搬家 |
| **D2** | **选「发事件经 projector」**，不直调 `ProjectionStore`。`ProjectionStore` 只有全量状态 `flush(state)`（`projection_store.py:180-302`），没有 append API；直调要求 MemoryManager 自己维护 `ProjectedSession` 并复刻 `project_incremental` 的 fold（`projector.py:86-122`）= 第二套投影，且全量 flush 会删掉状态里没有的行（实测，§2.4） | 新增代码最少、语义最安全 |
| **D3** | **自有 `SQLiteStore` 与统一库：文件合并（已经合并），类保留但退役其 messages CRUD**。`SQLiteStore` 同时是 `EventStore` 的 SQLite 底座（`event_store.py:27,63`），不能删类；`insert_message/list_messages/compact_messages/delete_session` 只服务 MemoryManager（`memory_manager.py:748,752,657,680,666`）+ 测试 | 类保留、方法分层裁剪 |
| **D4** | **两套 schema 物理不可合并已被实测钉死**（legacy 表插入 TEXT id → `IntegrityError: datatype mismatch`；统一表插入 legacy 行 → `no such column: timestamp`）。维持手段 = 现有门禁 `test_architecture_gates.py:287-300`，直到 `core/session/db.py` 删除的那个提交才随之退役 | 退役顺序必须"先清消费方，再删文件+门禁" |
| **D5** | **`core/session/db.py` 有 12 个生产消费点**（admin 1 / CLI `session` 5 / REPL slash 2 / TUI 2 / MCP 2），其中 #7/#8 受环境变量门控（默认不可用）、#4/#9/#10 已经因字段名不匹配而失效；另有 `SessionManager` + `SessionMemoryHook` 两个**零生产构造点**的包装层（grep 全仓只有测试）→ 按契约 §6.5 规则 2 直接删，不需要替代入口 | 退役面比 S3 文档估的小 |
| **D6** | **`memory_manager` 在 Web 进程里只有一个写入口，而且已经是死代码**：`api/routers/chat.py:395-462` 的 `_run_agent_loop_background` 全仓零调用（`findstr` 只命中定义与注释）。真正的 mm 写入/读取都在 CLI/TUI（`cli/tui/session.py:264,298-299,331,366-367`） | Web 侧零行为风险 |
| **D7** | **`/clear` 是当前最严重的活 bug**：`slash_commands.py:838` 调 `mm.clear()` → `DELETE FROM messages` + `DELETE FROM sessions`（`memory_manager.py:312-313`）。session 行被删后，随后 projector flush 因 `FOREIGN KEY constraint failed` 全部失败（实测：`/clear` 的确认消息 + 用户消息都落不了库，`messages` 恒为 0）。事件化方案里 `clear` 必须改成发 `compact.ended` 标记，不能保留 DELETE | 修一个真实数据丢失缺陷 |
| **D8** | **`messages.message_count` 只有 `persist_message` 维护**（`web_session.py:165-168`），projector 从不更新它。真实库实测：**93/93 个会话的 `message_count` 都是 0**，而实际每会话有 1/2/4 条消息（65 会话 2 条、17 会话 4 条、11 会话 1 条）。若 `persist_message` 继续退场，这个列会永久为 0 | 需要协调者裁决替代方案（§9 D2） |

---

## 1. 现状复核：四写者、真实副作用、消费方

### 1.1 四写者（逐条复核，全部带行号）

| # | 写者 | 写哪个文件 | 证据 | 定性 |
|---|---|---|---|---|
| ① | projector | 统一库 | `projection_store.py:310`（`INSERT INTO messages`，UPSERT） | ✅ 设计唯一写者 |
| ② | `persist_message` 兼容直写 | 统一库 | `web_session.py:140-143`（INSERT）；幂等防护 `web_session.py:110-118` | ⚠ 活路径（`/compact` persister），带幂等 |
| ③ | MemoryManager | **统一库**（同一文件） | `memory_manager.py:270`、`355`；路径解析 `memory_manager.py:579` → `71-112` | 🔴 本次处理对象 |
| ④ | `core/session/db.py` | **独立旧库** `~/.quantnodes-research/sessions.db` | `db.py:22`、`34-36`；INSERT `db.py:202`、`311` | 🔴 本次退役对象 |

门禁白名单现状（`tests/test_architecture_gates.py:259-265`）把 ① ② ③ ④ 四个文件全部登记为已知写者，并注明 ③ ④「到期随 C1 退役」。**白名单只能缩短**（同文件 `:257` 注释）。

### 1.2 MemoryManager 直写 messages 的确切位置

```
SQLiteStore.insert_message              memory_manager.py:249-280
  INSERT INTO sessions (upsert)         memory_manager.py:261-268
  INSERT INTO messages                  memory_manager.py:270-275   ← 直写 #1
SQLiteStore.delete_session              memory_manager.py:310-314
  DELETE FROM messages                  memory_manager.py:312        ← 破坏性
  DELETE FROM sessions                  memory_manager.py:313        ← 连带删会话行
SQLiteStore.compact_messages            memory_manager.py:324-364
  DELETE FROM messages ...              memory_manager.py:348-352
  INSERT INTO messages (summary)        memory_manager.py:355-359   ← 直写 #2
```

调用链：`UnifiedMemoryManager.append`（`:624-647`）→ `_primary_append`（`:737-776`）→
`self._backend.insert_message`（`:748`/`:752`）。`compact`（`:673-705`）→ `backend.compact_messages`（`:680`）。
`clear`（`:664-671`）→ `backend.delete_session`（`:666`）。

**关键细节（实测，决定事件化怎么写）**：

- seq 是 `len(existing)`（`memory_manager.py:747`），不是 `max(seq)+1`。而 projector 的 seq 是
  `len(state.messages) + 1`（`projection_handlers.py:105`、`:146`、`:184`）。
  **同一会话里混写必然撞 seq**：实测在带 `uq_messages_session_seq` 唯一索引的库上，
  `mm.append` 抛 `IntegrityError: UNIQUE constraint failed: messages.session_id, messages.seq`，
  被 `append` 的兜底捕获后降级成 `emergency_xxxxxxxx`（`memory_manager.py:633-647`），消息**静默丢失**。
  （真实库当前没有这个唯一索引——`PRAGMA index_list(messages)` 只有 3 个非唯一索引；
  该索引只由 `scripts/backfill_seq.py:206-209` 在 `--apply` 时创建。这是一颗未引爆的雷。）
- `insert_message` 会 upsert 一行 `sessions`（`:261-268`），其中 `user_id='anonymous'`、
  `title` 取默认值——CLI 会话因此能通过 `messages.session_id` 的 FK。
- 直写行 `message_type` 默认 `"assistant"`（`:252`），**role='user' 的行也会带 message_type='assistant'**。
  实测：`('user','assistant',0)`。与统一库现有不变量（`role='user' ⇒ message_type='user'`，
  真实库实测 109/109 行满足）冲突。

### 1.3 这些行被谁读（消费方矩阵）

| 消费方 | 位置 | 读法 | 现状 |
|---|---|---|---|
| CLI/TUI 历史注入 | `cli/tui/session.py:259-268`（旧路径）、`:326-332`（Gateway 路径） | `mm.get(session_id)` → `SQLiteStore.list_messages`（`memory_manager.py:657`） | **活**，是 TUI 唯一的对话记忆来源 |
| CLI/TUI 写入 | `cli/tui/session.py:296-304`、`:364-369` | `mm.append` | **活** |
| TUI Ctrl+L 清上下文 | `cli/tui/app.py:648-660` | `mm.clear` | 活（但语义见 D7） |
| Web `/clear` | `api/routers/slash_commands.py:834-838` | `mm.clear` | 活（**已知会炸 FK**，实测） |
| Web 旧 chat 后台任务 | `api/routers/chat.py:421-446` | `mm.append`×2 | **死代码**（`_run_agent_loop_background` 零调用方） |
| token 估算器（内部） | `memory_manager.py:506-517` | `backend.list_recent_sessions` + `list_messages` | 活（只读，采样最近 10 会话） |
| Web 读取面 | `api/session/store.py:87-110` → `projector.project_to_messages`（`:250-274`） | **不读 mm** | 实测：projector 路径看到 0 条 mm 写入的行；`SR_EVENT_LOG_READ=0` 走 DB 才看得到 1 条 |
| HTTP `GET /api/chat/session/{id}/messages` | `web_session.py:374-447`；挂载前缀见 `api/app.py:205` | 直读 `messages` | 看得到 mm 行（同表），但 order by `seq` 会很怪（mm 行 seq=0） |

**结论**：MemoryManager 的 messages 数据只有 CLI/TUI 自己读；Web 进程既不读也不（有效地）写。
这意味着事件化的**行为等价面只在 CLI/TUI**，而 CLI/TUI 是开发工具（`docs/legacy-sessiondb-tech-debt.md:46-48`），
等价性证据可以务实（见 §8）。

### 1.4 事件化前必须知道的三处实测交互

**（a）projector 的"全量 flush"会删掉非事件来源的行**

`ProjectionStore.flush` 全量模式：`msg_ids` 之外的行会被 `DELETE FROM messages WHERE session_id=? AND id NOT IN (...)`
（`projection_store.py:231-237`）。实测：库里 1 条 mm 写的行 + 新 EventStore（缓存未命中 → 全量）
emit 一条 web 消息后，**mm 的行消失，只剩 web 行**。这是"messages 必须全部可由 event_log 推导"这条隐式不变量的
物理执法者，也是事件化的最强理由。

**（b）`mm.clear()` 之后 projector 永久 flush 失败**

实测序列（统一库、真实 schema）：
1. projector 写 2 行（正常）；
2. `mm.clear("s1")` → messages=0、**sessions 行也没了**；
3. `/clear` 随后 emit 的 `message_received` + `assistant_message` → `Projector flush failed ... FOREIGN KEY constraint failed`（×2），messages 仍为 0；
4. `mm.append` 重建 sessions 行后，下一条 projector 事件才恢复。

对应代码：session 删除 `memory_manager.py:313`；flush 打开 FK `projection_store.py:222`；
`messages.session_id` FK CASCADE `web_session_db.py:82`。`test_chat_slash_commands.py:72-127`
正是因为 mock 掉了 `clear` 才没暴露——测试把 `get_default_memory_manager` 换成了空壳（`:83-89`）。

**（c）schema 创建顺序仍有两个 DDL 主人**

`SQLiteStore._init_schema`（`memory_manager.py:145-191`）只建 `sessions`/`messages`，
**不建 `message_parts` / `attempts` / FTS**；`web_session_db._ensure_schema`（`:37-221`）建全套。
实测：先由 MemoryManager 打开一个新库（CLI 场景）→ 表只有 `sessions, messages` →
之后 API 的 projector flush 报 `no such table: message_parts`，消息落不了库。
`memory_manager.py:145-153` 的 docstring 自己承认这是历史 DDL 分叉。

---

## 2. 事件化方案

### 2.1 两个候选，按"改动最小"裁决

| 维度 | 方案 A：发事件经 projector | 方案 B：直调 `ProjectionStore` |
|---|---|---|
| 新代码 | ~40 行（emit 映射 + emitter 注入 + session 兜底） | ~60-100 行（自持 `ProjectedSession`、touched 集合、缓存与失效） |
| 复用 | `EventStore.emit`（`event_store.py:228-274`）、`_should_flush` 边界表（`:580-592`）、`Projector.project_incremental`（`projector.py:86-122`）、`ProjectionStore.flush`（`projection_store.py:180`） | `ProjectionStore.flush`（仅此一个） |
| 语义正确性 | projector 自己维护 fold + 增量，跨实例可见（每次 `load_events(after_seq)` 都读库，`projector.py:108`） | MM 必须自己保证状态完整，否则全量 flush 删别人行（§1.4a） |
| 分层 | core → core（`EventStore` 在 `core/agent`）；projector 由 `EventStore` 懒导入（`event_store.py:569-578`），W-D 不需要新增任何 core→api 依赖 | core → api 直依赖 `api/session/projection_store.py`（比 A 更深，且新增一条 core→api 边） |
| 事件日志 | 顺带获得 replay / fork / trace | 无事件，`event_log` 与 messages 对不上 |
| 写者门禁 | messages 仍只由 `projection_store.py` 写 | 同左（但需给 ProjectionStore 加 append 面 = 改设计写者） |

**选 A。** 方案 B 被否的四条硬理由：(1) `ProjectionStore` 没有"追加一条消息"的 API，只有全量状态 diff；
(2) 它要求调用方提供完整 `ProjectedSession`，这就是把 `Projector` 的职责搬到 MemoryManager（违 less-is-more §6.5 规则 4：
平行的第二套实现）；
(3) 全量 flush 的删除语义会在状态不全时误删（§1.4a 实测）；
(4) MemoryManager 一旦持状态，还要处理 `compact.ended` 的 `"*"` 全量重写（`projector.py:148-159`），复杂度反超 A。

### 2.2 事件映射（复用现有 EventType，零新增事件名）

| MemoryManager API | 现实现 | 事件化后 | projector 落库形状 |
|---|---|---|---|
| `append(sid,"user",content,metadata)` | INSERT messages（`:270`） | `EventStore.emit(sid,"message_received",{message_id,user_message_id,role:"user",content,metadata})` | `_on_message_received`（`projection_handlers.py:90-114`）→ role=user / message_type=user / seq=len+1 |
| `append(sid,"assistant",content,metadata)` | 同上 | `emit(sid,"assistant_message",{message_id,content,message_type:"assistant",metadata})` | `_on_assistant_message`（`:116-161`）→ role=assistant |
| `get()` | `SQLiteStore.list_messages`（`:657`） | **不变**（只读，同表；projector 写完它就读得到） | — |
| `clear()` | DELETE messages + sessions（`:312-313`） | `emit(sid,"compact.ended",{summary:"…",reason:"manual"})`（**不**带 `compacted_until_seq`） | `_on_compact`（`:600-690`）→ system/compaction 标记行，`compacted_until_seq` 落 0 = "全部历史被覆盖"；历史过滤见 `history_convert.py:56-83`、`:162-167` |
| `compact()` | DELETE+INSERT（`:348-359`） | **删除方法**（零生产调用；`/compact` 已由 `SessionService.compact_history`（`service.py:1201-1315`）经 `compact.ended` 实现） | — |
| `list_recent_sessions` / `health_report` / cache / 应急缓冲 | — | **不变** | — |

`_should_flush` 已包含 `message_received` / `assistant_message` / `compact` / `compact.ended` / `iter_start`
（`event_store.py:580-592`），**无需改事件桥**。

**为什么 `clear` 用 `compact.ended` 而不是删行**：
- 复用现成 handler 与 `compacted_until_seq` 语义，零新事件类型（新增事件类型要同步改
  `core/events/event_v2.py` 注册表 + 前端 union，`stage-c1-team-contract-20261028.md` §2.2 明确要求）；
- Web 侧 `/clear` 的文档语义本来就是"清空 LLM 上下文、保留历史消息"（`slash_commands.py:821-829`），
  现状的 DELETE 与文档相反——这是 bug，不是契约；
- 删 session 行会连锁摧毁后续所有写入（§1.4b），而标记事件不会。

**注意（需协调者确认，§9 D1）**：`clear` **不要**自己算 `compacted_until_seq`。理由：
``EventStore.last_seq``（`event_store.py:393-395`）返回的是 **event seq**，不是 message seq，
用它推边界只在"每消息一事件"时近似成立；而 `_on_compact` 在 `compacted_until_seq is None`
且无 `messages` 列表时落到 `compacted_until_seq = 0`（`projection_handlers.py:665-666`），
恰好就是"全部历史被这次 clear 覆盖"的语义。所以 clear 只发 `{summary, reason:"manual"}`，
**零新读数、零新事件类型**。`compact()` 方法直接删除（它零生产调用，见 §7）。

### 2.3 实现形状（最小 diff）

```python
# core/agent/memory_manager.py（示意，非最终代码）
class UnifiedMemoryManager:
    def __init__(self, ..., emitter: Callable[[str, str, dict], None] | None = None):
        self._emitter = emitter           # 注入优先（测试/API 可传）
        self._lazy_event_store = None     # 持有实例，close() 才关得掉

    def _emit(self, session_id, event_type, data):
        if self._emitter is not None:
            return self._emitter(session_id, event_type, data)
        if self._lazy_event_store is None:
            from .event_store import EventStore        # 懒导入，规避循环
            self._lazy_event_store = EventStore(
                self._db_path, flush_to_messages=True,
            )
        return self._lazy_event_store.emit(session_id, event_type, data)

    def close(self):
        if self._lazy_event_store is not None:
            self._lazy_event_store.close()   # 需 event_store 新增 6 行 close()
            self._lazy_event_store = None
        if hasattr(self, "_sqlite_store"):
            self._sqlite_store.close()
```

> 删 `compact()` 会连带删掉 5 条测试（`test_memory_manager.py:178-213` 三条 +
> `test_memory_manager_comprehensive.py:106-127` 两条）。它们测的是"直写 compaction"这个即将消失的实现，
> 按契约 §2.2「允许缩水但必须显式」：**同一提交内删除 + `c1_inventory freeze`**，由协调者审阅 diff。

- 循环导入：`event_store.py:27` 在模块级 import memory_manager，所以 memory_manager **只能懒导入** event_store
  （`event_store.py:573` 懒导入 projector 是同款先例）。
- `append` 先确保会话行存在：复用现有 `INSERT INTO sessions ... ON CONFLICT` 语句
  （`memory_manager.py:261-268`）抽成 `_ensure_session_row`——**sessions 表不在门禁白名单内**，且
  `event_log.aggregate_id` 的 FK（`event_schema.py:67`）与 flush 的 `messages.session_id` FK 都依赖它。
- `metadata` 透传：projector 目前只把 `agent_id` 写进 `metadata_json`
  （`projection_handlers.py:147-150`、`:185-188`）。`MemoryManager.append(metadata=...)` 是公开签名
  （`memory_manager.py:526-530`），要保真需在 handler 里加 3 行 `metadata=data.get("metadata")` 合并——
  **`api/session/projection_handlers.py` 不在 W-D 所有权内，请求协调者代改**（§6）。
  若协调者拒绝，则 `append` 的 metadata 参数降级为"仅事件内可见、不落 messages.metadata_json"，
  并同步改 `test_memory_manager*` 的断言（现断言见 `test_memory_manager_comprehensive.py:69-74`）。

### 2.4 事件化必须同时做的两件事（否则是数据迁移事故）

1. **先回填历史**：现状库里已有 mm 直写的行（真实库实测 209 条 messages / 6247 条 event_log，
   **0 条** messages 行缺少对应事件——因为 mm 直写在真实库里其实没写过；但 CI/开发者本地库可能不同）。
   回填复用 `api/session/backfill_event_log.py:36` 的 `backfill_event_log(db_path, session_id=None, force=False)`：
   从 `messages + message_parts` 生成 `message_received/text.started/tool_call/assistant_message/compact` 事件
   （`:140-297`）。**注意它的 CLI 默认路径是旧文件名**（`:335-338` 指向
   `quantnodes_strategy_research_user.db`），必须显式 `--db-path` 指向统一库。
   回填后，下一次全量 flush 会用 `seq=1..N` 重写行（`_on_message_received` 的 seq 推导），
   顺带修掉 mm 行的 `seq=0` / `message_type` 错配。
2. **schema 单一主人**：`SQLiteStore._init_schema`（`memory_manager.py:145-191`）改为懒调
   `api.session.web_session_db._ensure_schema(conn)`（`:37`），删掉自己那份 messages/sessions DDL。
   理由：实测"先建库者赢"会让 projector flush 因缺 `message_parts` 失败（§1.4c）。
   `_ensure_schema` 已是唯一权威 DDL（含 `message_parts` / `attempts` / FTS / 版本化迁移 `:224-262`）。
   **这是一条新增的 core→api 边**（`core/agent/memory_manager.py` → `api/session/web_session_db.py`）。
   现有先例只有 `EventStore._get_projector`（`event_store.py:569-578`）——同样在 core/agent 里懒导入 api.session。
   架构门禁目前只禁 core→study / gateway→fastapi（`test_architecture_gates.py:73-119`），不违规；
   但若协调者认为 core→api 不可接受，替代方案是**把建表 SQL 从 MemoryManager 彻底删掉**、
   要求调用方先建库（CLI/TUI 启动时调一次 `web_session_db._ensure_schema`）——同样是 5 行，且不新增依赖边。
   W-D 倾向后者（更干净的依赖方向）。

---

## 3. `SQLiteStore` 与统一库：保留还是合并？

**结论：文件层面已经合并（同一个 `resolve_session_db_path()` 文件）；类层面保留 `SQLiteStore`，**
**但把它的 messages 写能力全部退役，只留"SQLite 底座 + 只读"。**

证据：

| 事实 | 证据 |
|---|---|
| 同一个文件 | `UnifiedMemoryManager.__init__` → `resolve_db_path`（`memory_manager.py:579`）→ `resolve_session_db_path`（`:71-100`）；`web_session_db._get_db_path` 也调它（`web_session_db.py:24-34`）；`EventStore.__init__` 同（`event_store.py:56`）。实测三者 resolve 到同一路径 |
| 但 `SQLiteStore` 是 EventStore 的地基 | `event_store.py:27` 直接 import `SQLiteStore`；`:63` 构造；`:120` 用 `_ensure_conn()` 建 event_log schema；`:193/:313` 用 `_lock` 串行化；`:64-81` 用 `health_check/auto_repair` |
| messages CRUD 只有 MemoryManager 用 | 生产：`memory_manager.py:657,666,680,748,752`（全部在 UnifiedMemoryManager 内）；测试：`test_memory_manager_comprehensive.py:203-227`、`test_memory_shared.py:111-126` |
| 它的 schema 与统一库不一致 | `memory_manager.py:145-191` 缺 `message_parts`/`attempts`/FTS；实测先建库会让 projector 报 `no such table: message_parts` |

**裁分**：

| `SQLiteStore` 成员 | 处置 | 理由 |
|---|---|---|
| `_ensure_conn`、`_lock`、`health_check`、`auto_repair`、`close` | 保留 | EventStore 依赖 |
| `_init_schema` | 改为代理 `web_session_db._ensure_schema` | 消除 DDL 双主 |
| `insert_message` | **删除** | 事件化后无调用方 |
| `delete_session` | **删除** | `clear` 改标记事件后无调用方 |
| `compact_messages` | **删除** | `compact` 改事件后无调用方 |
| `list_messages` | **保留（只读）** | `mm.get` 与 `DynamicAvgTokenEstimator`（`:507-511`）读它；且读路径不违反"单写者" |
| `list_recent_sessions` | 保留（只读） | 估算器用（`:507`） |
| `InMemoryStore` | 保留 | 降级模式 + `EventStore` 的降级后端（`event_store.py:73`）+ 现有测试 |
| `SessionCache` / `SessionLockMap` / `DynamicAvgTokenEstimator` | 保留 | 是真实产品能力（缓存/健康报告），且 `SessionCache` 被 EventStore 共用（`event_store.py:85`） |

**不删 `UnifiedMemoryManager`**：它是 CLI/TUI 的历史读入口（`cli/tui/session.py:264,331`），
且 `MemoryManagerFactory` 单例被 `tests/conftest.py:307-312` 用于释放连接。删它要同时改 CLI 历史注入，
超出"改动最小"。事件化后它退化为"事件写入 + 只读缓存 + 应急缓冲"的薄门面。

---

## 4. `core/session/db.py` 退役路径

### 4.1 全部消费方（逐点，带行号）

| # | 消费方 | 位置 | 用法 | 是否有生产调用 |
|---|---|---|---|---|
| 1 | admin metrics 端点 | `api/routers/admin.py:164-187`（import `:179`，构造 `:181`） | 读 `db.metrics_logger.get_stats()/get_recent()` | ✅ HTTP `/api/admin/metrics` |
| 2 | CLI `session stats` | `cli/commands/session.py:14-43`（import `:16`） | `metrics_logger` | ✅ `strategy-research session stats` |
| 3 | CLI `session list` | `cli/commands/session.py:46-63`（import `:48`） | `list_sessions` | ✅ |
| 4 | CLI `session show` | `cli/commands/session.py:66-98`（import `:68`） | `get_session` + `get_messages` | ✅（**且已崩**：`session.metadata_json` 不存在，实测 `AttributeError`） |
| 5 | CLI `session search` | `cli/commands/session.py:101-119`（import `:103`） | `search_messages`（FTS5） | ✅ |
| 6 | CLI `session delete` | `cli/commands/session.py:122-139`（import `:124`） | `get_session` + `delete_session` | ✅ |
| 7 | REPL `/history` | `cli/commands/slash_session.py:36-65`（import `:44`） | `list_sessions(limit)`，**需 `STRATEGY_RESEARCH_SESSIONS_DB` 环境变量**（`:24-29`），未设置直接返回 0（`:39-42`） | ✅（但默认不可用） |
| 8 | REPL `/search` | `cli/commands/slash_session.py:68-100`（import `:79`） | `search_messages`，同样受 env 门控（`:74-77`） | ✅（默认不可用） |
| 9 | TUI 启动探测最近会话 | `cli/tui/app.py:222-238`（import `:230`） | `list_sessions(limit=1)`，读 `sessions[0].title` | ✅（`Session` 无 `title` 字段 → 恒 `"(untitled)"`；**实测**） |
| 10 | TUI 恢复最近会话 | `cli/tui/app.py:254-285`（import `:257`） | `list_sessions` + `get_messages`；读 `sessions[0].session_id` | ✅（`Session` 无 `session_id` 字段 → 恒 `None` → **函数在 `:267-268` 直接 return，恢复功能实际从未工作**；**实测**） |
| 11 | MCP `list_sessions` | `core/mcp/server.py:634-653` | `SessionDB().list_sessions()` | ✅（MCP 工具） |
| 12 | MCP `search_messages` | `core/mcp/server.py:641-662` | `SessionDB().search_messages()` | ✅ |
| 13 | `SessionManager` | `core/session/manager.py:8,14-83` | 包装 `SessionDB` | ❌ **零生产构造点**（grep 全仓只有 `test_session.py:174-227`） |
| 14 | `SessionMemoryHook` | `core/hooks/bundled/session_memory.py:22-77`（写 `:64-73`） | `session_manager.add_message()` 归档 | ❌ **零生产构造点**（grep 全仓只有 `tests/test_session_memory.py`） |
| 15 | 测试 | `test_session.py`、`test_session_triggers.py`、`test_session_memory.py`、`test_session_metrics.py`、`test_session_rate_limiter.py`、`test_cli_session_cmd.py`、`test_integration.py:44-...`、`test_admin_metrics.py:27-105`、`test_p2_e2e.py:198-280`（mock）、`test_architecture_gates.py:287-300` | — | — |

> 注：`core/agent/context.py:224-233` 的 `_session_manager.search_messages()` 召回链
> **没有任何生产装配**——`ContextBuilder(session_manager=...)` 的参数只由 `AgentLoop` 转发
> （`loop.py:473`），而 `AgentLoop.__init__` 的 `session_manager` 只由 `role_factory` 转发
> （`role_factory.py:164`），域层调用方（`autoresearch.py:615`、`dag_planner.py:172`、
> `phase_engine.py:865`、`runner_knowledge.py:41`）**没有一个传它**（grep `session_manager=` 全仓仅 3 处，
> 全是转发语句）。→ 这条链是死代码，删除参数，无替代入口。

### 4.2 逐个替代入口

**原则（less-is-more）**：先退役"零调用/默认不可用/已崩"的，再为剩下的找现有函数；确实没有的，先列查过的入口。

| 消费方 | 推荐处置 | 替代入口（复用哪个现有函数） | 若必须保留则缺什么 |
|---|---|---|---|
| #1 admin metrics | **退役端点**（或改数据源） | 现有 `EventStore.health_report()`（`event_store.py:594-612`：degraded/backend/cache 命中率/驱逐数）就是现代替身；`GET /api/admin/metrics` 无前端消费者（前端只有生成的类型 `webui/frontend/src/api/types.gen.ts:504`，无调用点） | 若保留旧响应契约，需新建"写入指标"来源（persona: 让 projector 计数）= 新增代码，违 less-is-more |
| #2 `session stats` | **随端点一起退役** | 同上（`EventStore.health_report`；或 `scripts/c1_gate.py` 式的运维脚本） | — |
| #3 `session list` | 改用统一库 | **无现成函数**（`web_session.list_sessions` 是 FastAPI 端点，`Request` 绑定，`web_session.py:250-261`）。查过：`SessionStore`（`store.py`）只有 `get_messages`/attempt CRUD；`Projector.ProjectedSession` 不含会话元数据；`PlatformStore.list_sessions`（`platform_store.py:445`）只在 per-session 目录模式可用（`_get_global_conn` 单库模式直接 raise，`:149-154`） | 一个 5 行 helper（`SELECT id,title,updated_at ... ORDER BY updated_at DESC`）放进 `core/session/` 或 `api/session/store.py`，供 #3/#7/#9/#10/#11 共用 = **唯一值得新增的函数** |
| #4 `session show` | 改用统一库 | ✅ `SessionStore.get_messages(session_id, limit)`（`store.py:87-110`，默认走 projector） | 顺带修 `metadata_json` AttributeError（改读 `session.metadata`）；会话元数据用 #3 的 helper |
| #5 `session search` | **退役** | 查过：`web_session.search_sessions`（`web_session.py:585-653`）是 LIKE 全表扫描、内联在端点里（`:606-637`），无独立函数；`core/memory/fts5.py` 是**持久记忆**索引，不是会话消息 | 若要保留：抽出 `_like_search(conn, tokens, user_id, limit)`（协调者文件） |
| #6 `session delete` | **退役**（Web UI 已有软删 `DELETE /api/chat/session/{id}`，`web_session.py:301-316`） | — | — |
| #7 `/history` | 改用统一库 | 同 #3 的 helper | `STRATEGY_RESEARCH_SESSIONS_DB` 环境变量门控整个删除 |
| #8 `/search` | **退役** | 同 #5 | — |
| #9 TUI 最近会话标题 | 改用统一库 | 同 #3 的 helper（`limit=1`） | — |
| #10 TUI 恢复会话 | 改用统一库 | helper（取 id）+ `SessionStore.get_messages`（取历史） | 同时修 `session_id`/`title` 字段名（休 bug，不是新功能） |
| #11 MCP `list_sessions` | 改用统一库 | 同 #3 helper | — |
| #12 MCP `search_messages` | 改用统一库 | 同 #5 的 LIKE 逻辑（若 #5 退役，则 MCP 也退役） | — |
| #13 `SessionManager` | **删除**（零生产调用） | — | — |
| #14 `SessionMemoryHook` | **删除**（零生产调用；Markdown 归档只有它用） | — | — |
| #15 `context.py` 的 `session_manager` 召回 | **删除参数链**（`context.py:90,98,224-233`；`loop.py:347,403,473`；`role_factory.py:92,164,188,239`；`executor.py:33`；`engine_common.py:74`） | 无（功能从未接线；`PersistentMemory` 召回保留，`context.py:215-222`） | 这是跨 4 个 owner 的"删参数"改动，协调者派单 |

### 4.3 退役顺序（每步可独立回退，且门禁只缩短）

```
Step 0  基线确认（协调者）
        · c1_gate / c1_inventory 冻结事实；确认 §8 的子集基线
        · 不动代码

Step 1  MemoryManager 事件化（W-D 主改）
        · memory_manager.py：append 改发事件、clear 改发 compact.ended 标记；删 insert/delete/compact SQL；
          _init_schema 代理 web_session_db._ensure_schema
        · 同提交：协调者把 test_architecture_gates.py:263 的 "core/agent/memory_manager.py" 从白名单移除
        · 同提交：projection_handlers.py metadata 透传（协调者代改，3 行）
        · 验收：test_memory* + test_projector* 全绿；真实库副本上跑一次回填 + 一条 append 验证
        · 回退：revert 单个提交（MemoryManager 恢复直写，白名单同步恢复）

Step 2  历史回填（一次性脚本调用，不入库新代码）
        · python -m strategy_research.api.session.backfill_event_log --db-path <统一库> [--force]
        · 验收：event_log 覆盖 messages 全部 id（§10 复验脚本）

Step 3  消费方迁移（协调者代改，W-D 出清单）
        · 优先改"已崩/默认不可用"：TUI resume（#10）、session show（#4）、/history（#7）
        · 再改 MCP（#11/#12）、admin metrics（#1/#2）
        · 每步保留 §2.1 门一：旧入口在同一提交内被替换后才删
        · 验收：test_cli_session_cmd / test_session_dispatch_header / test_tui_event_routing / test_admin_metrics

Step 4  删零调用体（W-D 主改 + 协调者代改）
        · W-D：core/session/{manager,models,metrics,rate_limiter}.py + core/hooks/bundled/session_memory.py
        · 协调者：cli/commands/session.py、cli/commands/slash_session.py、cli/__init__.py:29-34,656-684,826,856,885-899
        · 协调者：api/routers/admin.py 的 /metrics、core/mcp/server.py:634-662
        · 协调者：context.py/loop.py/role_factory.py/executor.py 的 session_manager 参数链、engine_common.py:74（W-S）
        · 同提交：删除对应测试（见 §8.3），重新冻结 inventory（c1_inventory freeze）

Step 5  删除 core/session/db.py + 门禁退役（W-D 主改 + 协调者改门禁）
        · W-D：删 core/session/db.py、core/session/__init__.py（或留空包给未来）
        · 协调者：test_architecture_gates.py
            - 白名单删除 "core/session/db.py"（:264）
            - 删除 test_legacy_session_db_stays_separate_from_unified_db（:287-300）
        · 同提交：重新冻结 inventory
        · 验收：G2（无测试静默消失）、G3（13→12 条门禁，需协调者同步更新文档计数）
```

**为什么 Step 5 必须最后**：`test_architecture_gates.py:287-300` 直接 import `core.session.db`，
文件删了它必红；而它又是"schema 不可合并"的唯一执法者。两者只能在同一个提交里成对消失。

**为什么 Step 1 必须在 Step 3 之前**：Step 3 改 CLI 读取面时，若 MemoryManager 还在直写，
新读路径（projector）看不到 mm 写入的历史 → 用户可见的历史"变短"。Step 1+2 先把数据搬进事件流。

---

## 5. 两套 schema 不可合并的约束怎么维持

### 5.1 约束内容（实测复验，非推测）

| 差异 | 旧栈（`core/session/db.py:48-57`） | 统一库（`web_session_db.py:74-84` + `:95` + `:146`） |
|---|---|---|
| 主键 | `id INTEGER PRIMARY KEY` | `id TEXT PRIMARY KEY` |
| 时间列 | `timestamp REAL` | `created_at REAL NOT NULL` |
| 消息类型 | 无 | `message_type TEXT` |
| 会话列 | `workspace`/`metadata_json` | `user_id`/`title`/`starred`/`tags_json`/`message_count`/`archived` |
| 序号 | 无 | `seq INTEGER NOT NULL DEFAULT 0` |
| FTS | `content=messages, content_rowid=id`（`db.py:59-65`） | trigram，`content_rowid='rowid'`（`web_session_db.py:308-316`） |

实测交叉写入（临时脚本，`sqlite3` 直连）：

```
legacy 表 + unified 行（TEXT id, created_at） → IntegrityError: datatype mismatch
unified 表 + legacy 行（timestamp 列）        → OperationalError: table messages has no column named timestamp
```

### 5.2 维持手段（分阶段，不靠"记得别改"）

| 阶段 | 约束 | 执法者 |
|---|---|---|
| 现在 → Step 4 | 两栈**不得指向同一文件** | `test_architecture_gates.py:287-300`（`legacy._default_db_path().resolve() != resolve_session_db_path().resolve()`）；`db.py:25-36` 的决策注释 + `_default_db_path()` 硬编码 `SESSIONS_DB`（`db.py:22`） |
| 现在 → Step 4 | 旧栈不得被统一库读写路径 import | 已是事实：`core/session/**` 不被 `api/session/**` / `core/storage/**` import（grep 确认）；`api/routers/admin.py` 等是反向依赖（api/cli → core.session） |
| Step 5 | 两栈合一的唯一方式是"旧栈消失"，**不是**改路径 | `SESSIONS_DB` 常量随 `db.py` 删除；`test_legacy_session_db_stays_separate_from_unified_db` 同提交删除 |
| Step 5 后 | 防止"旧栈复活" | 建议（可选）加一条文本门禁：`src/` 中不得出现 `.quantnodes-research/sessions.db` 字面量，或 `core/session/db.py` 不得重建 |

**关键裁定**：`S3` 修订里"两库不得指向同一文件"的结论在退役期仍然成立，**不要**为了"让 `/history` 看到 Web 会话"
而去改 `SESSIONS_DB` 指向统一库——那会立刻写坏表（`db.py:25-33` 已经论证过一次，实测再次确认）。

---

## 6. 文件级改动清单

> 所有权依据：契约 §5 + 本次任务书。**`api/routers/web_session.py` 及所有非 W-D 文件一律标注「请求协调者代改」**，
> W-D 只出 patch 建议，不直接改。

### 6.1 W-D 独占可写

| 文件 | 改动 | 阶段 |
|---|---|---|
| `src/strategy_research/core/agent/memory_manager.py` | ① `append` 改发 `message_received`/`assistant_message`；② `clear` 改发 `compact.ended`（不带 `compacted_until_seq`）；③ **删除 `compact()`**（零生产调用）；④ 删 `SQLiteStore.insert_message` / `delete_session` / `compact_messages`（`:249-364`）；⑤ 新增 `_ensure_session_row`（复用 `:261-268` 的 SQL）与 `_emit`（懒建 EventStore）；⑥ `_init_schema` 代理 `web_session_db._ensure_schema`；⑦ 新增 `event_emitter` 注入参数；⑧ `close()` 一并关闭懒建 emitter | 1 |
| `src/strategy_research/core/session/manager.py` | 删除（零生产调用） | 4 |
| `src/strategy_research/core/session/models.py` | 删除（`Session`/`SessionMessage` 只服务旧栈 + 已崩的 TUI 字段读取） | 5 |
| `src/strategy_research/core/session/metrics.py` | 删除（唯一生产者是旧栈 `db.py:210,322`） | 4 |
| `src/strategy_research/core/session/rate_limiter.py` | 删除（唯一消费者是旧栈 `db.py:97,194,303`） | 4 |
| `src/strategy_research/core/session/db.py` | 删除（605 行旧栈） | 5 |
| `src/strategy_research/core/session/__init__.py` | 删除或清空（随包保留则只留空 docstring） | 5 |
| `src/strategy_research/core/storage/**` | **零改动**（理由：messages 不归 PlatformStore；`event_log` 写入已由 `platform_store.append_event_row`（`:237-249`）覆盖；本次不碰 `event_schema.py`） | — |
| `src/strategy_research/core/hooks/bundled/session_memory.py` | 删除（零生产构造） | 4 |
| `src/strategy_research/core/hooks/bundled/__init__.py` | 去掉 `SessionMemoryHook` 导出（`:2,6`） | 4 |

### 6.2 请求协调者代改（按依赖顺序）

| 文件 | 改动 | 阶段 | 为什么必须协调者 |
|---|---|---|---|
| `tests/test_architecture_gates.py` | ① `:263` 移除 `"core/agent/memory_manager.py"`（Step 1 同提交）；② `:264` 移除 `"core/session/db.py"` + 删 `:287-300`（Step 5 同提交） | 1 / 5 | C 独占（契约 §5） |
| `src/strategy_research/api/session/projection_handlers.py` | `_on_message_received`/`_on_assistant_message` 透传 `metadata`（约 3 行） | 1 | 不在 W-D 所有权 |
| `src/strategy_research/api/routers/web_session.py` | **无必须改动**；可选：`persist_message` 保持现状（幂等 + compaction persister）。**不要**在本次扩大它的职责 | — | 任务书指定"请求协调者代改" |
| `src/strategy_research/api/routers/chat.py` | 删除死簇 `_run_agent_loop_background`（`:395-462`）、`_persist_user_message`（`:321-325`）、`_persist_assistant_message`（`:352-370`）、`_AutoTitle` 通知（`:327-349`）、`_PartAccumulator`（`:216-315`）、`_run_test_script`（`:464-523`）——这是 Web 进程里唯一的 `mm.append` 调用点 | 3 | api/routers |
| `src/strategy_research/api/routers/slash_commands.py` | `/clear`（`:820-880`）在 Step 1 后自动变成"发标记事件"路径，**代码无需改**；验证 `test_chat_slash_commands.py` 的 mock 是否仍成立（它 mock 了 `mm.clear`，`:83-89`） | 3 | api/routers |
| `src/strategy_research/api/routers/admin.py` | 退役 `/metrics`（`:164-187`）或改数据源为 `EventStore.health_report()` | 4 | api/routers |
| `src/strategy_research/cli/commands/session.py` | 退役 `cmd_session_stats/list/show/search/delete`，或按 §4.2 改统一库 | 3-4 | cli |
| `src/strategy_research/cli/commands/slash_session.py` | 退役 `/history` / `/search`（env 门控，默认不可用） | 4 | cli |
| `src/strategy_research/cli/__init__.py` | 移除 `session` 子命令注册（`:29-34,656-684,826,856,885-899`） | 4 | cli |
| `src/strategy_research/cli/tui/app.py` | ① resume 探测/恢复（`:222-285`）改统一库（§4.2 #9/#10，同时修 `session_id`/`title` 字段 bug）；② Ctrl+L（`:648-660`）去掉 `mm.clear` 调用（UI 清屏 + `ctx.history=[]` 即可） | 3 | cli |
| `src/strategy_research/cli/tui/session.py` | ① `build_chat_agent_loop`（`:272-278`）补 `event_bus=`（否则 TUI 压缩仍走 `persist_message` 直写，`:281-286`）；② 历史注入/持久化代码保持（Step 1 后自动事件化） | 3 | cli |
| `src/strategy_research/core/mcp/server.py` | `_register_session_tools`（`:634-662`）改统一库或退役 | 4 | core/mcp |
| `src/strategy_research/core/agent/context.py` | 删 `session_manager` 参数与 `_recall_relevant` 的会话分支（`:90,98,224-233`） | 4 | 未列所有权（默认协调者） |
| `src/strategy_research/core/agent/loop.py` / `role_factory.py` / `executor.py` | 删 `session_manager` 参数链（`loop.py:347,403,473`；`role_factory.py:92,164,188,239`；`executor.py:33`） | 4 | W-P |
| `src/strategy_research/core/study/engine_common.py` | 删 `build_agent_ctx` 的 `session_manager` 键（`:74`） | 4 | W-S |
| `src/strategy_research/core/agent/event_store.py` | 加 `close()`（关 `_ps` 与懒建 projector）——MemoryManager 懒建 emitter 后需要释放连接 | 1 | 未列所有权（默认协调者） |
| `docs/stage-c1-acceptance-20261028.md` / 契约 | 门禁计数 13→12 同步 | 5 | C |

### 6.3 明确不改

| 文件/对象 | 不动的原因 |
|---|---|
| `api/session/projection_store.py` | 设计唯一写者，本次零改动（方案 A 不碰它） |
| `api/session/projector.py`、`projection_models.py` | 复用，零改动 |
| `core/storage/platform_store.py`、`event_schema.py` | event_log 已单写者，零改动 |
| `core/agent/cache.py` | `SessionCache`/`SessionLockMap`/`ConfigValidator` 被 EventStore 共用，零改动 |
| `scripts/backfill_seq.py` | 不执行 `--apply`（会加唯一索引，反而与历史 mm 行冲突；事件化后不再需要） |

---

## 7. less-is-more 调查表（查过并排除 / 查过并复用）

> 契约 §6.5 规则 1：新函数前先列现有入口；列不出 = 禁止新写。

| 需求 | 查过的现有入口（file:line） | 结论 |
|---|---|---|
| 写一条消息（事件） | `EventStore.emit`（`event_store.py:228-274`） | ✅ **复用**（append 唯一新逻辑是载荷映射） |
| 写一条消息（直写） | `web_session.persist_message`（`web_session.py:70-172`） | ❌ **排除**：它是待收缩的兼容直写（任务硬约束"写入必须复用 projection_store"），且无 projector 事件 |
| 写一条消息（投影 API） | `ProjectionStore.flush`（`projection_store.py:180-302`） | ❌ **排除**：只有全量状态 API，会误删（§2.1/§2.4a） |
| 事件→状态 fold | `Projector.project_incremental`（`projector.py:86-122`）、`project`（`:182-215`）、`apply`（`:230-238`） | ✅ **间接复用**（经 EventStore.flush） |
| 读历史（给 LLM/CLI） | `SQLiteStore.list_messages`（`memory_manager.py:282-308`）、`SessionStore.get_messages`（`store.py:87-110`）、`Projector.project_to_messages`（`projector.py:250-274`） | ✅ `SQLiteStore.list_messages` 保留（dict 形状，零转换）；**排除** `SessionStore/Projector` 读法（返回 `Message` 数据类，需转换，且会丢掉 mm 尚未回填的历史） |
| 会话列表 | `web_session.list_sessions` 端点（`web_session.py:250-261`）、`PlatformStore.list_sessions`（`platform_store.py:445-456`）、`db.list_sessions`（旧栈） | ❌ 前两个不可复用（Request 绑定 / 仅 per-session 目录模式）；→ **唯一值得新增** 一个 5 行查询 helper（供 5 个消费方共用），见 §4.2 |
| 消息搜索 | `web_session.search_sessions`（`web_session.py:585-653`，LIKE 内联）、`core/memory/fts5.py` 的 `MemoryFTS5.search`、`db.search_messages`（FTS5） | ❌ 前两个不是会话消息检索（第二个是持久记忆）；→ 建议退役 CLI/MCP 搜索，不新写 |
| 压缩标记消息 | `_on_compact`（`projection_handlers.py:600-690`）、`loop_compaction._persist_compaction_event`（`loop_compaction.py:124-256`）、`CompactionMessage.to_llm_message`（`compaction_message.py:187-211`）、`SessionService.compact_history`（`service.py:1201-1310`） | ✅ **复用** `compact.ended` + `_on_compact`（clear/compact 都走它）；`/compact` 已由 `SessionService.compact_history` 负责，MemoryManager.compact 零生产调用 → 删 |
| 历史过滤（compaction 边界） | `_hidden_until_seq`（`api/session/history_convert.py:56-83`）、`_decide_kept_compactions`（`:35-53`） | ❌ **不搬进 core**（core 不得 import api）；clear 用 `_on_compact` 的 simple-marker 分支（`compacted_until_seq=0`）即可，Web 侧过滤逻辑本来就会读它 |
| 会话 schema 建表 | `web_session_db._ensure_schema`（`:37-221`） | ✅ **复用**（替换 `SQLiteStore._init_schema`），消除 DDL 双主 |
| 历史数据回填 | `backfill_event_log`（`backfill_event_log.py:36-137`）、`scripts/backfill_message_parts.py` | ✅ **复用**（一次性调用） |
| 会话归档（Markdown） | `SessionManager.archive_session`（`manager.py:71-83`）、`SessionMemoryHook.archive_session`（`session_memory.py:49-77`） | ❌ **排除/删除**：零生产构造；Markdown 写入无任何调用方 |
| 写入限流 | `RateLimiter`（`rate_limiter.py:23-100`） | ❌ **排除/删除**：只被旧栈 `add_message/add_message_batch` 用（`db.py:194,303`） |
| 写入指标 | `MetricsLogger`（`metrics.py:28-157`） | ❌ **排除/删除**：只被旧栈写（`db.py:210,322`）；运维替身是 `EventStore.health_report`（`event_store.py:594`） |
| token 估算 | `DynamicAvgTokenEstimator`（`memory_manager.py:452-517`）、`CascadeEstimator`（`cache.py:295-365`） | ✅ 保留（`MemoryManager` 自有；`CascadeEstimator` 无生产装配但被 `SessionCache` 类型引用，不动） |
| 应急缓冲 | `UnifiedMemoryManager._emergency_buffer`（`memory_manager.py:638-647,660,671,711`） | ✅ 保留（emit 失败的最后兜底；`health_report` 上报） |
| 会话级串行锁 | `SessionLockMap`（`cache.py:371-386`）、`_compact_locks`（`compact.py`） | ✅ 保留，不新写 |
| 事件 close | `EventStore` 无 `close` | ⚠ 需新增 6 行（§6.2），无现成替代 |

---

## 8. 测试子集与基线比对

### 8.1 W-D 子集（契约 §6.3）

契约给出的 glob：`test_context_hub` `test_context_providers*` `test_memory*` `test_session*` `test_sse_single_track`。
本次任务书要求 `test_context_hub test_memory* test_session* test_projection*`。并集后实际文件清单：

```
tests/test_context_hub.py
tests/test_context_providers_bridge.py
tests/test_sse_single_track.py
tests/test_trace_projection.py                 # 事件投影（projection 面）
tests/test_memory_enhance.py
tests/test_memory_fts5.py
tests/test_memory_manager.py
tests/test_memory_manager_comprehensive.py
tests/test_memory_persistent.py
tests/test_memory_retriever_injector.py
tests/test_memory_shared.py
tests/test_session.py
tests/test_session_controller.py
tests/test_session_dispatch_header.py
tests/test_session_id_init.py
tests/test_session_memory.py
tests/test_session_metrics.py
tests/test_session_queue.py
tests/test_session_rate_limiter.py
tests/test_session_reload_recovery.py
tests/test_session_state.py
tests/test_session_triggers.py
tests/test_projector.py
tests/test_projector_connection_reuse.py
tests/test_projector_delta_metadata.py
tests/test_projector_handlers.py
tests/test_projector_incremental.py
tests/test_projector_other_parts.py
tests/test_projector_snapshot.py
tests/test_projector_thinking.py
```

实测收集数：**408 条**（`--collect-only`，本机 py312）。仓库里不存在 `tests/test_projection*.py`
（投影面实际叫 `test_projector*` + `test_trace_projection`），已按上表纳入。

### 8.2 基线比对命令（本机实测可跑）

```bat
:: 0) 契约 §7.1：若在 worktree 里跑，先设 PYTHONPATH 指向本 worktree 的 src
set PYTHONPATH=D:\Github\quantnodes-strategy-research\src

:: 1) 跑子集，落日志
D:\tools\python312\python.exe -m pytest ^
  tests/test_context_hub.py tests/test_context_providers_bridge.py tests/test_sse_single_track.py tests/test_trace_projection.py ^
  tests/test_memory_enhance.py tests/test_memory_fts5.py tests/test_memory_manager.py tests/test_memory_manager_comprehensive.py ^
  tests/test_memory_persistent.py tests/test_memory_retriever_injector.py tests/test_memory_shared.py ^
  tests/test_session.py tests/test_session_controller.py tests/test_session_dispatch_header.py tests/test_session_id_init.py ^
  tests/test_session_memory.py tests/test_session_metrics.py tests/test_session_queue.py tests/test_session_rate_limiter.py ^
  tests/test_session_reload_recovery.py tests/test_session_state.py tests/test_session_triggers.py ^
  tests/test_projector.py tests/test_projector_connection_reuse.py tests/test_projector_delta_metadata.py ^
  tests/test_projector_handlers.py tests/test_projector_incremental.py tests/test_projector_other_parts.py ^
  tests/test_projector_snapshot.py tests/test_projector_thinking.py ^
  -q --tb=no --no-header > my.log 2>&1

:: 2) 集合包含判定（exit 0 = 无基线外失败）
D:\tools\python312\python.exe scripts/c1_gate.py my.log ^
  --files test_context_hub test_context_providers test_memory test_session test_projector test_sse_single_track test_trace_projection

:: 3) 测试集合不缩水（契约 §2.2）
D:\tools\python312\python.exe -m pytest tests --collect-only -q -m "not llm and not e2e and not slow" > collect.log 2>&1
D:\tools\python312\python.exe scripts/c1_inventory.py verify collect.log
```

### 8.3 基线事实（实测）

| 项 | 值 | 说明 |
|---|---|---|
| 冻结基线（`tests/baselines/pr-lane-failures.json`） | `known_failures` = 103；pytest totals: 93 failed + 7 error；commit `4b0f3de0` | 契约 §6.1 写"100 条 = 93+7"；JSON 里 `known_failures` 有 103 条（含 3 条 `post_freeze_additions`），**比对单位是 node id 集合**，不是数量 |
| W-D 子集内基线条目 | **2 条**：`test_session_dispatch_header.py::TestDispatchRefreshesHeaderAfterArun::test_header_stats_called_after_arun` / `..._called_twice` | 实测这 2 条在当前 HEAD **已经通过**（`pytest tests/test_session_dispatch_header.py` → 3 passed）。`c1_gate` 会把它们列进 "fixed"（informational），不算回归 |
| 本次子集实跑（第 1 次） | 1 failed, 399 passed, 1 skipped（48.7s，30 个文件） | 唯一失败 `test_session_triggers.py::TestTriggerPerformance::test_search_performance` |
| 本次子集实跑（第 2 次，同命令同环境） | **407 passed, 1 skipped（39.6s）**；`c1_gate.py` → `clean: no failures outside the baseline` | 同一份代码两次结果不同 = **flaky 已证实** |
| 该失败的定性 | **flaky / 环境阈值**，不是回归 | 证据链：① 单跑该用例 1 passed（1.70s）；② 整文件跑 1 failed；③ 全子集第 1 次 failed、第 2 次 passed；④ 断言是 `elapsed < 0.1`（`test_session_triggers.py:76`），Windows + 杀软 + FTS5 触发器下 100 次搜索偶发超 100ms。属于"性能基线过硬"，非数据面行为变化。**它不在基线集合内 → 一旦触发 `c1_gate` 会判 regression**。处置（§9 D6）：放宽为 `elapsed < 2.0`（与同文件 `test_bulk_insert` 的 `30.0` 同风格），或加入基线并写理由；二选一须协调者签字 |
| 跳过 | 1 条 `test_memory_manager.py:125`（本机无 `sqlite3` CLI，auto-repair 用例 skip） | 预期内 |
| 收集数 | 408（子集） / 9822（PR 道全量，契约 §8） | Step 4/5 删测试后必须 `c1_inventory freeze` 重冻，并在提交里展示 diff |

### 8.4 事件化必须新增的等价性测试（落为测试，不是口头判断）

| # | 断言 | 对应契约 |
|---|---|---|
| E1 | `mm.append(user/assistant)` 后，**HTTP 读路径**（`SessionStore.get_messages` 的 event_log 分支）能看到这两条——事件化前为 0（实测），事件化后为 2 | 门一（行为等价） |
| E2 | `mm.append` 后 `messages.seq` 为连续 `1..N`，且 `role='user' ⇒ message_type='user'` | 数据不变量 |
| E3 | `/clear` 后：messages 行**保留**（文档语义），session 行**存在**，随后一条 web 消息仍能 flush 成功（现在会 FK 失败） | 门一 + 回归防护 |
| E4 | 新建会话（MemoryManager 先建库）→ API projector flush 成功（现在 `no such table: message_parts`） | schema 单主 |
| E5 | 旧栈路径断言（`test_legacy_session_db_stays_separate_from_unified_db`）在 Step 1-4 期间保持绿 | 门 G3 |
| E6 | 回填脚本前后 `messages` 行数不变、`event_log` 覆盖全部 message id | Step 2 验收 |

> 注：E1 的"事件化前为 0"已用临时脚本实测（§10 复验 3），可直接写成对照测试。

---

## 9. 未决项（需协调者拍板，W-D 不自行决定）

| # | 决策 | 选项 | W-D 建议 |
|---|---|---|---|
| **D1** | `/clear` 语义变更 | (a) 发 `compact.ended` 标记（保留历史，符合 `slash_commands.py:821-829` 文档）(b) 保留物理删除（现状，会炸 FK） | **(a)**；同时把 TUI Ctrl+L 改为纯 UI 清屏（去掉 `mm.clear` 调用，`cli/tui/app.py:654-660`） |
| **D2** | `message_count` 归属 | (a) 让 projector 维护（api/session 变更）(b) 前端改用 `/messages` 返回的 `total`（`web_session.py:444-447` 已有）(c) 接受永久 0 | **(b)**：真实库实测 93/93 已为 0（实际每会话 1/2/4 条），前端若依赖它早就是坏的；`total` 是权威值，零后端改动 |
| **D3** | admin `/metrics` + `session stats` 退役 | (a) 退役 (b) 新建写入指标来源 | **(a)**；`EventStore.health_report`（`event_store.py:594-612`）是现成替代 |
| **D4** | 旧库 `~/.quantnodes-research/sessions.db` 存量数据 | (a) 不迁移（文档声明丢弃）(b) 一次性导出脚本 | **(a)**，但先在旧栈删除提交的 message 里注明；若用户有真实数据，另开迁移工单 |
| **D5** | CLI/MCP 会话列表可否新增 1 个 helper | 是 / 否（改为全退役） | **是**——5 个消费方共用，且无任何现有函数可复用（§7 已列全）；放在 `core/session/`（W-D 所有权内） |
| **D6** | flaky 性能测试处置 | (a) 放宽阈值 (b) 加基线 | **(a)**，`0.1 → 2.0`，与同文件风格一致；改测试需协调者（测试文件不在 W-D 独占清单，但属 W-D 验收子集） |
| **D7** | `projection_handlers.py` metadata 透传 | 加 3 行 / 接受 metadata 丢失 | **加 3 行**（MemoryManager.append 的公开签名承诺了 metadata） |

---

## 10. 复验命令（本文每条实测结论的可复现方式）

> 以下脚本均为**临时文件**（`%TEMP%\c1_b4_*.py`），未提交仓库；语义是"只读真实库 / 只在 tmp 目录写"。

1. **mm 与统一库同文件**（§0 D1）：
   ```
   D:\tools\python312\python.exe %TEMP%\c1_b4_probe.py
   → resolve_session_db_path == SR_SESSIONS_DB 覆盖值；mm.append 后 messages 行可见；
     mm.clear 后 messages=0 且 sessions 行=0；legacy 默认 != unified
   ```
2. **schema 互写失败 + flush 删除语义**（§5.1 / §2.4a）：
   ```
   D:\tools\python312\python.exe %TEMP%\c1_b4_schema_probe.py
   → legacy 表插 TEXT id: IntegrityError: datatype mismatch
     unified 表插 timestamp: OperationalError: no such column
     ProjectionStore.flush(full) 后只剩状态里的 1 条（'unrelated' 被删）
   ```
3. **读路径分叉 + 全量 flush 删 mm 行**（§1.3 / §1.4a）：
   ```
   D:\tools\python312\python.exe %TEMP%\c1_b4_readpath_probe.py
   → SessionStore(event_log) sees: 0 / SessionStore(db) sees: 1
     新 EventStore 一条事件后 rows 只剩 web 行
   ```
4. **/clear 的 FK 雪崩**（§1.4b）：
   ```
   D:\tools\python312\python.exe %TEMP%\c1_b4_seq_probe.py
   → STEP2 messages=0, sessions 行=0
     Projector flush failed ... FOREIGN KEY constraint failed (×2)
     STEP3 ack 事件后 messages 仍 0
   ```
5. **seq 冲突**（§1.2）：
   ```
   D:\tools\python312\python.exe %TEMP%\c1_b4_seq2_probe.py
   → mm.append → UNIQUE constraint failed: messages.session_id, messages.seq
     append 返回 emergency_xxxxxxxx
   ```
6. **schema 建表顺序**（§1.4c）：
   ```
   D:\tools\python312\python.exe %TEMP%\c1_b4_order_probe.py
   → mm 建的库只有 ['messages','sessions']；之后 EventStore flush 报 no such table: message_parts
   ```
7. **真实库清点（只读 `mode=ro`）**（§0 D8 / §1.3）：
   ```
   D:\tools\python312\python.exe %TEMP%\c1_b4_inventory.py
   → sessions=93 messages=209 message_parts=308 event_log=6247 snapshots=1 attempts=110
     message_type: assistant 82 / error 18 / user 109
     seq=0 行: 0；重复 (session,seq): 无；orphan(FK 断链): 0
     message_count 漂移: 93/93（全部会话 message_count=0，实际 65 会话 2 条 / 17 会话 4 条 / 11 会话 1 条）
     messages 行的 message_id 全部能在 event_log 找到（0 条孤儿）
   ```
8. **TUI 字段 bug**（§4.1 #9/#10）：
   ```
   D:\tools\python312\python.exe %TEMP%\c1_b4_attr_probe.py
   → Session fields: id,created_at,updated_at,workspace,metadata,messages
     getattr(s,'session_id')=None；getattr(s,'title')=MISSING；metadata_json → AttributeError
   ```
9. **子集基线比对（本机实测，两次）**：
   ```
   D:\tools\python312\python.exe -m pytest <§8.1 的 30 个文件> -q --tb=no --no-header > my.log 2>&1
   D:\tools\python312\python.exe scripts/c1_gate.py my.log ^
     --files test_context_hub test_context_providers test_memory test_session test_projector test_sse_single_track test_trace_projection
   → 第 1 次: 1 failed, 399 passed, 1 skipped (48.7s)；c1_gate 报 1 条 regression (test_search_performance)
     第 2 次: 407 passed, 1 skipped (39.6s)；c1_gate → clean（2 条基线条目列为 not-failing/informational）
   ```

---

## 附：与上游文档的差异（如实记录）

| 上游说法 | 本文实测 | 依据 |
|---|---|---|
| S3 文档 §1：「③ MemoryManager 自有 SQLiteStore 直写（CLI/goal 用）」 | goal **不**用 MemoryManager，只用路径解析 `resolve_session_db_path`（`core/goal/store.py:342-344`） | grep `get_default_memory_manager` 只有 chat/slash_commands/cli.tui |
| S3 文档 §1：「④ 是独立的第二套库」 | 成立；且旧栈的 12 个消费点里只有约 6 个真正可用（#7/#8 env 门控默认关闭，#4/#9/#10 因字段名不匹配已失效），另 2 个包装层（SessionManager / SessionMemoryHook）零生产调用 | §4.1 |
| 契约 §7.3「无 xdist」 | 成立；本次子集单进程 39.6s（第 2 次）/ 48.7s（第 1 次） | 实测 |
| `p04` 报告 §2.3「messages 四个写入者」 | 成立，但 ③ 写的是**统一库同表**（不是另一个文件），因此冲突是"同表双 seq 方案"而非"库分叉" | §1.2 实测 |
| 契约 §6.1「冻结基线 100 条」 | `pr-lane-failures.json` 里 `known_failures` 103 条（含 3 条 `post_freeze_additions`），`pytest_totals` 93+7 | 直接读 JSON |

---

**（完）**
