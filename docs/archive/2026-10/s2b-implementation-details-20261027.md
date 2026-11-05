# S2b 实施细节 — 路由翻转（per-session 文件 + 惰性迁移 + 旧库只读）— 2026-10-27

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


> 前置：S2a 已完成（`be26ae15`：`_get_db()` = 全局入口、`_session_db(sid)` = 会话入口，
> 当前同指旧库）。本文件定 S2b 的实现细节、迁移协议、以及三个必须先解决的决策。

---

## 1. 现状事实（只读实测，2026-10-27）

| 项 | 值 |
|---|---|
| 旧库 | `<workspace>/.quantnodes_strategy_research_session.db`，5.4MB + 4.3MB WAL |
| `user_version` | **6**（既有迁移链，S2b 必须接在它之后） |
| 会话数 | 83 |
| 行数 | messages 190 / message_parts 299 / attempts 100 / **event_log 6036** / snapshots 1 / blob_refs 54 |
| **孤立数据** | **0**（messages/attempts/event_log 全部有对应 sessions 行） |
| 单会话最大 | 4 消息 / 2 attempts / **2407 事件**（其余多在 200 以下） |
| FTS 消费者 | **仅 `POST /api/chat/sessions/search`**；前端**未接线**（`webui/frontend/src` 无调用） |
| sessions 表 | 83 行，WebUI 会话列表/鉴权/用户枚举的唯一来源（跨会话实体） |

**关键判断**：数据量小（合计 6600 行）、零孤立、单会话有界 → **惰性迁移是安全且快的**
（最坏单会话 2407 行，毫秒级）。这消除了 S2b 最大的性能顾虑。

## 2. 目标布局

```
<workspace>/data/
├── platform.sqlite                  # 全局：sessions（实体登记）+ schema_migrations
│                                   #   + migrated_sessions（台账）+ search_index（见 §4）
└── sessions/<session_id>.sqlite     # 每会话：messages/message_parts/attempts/
                                    #   event_log/snapshots/blob_refs + messages_fts + 触发器
旧 .session.db                       # 只读源：迁移数据来源，此后不再写
```

**归属依据**（P-2 §10.5）：sessions 跨会话 → 全局；其余带 session_id → per-session。

## 3. 路由组件（新文件 `core/storage/session_files.py`）

```python
class SessionFileRouter:
    def __init__(self, workspace: Path, *, legacy_path: Path | None = None)
    def global_conn(self) -> sqlite3.Connection        # platform.sqlite（含 sessions 镜像）
    def session_conn(self, session_id: str) -> sqlite3.Connection
    def ensure_migrated(self, session_id: str) -> None # 懒迁移入口
    def is_migrated(self, session_id: str) -> bool
```

- **线程本地连接缓存**（沿用 `_get_db()` 现有机制，键 = 文件绝对路径）；
- **DDL 复用**：会话文件用 `web_session_db` 现有 `_ensure_schema`（含 FTS 五件套 +
  3 触发器，**顺序敏感块原样调用**，不重写）；
- **全局库**：只建 `sessions` + `schema_migrations` + `migrated_sessions` + `search_index`。

## 4. 决策一：FTS 跨会话搜索 —— **定案：遍历会话文件（B）**

> 完整调研见 `cross-session-search-research-20261027.md`（六方案对比 + 业界调研）。

**结论**：`search_sessions` 改为 fan-out 遍历 per-session 文件，各自 FTS 查询后合并。
不建索引、不做同步。

| 为什么不是别的 | 理由 |
|---|---|
| ~~A 全局 search_index~~ | 需要「写消息 → 同步索引」的跨库同步；实测 SQLite **禁止触发器跨库写**（`qualified table names are not allowed ... within triggers`），只能应用层同步；且 S3 写入归一后同步点要挪 |
| ~~C 删端点~~ | 功能回退、破契约 |
| ~~D 放弃分库~~ | 架构倒退（违背 P-2 自包含定案） |
| ~~E hash 引用索引~~ | 当前消息内容仅 28KB，hash 间接层不值得 |
| ~~F emit 单点索引~~ | 方向对（与 S3 同源），但 `EventStore.emit` 是最热路径，必须异步化才能加索引写 → 复杂度回升；列为**升级目标** |

**为什么 B 可行（实测支撑）**：
- 真实会话极短（WildChat ≈5 条消息/会话；本地中位 2 条）；
- 单文件查询 0.13-0.6ms，100 万级消息的会话也只需 20ms；
- 约束机制：**双层超时**（总 800ms / 单文件 100ms）+ 会话上限 500（按 `updated_at` 倒序）
  + 每文件 LIMIT 50，把线性退化变成有界延迟（详见 §4.2）。

