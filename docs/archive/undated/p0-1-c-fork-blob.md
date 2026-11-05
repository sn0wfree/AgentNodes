# P0-1 C — Fork/Resume + Blob 清理

> **Status:** Draft (branch `p0-1-event-sourcing`)
> **承接:** Phase A 完成 schema + EventV2，Phase B 完成 replay 下推 + snapshot。本步实现 fork/resume 与 blob 生命周期。

## 目标

让 event_log 真正支持"分叉"——同一 session 在任意 seq 处 fork 出独立子分支，且保证审计
完整性（fork 也是事件）。同时给 sidecar blob 引入引用计数 + TTL 清理，避免磁盘无限增长。

## C1: `EventStore.fork()`

### 语义

```
session_A (main branch, seq=0..100)  ── fork at seq=100 ──>  session_B (main, seq=101..N)
                                                          (复制 A 的 events 0..100 到 B)
```

调用方：
```python
new_sid, fork_event_id = store.fork(
    session_id="A",
    at_seq=100,
    new_session_id="B",     # 可选；默认自动生成
    parent_event_id=None,   # 可选；记录到 branch metadata
)
```

### 实现

1. **创建 sessions 行**：`INSERT INTO sessions (id, ...) VALUES ('B', ...)`（如果 sessions 表存在）
2. **复制事件**：`INSERT INTO event_log SELECT ... FROM event_log WHERE aggregate_id='A' AND seq <= 100 AND branch_id='main'`，并改 aggregate_id='B'
3. **重写 seq**：B 的事件 seq 从 1 开始（不能复用 A 的 100，否则 UNIQUE 冲突）
4. **branch_id 保持 main**：fork 在 v0.1 默认 main 分支；后续支持 branch_id != main
5. **写 fork metadata**：可选 `branches` 表（v0.1 暂不建表；先简化）
6. **返回**：new_sid + 首个事件的 id

### SQL 模板

```sql
INSERT INTO event_log
    (id, aggregate_id, seq, type, data_json, time_created,
     parent_event_id, branch_id)
SELECT
    -- 新事件 id（生成新 UUID），但保持 data/parent/branch 不变
    (SELECT lower(hex(randomblob(16))) FROM event_log WHERE id = e.id),
    'B',                  -- 新 aggregate_id
    ROW_NUMBER() OVER (ORDER BY seq),  -- 重新编号 1..N
    e.type,
    e.data_json,
    e.time_created,
    e.parent_event_id,
    e.branch_id
FROM event_log e
WHERE e.aggregate_id = 'A'
  AND e.seq <= 100
  AND e.branch_id = 'main'
ORDER BY e.seq ASC;
```

### 边界

- `at_seq < 0` 或 `at_seq > MAX(seq)` → 报错
- `new_session_id` 已存在 → 报错
- 源 session 为空 → 不创建空 fork

### 并发

整个 fork 在 SQLiteStore `_lock` 下完成，避免与其他 emit 竞态。

## C2: Resume（最小化）

**决策**：v0.1 不单独实现 `resume_snapshot` 方法——`Projector.project()` 已经实现了"加载 snapshot + delta" 语义。Phase C 只需要在 service 层加路由即可：

```python
# 服务端
GET /api/sessions/{id}/resume → 重启 session（恢复 Projector 缓存 + EventStore SSE）
```

## C3: Blob 引用计数 + TTL 清理

### 设计

新增 `blob_refs` 表：
```sql
CREATE TABLE IF NOT EXISTS blob_refs (
    blob_path TEXT PRIMARY KEY,
    ref_count INTEGER NOT NULL DEFAULT 0,
    first_seen REAL NOT NULL,
    last_access REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_blob_refs_last_access
    ON blob_refs(last_access);
```

### 引用计数策略

`_LoopEventForwarder._offload_large_fields` 是当前唯一 blob writer。增加：
1. **offload 时**：INSERT OR IGNORE blob_refs (path, ref_count=1, first_seen=now, last_access=now)
   - 已存在则 `UPDATE blob_refs SET ref_count = ref_count + 1, last_access = now`
2. **删除 event_log 行时**：DELETE FROM event_log ... → DECREMENT blob_refs
   - 但 event_log 是 append-only，不删除行——所以 ref_count 只会单调递增
