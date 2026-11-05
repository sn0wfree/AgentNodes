# Phase 7+8 — EventStore + MemoryManager 单一事实源

> 状态：实施中（auto-repair 启用）
> 范围：**Phase 7（BaseEventBus 单一事实源）+ Phase 8（MemoryManager 三合一）合并实施**
> 核心原则：**SQLite = 单一事实源，内存 = cache 加速（用户确认）**
> 上游：[phase6-agent-loop-factory.md](./chat-agent-refactor-phase6-agent-loop-factory.md)

## 1. 目标

消除 3 个事实源并存的历史债务（Phase 1 已标记），将 EventBus + MemoryManager 重构为：

| 关注点 | 旧实现 | 新实现 |
|---|---|---|
| Session history 来源 | 3 处分散：chat.py dict / TUI list / SQLite messages 表 | **单一 MemoryManager**（SQLite 事实源 + LRU cache） |
| Event 流 | 2 处：EventBus (内存 buffer) + EventBusV2 (SQLite event_log 双写) | **单一 EventStore**（SQLite event_log + LRU cache + SSE push） |
| TUI 与 Web 隔离 | TUI 独立内存，进程退出丢历史 | **共享 `~/.quantnodes/sessions.db`**（跨进程 SQLite WAL） |
| Cache 失效策略 | 无 cache | **WRITE_THROUGH + LRU(1000)** 默认；可配置 |

## 2. 现状（Phase 7+8 改造前）

### 2.1 Session History — 3 处分散

| 来源 | 位置 | 类型 | 持久化 | 问题 |
|---|---|---|---|---|
| `_session_histories` 模块级 dict | `api/routers/chat.py:67` | 内存 | ❌ | 内存泄漏（Phase 1 标记） |
| `self.ctx.history` | `cli/tui/session.py:236,262` | 内存 list | ❌ | TUI 退出即丢 |
| `messages` SQLite table | `service.py` via `persist_message` | SQLite | ✅ | 生产路径事实源 |

### 2.2 Event 流 — 2 处实现

| 实现 | 位置 | 持久化 | 订阅者 |
|---|---|---|---|
| `EventBus` legacy | `events.py:57` | ❌ 内存 buffer (`_buffers`) | asyncio.Queue subscribers |
| `EventBusV2` (B2) | `event_bus_v2.py:43` | ✅ SQLite event_log + 双写 legacy EventBus | SSE + projector flush |

**问题**：
- EventBusV2 双写导致 event_log 与内存 buffer 可能不一致
- Projector flush 是异步的（B4），event_log 写入后 messages 表可能未更新
- 3 个事实源（event_log + buffer + messages）靠 projector 同步

## 3. 用户决策（已确认）

| 决策点 | 选择 | 理由 |
|---|---|---|
| Q1 chat.py:_session_histories | **A 直接删除 + 保留 emergency fallback** | 删除主路径，但保留为最末兜底（当 MemoryManager 不可用时启用） |
| Q2 cache 写/淘汰策略 | **WRITE_THROUGH + LRU** | 强一致 + 简单 |
| Q3 EventBus legacy | **直接删除** | 不保留 facade |
| Q4 TUI SQLite | **共享 `~/.quantnodes/sessions.db`** | TUI 与 web 跨进程共享 SQLite WAL |
| Q5 cache 与 compaction 联动 | **max(min_entries, derive_from_compact)** | cache 至少能装下 compaction 触发前的所有消息 |
| Q6 动态估算算法 | **(p86 + 1σ) × 2** | p86 鲁棒上界 + σ 考虑分布右尾 + safety_factor 2.0 |
| Q7 min_entries | **1000** | 覆盖绝大多数实际场景 |
| Q8 compaction_linked | **True** | 智能默认 |
| Q9 re-resolve 频率 | **C：定期检查（默认 60s）** | 缓存结果定期重算 |
| Q10 兜底方案 | **4 层 cascade + 容量 cascade + 后端降级 + emergency buffer + ConfigValidator + SQLite auto-repair + Two-phase commit + Health endpoint** | 全链路失效覆盖 |
| Q11 auto-repair | **启用** | SQLite 损坏时自动 .dump + 重导入 |

## 4. 架构设计

### 4.1 核心抽象

