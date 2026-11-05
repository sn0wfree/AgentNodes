# P0-1 Append-only 事件源架构升级设计（2026-08-15）

> **Status:** Completed (branch `p0-1-event-sourcing`, 2026-08-16)
> **Scope:** 将 `event_log` 升级为 "模型可见即可重建" 的完整事件源架构，fork/resume/replay 全免费
> **用户决策:** ① P0-1 优先于 P0-2 执行 ② Blob TTL 默认 1 年 ③ git first doc first

## 完成状态

| 阶段 | 标题 | 状态 | 提交 |
|------|------|------|------|
| 母文档 | 设计（本文档） | ✅ | `ef5a215` |
| **Phase A** | Schema + EventV2 合并 | ✅ | - |
| A1 | Schema 统一 + 列回填 | ✅ | `d5e4380` |
| A2 | EventV2 下沉 core/events | ✅ | `60bba1b` |
| A3 | parent_event_id + branch_id 字段 | ✅ | `da09867` |
| A4 | 版本化 UNIQUE 迁移到 fork-aware | ✅ | `746a4db` |
| **Phase B** | 重放优化 + Snapshot | ✅ | - |
| B1 | Replay SQL 过滤下推 | ✅ | `a00f9d7` |
| B2 | Projector Snapshot | ✅ | `a6293e1` |
| B3 | InMemoryStore replay parity | ✅ | `c7868cd` |
| B4 | Cache 命中率暴露 | ✅ | `c7868cd` |
| **Phase C** | Fork/Resume + Blob 清理 | ✅/⏭ | - |
| C1 | `EventStore.fork()` | ✅ | `06d8d3e` |
| C2 | Resume 路由 | ⏭ Projector 已具备，service 路由后续 | - |
| C3 | Blob 引用计数 + TTL 清理 | ✅ | `e0a6763` |
| C4 | API 端点 | ⏭ | - |
| **Phase D** | 收尾 | ✅ | `8543d5b` |

**测试结果**：565+ 相关测试全绿；`TraceProjection.project()` 在 5000 事件 SQLite session 上 < 2ms（远低于 100ms 目标）。
**ruff**：3 存量错误（C901 + 2×I001），无新增。

## 后续（P0-2 候选）

- SSE 双路径统一（EventStore-backed，删除 SSEEventBuffer 独立 ring buffer）
- `branch_id != "main"` 多分支 fork
- Resume API 端点 + service 层集成
- `blob_refs` decrement 路径（事件真正删除时）
- 前端 BranchTree + "Fork from here" 按钮

---

## 0. 背景与目标

### 0.1 量化研究场景价值

事件源架构对量化研究有**极高**价值：

- **100% 可复现**：同一事件流必然投影出同一状态
- **可审计**：每次 LLM 请求、每个工具调用、每次 token 消耗都有不可变记录
- **金融合规核心要求**：append-only 日志是金融审计的标准形态
- **fork/resume/replay 免费**：从任意 seq 分支、从快照恢复、按需重放

### 0.2 现状

`event_log` 已是事件源的基础，但存在 7 个问题（见 §1）。本次升级把基础设施补齐，使
"模型可见即可重建" 成为工程事实而非纸面承诺。

---

## 1. 问题清单

| # | 问题 | 风险 | 现状证据 |
|---|------|------|----------|
| 1 | **双 Schema** | 数据损坏 | `web_session.py:251`（完整）vs `event_store.py:145`（缺 FK/UNIQUE/NOT NULL/第二索引）；另有 `backfill_event_log.py:66` 第三份 |
| 2 | **双 EventV2** | 序列化不一致 | `event_store.py:36`（tuple 读取、无校验）vs `event_v2.py:196`（dict 读取、create/校验/序列化/predicates 齐全） |
| 3 | **O(N) 重放** | 延迟尖峰 | `_replay()` 全量重放；`TraceProjection` 95% 事件反序列化后丢弃 |
| 4 | **无 Blob 清理** | 磁盘无限增长 | `trace-blobs/` 无 TTL、无引用计数、session 删除不清理 |
| 5 | **无 Fork/Resume** | 无法分支 | 无 branch_id、无 fork 点 snapshot |
| 6 | **SSE 双路径** | 事件丢失 | `EventStore._sse_pusher` + `SSEEventBuffer` 并存，buffer 5 分钟 TTL |
| 7 | **Cache Miss = 全量重放** | 冷启动延迟 | `Projector.project()` 每次从 seq=0 重建 |