**业界对照**：SQLite 官方推荐「每租户一文件」并明确跨文件搜索要靠应用层 fan-out；
Claude Code（每会话一 .jsonl）**不做跨会话全文搜索**；OpenCode / LangGraph 保持单库。
→ 我们的 B 已是业界最完整的做法。

**升级触发器**（任一命中则切 F：emit 单点索引 + 异步队列）：
实测 P95 > 500ms / 会话数 > 500 / 出现「搜全部历史」的真实需求。

### 4.1 B 的实现要点

1. 从全局库取用户会话（`updated_at DESC`、`archived = 0`，上限见 §4.3）；
2. 逐个 `_session_db(sid)` 查 `messages_fts MATCH`（**连接复用**，不新建连接）；
3. 合并按 rank 排序、截断 limit；
4. 单会话文件损坏/缺失 → 跳过并计数（不整体失败）；
5. 返回既有字段 + `scanned_sessions` / `skipped_sessions` / `timed_out` / `truncated`。

### 4.2 超时参数（实测标定）

**双层超时**，用 `sqlite3.set_progress_handler` 实现（实测可精确中断，开销 -3% 无感知）：

| 参数 | 值 | 依据（实测） |
|---|---|---|
| `SR_SEARCH_TIMEOUT_MS` | **800** | 覆盖 2000+ 真实会话（约 2 年重度使用）；实测 500 会话全量仅 372ms |
| `SR_SEARCH_PER_SESSION_MS` | **100** | 10 万条消息的会话也只需 20ms，100ms 有充分余量（异常巨大的文件才触发） |
| `SR_SEARCH_MAX_SESSIONS` | **500** | 按 `updated_at` 倒序，优先搜最近会话 |
| `SR_SEARCH_HITS_PER_SESSION` | **50** | 与 LIMIT 配合，避免单会话占满结果 |

**实现要点**：
- 连接复用（`_session_db` 线程本地缓存）：单文件成本从 0.45ms 降到 0.13ms（3.5x）；
- `set_progress_handler` **必须在 `finally` 里清理**（`(None, 0)`），否则残留 handler 会中断后续复用连接的查询；
- 中断粒度 = **单文件**（该文件剩余结果丢弃并计入 `skipped_sessions`），不做「半个文件」的不完整状态；
- 返回结构诚实反映未搜完：`timed_out` + `scanned_sessions` + `total_sessions`。

### 4.3 参数标定实测数据

**单文件查询耗时 vs 消息数**（trigram FTS，热缓存）：

| 消息数 | 耗时 |
|---|---|
| 100 | 0.59 ms |
| 1,000 | 1.76 ms |
| 10,000 | 10.2 ms |
| 30,000 | 34.5 ms |
| 100,000 | ~20 ms（LIMIT 50 生效后） |

**参数取值结论**（保守优先）：
- 总超时 **800ms** = 实测「500 会话全量 372ms」的 2.1 倍余量，同时覆盖 2000 级会话；
- 单文件 **100ms** = 实测「10 万条消息会话 20ms」的 5 倍余量，只会拦住异常巨大的文件；
- 原则：**宁可让正常搜索跑完（不被误杀），也不为压 P95 牺牲覆盖率**；超时只作为
  「病态数据/规模失控」的兜底，不是常态路径。

### 4.4 搜索性能日志 + 自适应调参（只升不降）

**日志**：每次搜索写一行结构化 JSON（`search.perf`），便于后续 `grep` / `jq` / 导入分析：

```json
search.perf {"ts":1793...,"query_len":5,"query_kind":"fts",
 "total_sessions":83,"scanned":83,"skipped":0,"hits":42,
 "elapsed_ms":135.2,"timed_out":false,
 "budget_ms":800,"per_session_ms":100,"avg_file_ms":1.63,"user_id":"..."}
```

核心字段：`elapsed_ms`（总耗时）、`avg_file_ms`（= elapsed / scanned，**不受会话数影响**，
是调参主指标）、`timed_out`、`skipped`。

**自适应规则（单向，只上调）**：

| 阶段 | 条件 | 动作 |
|---|---|---|
| **观察期** | 前 200 次搜索 | **不调整**（数据不足，避免误判） |
| 上调 | P95 > 预算 × 0.7 **或** 超时率 > 5% | 预算 ×1.2（上限 3000ms） |
| 冷却 | 每次上调后 | 至少 100 次搜索不再调 |
| **下调** | — | **永不**（用户决策：宁可慢，不可因偶发快查询退化） |

单文件预算跟随：`per_session_ms = max(50, budget / 8)`。

