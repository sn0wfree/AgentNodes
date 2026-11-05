# 平台数据面业界调研 — 2026-10-26

> 配套：`platform-data-plane-discussion-20261026.md`（讨论稿，未定案）。本报告校准其 5 个悬而未决点，**不做终拍**。
> 对象：LangGraph checkpointer · Temporal（历史+归档）· Langfuse（双库+去重）· Claude Code（transcript）· OpenClaw · 事件溯源标准模式。
> 结论先行：**业界没有统一答案，但按规模分层清晰——单机单库快照+事件（LangGraph/ES-in-SQLite）→ 双库分治（Langfuse，SaaS 规模）→ 主库+归档到 blob（Temporal）；我们讨论稿的方案 A 落在业界单机形态的主流位置，四防护各有先例。**

---

## 1. 六路证据

### LangGraph（checkpointer —— 快照流派）
- 持久化单元是 **checkpoint**（每个 super-step 一份）：`channel_values`（状态载荷）+ `channel_versions`（每通道单调版本）+ **pending_writes**（节点中途失败时，同层兄弟节点已完成的写入被保留、恢复时应用）。
- 后端迁移路径：InMemorySaver → **SqliteSaver**（本地/轻量）→ PostgresSaver（生产）；另有 Redis/KurrentDB 等社区实现。
- **关键区分**：checkpointer（线程内短期记忆）与 store（长期记忆）是两套系统——不是一份存储打天下。
- 流派定性：**快照流派**（不是事件溯源）——靠"每步存全量快照"实现 time-travel/fork/恢复。

### Temporal（事件溯源流派 + 归档制度）
- 事件史（Event History）存主库，按**固定数量的 history shard** 分片；visibility（可见性/查询）记录分离管理。
- **保留期（retention）制度**：workflow 关闭后超过 namespace 保留期 → 从主库**删除**；除非启用 **Archival** → 自动复制到 blob 存储（S3/GCS）长期保存，UI/API 仍可查。
- 教训条目：retention 是一等配置（per-namespace），archival 是其必配的后悔药。

### Langfuse（双库分治 + 去重 —— SaaS 规模形态）
- V3 架构：**Postgres 管事务数据**（项目/用户/**prompt 管理**/API key），**ClickHouse 管海量遥测**（traces/observations/scores），S3 管大 blob，Redis 做异步 ingestion 队列。
- **重复载荷去重在数据建模层做**（不逐事件复制 prompt 内容）——与我们内容寻址 blob 同思想。
- 自托管社区有 trim 脚本按需裁剪 trace 存储——保留策略是运维常态需求。

### Claude Code（文件流派 + 保留教训）
- 每会话一个 **JSONL 文件**（`~/.claude/projects/` 下按项目路径编码），`sessions-index.json` 只是**缓存**——权威数据是 JSONL 本体（issue #44346 明确此分层）。
- `--resume <uuid>` 按 UUID 恢复；不支持跨设备（文件就在本地）。
- **教训**：约 30 天后 transcript **静默自动删除**（issue #62959），用户发现后不满——**粗暴的统一保留策略 + 删除不可见 = 社区反弹**。保留必须分级、可见、可配置。

### OpenClaw（最小持久化流派，前轮调研已得）
- 审计账本**只投影元数据**（不复制 prompt/消息/工具结果）；scheduler 持久化只存 deadline，owner 启动时重建计划；session 状态带版本迁移。
- 思想：**持久化最小面 + fold 重建**，与我们同构。

### 事件溯源标准模式（SQLite 单文件形态）
- 标准表：`(global_seq INTEGER PRIMARY KEY, aggregate_id, aggregate_version, event_type, payload JSON, ts)`；**只 INSERT/SELECT**；`UNIQUE(aggregate_id, aggregate_version)` 提供乐观并发；周期快照；**天然适配单 SQLite 文件**（2026-03 SQLite 官方论坛专文）。
- 我们的信封 `{seq, session_key, run_id, kind, data, schema_v}` 就是该模式把 aggregate_id 换成 session_key 的直接实例。

## 2. 五个悬点的业界校准（仍不终拍）

| # | 悬点 | 业界证据 | 校准后的倾向 |
|---|---|---|---|
| 1 | 单库 A vs 两库 B | LangGraph SqliteSaver + ES-in-SQLite = 单库是**单机形态主流**；Langfuse 双库是 **SaaS 多租户规模**才需要的形态；Temporal 主库+分离件是服务端形态 | **A 起步**（与讨论稿一致），触发条件不变（>1GB/延迟/VACUUM 扰）→ 演进到 B。规模决定形态，我们当前规模在 A 侧 |
| 2 | blob 去重表 GC | Langfuse 建模层去重、未见逐条 GC 制度 | **v1 不 GC 可行**（去重后总量小）；真要 GC 时按「无事件引用的哈希」低频清扫 |
| 3 | 分级保留类目 | Temporal per-namespace retention + archival；**Claude Code 反例**：统一 30 天静默删 → 用户反弹 | 倾向确认：per-kind 保留级 + **删除可见**（归档事件进事件流）+ 保留表进 ConfigHub。类目 v1 粗分两档（fold 关键 / 低值）够用 |
| 4 | 快照周期与形态 | LangGraph 每 super-step 一份（小规模无压力）；Temporal 靠事件重放+history 完整 | **每 turn settle 一份 + 每 N 事件兜底一份**；形态=投影表整体导出（快照即 proj_* 命名空间拷贝）|
| 5 | 归档格式与位置 | Temporal → blob（S3/GCS）；Claude Code 文件即权威 | v1 **JSONL 月度文件**（人可读、可 grep、Claude Code 同款），放 workspace 下归档目录；parquet 等分析需求真出现再上 |

