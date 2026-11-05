# P0-1 A4 — EventLog 版本化迁移（UNIQUE 升级）

> **Status:** Applied (branch `p0-1-event-sourcing`)
> **承接:** A1-A3 把 `parent_event_id` / `branch_id` 接入数据模型；本步把 schema 升级为 fork-aware 版本，并通过 user_version 做幂等迁移。

## 目标

把 `event_log` 的 `UNIQUE (aggregate_id, seq)` 升级为 `UNIQUE (aggregate_id, branch_id, seq)`，
并提供生产级迁移路径（老 DB 重建表 + 新 DB 直接用 DDL）。

## 升级必要性

| UNIQUE | 适用场景 | fork 行为 |
|--------|----------|-----------|
| `(aggregate_id, seq)` | 只有 main 分支 | fork 后两分支共享 seq 空间 → UNIQUE 冲突 |
| `(aggregate_id, branch_id, seq)` | 多分支 | 每分支独立 seq 空间 → 安全 |

Phase C（fork）实施前必须升级。当前数据全部在 `branch_id='main'`，迁移在数据上是无损的
（main 分支 seq 仍唯一）。

## 实现

### 1. 修订 `EVENT_LOG_DDL`

新 fresh-table DDL：

```sql
CREATE TABLE IF NOT EXISTS event_log (
    id TEXT PRIMARY KEY,
    aggregate_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    type TEXT NOT NULL,
    data_json TEXT NOT NULL,
    time_created REAL NOT NULL,
    parent_event_id TEXT,
    branch_id TEXT NOT NULL DEFAULT 'main',
    FOREIGN KEY (aggregate_id) REFERENCES sessions(id) ON DELETE CASCADE,
    UNIQUE (aggregate_id, branch_id, seq)
)
```

`CREATE TABLE IF NOT EXISTS` 不会重建已存在的表——老 DB 保留旧 UNIQUE 直到显式迁移。

### 2. `migrate_event_log_unique(conn)` 函数

```python
def migrate_event_log_unique(conn: sqlite3.Connection) -> bool:
    """Upgrade UNIQUE(aggregate_id, seq) → UNIQUE(aggregate_id, branch_id, seq).
    Returns True if migrated, False if no-op (fresh DB or already migrated).
    Idempotent. Wrapped in BEGIN IMMEDIATE for atomicity.
    """
```

重建表流程：
1. 检查 `UNIQUE (aggregate_id, seq)` 是否存在——若不存在则 no-op
2. `BEGIN IMMEDIATE` → 锁库
3. 创建 `event_log_new`（新 schema）
4. `INSERT INTO event_log_new SELECT ... FROM event_log`
5. `DROP TABLE event_log` → `ALTER TABLE event_log_new RENAME TO event_log`
6. 重建索引
7. `COMMIT`

### 3. web_session.py 版本化迁移

`_run_schema_migrations` 增加 `user_version=6` 步骤：

```python
if version < 6:
    from ...core.storage.event_schema import migrate_event_log_unique
    migrate_event_log_unique(conn)
    conn.execute("PRAGMA user_version = 6")
```

EventStore 路径不调用 `_run_schema_migrations`（它有自己的轻量 `_init_event_log_schema`）。
所以 EventStore 启动时也要主动触发 `migrate_event_log_unique`：

```python
def _init_event_log_schema(self) -> None:
    ...
    ensure_event_log_schema(conn)
    migrate_event_log_unique(conn)  # idempotent, no-op if fresh
```

### 4. 独立迁移脚本

`scripts/migrate_event_log_p0_1_a4.py`：供运维手动跑（命令行 `--db-path`），底层调用 `migrate_event_log_unique`。

## 测试

- `tests/test_event_schema.py`（新建）：覆盖迁移函数
  - fresh DB：no-op（UNIQUE 已正确）
  - old DB（`UNIQUE (aggregate_id, seq)`）：迁移后 UNIQUE 升级
  - post-migration DB：再次调用 no-op
  - 数据保真：迁移前后 row count、内容一致
  - 索引重建正确
- `tests/test_event_store.py` / `test_event_log_schema.py`：仍全绿

## 风险

| 风险 | 缓解 |
|------|------|
| 重建表短暂锁库 | `BEGIN IMMEDIATE` 单事务；5000 events 估计 < 1s |
| DROP TABLE 期间外键悬空 | SQLite DROP TABLE 不级联；FK 仅在 INSERT 时检查，迁移过程中无 INSERT |
| 多次迁移并发触发 | `BEGIN IMMEDIATE` 锁 + idempotent 检查 |
| 老 DB 没有 `parent_event_id`/`branch_id` 列 | A1 已在 `ensure_event_log_schema` 里 `_add_column` 回填；本步前先确保此步骤跑过 |

## 兼容性

- EventStore 启动顺序：`ensure_event_log_schema`（A1）→ 列回填（A3 字段）→ `migrate_event_log_unique`（A4）
- 三步顺序保证：任意老 DB 升级路径完整
- Fresh DB：第一步直接建新 schema，后两步 no-op

## 后续

A4 完成后，`branch_id` 列与 fork-aware UNIQUE 已就位。Phase C（fork/resume）实施时，
`EventStore.fork()` 创建新分支时分配独立 seq 空间，UNIQUE 不再冲突。