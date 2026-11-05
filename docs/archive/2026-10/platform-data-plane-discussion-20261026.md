# 平台数据面讨论稿：P-1 事实流 × P-2 运行状态库 — 2026-10-26

> 状态：**最终定案（2026-10-26 四次修订，用户拍板）：维持现状全量持久化 + 永不清理 + 每 session 一库**——event_log 收全量事件（含 delta）永久保留，沿用现有 projector，JSONL 流层取消；**每 session_id 一个自包含 SQLite**（event_log+blobs+message）+ 全局小库（登记/goal/config）；膨胀靠 per-file 天然有界 + 归档=移文件 + 监视管理。§1–§9 为论证过程，**§10 = 分库修订（最新）**。
> 范围：P-1（事件流）/ P-2（运行状态库）的存储形态、真源划分、性能与膨胀对策。
> 业界调研：`platform-data-plane-industry-research-20261026.md`（含 OpenCode 专项 §4、流式专项 §5）。

---

## 1. 需求回顾

### P-1 事实流
| # | 需求 | 说明 |
|---|---|---|
| 1 | 事件信封（1.6 落点） | `{seq, ts, session_key, run_id?, kind, data, schema_v}`；seq 按 session 单调（fold 正确性）；kind 受控枚举；schema_v 进信封 |
| 2 | 传输不重放 ≠ 内核不重放 | 传输层（SSE）断线不补发，客户端用 status/fold 恢复；**内核 fold 必须支持按 session 从任意 seq 重放** |
| 3 | 保留：快照+尾巴 | fold = 最近快照 + 尾巴回放，不扫全史；全量事件定期归档冷存 |

### P-2 运行状态库
| # | 需求 | 说明 |
|---|---|---|
| 1 | 持有内容 | session、goal/todo（study 划归研究域账本） |
| 2 | 唯一写入者（候选不变量 7） | 只有 Gateway 内 store 类可写；runtime/job/工具发事件不经手 sqlite |
| 3 | 真源双轨制 | **运行态**（turn 生命周期/队列）= 事件是真源，库是投影可重建；**实体定义**（会话设置/goal 内容）= 库是真源，事件做审计。判据：**fold 所需一切必须在事件里** |

### 联合约束（单看 P-2 看不见）
P-2 运行态投影是 P-1 事件 fold 的产物——源与导出的关系。「事件追加 + 状态更新」最好同事务，否则需 outbox 模式（多一套机制、多一类 bug）。

## 2. 联合方案评估

| | A · 平台单库 | B · 两库（events/state） |
|---|---|---|
| 结构 | platform.sqlite：events + 实体 + 投影三命名空间 | events.sqlite + state.sqlite |
| 事件+状态原子性 | ✅ 同事务 | ❌ 跨库，需 outbox/自愈 |
| 事件归档 | ⚠ 删行文件胀，月度离线 VACUUM | ✅ 独立瘦身 |
| 迁移 | 键控 schema_migrations 表（按域记版本） | 各自 user_version |
| 运维 | 一个文件一个 WAL 一个备份 | 两套 |

**当前推荐（未拍）：方案 A**——原子性白拿、与研究域分库自然形成「平台一个库、研究域各一个库」的文件级接缝；代价：实体定义不可重建（缓解：月度归档 + 实体备份）。逃生门：主文件事件区 > ~1GB / 写延迟劣化 / VACUUM 打扰工作 → 拆 events.sqlite（投影随事件走、实体留平台库）；归档通道（JSONL/parquet）第一天常在。

## 3. 性能与膨胀防护（方案 A 的前提）

| # | 防护 | 内容 |
|---|---|---|
| 1 | **内容寻址去重** | LLM_REQUEST 每条带全量 system+tools ≈ 25MB/轮（主炸弹）；按哈希存 blob 表，事件记哈希引用，同版本只存一次（缓存边界设计保证命中率高） |
| 2 | **事件分级保留** | fold 关键类（状态迁移）长期；高频低值类（进度 tick/token 计数）留 7 天批量清；kind 枚举带保留级 |
| 3 | **归档制度** | 月度导出 JSONL → 区间删除 → 离线 VACUUM；投影一律原地 UPDATE；auto_vacuum=INCREMENTAL |
| 4 | **性能参数** | WAL；subscribe 走进程内内存扇出（sqlite 只管持久与回放，稳态零查询）；批量提交（DAG 层完成一次事务）；durability 档位（normal/full）进 ConfigHub |
| 5 | **纪律字面化** | 单事件载荷硬上限 64KB，超限转 blob 指针 |
| 6 | **监视** | 周期发 store_size 事件（page_count/freelist/blob 大小），超阈值（800MB）时间线告警 |

