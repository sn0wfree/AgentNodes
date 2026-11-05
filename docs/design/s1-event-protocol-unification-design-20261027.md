# S1 设计 — 事件协议统一（0.4 第一步）— 2026-10-27

> 前置：可行性报告（`p04-data-plane-convergence-feasibility-20261027.md`）判定
> 收敛可行但需先统一事件协议。本文件是 S1 的实施设计，含一个**对可行性报告的
> 重要修正**（§1），整体比原估便宜。

---

## 1. 对可行性报告的修正：不存在「两套 schema 搬迁」

报告 §2.2 判断「旧 EventStore 表与 PlatformStore 表 schema 不同，收敛必须先合并协议」。
深入核实后事实是：

- **`core/storage/event_schema.py` 已经是事件 schema 的单一事实源**（P0-1 A1+A4
  就消重过三处 DDL 分歧），canonical 表为 8 列：
  `id, aggregate_id, seq, type, data_json, time_created, parent_event_id, branch_id`，
  带 `UNIQUE(aggregate_id, branch_id, seq)`、FK、两个索引、幂等迁移器
  （`ensure_event_log_schema` + `migrate_event_log_unique` + `_add_column`）；
- **真实旧库的 `event_log` 已经就是这张 canonical 表**（第 8 列 branch_id 在此前
  清点输出中被截断遗漏）；
- 真正「跑偏」的是 **PlatformStore 自己手写的简表**（`seq/ts/kind/data_json/run_id/
  schema_v`）——它是 event_schema.py 文档里警告过的「第四处 DDL 重复」。

**修正后的方向**：不是「把旧表改成 PlatformStore 的表」，而是 **PlatformStore 采纳
canonical 表，再由 event_schema.py 统一补上 P-1 信封缺的三列**。存量 6009 行数据
零搬迁（加列即可），这把 S1 从 3d 降到 2d。

## 2. 协议定义：canonical 表 = 信封超集

| P-1 信封字段 | canonical 列 | 说明 |
|---|---|---|
| `seq` | `seq` | per (aggregate_id, branch_id) 单调，语义一致 |
| `ts` | `time_created` | 改名无收益，保留现名 |
| `kind` | `type` | 同上；词表 = `EventType` 枚举（projector 依赖） |
| `data` | `data_json` | 一致 |
| `session_key` | **新增列** `session_key TEXT` | 逻辑名（`chat:main`），现表只有物理 aggregate_id |
| `run_id` | **新增列** `run_id TEXT` | P-4 血统（turn:run） |
| `schema_v` | **新增列** `schema_v TEXT NOT NULL DEFAULT '1'` | 事件格式版本 |
| （信封外） | 保留 `id`（事件 uuid）、`aggregate_id`、`branch_id`、`parent_event_id` | v2 事件树/fork/HITL 恢复需要——**采纳保留**（用户已拍板方向） |

迁移：`_add_column` × 3（幂等）+ 新库 DDL 直接含三列 + `schema_migrations` 记
`('event_log', 2)`。**旧库 6009 行零搬迁**。

## 3. PlatformStore 改造

1. **删自己手写的 event_log 简表 DDL**，改用 `ensure_event_log_schema` /
   `migrate_event_log_unique`（消除第四处重复）；
2. **新增兼容单库模式**：`PlatformStore(db_path=...)`——所有 session 共用一个
   连接（S1 用于指向现有 `.session.db`；`data_dir` per-session 模式保留，S2 切换）；
3. **emit 升级**（信封全字段，全部可选带默认）：
   `emit(session_id, kind, data, *, run_id=None, schema_v="1", session_key=None,
   parent_event_id=None, branch_id="main")`——内部按 canonical 列落库；
