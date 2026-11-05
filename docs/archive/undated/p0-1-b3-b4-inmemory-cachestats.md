# P0-1 B3+B4 — InMemoryStore from_seq + Cache 命中率

> **Status:** Applied (branch `p0-1-event-sourcing`)
> **承接:** B1 把 replay 的 SQL 下推顺手修了 InMemoryStore 的 from_seq bug。B4 把 SessionCache 命中率暴露。

## B3: InMemoryStore 忽略 from_seq

### 现状（修复前）
`EventStore._replay()` 在 InMemoryStore 路径：
```python
return list(data.get(session_id, []))   # from_seq 被吞掉
```

### 修复（B1 已实施）
B1 把 InMemoryStore 路径改成与 SQLite 一致的 Python-side 过滤：
```python
events = [ev for ev in data.get(session_id, []) if ev.seq > from_seq]
if types:
    events = [ev for ev in events if ev.type in type_set]
if branch_id is not None:
    events = [ev for ev in events if ev.branch_id == branch_id]
events.sort(key=lambda ev: ev.seq)
if limit is not None:
    events = events[:limit]
return events
```

— from_seq / types / branch_id / limit 现在在两个后端行为一致。

## B4: Cache 命中率暴露

### 现状
`EventStore.health_report()` 报告 cache_session_count 但**没有**命中/未命中次数。
会话级 LRU cache 的 hit rate 是评估"cache 尺寸是否合理"的关键指标。

### 改动

`SessionCache` 增加轻量命中统计（在 `get()`/`append()`/LRU 路径）：

```python
class SessionCache:
    def __init__(self, config):
        ...
        self._stats = CacheStats(
            hits=0, misses=0, evictions=0,
            inserts=0, last_reset_at=time.time(),
        )
```

`get(session_id)`：
- 命中：`_stats.hits += 1` → 返回 cached
- 未命中：`_stats.misses += 1` → 返回 None

`append()`：`_stats.inserts += 1`

LRU 淘汰：`_stats.evictions += 1`

`health()` 暴露 `{hits, misses, hit_rate, inserts, evictions, age_seconds}`：

```python
@property
def hit_rate(self) -> float:
    total = self._stats.hits + self._stats.misses
    return self._stats.hits / total if total > 0 else 0.0
```

`EventStore.health_report()` 增字段：
```python
"event_store": {
    ...,
    "cache": {
        "session_count": self._cache.session_count,
        "hits": ...,
        "misses": ...,
        "hit_rate": ...,
        "evictions": ...,
    },
}
```

### 影响

- 轻量：每次 get/append 一个 int 自增，几乎无开销
- 不影响现有调用方（health_report 是 dict，新增字段向后兼容）
- 测试：写入/读取/淘汰序列，验证 hit_rate 计算正确

## 测试

`tests/test_event_store_health.py`（新建）或追加到 `test_event_store.py`：
- InMemoryStore from_seq 过滤
- cache hit/miss 计数
- hit_rate 计算（含零除保护）

## 风险

| 风险 | 缓解 |
|------|------|
| 命中率统计线程安全 | `_stats` 用现有 `_lock` 保护，或用 `threading.local`；v0.1 单线程主要场景 |
| 统计污染（生产 DB 长期） | `last_reset_at` 时间戳；运维可监控 hit_rate 趋势 |
| 历史调用方依赖 health_report 形态 | 新增字段不动现有；保持 dict 输出 |