```
SQLite (WAL mode, busy_timeout=5000, integrity_check on startup)
   ↑↓
SessionCache (LRU 1000 entries，按 session 淘汰)
   ↑↓
MemoryManager / EventStore (Protocol 接口)
   ↑↓
调用方：chat.py / tui/session.py / service.py / sse_buffer.py
```

### 4.2 CacheConfig（可配置）

```python
@dataclass
class CacheConfig:
    # 写策略（5 选 1）
    write_policy: WritePolicy = WritePolicy.WRITE_THROUGH
    
    # 淘汰策略（3 选 1）
    eviction_policy: EvictionPolicy = EvictionPolicy.LRU
    
    # 容量基础参数
    min_entries: int = 1000                       # 硬下限
    max_entries: int = 1000                       # 基础配置
    
    # 动态估算参数（p86 + σ × safety）
    avg_tokens_per_message: int = 200             # 冷启动 fallback
    avg_tokens_estimation_window: int = 100       # 滑动窗口
    avg_tokens_safety_factor: float = 2.0         # safety_factor
    avg_tokens_min_samples: int = 10              # 最少样本
    chars_per_token: float = 3.0                  # 与 CompactConfig 对齐
    
    # Compaction 联动
    compaction_linked: bool = True
    compact_config: CompactConfig | None = None
    
    # Re-resolve 频率（用户选 C）
    re_resolve_interval_seconds: float = 60.0
    
    # TTL 策略（eviction_policy=TTL 时）
    ttl_seconds: float = 60.0
```

### 4.3 MemoryManager Protocol

```python
class MemoryManager(Protocol):
    def append(self, session_id: str, message: Message) -> str: ...
    def get(self, session_id: str, *, use_cache: bool = True) -> list[Message]: ...
    def clear(self, session_id: str) -> None: ...
    def compact(self, session_id: str, strategy: CompactionStrategy) -> bool: ...
    def list_recent_sessions(self, limit: int = 10) -> list[str]: ...
    def health_report(self) -> HealthReport: ...


class UnifiedMemoryManager:
    """默认实现。SQLite = source of truth, 内存 = cache."""
    
    def __init__(
        self,
        db_path: Path,
        cache_config: CacheConfig | None = None,
        compact_config: CompactConfig | None = None,
    ):
        # 1. SQLite 健康检查 + auto-repair
        db = SQLiteStore(db_path)
        if not db.health_check():
            if db.auto_repair():
                logger.warning("SQLite auto-repair succeeded")
            else:
                logger.error("SQLite auto-repair failed; degraded mode")
                db = InMemoryStore()
        
        # 2. CascadeEstimator（4 层兜底）
        estimator = CascadeEstimator([
            P86PlusSigmaLayer(samples, safety_factor),
            MeanTimesSafetyLayer(samples, safety_factor),
            StaticConfigLayer(config.avg_tokens_per_message),
            HardcodedLayer(),
        ])
        
        # 3. SessionCache（集成估算器 + compaction 联动）
        cache = SessionCache(cache_config, estimator, compact_config)
        
        # 4. 兜底：emergency buffer
        self._emergency_buffer = {}
```

### 4.4 EventStore

```python
class EventStore:
    """Phase 7 — SQLite event_log 单一事实源 + cache + SSE push."""
    
    def __init__(
        self,
        db_path: Path,
        cache_config: CacheConfig | None = None,
        sse_pusher: Callable | None = None,
    ):
        # 同 MemoryManager：SQLite health_check + auto-repair
        ...
    
    def emit(self, session_id: str, event_type: str, data: dict) -> EventV2: ...
    def subscribe(self, session_id: str) -> AsyncIterator[EventV2]: ...
    def replay(self, session_id: str, from_seq: int = 0) -> list[EventV2]: ...
```

## 5. 兜底方案（13 个失效场景全覆盖）

### 5.1 算法层 4 层 cascade

```
Layer 1: p86 + σ × safety_factor
  ↓ 异常
Layer 2: mean × safety_factor
  ↓ 异常
Layer 3: config.avg_tokens_per_message (静态)
  ↓ 异常
Layer 4: hardcoded 200
```

### 5.2 Sanity Clamp

```python
if value < 10: return 10
if value > 10_000: return 10_000
```

### 5.3 容量 cascade

```
Layer 1: derived from compaction + estimated_avg
  ↓ 异常/无效
Layer 2: derived from compaction + config.avg_tokens_per_message
  ↓ 异常
Layer 3: config.max_entries
  ↓ 异常
Layer 4: config.min_entries (1000 floor)
```

