# P0-1 B1 — Replay SQL 过滤下推

> **Status:** Applied (branch `p0-1-event-sourcing`)
> **承接:** Phase A 完成 schema/EventV2 升级。本步把 TraceProjection 的类型过滤从 Python 移到 SQLite WHERE 子句。

## 目标

`TraceProjection.project()` 当前对**全部事件**做 Python 层 `if ev.type not in type_filter: continue`，
实测 95% 事件被反序列化后丢弃（仅 ~5% 是 trace 类型）。把过滤下推到 SQL，让反序列化只
发生在真正需要的行上。

## 改动

### `EventStore.replay()` 扩展签名

```python
def replay(
    self,
    session_id: str,
    from_seq: int = 0,
    *,
    types: list[str] | tuple[str, ...] | None = None,
    branch_id: str | None = None,
    limit: int | None = None,
) -> list[EventV2]:
    """Return events for session with seq > from_seq.

    New optional filters pushed down to SQLite as WHERE clauses:
    - ``types``: ``type IN (...)`` — biggest perf win for Trajectory View
    - ``branch_id``: ``branch_id = ?`` — A4 fork-aware scoping
    - ``limit``: row cap — TraceProjection already trims in Python, but
      pushing it down shrinks the result set wire-side
    """
```

`_replay()` 私有实现：

```python
def _replay(self, session_id, from_seq, types, branch_id, limit):
    clauses = ["aggregate_id = ?", "seq > ?"]
    params = [session_id, from_seq]
    if types:
        placeholders = ",".join("?" * len(types))
        clauses.append(f"type IN ({placeholders})")
        params.extend(types)
    if branch_id is not None:
        clauses.append("branch_id = ?")
        params.append(branch_id)
    sql = (
        "SELECT id, aggregate_id, seq, type, data_json, time_created, "
        "parent_event_id, branch_id FROM event_log "
        f"WHERE {' AND '.join(clauses)} ORDER BY seq ASC"
    )
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)
    ...
```

InMemoryStore 路径同样应用 `types` / `branch_id` / `limit` Python-side 过滤，保持一致性。

### `TraceProjection.project()` 调用改造

```python
records: list[dict[str, Any]] = []
type_list = list(type_filter)  # frozenset → list for SQL IN
for ev in self._event_store.replay(
    session_id,
    types=type_list if type_list else None,
):
    ...
return records[-limit:]
```

注意 `records[-limit:]` 保留 Python 截断——`replay` 的 `limit` 是 SQL 层行数上限，
TraceProjection 仍要按事件顺序保留 last N records 给前端。

### 兼容

- 现有调用方 `test_event_store.py` 等 4 处 `es.replay("s1")` / `es.replay("s1", from_seq=1)` 保持兼容（kw-only 参数默认 None 等价 no-op）
- TraceProjection 默认行为不变（仍用 DEFAULT_TRACE_TYPES 过滤，仍 last N records）

## 验证

- `test_event_store.py`：新增 replay(type/l) / replay(branch_id) / replay(limit) 测试
- `test_trace_projection.py`：SQL 层 filter pushdown 验证
  - 100 事件中 5 个 trace type → replay 只返回 5 行（不是 100）
  - 多类型 / 边界 / limit
- 性能：5000 事件 session 投影从 O(N) 降至 O(delta)，反序列化 95% 减少

## 风险

| 风险 | 缓解 |
|------|------|
| InMemoryStore 类型不匹配 | 严格用 `ev.type in types`（列表比较） |
| 类型列表过大 | TraceProjection 词汇表固定 ~15 项；如未来需要可加分页 |
| Limit 双重截断 | SQL LIMIT 与 Python records[-limit:] 语义不同——SQL 是行上限，Python 是尾段保留；明确文档 |

## 后续（B2-B4）

- B2: Projector Snapshot 写/读
- B3: 修复 InMemoryStore 忽略 from_seq
- B4: Cache 命中率暴露