## 4. 五悬点（按调研校准倾向落，ConfigHub 可调）

1. ~~方案 A vs B~~ → **A 定案**（单机主流形态；B 纯 ES 形态=逃生门终点）
2. blob GC → v1 不 GC；将来按「无事件引用的哈希」低频清扫
3. 保留分级 → v1 两档（fold 关键长期 / 低值 7 天）；**删除可见**（归档动作发事件）；保留表进 ConfigHub
4. 快照 → 每 turn settle + 每 N 事件兜底；形态=proj_* 投影导出
5. 归档 → JSONL 月度文件（workspace 归档目录）；parquet 等分析需求出现再上

## 6. 方案 A 劣势 × 优化方案（2026-10-26 定案补充）

| 劣势 | 优化 | 残余风险 |
|---|---|---|
| VACUUM 独占主文件 | `auto_vacuum=INCREMENTAL` 建库即开，分批回收不独占；归档=导出→DELETE→分批 vacuum 三步节流；**全量 VACUUM 只在启动时**（与 fold 同窗口，用户无感）；WAL passive 检查点+空闲 TRUNCATE | 无 |
| 实体备份要捞大文件 | **审计事件=实体备份**（实体变更必发事件 → 月度归档含全部实体变更史，可重放重建）+ 实体镜像 sidecar（KB 级 JSONL，变更后顺手写，恢复直读不重放）；全文件备份只在迁移前 | 无 |
| 损坏域不隔离 | 启动 `quick_check` 并入 fold；每周后台 `integrity_check` 发事件留痕；**恢复三角**：实体←镜像/审计重放 · 投影←fold · 事件尾巴←归档+WAL（三来源不依赖主文件健康）；durability normal/full 进 ConfigHub | 主文件损坏瞬间未归档的分钟级增量可能丢（WAL 通常可救） |
| 键控迁移表 | `schema_migrations(domain, version, applied_at)` 三域独立版本、各 store 类负责己域、迁移即事务、发 `schema_migrated` 事件；比 user_version 更规范（按域可见历史） | 无 |

**运行制度**：每变更=事件+投影同事务（实体变更附带镜像）；实时=内存扇出+批量一事务；每周=integrity_check；每月=归档→删除→分批 vacuum；每启动=quick_check→迁移→fold→（可选全量 VACUUM）；store_size 超 800MB 告警。

## 7. 流式输出 × 事件流（2026-10-26 定案）

### 7.1 业界口径（详见调研 §5）
Temporal 事件史硬上限 51,200 事件/50MB、单请求载荷 2MB、大载荷外置；LangGraph token 走独立流式通道、持久化只在 checkpoint 边界；OpenCode 按 part 落库不按 token。**没有一家把 token delta 写进真源日志**。

### 7.2 定案：双通道 + JSONL
```
token delta        ──► SSE 内存扇出（UI 实时）              ✗ 不落 SQLite
流式分块           ──► stream/rounds/R-xxxx/T-xxxx.jsonl.gz  （追加写 · gzip · 24h~7d 保留）
消息/part 完成     ──► 一次 message_appended 事件（内容经 blob 引用）  ✓ 真源
turn 状态迁移      ──► 事件                                     ✓ 真源
```
- **JSONL 不是真源**：真源=SQLite 的 message_appended；JSONL 丢失只影响「刷新看半截/回放流」，不影响任何重建（Claude Code「索引非权威」#44346 同款纪律）。
- **接口隔离**：`StreamWriter.append(chunk)` / `StreamReader.read_range(turn_id, from_seq)`；事件只记 `stream_ref{uri, schema_v, sha256, bytes}`。JSONL 与未来 ClickHouse 是同一接口的两个实现——替换=换实现类，调用方不改。
- **chunk schema（一次定死）**：每行 `{seq, delta, ts}`，gzip，按 turn 一文件。

### 7.3 ClickHouse 延后（已进 roadmap）· 替换边界的准确口径
本轮不做，且**「CH 替 SQLite」只在局部成立**：
- ✅ CH 可替：事件扫描/流式分块的分析角色——`StreamWriter` 加实现即可，历史 JSONL 作 ingestion 源；JSONL 与 CH 可并存（原始归档+分析索引）。
- ❌ CH 不可替：`entity_*`（会话设置/goal）——CH 无多语句事务、无唯一约束、更新靠异步 mutation、默认异步 insert 不保证读己所写；`blobs`——CH 不是对象存储，去重仅 ReplacingMergeTree 异步最终一致。
- **分析层首选 DuckDB + Parquet**（已在栈内、Windows 原生、零服务）：事件归档合入 parquet 后 `read_parquet` 直查，并与 market.duckdb 同引擎。CH 仅在服务化/多用户/百 GB 级时考虑；届时运行真源换 Postgres，仍不是 CH。
- 触发条件：跨研究聚合分析成为真实需求（RSI D3 统计各 tier 表现）或归档 >10GB。