### 5.4 后端层降级

```
SQLite 健康 → SQLiteStore
  ↓ health_check 失败
SQLite 损坏 → auto_repair (sqlite3 .dump + re-import)
  ↓ 失败
降级到 InMemoryStore (is_degraded=True)
```

### 5.5 系统层 emergency buffer

```python
# MemoryManager 内 emergency buffer
self._emergency_buffer: dict[str, list[Message]] = {}

def append(self, session_id, message):
    try:
        return self._primary_append(session_id, message)
    except Exception:
        self._emergency_buffer.setdefault(session_id, []).append(message)
        return f"emergency_{uuid.uuid4().hex[:8]}"
```

### 5.6 chat.py legacy 兜底（最末兜底）

```python
# chat.py 中保留 _emergency_session_histories（不是主路径）
def _get_or_create_history(session_id: str) -> list[dict[str, Any]]:
    try:
        mm = get_default_memory_manager()
        if mm.is_degraded:
            return _emergency_session_histories.setdefault(session_id, [])
        return mm.get(session_id)
    except Exception:
        return _emergency_session_histories.setdefault(session_id, [])
```

### 5.7 ConfigValidator（env sanitize）

```python
# 防止 env vars 产生负数 / NaN / 非法值
config = ConfigValidator.validate(config_from_env())
```

### 5.8 SQLite auto-repair（启用）

```python
def auto_repair(self) -> bool:
    """sqlite3 .dump + re-import 到新文件"""
    backup = db.with_suffix(f".corrupt.{int(time.time())}.db")
    shutil.copy2(db, backup)
    dump = subprocess.run(["sqlite3", str(db), ".dump"], ...)
    fresh = db.with_suffix(f".repaired.db")
    subprocess.run(["sqlite3", str(fresh)], input=dump.stdout, ...)
    shutil.move(fresh, db)
```

### 5.9 Two-phase commit compaction

```python
def compact(self, session_id, strategy) -> bool:
    try:
        self._db.compact_messages(session_id, strategy)  # Phase 1
    except Exception:
        return False  # cache NOT invalidated
    
    try:
        self._cache.invalidate(session_id)              # Phase 2
        self._estimator.invalidate()
    except Exception:
        pass  # SQLite 已是新数据，下次 get 走 cold path
    
    return True
```

### 5.10 Health Endpoint

```python
@router.get("/api/health/memory")
async def memory_health() -> dict:
    mm = get_default_memory_manager()
    return mm.health_report().__dict__
```

## 6. 数据流

### 6.1 写入路径

```
MemoryManager.append(sid, msg):
    async with locks.get(sid):
        msg_id = db.insert_message(sid, msg)        # SQLite (WAL)
        cache.append(sid, {**msg, "id": msg_id})    # LRU 提到头部
        # WRITE_THROUGH 保证 cache ⊆ SQLite
```

### 6.2 读取路径

```
MemoryManager.get(sid, use_cache=True):
    if use_cache and (cached := cache.get(sid)):
        cache.touch(sid)                            # LRU 更新
        return cached
    msgs = db.list_messages(sid)                    # SQLite cold miss
    cache.set(sid, msgs)                            # 填 cache
    return msgs
```

### 6.3 估算路径

```
cache.append(sid, msg) → maybe_re_resolve():
    now - last_resolve >= 60s → estimator.estimate():
        for layer in [p86, mean, config, hardcoded]:
            try: return layer.compute()
            except: continue
        return hardcoded
    derived = derive(estimated_avg, compact)
    current_max = max(min_entries, derived)
```

## 7. 10 步实施

| 步骤 | 内容 | 改动量 | 风险 |
|---|---|---|---|
| 1 | 写本 doc | ~600 行 | 0 |
| 2 | 新建 `core/agent/cache.py` | ~280 行 | 低 |
| 3 | 新建 `core/agent/memory_manager.py` | ~320 行 | 低 |
| 4 | 删 chat.py 主路径 + 改 emergency fallback | -25 行 | 中 |
| 5 | 改 cli/tui/session.py + app.py | -15 行 | 中 |
| 6 | 改 service.py:_run_with_agent | ~30 行 | 中 |
| 7 | 新建 `core/agent/event_store.py` | ~280 行 | 中 |
| 8 | 删 EventBus + EventBusV2 + 替换 sse_buffer.py | ~150 行 | **高** |
| 9 | 写 memory_manager_test（~20）+ event_store_test（~10）+ cascade_test（~10） | ~600 行 | 中 |
| 10 | 全量回归 + grep 验证 | — | 中 |