---

## 2. 目标状态

```
┌─────────────────────────────────────────────────────────────┐
│                    event_log (append-only)                   │
│  id | aggregate_id | seq | type | data_json | time_created   │
│  + parent_event_id | branch_id                               │
│  UNIQUE(aggregate_id, seq), FK→sessions, type_time index     │
└──────────────────────────────┬──────────────────────────────┘
                               │ replay(branch, from_seq, types)
            ┌──────────────────┼──────────────────┐
            ▼                  ▼                  ▼
     ┌────────────┐    ┌─────────────┐    ┌──────────────┐
     │ Projector  │    │ TraceProject│    │ SSE / fork   │
     │ snapshot   │    │ ion (SQL    │    │ resume       │
     │ + delta    │    │ filter push)│    │              │
     └────────────┘    └─────────────┘    └──────────────┘
```

---

## 3. 设计决策

### 3.1 统一 Schema（单一事实源）

以 `web_session.py:251` 为 canonical，`event_store.py` 与其对齐：

```sql
CREATE TABLE IF NOT EXISTS event_log (
    id TEXT PRIMARY KEY,
    aggregate_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    type TEXT NOT NULL,
    data_json TEXT NOT NULL,
    time_created REAL NOT NULL,
    parent_event_id TEXT,            -- 新增：trace 树结构
    branch_id TEXT NOT NULL DEFAULT 'main',  -- 新增：fork 分支
    FOREIGN KEY (aggregate_id) REFERENCES sessions(id) ON DELETE CASCADE,
    UNIQUE (aggregate_id, branch_id, seq)    -- 分支内单调 seq
);
CREATE INDEX idx_event_log_aggregate_seq ON event_log(aggregate_id, seq);
CREATE INDEX idx_event_log_type_time ON event_log(type, time_created);
```

- `event_store.py` 删除自己那份 DDL，改为调用共享的 `ensure_schema()`（放 `api/session/store.py` 或新 `core/storage/event_schema.py`）
- `backfill_event_log.py` 同步更新
- 迁移：现有 DB `ALTER TABLE` 加列 + `UNIQUE`（SQLite 重建表方式），见 §5

### 3.2 合并 EventV2

删除 `event_store.py:36` 的简化版，统一使用 `event_v2.py:196` 的完整版：

- `event_store.py` 改为 `from ...api.session.event_v2 import EventV2`
- 新增字段 `parent_event_id: str | None = None`、`branch_id: str = "main"`
- `to_row()` / `from_row()` 同步更新（`from_row` 已支持 dict 访问）
- 保留 `event_v2.py` 的 `create()` 校验与 `is_*_event()` predicates

> 注意：`core/agent` 层依赖 `api/session` 层。需要确认是否引入循环依赖。若 `event_v2.py`
> 不依赖 core，则单向依赖安全。若存在反向依赖，则把 EventV2 下沉到 `core/events/event_v2.py`。

### 3.3 重放性能优化

`EventStore.replay()` 增加两个下推参数：

```python
def replay(
    self,
    session_id: str,
    from_seq: int = 0,
    types: list[str] | None = None,     # SQL WHERE type IN (...)
    branch_id: str = "main",            # WHERE branch_id = ?
    limit: int | None = None,           # LIMIT 分页
) -> list[EventV2]:
```

- `TraceProjection.project()` 传 `types=DEFAULT_TRACE_TYPES`，95% 事件在 SQL 层过滤
- `Projector` 增加 snapshot（见 §3.4），cache miss 只重放 delta
- 修复 `InMemoryStore` 忽略 `from_seq` 的 bug

### 3.4 Projector Snapshot

新增 `snapshots` 表：

```sql
CREATE TABLE IF NOT EXISTS snapshots (
    session_id TEXT PRIMARY KEY,
    branch_id TEXT DEFAULT 'main',
    seq INTEGER NOT NULL,           -- 快照对应的最后事件 seq
    snapshot_json TEXT NOT NULL,    -- 序列化的 ProjectedSession
    created_at REAL NOT NULL
);
```

- `Projector.flush()` 后：若 `seq % N == 0`（默认 N=200）则写入 snapshot
- `Projector.project()`：先读 snapshot，只重放 `seq > snapshot.seq` 的 delta
- 兜底：无 snapshot → 全量重建（幂等）