**为什么只升不降**：
- 偶发的快查询（缓存命中、小数据集）会把 P95 拉低，触发下调 → 下次遇到真实负载又超时 → 震荡；
- 上调的代价是「最多慢一点」，下调的风险是「搜索不完整」——**不对称，宁可保守**；
- 若确实需要回收，通过重启 + 删除 `data/search_params.json` 回到默认 800/100。

**持久化**：`data/search_params.json` 保存当前值（`budget_ms` / `per_session_ms` /
`updated_at` / `samples` / `p95_ms`）；启动优先读取，无则用默认值。

**安全阀**：
| 风险 | 措施 |
|---|---|
| 参数无限上调 | 硬上限 3000ms / 单文件 ≥50ms |
| 自适应异常 | 任何异常 → 保留当前值 + 记 `search.adaptive` 错误，不影响搜索 |
| 超时样本污染统计 | 超时样本只计超时率，不参与 P95 计算 |
| 并发写参数文件 | Gateway workers=1 已钉死，单写者；写文件用临时文件 + rename |

**分析价值**：日志积累后可做——按 `query_kind`（fts / like 兜底）分列成本、
按 `total_sessions` 分桶画「规模 vs 延迟」曲线验证升级阈值、按 `query_len` 找慢查询模式。

### 4.5 参数标定实测数据（续）

**会话规模增长**（每会话 100 条消息，激进假设）：

| 会话数 | 全量扫描 |
|---|---|
| 100 | 71 ms |
| 300 | 214 ms |
| 500 | 372 ms |
| 1,000 | 786 ms |
| 2,000 | 1,549 ms |

**业界会话长度调研**：
- WildChat（100 万真实对话）：2.5M 轮次 / 1M 会话 ≈ **2.5 轮/会话 ≈ 5 条消息**；
- 本地实测：83 会话 / 190 消息，中位数 2 条、最大 4 条；
- 结论：**真实会话极短（5-10 条消息）**，上表按每会话 100 条标定属保守估计，实际成本约 1/3。

**并发测试结论（不采用并发）**：

| 场景 | 4 线程相对串行 |
|---|---|
| 小文件（40 条）× 100-600 个 | 1.1-2.1x（不稳定） |
| **CPU 密集（20k 条 × 4 个）** | **0.50-0.70x（稳定变慢）** |
| 8+ 线程 | 0.4-1.0x |

原因：Python `sqlite3` 在执行 SQL 期间**持有 GIL**，CPU 密集查询无法并行；只有 I/O 段（连接打开）能重叠，
收益上限约 1.4x，且随文件增大转为负收益。**结论：串行 + 连接复用 + 超时，不用线程池。**

### 4.6 已发现的既有缺陷：中文 2 字词搜不到（需一并修复）

实测 `trigram` 分词器**要求查询串至少 3 个字符**：

| 查询 | 结果 |
|---|---|
| `策略`、`回测`、`动量`（2 字） | **0 hits** |
| `策略回测`、`夏普比率`（4 字） | ✅ |
| `abc`、`test`（≥3 字符） | ✅ |

中文高频词大量是 2 字，**当前搜索对中文基本不可用**（这也是前端未接线的可能原因）。

**修复方案**：查询预处理——`len(query) < 3` 时改用该会话 `messages` 表的 `LIKE '%词%'` 扫描
（慢但正确）；否则走 FTS。约 0.3d，**必须与 B 一并实现**，否则「搜索可用」是假的。

### 4.7 升级触发器

任一命中则切 F（`EventStore.emit` 单点索引 + 异步队列）：
- 实测搜索 P95 > 500ms；
- 会话数 > 2,000（800ms 预算开始覆盖不足）；
- 出现「必须搜索全部历史」的真实需求。

## 5. 决策二：sessions 表的归属与双写

S2b 后 `sessions` 在 `platform.sqlite`，但**旧表里也有它**（83 行，含 message_count /
starred / tags / archived）。

- **一次性搬迁**：S2b 首次启动时把旧 `sessions` 全量复制到 `platform.sqlite`（83 行，
  微秒级），记 `schema_migrations('sessions', 1)`；
- **此后唯一写入点是全局库**，旧库的 sessions 表不再读；
- **message_count 漂移**：S3（消息写入归一）之后由 projector 统一维护；S2b 阶段
  仍在 `persist_message` 里更新全局库的 sessions 计数（S2a 已拆成两次 commit，天然支持）。

## 6. 决策三：迁移协议（幂等 + 可校验 + 可回退）

