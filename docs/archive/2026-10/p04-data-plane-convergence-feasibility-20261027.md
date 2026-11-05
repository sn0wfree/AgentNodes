# 0.4 数据面收敛 — 可行性验证报告 — 2026-10-27

> 验证方式：只读调查（真实库 SQLite `mode=ro` 清点 + 静态 grep + 源码阅读），
> 未写任何生产代码。工具产物：`scripts/_p04_inventory.py` / `_p04_deps.py`（验证脚本，可复跑）。

---

## 1. 要验证的三个问题

1. WebUI session 库的表/行数/依赖方清点——迁移面有多大；
2. projector 能否只依赖 `event_log`（它本来就是 fold）——可移植性；
3. 「旧库只读兼容 + 惰性迁移」的工作量——迁移成本。

## 2. 事实发现

### 2.1 库的规模（真实库，只读清点）

| 项 | 值 |
|---|---|
| 文件 | `<workspace>/.quantnodes_strategy_research_session.db`（5.4MB + WAL 4.3MB） |
| 表 | 12 张：messages(187) / message_parts(298) / **event_log(6009)** / attempts(98, 17列) / sessions(81) / snapshots(1) / blob_refs(54) + FTS5 五件套（含 3 个同步触发器） |
| event_log 真实写入 | text.ended(146) / thinking_*(268) / assistant_message(137) / llm_request(121) / attempt.*(169) / tool_*(189)… |

### 2.2 ❗核心冲突：两套同名的 `event_log`，schema 不同

| | EventStore（旧，core/agent/event_store.py） | PlatformStore（新，core/storage/platform_store.py） |
|---|---|---|
| schema | `id, aggregate_id, seq, type, data_json, time_created, parent_event_id` | `seq, ts, kind, data_json, run_id, schema_v` |
| 词表 | EventType 枚举（20+ kinds，projector 依赖） | 自由字符串 kind |
| 血统 | aggregate_id / parent_event_id | run_id / schema_v |
| 谁在写 | api 全链（6009 条真实数据） | 无（仅 Gateway 构造点存在） |

而 **P-1 定案信封 = `{seq, ts, session_key, run_id, kind, data, schema_v}`**——两边各对一半：旧表缺 `ts/session_key/run_id`，新表缺 v2 词表与 `aggregate_id/parent_event_id`。**收敛必须先合并 schema，这不是搬表，是统一事件协议。**

### 2.3 messages 表有**四个**写入者（比差距报告多一个）

| 写入者 | 动作 | 服务对象 |
|---|---|---|
| `api/session/projection_store.py` | projector 投影 W/D/R | AgentLoop 事件链 |
| `core/agent/memory_manager.py` | 直接 INSERT（SQLiteStore） | CLI / chat 紧急回退 / goal |
| `api/routers/web_session.py` | CRUD | WebUI 会话路由 |
| `core/session/db.py` | CRUD + FTS 触发器 | `web_session` 路由 / CLI slash_session / TUI app |

### 2.4 projector 自足性：✅ 可移植

- `projection_handlers.py` 是**纯函数**（零 IO，fold 事件→状态）；`projection_store.py` 的连接以 `db_path` 参数化——**把 db_path 指向新库即可工作**，前提是新库有同构的 `event_log`；
- 它 fold 的 20+ 种 kind 全部来自 `EventType` 枚举（v2 词表）——所以「新库 event_log 用 P-1 信封 + kind 取 v2 词表」后，projector 无需改逻辑。

### 2.5 依赖面（迁移波及范围）

| 通道 | 文件数 | 主要文件 |
|---|---|---|
| EventStore | 11 | api/container、dependencies、chat、service、trace_projection、study.langgraph_engine、_archived×5 |
| event_bus | 11 | service、loop_events、study.scheduler、goal.workflow、cli.workflow_worker… |
| Projector | 8 | container、dependencies、chat、service、store、event_store |
| MemoryManager | 10 | container、dependencies、chat、slash_commands、web_session_db、cli.tui、goal.store |
| web_session_db | 11 | 全部会话路由 + service + store |
| SSE 双轨 | 7 | container、dependencies、chat、slash_commands、sse_buffer、bridge_v2 |

迁移脚本先例已有：`scripts/migrate_study_history.py`（表搬移 + 回填模式可复用）。

## 3. 结论

