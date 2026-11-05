# S2 设计 — 库文件拆分（per-session 自包含 + 旧库只读兼容 + 惰性迁移）— 2026-10-27

> ## ⚠️ 已作废（2026-10-27，同日实测后推翻）
>
> 实测：真实库 5.34 MB / 83 会话 = **66 KB/会话**；文档中的 2GB 重开线需要
> **31,833 个会话**（每天 5 个 = 17.4 年，每天 20 个 = 4.4 年）。分库要解决的
> 膨胀问题当前不存在。
>
> 另：`messages` / `message_parts` 的 `FOREIGN KEY (session_id) REFERENCES
> sessions(id)` 在分库后无法满足（sessions 在全局库），必须镜像占位行或删除
> FK；加上 Projector / EventStore 单路径需改按会话解析、container 重接线、
> `_ensure_schema` 拆分，真实成本 5-7d（非规划的 3.2d）。
>
> **决定：分库推迟到出现实测需求（单文件 >500MB 或 >5000 会话）再做。**
> S2a 代码已回退（`df40ca9e`）；本文档保留为决策记录。


> 前置：S1 已完成（事件协议统一，PlatformStore 事件唯一写入者的存储基础就位）。
> 依据：P-2 定案「每 session 一自包含 SQLite（event+blobs+message+goal/todo），
> 全局小库（session 登记索引·goal/todo·config 指纹·迁移记录）」；数据面讨论稿
> §10.5「分界原则=跟会话走的进 session 库、跨会话的进全局库」。

---

## 1. 现状与目标

**现状**：单一 `.quantnodes_strategy_research_session.db`（12 表 + FTS + 触发器），
全项目 **33 个 `_get_db()` 调用点 / 11 个文件**共用一条线程本地连接。表分两类用途：

| 类 | 表 | 例子 |
|---|---|---|
| **会话级**（有 session_id 上下文） | messages / message_parts / attempts / event_log / snapshots / blob_refs / messages_fts* | service.get_messages、projector flush、EventStore |
| **跨会话**（无 session 上下文） | sessions（会话列表 CRUD / 归属鉴权 / 计数） | web_session 列表、`_fetch_session_owned`、hypothesis 按用户枚举 |

**目标（S2 末）**：

```
<workspace>/data/
├── platform.sqlite            # 全局：sessions（会话实体登记）+ schema_migrations
│                              #   + migrated_sessions（迁移台账）
└── sessions/<session_id>.sqlite  # 每会话自包含：messages/message_parts/attempts/
                                 #   event_log/snapshots + FTS 五件套 + 触发器
旧 .session.db                  # 只读兼容源：惰性迁移的数据来源，永不再写
```

归属依据（P-2 §10.5）：sessions 是跨会话实体 → 全局；其余跟会话走 → per-session。

## 2. 关键机制：连接路由器（新组件）

新增 `core/storage/session_files.py` —— **全项目唯一的库文件路由**：

```python
global_conn() -> sqlite3.Connection          # platform.sqlite（建表+迁移台账）
session_conn(session_id) -> sqlite3.Connection  # 惰性迁移 + 每会话文件 + FTS 确保
legacy_conn() -> sqlite3.Connection          # 旧库只读（迁移源；显式打开才存在）
migrated(session_id) -> bool                 # 台账查询
```

- **惰性迁移**（首次 `session_conn(sid)` 时）：台账无记录 → 查旧库有无该会话行
  （sessions/messages 任一）→ 有则事务内复制 `messages/message_parts/attempts/
  event_log/snapshots` 到新文件 → 记台账 → 校验行数（不一致即抛错不静默）；
- **新会话**：台账直接登记，文件按需创建；
- **FTS5**：每会话文件的 `_ensure_schema` 摘自 `web_session_db._ensure_schema`
  （DDL 与触发器**顺序敏感块原样搬**，该文件注释明确警告不可重排）；
- 线程本地连接缓存（沿用 `_get_db()` 的机制，键 = 文件路径）。

## 3. 分两步落地（每步独立可回退）

### S2a（本轮）：路由接线，行为零变化

引入路由组件 + 33 个调用点改道，但**路由内部仍全部返回旧库连接**——
纯结构重构，全套测试必须原样绿。改道分类：

| 调用点类型 | 改为 | 数量（约） |
|---|---|---|
| sessions 表操作（列表/CRUD/鉴权/计数） | `global_conn()` | ~20 |
| messages/message_parts/attempts/event_log 操作 | `session_conn(session_id)` | ~13 |

判别方法：逐调用点看 SQL 触及的表（§1 表）。少数横跨的（如 service 先查 sessions
再写 messages）拆成两次取连接。

**同时**：`web_session_db` 的 `_ensure_schema` 拆为 `ensure_global_schema`（sessions
表）与 `ensure_session_schema`（会话表 + FTS），路由分别调用；EventStore 的
PlatformStore 兼容模式（S1 的 `db_path=`）在 S2a **保持不变**（仍指旧库）——
S2b 才切 per-session 模式。

### S2b（下轮）：翻转路由内部

1. `session_conn` 指向 `data/sessions/<sid>.sqlite`（惰性迁移生效）；
2. `global_conn` 指向 `data/platform.sqlite`；
3. 旧库转为只读（路由不再返回它的可写连接）；
4. EventStore 的 PlatformStore 切 `data_dir=` per-session 模式（S1 的接口已备好）；
5. `resolve_session_db_path` 降级为「迁移源路径」函数，新代码不再用它定位写入。

## 4. 路径基座

`<workspace>/data/`（`SR_WORKSPACE_PATH` 解析，与 D-4 研究目录同根；测试走
tmp workspace，天然隔离）。`SR_SESSIONS_DB` 环境变量保留：显式指定时整体覆盖
旧库路径（迁移源），不改变新布局位置。

## 5. 验收

**S2a（本轮）**：
1. 33 个调用点全部经路由（`git grep "_get_db()"` 在 api/ 下归零，门禁测试钉住）；
2. 行为零变化：webui/chat/sse/projector/service 全家族测试原样绿；
3. 路由单测：global/session 两入口返回正确 schema 的连接（当前都指旧库，
   断言表存在性）；FTS 触发器存在。

**S2b（下轮，预告）**：新旧会话并行可读；旧会话首次访问自动迁移且行数校验一致；
金丝雀保持绿；`test_webui_*` 全绿。

## 6. 明确不做（S2 边界）

- 写入者归一（S3：memory_manager / web_session / core.session.db 的 messages
  直写仍保留，S2b 后它们经路由写到 per-session 文件）；
- PlatformStore 的 blobs/工件登记接线（0.4 后续）；
- goal/todo 入库（P-2 后续）；
- 删除旧库文件（永不——只读档案）。

## 7. 风险

| 风险 | 缓解 |
|---|---|
| 33 个调用点改道遗漏/错分类 | S2a 零行为变化 + 全家族回归；每个调用点按 SQL 触及表判别并逐一核对 |
| FTS DDL 搬移破坏顺序敏感块 | 原样整块搬（web_session_db 注释警告），S2a 断言触发器存在 |
| service/store 等经 `from ..routers.web_session import _get_db` 反向取连接 | 一并改道到路由（消 api 内部循环引用，属结构改善） |
| 线程本地缓存键 | 键 = 文件路径（沿用现机制），per-session 后键数量增多但每连接轻量 |