### 7.4 空间账（对比「流式进 SQLite」变体）
一轮研究（300–500 次调用 × 1–2K 输出 token）：流进 SQLite 主文件增加 1.3–6MB/轮（含行开销，删除后还留 FAT 页需 VACUUM）；JSONL gzip 后 0.15–0.5MB/轮且删文件即刻还盘。**SQLite 小 40–60%，总占用小 3–5 倍**。相对纯 A⁻，SQLite 大小不变（流本来就不进库），JSONL 是额外但可压可删的一份。

### 7.5 护栏
单 session 事件数告警线 10k / 强制归档切分 50k（Temporal 同量级参照）；单事件 ≤64KB（已定）；stream 目录大小进 store_size 监视。

### 7.6 措辞精确化
铁律一「token 永不落库」的「库」专指 SQLite。token 三去向：SSE→屏幕 ✓ · 合并→JSONL 文件 ✓ · SQLite ✗。铁律一与铁律三是一套：进 JSONL 的正是 token 攒出的粗块。

## 8. 现状对照与 A⁻ 落地修正（2026-10-26 代码勘查）

### 8.1 现状（与铁律的差距）
| 证据 | 位置 | 行为 |
|---|---|---|
| 每片都发事件 | `core/agent/loop_stream.py:76,202` | 每 chunk emit text_delta（含 raw）+ text.started/text.ended 三步协议 + thinking 系列 + llm_usage 仅一次 |
| **每片都进 SQLite** | `core/agent/event_store.py:241`（emit→_create_and_persist） | **无过滤**，text_delta 连 raw 逐片写 event_log 表 |
| **projector 已存在** | `api/session/projector.py` 等 | 事件折叠进 **messages+message_parts** 表；`_should_flush`（event_store.py:637）只在边界事件（assistant_message/compact/iter_start）批量 flush——「长任务刷新仍见已完成迭代」靠它 |

**结论**：当前系统已是「全事件持久化 + projector 投影」，逐片持久化比所有业界（Temporal/LangGraph/OpenCode）都细——正是数据面膨胀主犯。

### 8.2 ~~A⁻ 落地修正~~ → 被用户否决（见 §9）
~~最小手术：text_delta 摘出 event_log~~ → **用户拍板维持现状**（全量持久化 + 现有 projector），理由：零 UI 迁移、单一事实源、回放一切；膨胀代价由分级保留+归档+监视管理（§9）。

## 9. 最终定案（2026-10-26 二次修订，用户拍板：维持现状方案）

**event_log 收全量事件（含 text_delta/thinking_delta），沿用现有 projector，JSONL 流层取消。**

### 9.1 修订后的三条铁律（三次修订：delta 不清理）
| 原（A⁻ 版） | 最终版 |
|---|---|
| token 永不落库 | **token 进 event_log＝回放素材，永久保留不清理**（语义事件同样永久） |
| 消息完成才 emit | **全量 emit 照旧；projector 边界批量折叠**（assistant_message/iter_start），不做每片折叠 |
| JSONL 不是真源 | **event_log 是唯一真源**；JSONL 流层整体取消（StreamWriter/分块文件不再需要） |

### 9.2 这个选择买到什么 / 付出什么
**买到**：零 UI 迁移（刷新体验照旧走 SSE replay）· 单一事实源（回放一切含完整 token 流，无第二存储）· 不新建 StreamWriter 接口 · 实现成本≈0（现状即目标形态）· **无任何自动删除**（回放素材永不丢）。
**付出**：event_log 永久增长且无删除——估算重度使用月增 1–3GB（delta 行为主，知情偏离业界：Temporal/LangGraph/OpenCode 均不逐片持久化）。管理手段：① blob 去重（system+tools）照旧 ② 月度归档改为**纯导出备份（不删除主库数据）**，兼作未来 OLAP 源 ③ store_size 监视（10k/50k 行数告警、800MB 文件告警）④ **重开触发线：主文件 >2GB 时回到桌面讨论归档删除政策**（不自动执行，只提醒）。
**修订连带**：§7 的 StreamWriter 接口/chunk schema/stream_ref/JSONL 保留期等条目**全部作废**；ClickHouse/DuckDB 分析层延后项不变（分析对象=event_log 归档导出）。