| 问题 | 判定 |
|---|---|
| projector 可移植？ | ✅ 纯函数 + 参数化 db_path，schema 对齐后零改动 |
| 单写者可行？ | ✅ 路径清晰：唯一写入者 = PlatformStore；其余三者改为「发 message_received 事件」 |
| 旧库只读兼容 + 惰性迁移 | ✅ 有先例可复用；sessions 表归全局库（跨会话）、attempts/messages 归 per-session 文件，映射关系明确 |
| 原估 3-5d 是否够？ | **❌ 不够。实际 6-8d**——范围比预想多出：两套事件协议合并（2.2）、第四写入者（2.3）、FTS5 触发器重建、attempts/sessions 分表归属 |

**总体判定：可行，值得做，但必须分四个独立可回退的步骤做，每步有独立验收。**

## 4. 0.4 实施设计草案（四步）

### S1 事件协议统一（3d，独立有价值，可单独提交）

1. `PlatformStore.event_log` 升级为 **P-1 信封 schema**：`{seq, ts, session_key, run_id, kind, data_json, schema_v, aggregate_id, parent_event_id}`——kind 词表取自 `EventType`（吸收 v2），血统字段全保留；
2. `EventStore` 改为 **PlatformStore 的门面**：emit/replay/replay_from 转调 PlatformStore，签名不变——11 个使用方零改动；
3. schema_migrations 键控迁移（新版本 v2），旧表数据搬迁；
4. **验收**：EventStore 既有测试全绿 + 新增「v2 事件经 PlatformStore 往返相等」对账测试；Projector 不动。

### S2 库文件拆分 + 惰性迁移（2d）

1. `resolve_session_db_path` 升级：新会话 → `data/sessions/<session_id>.sqlite`（PlatformStore）；旧库转只读兼容层；
2. 惰性迁移脚本（仿 `migrate_study_history.py`）：首次访问旧会话时搬 `messages/message_parts/attempts/event_log` 到新文件；`sessions` 列表迁全局 `platform.sqlite`（跨会话实体）；
3. FTS5 触发器在新库重建（DDL 摘自 web_session_db，顺序敏感块不动）；
4. **验收**：新旧会话并行可读；旧会话首次访问自动迁移且校验行数一致；金丝雀（真实配置对账）保持绿。

### S3 消息写入归一（2d）

1. 四写入者 → 一：`memory_manager` / `web_session` / `core.session.db` 的 messages 写入全部改为 `emit("message_received"/"assistant_message")`；projector 成为 messages 唯一写入者；
2. MemoryManager 的 `get()` 改读投影（或经 PlatformStore.replay fold），紧急缓冲保留；
3. **验收**：`messages` 表在源码中只剩 `projection_store` 一个 INSERT（架构门禁新增测试钉死）；WebUI/CLI 读写回归全绿。

### S4 SSE 单轨（1d，原 P5-3 并入）

1. 删 `sse_buffer`/`bridge_v2` 双轨，SSE 订阅统一走 PlatformStore 的事件发布 + projector 投影；
2. **验收**：`test_sse_*` 全绿；WebUI 流式行为不变。

## 5. 风险

| 风险 | 缓解 |
|---|---|
| 事件 schema 迁移丢失 v2 元数据（aggregate/parent） | S1 的对账测试逐字段比对搬迁前后 |
| FTS5 触发器顺序敏感 | DDL 原样搬（web_session_db 注释明确「order-sensitive blocks must not be rearranged」） |
| 惰性迁移期间的混合读写（新会话新库、旧会话旧库） | S2 旧库只读兼容层统一入口，读路径分派写在 resolver 一处 |
| WAL 大小（真实库 WAL 已 4.3MB） | 迁移前 checkpoint；新库沿用 auto_vacuum=INCREMENTAL 定案 |
| 与 C1 执行体收敛的顺序耦合 | S1-S2 不依赖 AgentLoop 迁移，可先行；S3 的 message_received 事件目前由 AgentLoop 发，新引擎 react 节点需发同 kind——已在 F3 react 节点工具事件基础上加一个映射层即可 |

## 6. 建议

1. **批准 0.4 按 S1→S2→S3→S4 实施，总工期 6-8d**；S1 完成即独立有用（事件单源，11 个使用方零改动受益）；
2. S1 开工前唯一剩余决策：P-1 信封里 `aggregate_id/parent_event_id` 是吸收进信封（我推荐，v2 语义有用）还是丢弃（不推荐，HITL/续跑会用到）——需要你拍板；
3. C1（执行体收敛）可以与 S2/S3 并行推进（不同文件面），但 S3 依赖 C1 至少完成 chat 链路，总体仍建议先 S 后 C。