## 3. 额外收获（超出五个悬点）

1. **pending_writes 语义**（LangGraph）：同层兄弟节点已完成的写入在故障恢复时保留——直接喂给 A1/B1 的 DAG 失败语义讨论（③ 失败怎么办），比"标红跳过"更精细。
2. **缓存非权威**（Claude Code #44346）：sessions-index 只是缓存、JSONL 才是权威——印证我们 P-3 登记表定位：**登记行是索引/缓存，文件才是权威**，二者损坏时以文件为准重建登记。
3. **两套记忆系统**（LangGraph checkpointer vs store）：我们 P-2 双轨制（运行态投影 vs 实体定义）与其划分同构，方向再获印证。
4. **异步 ingestion + 队列**（Langfuse Redis）：我们单进程不需要，但若未来事件写入成为热点，Redis/队列是现成演进路径——记入逃生门清单备查。

## 4. 补充调研：OpenCode 后端存储设计（2026-10-26 专项）

### 形态演变：JSON 文件树 → SQLite 单库（与 Claude Code 相反的演进方向）
- **Gen 1**：`storage/session/<projectID>/<sessionID>.json` + `message/<sessionID>/*.json` + `part/<messageID>/*.json`——每实体一个文件
- **Gen 2（现）**：单一 **`opencode.db`**（`~/.local/share/opencode/`，XDG；Windows `%LOCALAPPDATA%`）；v1→v2 迁移把文件镜像入库，文件库降级为 db 打不开时的 fallback
- **关键信号**：Claude Code 用 JSONL、OpenCode 从 JSON 文件**迁进** SQLite——文件流派在「需要跨会话查询/事务/生态读」时收敛到单库。我们方案 A 直接落在终点形态