```
ensure_migrated(sid):
  if is_migrated(sid): return                     # 台账命中，零成本
  if not legacy.exists(): mark_migrated(sid, "empty"); return
  src = legacy(ro)
  if not session_exists_in_legacy(sid): mark_migrated(sid, "absent"); return
  dst = open_session_file(sid)                    # 建 DDL + FTS
  BEGIN IMMEDIATE
    for table in (messages, message_parts, attempts, event_log, snapshots, blob_refs):
        INSERT INTO dst.<table> SELECT * FROM src.<table> WHERE <sid 列> = ?
    verify: 逐表 COUNT 相等（不等 → ROLLBACK + raise）
    record in platform.migrated_sessions(sid, row_counts_json, migrated_at)
  COMMIT
```

- **幂等**：台账是唯一判据；迁移事务内校验失败即回滚，台账不写，下次重试；
- **可回退**：删 `data/` 目录 + 台账，重启后从旧库重新迁移（旧库始终只读、内容不变）；
- **旧库只读**：`legacy_conn()` 用 `file:...?mode=ro` 打开，物理上防误写。

## 7. 接线改动（在 S2a 的两入口内翻转）

| 文件 | 改动 |
|---|---|
| `web_session_db._get_db()` | → `router.global_conn()` |
| `web_session_db._session_db(sid)` | → `router.session_conn(sid)`（内部触发 `ensure_migrated`） |
| `web_session_db._get_db_path()` | → 保留，语义降级为「全局库路径」（测试大量 patch 它） |
| `EventStore`（S1 门面） | PlatformStore 从 `db_path=legacy` 切 `data_dir=data/`；`_db_path` 用于 messages 的部分不动（EventStore 只碰 event_log） |
| `projection_store` / `Projector` | `db_path` 改传会话文件路径（S2a 已让 projector 零改动，只换构造参数） |
| `search_sessions` | 重写为 fan-out 遍历（§4.1）+ 双层超时（§4.2）+ 短查询 LIKE 兜底（§4.4）；**不建任何索引表** |

## 8. 验收

1. **迁移正确性**：83 会话逐会话行数对账（messages/parts/attempts/event_log/snapshots/blob_refs 六表 COUNT 全等）；
2. **幂等**：重复 `ensure_migrated` 不重复插入、不报错；
3. **隔离性**：两个会话各写消息互不可见（不同文件）；
4. **旧库只读**：迁移后旧库文件 mtime 不变（可用 stat 断言）；
5. **搜索正确性**：
   - 跨会话命中：在会话 A 写入的消息能从会话 B 的搜索中查到（同用户）；
   - 中文 2 字词：`策略`/`回测` 走 LIKE 兜底后能命中（§4.4 修复前为 0 hits）；
   - 归档过滤：`archived=1` 的会话不出现在结果里；
   - 用户隔离：A 用户搜不到 B 用户的会话。
6. **搜索健壮性**：
   - 超时：构造超过 800ms 的会话量 → 返回部分结果 + `timed_out=true` + `scanned_sessions`；
   - 单文件超时：构造超大单会话 → 该会话计入 `skipped_sessions`，其余正常返回；
   - 损坏文件：删除某个会话文件 → 跳过不报错；
   - handler 清理：连续多次搜索后，复用的连接仍能正常查询（验证 `finally` 清理）。
7. **回归**：webui / sse / message_parts / event_log_schema / projector 全家绿；
8. **回退演练**：删 `data/` → 重启 → 数据从旧库恢复。

## 9. 风险与缓解

| 风险 | 缓解 |
|---|---|
| 迁移事务与正在运行的 WebUI 写入竞争 | 迁移在 `BEGIN IMMEDIATE` + 台账双保险下进行；旧库只读，写入目标只有新文件 |
| 迁移后 `_get_db()` 语义变化影响 29 个调用点 | S2a 已分类；S2b 只换两个函数内部实现，调用点零改动 |
| FTS 触发器重建顺序 | 复用 `web_session_db._ensure_schema` 原样调用（不重写 DDL） |
| 测试大量 patch `_get_db_path` | 保留该函数，语义改为「全局库路径」；涉及会话文件的测试改用 `_session_db` |
| 旧库遗留 `-wal`/`-shm` 未 checkpoint | 迁移前对旧库执行 `PRAGMA wal_checkpoint(TRUNCATE)`（只读连接下改为一过性读写） |

## 10. 决策状态（2026-10-27 已定案）

| # | 决策 | 状态 |
|---|---|---|
| 1 | **FTS = 遍历会话文件（B）+ 三条约束** | ✅ 定案（调研见 `cross-session-search-research-20261027.md`） |
| 2 | **sessions + blob_refs 一次性搬全局库，全局唯一写入** | ✅ 定案（`blob_refs` 无 session 列，属跨会话共享资源，与 sessions 同归全局） |
| 3 | **旧库只读保留、永不删除**（回退依赖） | ✅ 定案 |
| 4 | 迁移协议：台账 + 事务内逐表 COUNT 校验 | ✅ 定案 |
