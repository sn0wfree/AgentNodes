# P0-1 B2 — Projector Snapshot

> **Status:** Applied (branch `p0-1-event-sourcing`)
> **承接:** B1 已把 replay 过滤下推到 SQL。本步引入 snapshot，把 project() 从 O(N) 降为 O(delta)。

## 目标

`Projector.project(session_id)` 每次冷启动/缓存失效都从 seq=0 全量重放事件，构造
`ProjectedSession`。5000 事件 session 需要 5000 次 SQL 读 + 5000 次 JSON 解析 + 5000 次
handler apply。

Snapshot 方案：每 N=200 事件自动写一份 `ProjectedSession` 到 `snapshots` 表。
`project()` 命中最近 snapshot 后，只 replay `seq > snapshot.seq` 的 delta。

## 设计

### Schema

```sql
CREATE TABLE IF NOT EXISTS snapshots (
    session_id TEXT PRIMARY KEY,
    seq INTEGER NOT NULL,           -- 快照对应的最后事件 seq
    snapshot_json TEXT NOT NULL,    -- 序列化的 ProjectedSession
    created_at REAL NOT NULL
)
CREATE INDEX IF NOT EXISTS idx_snapshots_session_seq ON snapshots(session_id, seq)
```

(主键 session_id 假设单分支；fork 后可改为 (session_id, branch_id)。)

### 序列化

`ProjectedSession` 已经是 dataclass with `Dict[str, ProjectedMessage]`，可直接 `dataclasses.asdict()` → JSON。

```python
def _serialize(state: ProjectedSession) -> str:
    d = {
        "session_id": state.session_id,
        "last_seq": state.last_seq,
        "messages": {mid: dataclasses.asdict(m) for mid, m in state.messages.items()},
    }
    return json.dumps(d, ensure_ascii=False)

def _deserialize(s: str) -> ProjectedSession:
    d = json.loads(s)
    state = ProjectedSession(session_id=d["session_id"], last_seq=d["last_seq"])
    state.messages = {
        mid: ProjectedMessage(**m)
        for mid, m in d["messages"].items()
    }
    return state
```

(ProjectedMessage/ProjectedPart 都是简单 dataclass，无 set/frozenset，JSON 安全。)

### 触发策略

`flush()` 后，若 `seq % 200 == 0`（默认 `SR_SNAPSHOT_INTERVAL=200`）：
```python
if state.last_seq > 0 and state.last_seq % snapshot_interval == 0:
    self._save_snapshot(conn, state)
```

常量命名 `SR_SNAPSHOT_INTERVAL` 通过 `flush(snapshot_interval: int = 200)` 参数覆盖。

### project() 行为

```python
def project(self, session_id, after_seq=0):
    state = ProjectedSession(session_id=session_id)
    snap = self._load_snapshot(conn, session_id)
    if snap is not None and snap.seq >= after_seq:
        state = snap.state
        after_seq = snap.seq
    events = self.load_events(session_id, after_seq=after_seq)
    for event in events:
        self._apply(event, state)
    state.last_seq = max((e.seq for e in events), default=after_seq)
    return state
```

### 不变量

- `project()` 仍是纯函数（输入=event_log 内容）→ snapshot 是缓存层，不影响正确性
- 任何 cache miss 走全量重建（`after_seq=0`）→ 与无 snapshot 行为一致
- Snapshot 与 event_log 一致性：snapshot.seq < event_log MAX(seq)（snapshot 是历史点）
- 写入：`flush()` 在同事务内 `UPSERT` snapshot，避免 partial write

## 调用方

- `Projector.project_incremental()` 已有 `_cache` 内存缓存，**不**与 snapshot 冲突（snapshot 是 disk 持久层）
- `EventStore._should_flush()` 已在 boundary events 触发 `flush`，由其带动 snapshot 写入
- `web_session._get_db()` 启动时若调用 `project()`，命中 snapshot → 启动更快

## 测试

`tests/test_projector_snapshot.py`（新建）：
- snapshot 写入/读取 round-trip
- 写入 delta 后从 snapshot 重建等价于从 seq=0 重建
- 多 session 独立 snapshot
- snapshot 表 schema/索引存在
- 默认 200 间隔触发，可通过参数覆盖
- 内存 `_cache` 与 disk `snapshots` 互不影响

## 风险

| 风险 | 缓解 |
|------|------|
| JSON 序列化 ProjectedSession 字段类型变更 | ProjectedMessage/Part 是稳定 dataclass；字段扩展需同步 _serialize/_deserialize |
| 写入频率与 flush 频率耦合 | snapshot_interval=200 与 boundary 事件解耦：snapshot 只在 seq % 200 == 0 写 |
| fork 后多分支共享 session_id | v0.1 单分支；fork 落地时升级 PK 为 (session_id, branch_id) |
| 磁盘增长 | snapshot 平均 ~5KB × N sessions，可接受；Phase D 收尾时评估压缩 |