预计 10 commits。

## 8. 测试策略

### 8.1 tests/test_memory_manager.py（~20 测试）

| # | 测试 | 验证 |
|---|---|---|
| 1 | `test_append_writes_to_sqlite_and_cache` | WRITE_THROUGH 强一致 |
| 2 | `test_get_returns_cache_when_warm` | cache 命中 |
| 3 | `test_get_rebuilds_cache_on_miss` | SQLite 重建 |
| 4 | `test_use_cache_false_bypasses_cache` | 直读 SQLite |
| 5 | `test_clear_deletes_from_sqlite_and_cache` | 双清 |
| 6 | `test_lru_eviction_drops_oldest_session` | LRU 1000 |
| 7 | `test_concurrent_append_thread_safe` | per-session lock |
| 8 | `test_compact_invalidates_cache` | compaction → cache |
| 9 | `test_default_db_path_creates_dir` | ~/.quantnodes mkdir |
| 10 | `test_env_override_db_path` | SR_SESSIONS_DB env |
| 11 | `test_cross_process_sqlite_sharing` | TUI → Web 跨进程 |
| 12 | `test_factory_singleton` | Factory 复用 |
| 13 | `test_cache_config_from_env` | env 解析 |
| 14 | `test_chat_legacy_fallback_when_mm_fails` | emergency 接管 |
| 15 | `test_emergency_buffer_used_when_all_backends_fail` | 最后兜底 |
| 16 | `test_health_report_shows_degraded_state` | health 反映降级 |
| 17 | `test_list_recent_sessions` | 按 updated_at desc |
| 18 | `test_compact_phase1_failure_keeps_cache` | Two-phase |
| 19 | `test_compact_phase2_failure_recovers_on_next_get` | Phase 2 失败 |
| 20 | `test_two_phase_compact_success` | 完整链路 |

### 8.2 tests/test_event_store.py（~10 测试）

| # | 测试 | 验证 |
|---|---|---|
| 1 | `test_emit_writes_to_sqlite_and_cache_and_sse` | 三处同步 |
| 2 | `test_subscribe_replays_cache_then_live` | iterator |
| 3 | `test_sse_pusher_callback_invoked` | callback |
| 4 | `test_concurrent_emit_thread_safe` | lock |
| 5 | `test_event_log_persistence` | 进程重启 |
| 6 | `test_replay_from_seq` | 增量重放 |
| 7 | `test_last_seq_monotonic` | seq 递增 |
| 8 | `test_lru_eviction_protects_event_log` | 淘汰不丢 SQLite |
| 9 | `test_factory_create_with_db_path` | 路径覆盖 |
| 10 | `test_event_bus_v2_replaced` | grep 0 命中 |

### 8.3 tests/test_cache_cascade.py（~10 测试）

| # | 测试 | 验证 |
|---|---|---|
| 1 | `test_p86_plus_sigma_basic` | 算法正确 |
| 2 | `test_p86_robust_to_outliers` | outlier 鲁棒 |
| 3 | `test_safety_factor_multiplies_result` | safety × |
| 4 | `test_cascade_p86_to_mean` | Layer 1 → Layer 2 |
| 5 | `test_cascade_mean_to_static` | Layer 2 → Layer 3 |
| 6 | `test_cascade_static_to_hardcoded` | Layer 3 → Layer 4 |
| 7 | `test_sanity_clamp_prevents_extreme` | clamp [10, 10K] |
| 8 | `test_cascade_layer_failure_counts` | telemetry |
| 9 | `test_cascade_active_layer_telemetry` | health |
| 10 | `test_re_resolve_interval_triggers_recount` | 60s 触发 |

### 8.4 tests/test_config_validator.py（~5 测试）

| # | 测试 |
|---|---|
| 1 | `test_validator_sanitizes_negative_values` |
| 2 | `test_validator_sanitizes_zero_values` |
| 3 | `test_validator_sanitizes_nan_values` |
| 4 | `test_validator_preserves_valid_values` |
| 5 | `test_validator_logs_issues` |