4. **新增行级 API**（给 EventStore 门面用）：
   - `append_event_row(row: dict) -> int`：分配 per-(aggregate,branch) seq 并插入
     （把 EventStore `_next_seq` 的 MAX 逻辑收进来，与写同锁保证原子）；
   - `replay_rows(aggregate_id, from_seq, *, types, branch_id, limit)`：对应
     `_replay` 的 WHERE/ORDER 语义；
   返回/接受**行 dict**（不含 EventV2 依赖）——PlatformStore 不 import 事件对象，
   依赖方向保持 平台 ← 编排。

## 4. EventStore 门面化（换存储，不换门面）

EventStore 的编排职责**全部保留**：trace 注入、热缓存、SSE pusher、live
subscribers、fanout、`flush_to_messages` projector 编排、InMemoryStore 降级模式。

只换三个存储函数的实现：

| EventStore 函数 | 改造后 |
|---|---|
| `_persist(event)` | 组装信封行 → `PlatformStore.append_event_row(row)` |
| `_replay(...)` | `PlatformStore.replay_rows(...)` → 行 dict → EventV2（现 `_row_to_event` 逻辑保留在门面） |
| `_next_seq(...)` | 删除（seq 由 `append_event_row` 内部分配并返回；`latest_seq()` 语义转调 PlatformStore） |

构造：`EventStore(db_path=...)` 时以 `PlatformStore(db_path=...)` 兼容模式持有一个
实例（`_ps`）；`_backend`/`SQLiteStore` 从 event_log 路径退役（messages 等表仍由
SQLiteStore 服务——**本次不动**）。

`ensure_event_log_schema` 在 PlatformStore 建连时执行，EventStore 的
`_init_event_log_schema` 保留为显式调用点（web_session 同样调用，幂等无冲突）。

## 5. 明确不做（S1 边界）

- 库文件拆分 / per-session 切换（S2）；
- messages/attempts/sessions 表的任何变动（S2/S3）；
- MemoryManager / web_session / core.session.db 写入路径（S3）；
- SSE 双轨（S4）；
- PlatformStore per-session 模式的行为变化（`data_dir` 用户——当前仅 Gateway
  构造点且生产未传 data_dir，无存量）。

## 6. 验收

1. **往返相等**：EventV2 → emit → replay → 逐字段相等（含 parent_event_id /
   branch_id / run_id / schema_v / seq）；分页（from_seq/limit）、类型过滤、
   branch 过滤与改造前行为一致；
2. **存量迁移**：旧 schema 库（8 列）打开 → 三列补齐 → 既有 6009 行可 replay、
   新事件可 append，seq 连续；
3. **既有测试全绿**：`test_event_store*.py` 全家族 + PlatformStore 17 + artifact 15；
4. **projector 零改动**：`api/session/projector*` 三个文件 diff 为零；
5. **架构门禁**：`core/storage` 仍不 import `core/study`；`EventStore` 不直接执行
   event_log SQL（新增一条门禁断言，钉住门面化）。

## 7. 工期与风险

| 项 | 估 |
|---|---|
| event_schema 三列迁移 + PlatformStore 采纳 canonical | 0.5d |
| 行级 API（seq 原子分配） | 0.5d |
| EventStore 三函数切换 + 降级模式保留 | 0.5d |
| 对账/迁移测试 + 全家族回归 | 0.5d |
| **合计** | **2d**（可行性报告原估 3d，§1 修正后下调） |

| 风险 | 缓解 |
|---|---|
| seq 分配从 EventStore 移入 PlatformStore 后锁语义变化 | PlatformStore 单写连接 + store 级锁（F2 已有）；并发测试覆盖 |
| 兼容单库模式与 per-session 模式行为分叉 | 两种模式共用同一套行级 API 与 DDL，仅连接选择不同；测试对两模式跑同一用例 |
| `_fan_out_parent` / live 路径依赖 EventV2 字段 | 门面不碰这些编排，零改动 |
| 加列对旧库的锁 | ALTER ADD COLUMN 为元数据操作，毫秒级；真实库 5.4MB 无压力 |