### 3.5 Fork/Resume

新增 API 层方法（不强制前端，先做后端能力）：

```python
# EventStore 新方法
def fork(session_id: str, at_seq: int, new_session_id: str) -> int:
    """复制 [0, at_seq] 的事件到新 session，返回新 seq 起点。"""

def resume_snapshot(session_id: str, branch_id: str = "main") -> ProjectedSession:
    """从最近 snapshot + delta 恢复（与 projector 共用）。"""
```

- fork = 纯事件复制：新 session 的 event_log 写入前 at_seq 条（branch_id 可重命名为新根）
- resume = snapshot + delta 重放
- `branches` 元数据表（可选，v0.1 用 `branch_id` 字段即可）：

```sql
CREATE TABLE IF NOT EXISTS branches (
    session_id TEXT PRIMARY KEY,
    parent_session_id TEXT,
    fork_at_seq INTEGER,
    created_at REAL NOT NULL
);
```

### 3.6 Blob 清理（TTL = 365 天）

新增 `blob_refs` 表 + 后台清理任务：

```sql
CREATE TABLE IF NOT EXISTS blob_refs (
    blob_path TEXT PRIMARY KEY,
    ref_count INTEGER NOT NULL DEFAULT 0,
    first_seen REAL NOT NULL,
    last_access REAL NOT NULL
);
```

| 参数 | 值 | 说明 |
|------|-----|------|
| `SR_BLOB_TTL_DAYS` | **365**（默认 1 年） | 非活跃 blob 保留时长 |
| 清理频率 | 每周 | 减少 I/O |
| ref_count = 0 | 可清理 | 活跃 blob 永不清理 |
| session 删除 | 递减 ref_count，不删文件 | 审计留痕 |
| 手动清理 | `POST /api/admin/cleanup-blobs` | 运维入口 |

清理逻辑：

```
ref_count == 0 AND last_access < NOW() - 365 天  →  可清理
删除前写审计日志（blob_path, size, 触发原因）
```

> **1 年 TTL 的理由**：金融合规审计通常要求保留至少 1 年交易/决策记录。
> 磁盘成本可接受（blob 只存 >4KB 的大字段，单条 < 50KB 为主）。

### 3.7 SSE 统一（v0.2，可后置）

P0-1 首版**不合并** SSE 双路径（避免 4 周内引入回归）。保留 `_sse_pusher` 桥接现状，
仅保证 `subscribe()` 在 `parent_event_id`/`branch_id` 字段下行为不变。SSE 统一放入
P0-1 收尾或 P0-2 之后单独做。

---

## 4. 实施阶段

### Phase A: Schema 统一 + EventV2 合并（Week 1）

| 步骤 | 内容 | 验证 |
|------|------|------|
| A1 | 新增共享 `ensure_event_log_schema()`（含新列），`event_store.py`/`web_session.py`/`backfill_event_log.py` 统一调用 | 三处 DDL 收敛到一份 |
| A2 | `event_store.py` 改用 `event_v2.py` 的 EventV2，删本地简化版 | `test_event_store.py`、`test_event_v2.py` 通过 |
| A3 | EventV2 增加 `parent_event_id` / `branch_id` 字段，`to_row`/`from_row` 更新 | round-trip 测试通过 |
| A4 | 迁移脚本（现有 DB `ALTER TABLE` 加列 + UNIQUE 重建表） | 空库/旧库/新库三态测试 |

### Phase B: 重放优化 + Snapshot（Week 2）

| 步骤 | 内容 | 验证 |
|------|------|------|
| B1 | `replay()` 增加 `types`/`branch_id`/`limit` 下推 | `test_trace_projection.py` 传递 types，SQL 层过滤 |
| B2 | `snapshots` 表 + `Projector` snapshot 写入/读取 | `test_projector_incremental.py` 增加 snapshot 用例 |
| B3 | 修复 `InMemoryStore` 忽略 `from_seq` bug | 新增回归测试 |
| B4 | cache 命中率暴露到 `health_report()` | `health_report` 断言 |

### Phase C: Fork/Resume + Blob 清理（Week 3）