### 当前 schema（session → message → part 三级）
| 表 | 关键列 | 说明 |
|---|---|---|
| session | id, **parent_id**, slug, title, directory, time_created/updated, model(JSON), cost | **子代理=同表自引用 parent_id**（与我们 run 树同构）；顶层会话=parent_id IS NULL |
| message | id, session_id, **data(JSON blob)** | role/时间戳/模型/含缓存的 token 用量/finish reason 全在 JSON 里 |
| part | id, message_id, session_id, data(JSON blob) | 内容细分件：text/reasoning/tool 调用及状态/patch/**step-start/step-finish 标记**（数量最多的行=高频低值类）；compaction=合成 user 消息+compaction part |

### 设计性格与教训
1. **JSON blob 列换 schema 灵活**：新 part 类型免迁移——代价是**schema 漂移成为常态**，第三方读者必须 `PRAGMA table_info` + `json_valid` 防御性解析（生态工具文档明文教学）。我们的信封契约+键控迁移是更纪律的一侧
2. **无事件日志、无去重**：存的是状态与消息（resume=直接载入），不存事件流——因为它 turn=单循环、不需要 fold/审计；**step 标记是最多的行**（正是我们「高频低值保留级」的同类）
3. **WAL + 只读读者 busy_timeout**：实时写在 -wal/-shm 未 checkpoint 时对外不可见；生态已长出直接读 db 的工具（vibe-replay/mnemo 索引搜索）——**单库可查询性催生生态**，印证我们「SQL 可查当前态」的价值
4. **保留**：默认裁剪旧数据（pruning by default）
5. cost 字段存 0.0 表示「未计费」而非真免费——语义含混的教训：**我们的预算字段必须显式区分零值与未记录**

### 对方案 A 的校准
四点同构验证：单机单库 ✓ · 实体与内容同库 ✓ · 三级结构（session→turn/event→message）✓ · 子代理树自引用 ✓。两点保持分歧（我们有而它无）：事件流+fold（steer/审计/多形状 turn 需要）、引用代替复制去重（它 O(n²) 复制照收——规模未痛）。

## 5. 专项：事件流 × 流式输出（2026-10-26 用户提问）

> 问题：token 级流式输出要不要落事件流？业界怎么切这条边界。

### 业界四条硬口径

| 系统 | 口径 | 证据 |
|---|---|---|
| **Temporal** | 事件史有**硬上限 51,200 事件 / 50MB**（10,240 事件或 10MB 告警，超限**直接终止执行**）；单请求载荷上限 **2MB**（gRPC 4MB，含整个事件史事务）；大载荷官方建议**外置存储、内联保持小** | [Cloud limits](https://docs.temporal.io/cloud/limits) |
| **Temporal（语义）** | **Query 不写历史**，Signal/Update 写历史；Activity 的**参数与结果进历史，执行过程不进**——即历史只记「发生了」不记「中间过程」 | [workflow message passing](https://docs.temporal.io/encyclopedia/workflow-message-passing) |
| **LangGraph** | 流式有**七种模式**：`values`（每步全量状态）/`updates`（每步变更）/`messages`（LLM token 对）/`custom`/`checkpoints`/`tasks`/`debug`；**持久化发生在 checkpoint（super-step 边界）**，token delta 走 `messages` 流式通道；`checkpoints`/`tasks` 模式需 checkpointer | [Streaming](https://docs.langchain.com/oss/python/langgraph/streaming) |
| **OpenCode** | 服务端 SSE 推送事件流（`/event`、`/global/event`），但官方文档**未声明** delta 是否落库；其 part 表存在 **step-start/step-finish 标记**且为最多行——侧面证据：**按段（part）落库，不按 token** | [server docs](https://opencode.ai/docs/server/) + 本地存储解析 |

### 收敛出的三条规律

1. **流式与持久化是两条通道，从不合一**。LangGraph 最明确：token 走 `messages` 流，持久化在 `checkpoints`。Temporal 更极端：过程根本不进历史。**没有一家把 token delta 写进持久日志**——写入风暴 + 回放无意义（token 无法重放成同一结果，因为模型本身不确定）。
2. **持久化边界=语义单元完成**：消息完成、part 完成、super-step 完成。中间态只服务 UI。
3. **历史/日志必须设硬上限**（Temporal 的数字给了我们量级参照：几万条、几十 MB 就是设计红线，超了要切分或外置）。

### 对我们的落法（直接回答 A⁻ 应怎么切）

```
token delta  ──► 只走 SSE/内存扇出（UI 实时）        ✗ 不进事件表
part/消息完成 ──► 一次 message_appended 事件（内容经 blob 引用）  ✓ 进事件表
turn 状态迁移 ──► 事件                                    ✓
```

三条附属结论：
- **流式内容永不落库**，只落「完成态」。若需要恢复未完成的流，靠重新生成（模型非确定性，重放无意义）。
- **消息完成才 emit** —— 这同时解决了「每个 chunk 一条 INSERT」的写入风暴问题（一轮研究 token 事件数从万级回到数十级）。
- **给我们的事件表加上与 Temporal 同类的护栏**：单事件 ≤64KB（已定）、单 session 事件数告警线（建议 10k 告警 / 50k 强制归档切分）= 已定 store_size 监视的补充。

## 6. 出处

- LangGraph：[Persistence](https://docs.langchain.com/oss/python/langgraph/persistence) · [Checkpointers](https://docs.langchain.com/oss/python/langgraph/checkpointers) · [checkpoints reference](https://reference.langchain.com/python/langgraph/checkpoints) · [streaming](https://docs.langchain.com/oss/python/langgraph/streaming) · [sqlite saver 源码](https://github.com/langchain-ai/langgraph/blob/main/libs/checkpoint-sqlite/langgraph/checkpoint/sqlite/__init__.py)
- Temporal：[Archival setup](https://docs.temporal.io/self-hosted-guide/archival) · [history service 架构](https://github.com/temporalio/temporal/blob/main/docs/architecture/history-service.md) · [retention 讨论](https://community.temporal.io/t/temporal-history-retention-period/4348) · [Cloud limits（51,200 事件/50MB、2MB 载荷）](https://docs.temporal.io/cloud/limits) · [事件/消息语义](https://docs.temporal.io/encyclopedia/workflow-message-passing)
- Langfuse：[Simplifying for Scale](https://langfuse.com/blog/2026-03-10-simplify-langfuse-for-scale) · [V3 基础设施演进](https://langfuse.com/blog/2024-12-langfuse-v3-infrastructure-evolution) · [ClickHouse at agent scale](https://langfuse.com/resources/engineering/clickhouse-at-agent-scale) · [数据模型](https://langfuse.com/docs/observability/data-model)
- Claude Code：[#44346 index 非权威](https://github.com/anthropics/claude-code/issues/44346) · [#62959 保留删除](https://github.com/anthropics/claude-code/issues/62959) · [#42219 本地文件](https://github.com/anthropics/claude-code/issues/42219)
- 事件溯源模式：[SQLite 官方论坛：Append-Only Design](https://www.sqliteforum.com/p/event-sourcing-with-sqlite) · [Azure Event Sourcing](https://learn.microsoft.com/en-us/azure/architecture/patterns/event-sourcing)
- OpenClaw：见 `queue-mode-research-20261024.md` / `session-granularity-research-20261024.md` 已引文档
- OpenCode：[opencode.db 本地存储解析](https://vibe-replay.com/blog/opencode-local-storage) · [两代存储（JSON→SQLite）](https://docs.rs/opencode-session-restore/latest/opencode_session_restore/) · [会话持久化综述](https://blog.devgenius.io/agent-session-persistence-in-opencode-9c3440448c06) · [上下文指南](https://iceberglakehouse.com/posts/2026-03-context-opencode)