3. **实际场景**：当 session 被删除（`sessions` FK CASCADE），关联 events 被删除——但 blobs 文件仍在磁盘。`ref_count` 用于统计"活跃引用"，但因为 event_log 行不删，ref_count 永远不递减。

**简化决策**：v0.1 的 blob_refs 只做"追踪"，清理基于 `last_access + TTL`（不管 ref_count）。这与设计文档 §3.6 一致（活跃 blob 永不清理）。

### TTL = 365 天

```python
DEFAULT_BLOB_TTL_DAYS = 365  # 金融合规审计

def cleanup_stale_blobs(conn, ttl_days=365):
    """Mark blobs whose last_access < NOW - ttl_days as cleanable.
    Does NOT delete yet — actual file removal is a separate audit-logged
    step (see scripts/cleanup_blobs.py).
    """
    threshold = time.time() - ttl_days * 86400
    rows = conn.execute(
        "SELECT blob_path, ref_count, last_access FROM blob_refs "
        "WHERE last_access < ? AND ref_count <= 0",
        (threshold,),
    ).fetchall()
    return rows
```

### 手动清理脚本

`scripts/cleanup_blobs.py`：运维入口，列出可清理 blob + 审计日志 + 实际删除。

### 写时机

`_LoopEventForwarder._offload_large_fields` 修改（service.py）：

```python
def _offload_with_refs(conn, blob_path):
    conn.execute(
        "INSERT INTO blob_refs (blob_path, ref_count, first_seen, last_access) "
        "VALUES (?, 1, ?, ?) "
        "ON CONFLICT(blob_path) DO UPDATE SET "
        "ref_count = ref_count + 1, last_access = excluded.last_access",
        (blob_path, time.time(), time.time()),
    )
```

### Schema DDL

加到 `core/storage/event_schema.py` 的 `EVENT_LOG_SCHEMA_SQL`：

不，blob_refs 不属于 event_log——它属于 trace-blobs 元数据。单独加到 `core/storage/blob_schema.py`，由 web_session._ensure_schema 和 EventStore._init_event_log_schema 都调用。

## C4: API 端点

```python
# routers/session.py
POST /api/sessions/{id}/fork
    body: {"at_seq": int, "new_session_id": str | None}
    → 201 {"session_id": str, "forked_event_count": int}

POST /api/sessions/{id}/resume
    body: {"branch_id": str}
    → 200 {"session_id": str, "last_seq": int, "from_snapshot": bool}
```

## 实施步骤

| 步骤 | 内容 | 验证 |
|------|------|------|
| C1 | `EventStore.fork()` 实现 + 测试 | fork 后两 session 独立重放 |
| C2 | `Projector.project()` 已具备；service 层 resume 路由（最小） | resume 与全量重建等价 |
| C3 | `blob_refs` schema + `_offload_with_refs` + `cleanup_stale_blobs` + 脚本 | 引用计数正确、TTL 触发 |
| C4 | API 端点 | curl smoke-test |

## 测试

`tests/test_event_store_fork.py`：
- fork 单事件 session
- fork 多次（同一 source 产生多个 child）
- at_seq 边界（at_seq=0, at_seq > MAX）
- 已存在 new_session_id 报错
- fork 后 source 仍可写新事件，target 不受影响

`tests/test_blob_cleanup.py`：
- offload → ref_count=1
- 重复 offload 同一 path → ref_count=2
- last_access 在 TTL 外 → cleanup 标记
- 活跃（ref_count > 0 或 last_access 在 TTL 内）→ 不清理

## 风险

| 风险 | 缓解 |
|------|------|
| 大 session fork 慢 | COPY 是 O(N) SQL 单语句，5000 事件 < 1s |
| Fork 时 source 写入竞态 | `_lock` 内执行 |
| Blob 删除误删活跃数据 | TTL = 365 天 + 审计日志 + 双校验 ref_count = 0 |
| ref_count 单调递增导致永不清理 | v0.1 接受；后续版本实现"删除 event 时 decrement" |
| Fork 不写 events 表（只复制）→ sessions 表可能没有 B 行 | C1 步骤 1：INSERT INTO sessions |