### 9.3 保留不变的部分
platform.sqlite 单库 · event 唯一真源唯一写入入口（emit）· blobs 哈希去重 · message/message_parts（现有 projector 维护）· entity_* · 真源双轨制 · PlatformStore 唯一写入者（不变量 7）· 恢复三角 · 键控迁移表。
**运行制度表（§6）沿用，一处修订**：月度归档步骤改为「纯导出备份（不删除主库数据）」；`incremental_vacuum` 保留（回收投影/实体原地更新的空闲页，与事件无关）。

## 10. 分库修订（2026-10-26 四次修订，用户拍板：每 session 一库）

### 10.1 布局
```
data/
├─ platform.sqlite            ← 全局小库（恒小）：session 登记索引 · goal 摘要索引 · config 指纹 · 迁移记录
└─ sessions/
   └─ <session_id>.sqlite     ← 自包含：event_log（全量含 delta·永不清理）+ blobs + messages/message_parts + goal/todo
```
分界原则：**跟会话走的数据进 session 库，跨会话的数据进全局库**（P-3 登记表落全局）。

### 10.2 为什么成立（与已定基石咬合）
- **session=串行域**：session lane 已串行化该会话全部 turn → 单文件单写者天然成立，零锁竞争
- **home_session_key**：研究 round 全进 home session → 该研究全部事件聚在一个文件，回放=开一个文件
- 先例：Claude Code 每会话一文件（我们用 SQLite 替其 JSONL）；OpenCode 单库是对照形态

### 10.3 各决策点变化
| 点 | 单库 → 分库 |
|---|---|
| 事件+投影同事务 | ✓ 仍同事务（文件内） |
| 唯一写入者 | ✓ PlatformStore 管连接池，仍唯一入口 |
| **永不清理的代价** | ✓✓ 每文件天然有界；2GB 触发线变 per-file |
| 归档 | ✓✓ 整文件移走即归档（自包含含 blobs） |
| 损坏域 / 启动 / 迁移 | ✓ 单 session 隔离 · 懒加载更快 · 每文件 user_version 回归 |
| 跨 session 即时 SQL | ✗ 唯一真实代价——由 P-3 登记索引 + 归档 OLAP 覆盖 |
| 连接管理 | 活跃 session LRU 连接池（十几个量级） |

### 10.4 连带修订
- 2GB 重开触发线 → **per-session 文件** >2GB（或 sessions/ 目录总量 >20GB）
- 恢复三角 → 单文件损坏只影响该 session；重建=该文件的重放/归档
- 运行制度：启动 quick_check 只查全局库；session 文件**打开时**quick_check；归档=关闭+移文件
- §9.3 的「platform.sqlite 单库」表述由本节取代；其余（真源双轨/不变量 7/blobs 去重/永不清理）全部不变

### 10.5 三点补充（2026-10-26 同日追问定案）
1. **goal/todo 进 session 库**：todo 无争议；goal 本体进 session 库（在哪个会话立的住哪），跨会话「发现」走全局登记索引的一行轻量摘要（goal 标题/study_id/home_session 指针）——与 P-3 登记同机制，本体不双写。会话文件保持自包含（归档移文件时 goal 跟随）。
2. **WAL 保留**：串行化的是 turn 不是读者——UI 查询/status/SSE 重连需读写并发 + 崩溃安全；每 session 文件独立 WAL + busy_timeout。
3. **subagent 结果保存（不开 subsession 文件）**：subagent 事件写**同一 session 文件**、以 run_id（`{parent}:{ordinal}`）标记归属；并发写由 PlatformStore **每 session 单写连接**串行化短事务（应用层锁），WAL 承担读写并发；subagent 大结果照旧走 P-3 登记 + round 目录 parquet。不开 subsession 文件的理由：turn 树自包含单文件（fence/回放/归档单文件操作）· 子事件量小（subagent 时间在等 LLM/跑回测，不在写库）· 避免文件碎片化。
4. **「跨 session SQL」= 多会话聚合查询**（列会话/按 study 聚合/周期统计）——分库后走两条入口：轻量=全局登记索引，重分析=归档 OLAP；单会话内操作不受影响。

## 5. 相关决策线索

- 纪要线索 24（讨论开启，本稿入库未定案）
- 关联：1.6 事件协议化 · 0.2 SSE 双轨 · 0.4 持久化契约 · 2.4 读写分仓 · ConfigHub retention 段