### 8.5 tests/test_sqlite_health.py（~4 测试）

| # | 测试 |
|---|---|
| 1 | `test_health_check_returns_true_for_healthy_db` |
| 2 | `test_health_check_returns_false_for_corrupted_db` |
| 3 | `test_auto_repair_restores_data` |
| 4 | `test_auto_repair_keeps_backup` |

### 8.6 现有测试（不能破）

- `test_memory_persistent.py` / `test_memory_enhance.py` / `test_memory_fts5.py`
- `test_session*.py` + `test_session_memory.py`
- `test_chat_send_sync_run_traversal.py`
- `test_event_bus_v2.py` + `test_event_bus_v2_internal.py`（删除前确认）

## 9. 风险与缓解

| 风险 | 影响 | 缓解 |
|---|---|---|
| TUI + Web 跨进程 SQLite 文件锁 | 中 | WAL + busy_timeout=5000 |
| EventBus 删除影响 B4 projector | 高 | 步骤 8：先迁移 projector.flush 到 EventStore.on_write trigger |
| LRU 1000 不够 | 低 | env 可调 |
| 写策略误选 | 低 | 默认 WRITE_THROUGH |
| TUI 退出丢状态 | 中 | SQLite 持久化 |
| SQLite 损坏 | 中 | auto_repair (sqlite3 .dump) — 启用 |
| MemoryManager 整体不可用 | 低 | chat.py:_emergency_session_histories |
| Config env 异常 | 低 | ConfigValidator.sanitize |

## 10. 提交策略

| Commit | 范围 | 信息 |
|---|---|---|
| 1/10 | 本 doc | `docs(chat-agent): Phase 7+8 设计 — EventStore + MemoryManager 单一事实源` |
| 2/10 | `core/agent/cache.py` | `feat(agent): CacheConfig + CascadeEstimator + SessionCache + ConfigValidator` |
| 3/10 | `core/agent/memory_manager.py` | `feat(agent): MemoryManager Protocol + UnifiedMemoryManager + auto-repair + emergency buffer` |
| 4/10 | chat.py 改 emergency fallback | `refactor(chat): 删主路径 + 保留 emergency fallback` |
| 5/10 | cli/tui/session.py + app.py | `refactor(tui): 走 MemoryManager 共享 ~/.quantnodes/sessions.db` |
| 6/10 | service.py | `refactor(service): history 来源走 MemoryManager` |
| 7/10 | `core/agent/event_store.py` | `feat(agent): EventStore — SQLite event_log 单一事实源` |
| 8/10 | 删 EventBus + EventBusV2 + sse_buffer.py | `refactor(event): 删 EventBus + EventBusV2 走 EventStore` |
| 9/10 | 测试套件 | `test(agent): MemoryManager + EventStore + Cascade + Validator + SQLite health 测试` |
| 10/10 | 全量回归 + grep | `chore(chat): Phase 7+8 全量验证` |

## 11. 验证清单

- [ ] `test_memory_persistent.py` / `test_memory_enhance.py` / `test_memory_fts5.py` 通过
- [ ] `test_session*.py` + `test_session_memory.py` 通过
- [ ] `test_chat_send_sync_run_traversal.py` 通过
- [ ] `test_event_bus_v2.py` + `test_event_bus_v2_internal.py` 通过（删除前确认）
- [ ] `python3 -m ruff check` clean
- [ ] `grep -r "_session_histories" src/` → 0 命中（主路径）
- [ ] `grep -r "class EventBus\b\|EventBusV2" src/` → 仅 event_store.py
- [ ] TUI + Web 跨进程 SQLite 共享测试
- [ ] 5 写策略 + 3 淘汰策略配置测试
- [ ] CascadeEstimator 4 层兜底测试
- [ ] ConfigValidator sanitize 测试
- [ ] SQLite health_check + auto_repair 测试（启用）
- [ ] Two-phase commit compaction 测试
- [ ] emergency buffer 测试
- [ ] Health endpoint 测试

## 12. 后续阶段（Phase 9+）

按 phase1 9 层架构：

| 阶段 | 任务 |
|---|---|
| Phase 9 | StructuredOutputParser 落地（strict→repair→regex→none 4 层降级） |
| Phase 10 | CircuitBreaker / RetryPolicy 整合进 AgentLoop |
