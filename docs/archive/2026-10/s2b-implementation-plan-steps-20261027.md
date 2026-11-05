# S2b 实施规划 — 分步改造清单 — 2026-10-27

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


> 设计依据：`s2b-implementation-details-20261027.md`（已定案：B 遍历 / sessions 搬全局 /
> 台账迁移 / 800ms+100ms 超时 / 只升不降自适应 / 中文短词 LIKE 兜底）。
> 本文件把设计拆成**可独立验证的 6 步**，每步有明确验收与回退点。

---

## 0. 关键结构约束（勘察结论）

| 现状 | 影响 |
|---|---|
| `Projector(db_path)` 绑定单一库（`store.py:210`、`event_store.py:574`） | per-session 后必须**按会话动态构造或缓存** |
| `EventStore(db_path)` 同理（S1 门面，内部持 PlatformStore） | 同上；但 EventStore 只碰 event_log，可保留单实例但换 per-session 路由 |
| `web_session_db._get_db()` / `_session_db()`（S2a 已拆） | 路由翻转点，只需改这两个函数内部 |
| `container.build_container(db_path)` 单路径装配 | 需支持「全局库路径」语义（会话库按需取） |
| `projection_store` 的 `tidy`/`flush` 用同一连接 | 批内事务不变，只是连接来源换成会话文件 |

**结论**：S2b 的复杂度集中在「投影器/事件存储的按会话化」，不在调用点（S2a 已解决）。

---

## 0. 已确认的两个决策（2026-10-27）

| 决策 | 选择 | 含义 |
|---|---|---|
| 翻转策略 | **带开关灰度** | `SR_SESSION_FILES`（默认关）——关时走旧单库（当前行为完全不变），开时走 per-session；一行配置回退 |
| 迁移时机 | **启动时全量迁移** | 首次启动迁移全部 83 会话（实测毫秒级），而非惰性；启动快照一致、后续代码路径无「混合态」分支 |

**启动迁移的实现**：`container.build_container` 或 `create_app` 启动阶段调用
`migrate_all()`；台账保证幂等（第二次启动零成本跳过）。失败则**不启动新布局**
（回落单库 + 记录错误事件），避免半迁移状态。

---

## 1. 六步改造计划

### 步骤 1：路组件 + 全局库（无行为变化，可独立验证）

**产出** `core/storage/session_files.py`：

```python
class SessionFileRouter:
    def __init__(self, workspace: Path, *, legacy_path: Path | None = None)
    def global_conn(self) -> sqlite3.Connection      # platform.sqlite
    def session_path(self, session_id: str) -> Path  # 只算路径，不建连
    def session_conn(self, session_id: str) -> sqlite3.Connection
    def is_migrated(self, session_id: str) -> bool
    def close(self) -> None
```

- `platform.sqlite`：`sessions` + `schema_migrations` + `migrated_sessions`；
- 全局库**不迁数据**（步骤 3 才搬）；
- 路由**暂不启用**：只建组件 + 单测（建表、路径计算、幂等）。

**验收**：路由单测绿；无生产代码引用（零行为变化）。
**回退**：删除文件即可。

### 步骤 2：迁移引擎（离线、可演练）

**产出** `core/storage/session_migration.py`：

```python
def migrate_session(router, session_id, *, src_conn) -> dict  # 行数统计
def migrate_all(router, *, src_conn) -> dict                 # 批处理
```

- 逐表复制（messages / message_parts / attempts / event_log / snapshots / blob_refs）；
- **事务内逐表 COUNT 校验**，不等即 ROLLBACK + raise；
- 台账 `migrated_sessions(session_id, row_counts_json, migrated_at, source)`；
- `sessions` 全量搬全局库（83 行）+ `blob_refs`（全局共享引用计数）。

**验收**：用**副本**旧库跑 → 83 会话逐表 COUNT 相等；重复执行幂等；`data/` 删除后可重跑。
**回退**：删 `data/` 重来（旧库不动）。

### 步骤 3：路由翻转（核心，风险最高）

**改动**（只改两个函数内部 + 投影器按会话化）：

| 文件 | 改动 |
|---|---|
| `web_session_db._get_db()` | → `router.global_conn()` |
| `web_session_db._session_db(sid)` | → `router.session_conn(sid)`（触发惰性迁移） |
| `web_session_db._ensure_schema` | 拆为 `ensure_global_schema` / `ensure_session_schema`（当前共用，翻转后要分开） |
| `EventStore` | 增加 `session_db_resolver: Callable[[str], Path]`；`_persist/_replay/_next_seq` 按 aggregate_id 选库（不经 PlatformStore 单实例） |
| `Projector` | 由「单 db_path」改为「按 session 取连接」（`Projector(resolver)`，内部缓存每会话连接） |
| `container.build_container` | 传 router 而非单一 db_path |