| 步骤 | 内容 | 验证 |
|------|------|------|
| C1 | `EventStore.fork()`：复制 [0, at_seq] 事件到新 session | fork 后两 session 独立重放 |
| C2 | `resume_snapshot()`：snapshot + delta 恢复 | resume 与全量重建一致 |
| C3 | `blob_refs` 表 + 写入点 + 清理任务（TTL=365） | 清理只删 ref_count=0 且过期 |
| C4 | fork/resume API 端点（REST） | curl smoke-test |

### Phase D: 收尾（Week 4）

| 步骤 | 内容 | 验证 |
|------|------|------|
| D1 | 全量测试：`pytest` 全部通过 + 新增 20+ 用例 | 绿色 |
| D2 | 性能验证：5000 事件 session 重放 < 100ms | benchmark |
| D3 | 文档更新 + ruff 检查 | 无新告警 |

---

## 5. 迁移脚本策略

SQLite 加列 + 加 UNIQUE 需重建表：

```sql
-- 1. 旧表加列（兼容存量）
ALTER TABLE event_log ADD COLUMN parent_event_id TEXT;
ALTER TABLE event_log ADD COLUMN branch_id TEXT NOT NULL DEFAULT 'main';

-- 2. UNIQUE(aggregate_id, branch_id, seq) 需要重建表
CREATE TABLE event_log_new (... full schema ...);
INSERT INTO event_log_new SELECT id, aggregate_id, seq, type, data_json,
    time_created, parent_event_id, branch_id FROM event_log;
DROP TABLE event_log;
ALTER TABLE event_log_new RENAME TO event_log;
CREATE INDEX idx_event_log_aggregate_seq ON event_log(aggregate_id, seq);
CREATE INDEX idx_event_log_type_time ON event_log(type, time_created);
```

> **风险**：重建表会短暂锁库。生产库建议备份 + 低峰期执行。幂等：
> `PRAGMA user_version` 版本化迁移（沿用 `web_session.py` 的 `_run_schema_migrations` 模式）。

---

## 6. 关键文件

| 文件 | 修改类型 | 说明 |
|------|----------|------|
| `core/agent/event_store.py` | 重大 | 删本地 EventV2 + DDL，改用共享 schema/EventV2；replay 下推；fork/resume |
| `api/session/event_v2.py` | 中 | 加 parent_event_id / branch_id |
| `api/routers/web_session.py` | 小 | DDL 收敛到共享函数 |
| `api/session/backfill_event_log.py` | 小 | DDL 收敛 |
| `api/session/projector.py` | 中 | snapshot 写入/读取 |
| `api/session/trace_projection.py` | 小 | 传 types 下推 |
| `api/session/service.py` | 中 | fork/resume API |
| `api/routers/session.py` | 小 | 新端点 |
| 新 `core/storage/event_schema.py` | 新建 | 共享 DDL + 迁移 |
| 新 `core/storage/blob_refs.py` | 新建 | blob 引用计数 + 清理任务 |

---

## 7. 风险与缓解

| 风险 | 影响 | 缓解 |
|------|------|------|
| 双 Schema 合并破坏存量数据 | 高 | 备份 + 版本化迁移 + 三态测试（空/旧/新库） |
| `core→api` 依赖循环 | 高 | 若 event_v2 依赖 core 则下沉 EventV2 到 `core/events/` |
| Snapshot 并发写不一致 | 中 | optimistic：snapshot 写入记录 `(branch_id, seq)`，读取校验 |
| Fork 并发 seq 冲突 | 中 | fork 在 `_backend._lock` 内完成复制 |
| SSE 双路径回归 | 中 | 首版不合并 SSE（见 §3.7） |

---

## 8. 成功标准

1. **Schema 统一**：生产代码只有一份 event_log DDL
2. **EventV2 统一**：无重复 dataclass
3. **重放性能**：5000 事件 session 重放 < 100ms（当前 > 1s）
4. **Fork/Resume**：fork 后两 session 独立重放；resume 与全量重建逐字节一致
5. **Blob 清理**：TTL=365 天，引用计数正确，删除前审计
6. **SSE 行为不变**：现有 SSE 测试全绿
7. **测试**：112+ 现有全绿 + 新增 20+ 用例

---

## 9. 后续（P0-2 之后）

- SSE 双路径统一（EventStore-backed）
- branch tree 前端可视化 + "Fork from here" 按钮
- 事件 type 索引物化视图（按 type 聚合统计）