**验收**：
- 新旧会话并行可读；
- 旧会话首次访问自动迁移；
- **完整回归**：webui / sse / message_parts / projector / event_log / study 全家族；
- 旧库 mtime 不变（只读断言）。

**回退**：`SR_SESSION_FILES=0` 开关 → 路由退回单库模式（默认值即关，翻转前零影响）。

### 步骤 4：搜索重写（B 方案 + 超时 + 中文兜底）

**产出**：重写 `search_sessions`。

- fan-out 遍历（`updated_at DESC`、`archived=0`、上限 500）；
- 双层超时（800ms / 100ms，`set_progress_handler`，`finally` 清理）；
- 连接复用（走 `_session_db`）；
- **中文短查询（<3 字符）走 LIKE 兜底**；
- 返回 `scanned_sessions` / `skipped_sessions` / `timed_out` / `truncated`。

**验收**：§8 的 8 条搜索验收（跨会话、中文短词、归档过滤、用户隔离、超时、损坏文件、handler 清理、幂等）。

### 步骤 5：性能日志 + 自适应

**产出** `core/storage/search_perf.py`：

- `SearchMetrics` 数据类 + `emit()`（一行 JSON，`search.perf`）；
- `AdaptiveBudget`（观察期 200 / ×1.2 上调 / 冷却 100 / **只升不降** / 400-3000ms）；
- `data/search_params.json` 持久化（启动读、tmp+rename 写）。

**验收**：模拟序列测试（快→不调、慢→上调、上限封顶、异常不影响搜索）。

### 步骤 6：收尾与文档

- 更新 `target-directory-structure` / 架构图相关说明；
- `resolve_session_db_path` 语义降级说明（迁移源）；
- 全量回归 + 审计记录。

---

## 2. 每步的验证命令

| 步骤 | 命令 |
|---|---|
| 1 | `pytest tests/test_session_files_router.py` |
| 2 | `pytest tests/test_session_migration.py` |
| 3 | `pytest tests/test_webui_api.py tests/test_sse_*.py tests/test_message_parts_*.py tests/test_projector*.py tests/test_event_store*.py` |
| 4 | `pytest tests/test_search_sessions.py` |
| 5 | `pytest tests/test_search_perf.py` |
| 6 | 全量分道跑 |

## 3. 工期

| 步骤 | 估时 |
|---|---|
| 1 路由组件 | 0.5d |
| 2 迁移引擎 | 0.5d |
| 3 路由翻转 | 1d（风险最高） |
| 4 搜索重写 | 0.5d |
| 5 日志 + 自适应 | 0.4d |
| 6 收尾 | 0.3d |
| **合计** | **3.2d** |

## 4. 风险与回退

| 风险 | 缓解 |
|---|---|
| 步骤 3 破坏 WebUI | 先做步骤 2 的离线演练；保留 `SR_SESSION_FILES=0` 开关一版 |
| 投影器按会话化引入连接泄漏 | 路由统一管理连接（LRU 上限），测试断言连接数 |
| 迁移中断 | 台账 + 事务；失败可重试 |
| EventStore 多实例内存增长 | 按会话缓存，LRU 上限（如 64）；超出关闭最旧 |
| 测试大量 patch `_get_db_path` | S2a 已改双 patch；翻转后保留该函数为「全局库路径」 |

## 5. 步骤 3 的额外设计（关键）

`EventStore` 与 `Projector` 的「按会话化」有两种做法：

| 做法 | 说明 | 选择 |
|---|---|---|
| **A：单实例 + resolver** | EventStore/Projector 持 `resolver(session_id) -> Path`，内部按会话懒开连接 + LRU 缓存 | ✅ 推荐（改动小、内存可控） |
| B：每会话一实例 | 调用方按会话 new 一个 | ❌ 破坏现有调用方，且实例管理复杂 |

**A 的实现要点**：
- `EventStore.__init__(db_path=None, *, session_db_resolver=None)`——二选一；
- 内部 `self._conns: dict[str, Connection]` + LRU（cap 64）；
- `_persist` / `_replay` / `_next_seq` 用 `self._conn_for(aggregate_id)`；
- `Projector` 同理（`ProjectionStore` 已有 `db_path` 参数，改为按会话解析）。
