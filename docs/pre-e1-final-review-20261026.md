# E1 前最终评估 — 2026-10-26

> ⚠ **历史文档**：E1 前评审，已归档。

> 性质：开工前最后一道全层审查。方法：入口→Gateway→执行面→数据面→研究域→横切面逐层扫荡 + 决策两两一致性检查。
> 结论：**无结构性遗漏，可以开工**。扫出 3 件事（1 个待点头的小决策 + 1 笔文档欠账 + 2 个归位确认），均不阻塞 E1。

---

## 1. 决策一致性检查（全部通过）

抽查易冲突的决策对，结论如下：

| 决策对 | 检查结果 |
|---|---|
| 事件永不清理（P-1） vs mailbox cap 20 溢出 summarize | ✅ 不同层——mailbox 是内存排队，与持久化无交集 |
| turn:run=1:N（P-4） vs fence activeRunId | ✅ 互相成就——run 层正是同 turn 新旧超越所需 |
| home_session 定死 vs 每 session 一库自包含 | ✅ 研究 round 全进 home session 文件，时序天然聚拢 |
| 研究产物在 workspace/studies（D-4） vs 会话归档移文件 | ✅ 时序（session 库）/事实（study 目录）分离，互不牵动 |
| research preset Tier0 冻结 vs 现有 phase_engine（langgraph） | ✅ B1 迁移路径清晰（preset 化） |
| goal 本体在 session 库 vs Director 跨会话读目标 | ✅ 全局登记索引存一行摘要（P-3 同机制），本体不双写 |
| 永不清理 vs 磁盘容量 | ✅ per-session 文件天然有界 + 2GB per-file 重开触发线 + 归档移文件 |

## 2. 扫出的三件事

### 2.1 存量数据迁移策略（✅ 2026-10-26 用户点头定案）
现状系统有活数据（旧 session/study/goal 三库 + 现有 event_log 表），改为每 session 分库布局后老会话去向未讨论。
**定案**：旧库**只读兼容（legacy 只读模式）**+ 新会话走新布局；一次性迁移脚本后置（仓库已有 `migrate_study_history.py` 先例可参照）。不在 E1 前做，0.4 数据面落地时实现只读适配。

### 2.2 entry-controller-contract.md 收薄重写（文档欠账）
v6 之后标记「待重写为协议端点+mailbox 一页」，一直未还。
**处置**：E1 第一步顺手做——E1 本身就是在实现它描述的东西，边写契约边重写文档。

### 2.3 两个小归位（✅ 2026-10-26 定案，设计要点如下）
| # | 项 | 归位 |
|---|---|---|
| ① | 上下文压缩（compaction）——现状有 loop_compaction 机制，新架构未提 | **沿用现状，归 ContextHub/A1**（并入 A1 前讨论清单：steer→langgraph 对接 / react 节点迁移 / 失败语义 / compaction 确认） |
| ② | LLM 速率限制器——subagent 规则依赖"速率上限"但限流器住哪未定 | **归 ProviderHub 的 provider client 层**（A1 设计时定参数与算法） |

**① compaction 职责切分**：ContextHub 管 diagnose（arbitrate 阶段发现预算超限→发压缩需求，预算账本在它那）；runtime 管 treat（react 节点内执行 LLM 摘要——控制面内不得调模型，不变量 1）；摘要产物沿用现状（合成消息 + compaction/compact.ended 事件，已在 projector 边界清单）；**不可否决**（OpenClaw 同款，Tier 0 行为）；压缩重写前缀 → KV 缓存失效一次属预期成本；永不清理决策下压缩前原始上下文也可回放。
**② 限流器设计**：per `provider:model` 令牌桶；RPM+TPM 两维（TPM 由 ContextHub token 账本预估、调用前扣）；超限**排队不拒绝**（asyncio 队列），provider 429 → 指数退避（客户端层），持续失败 → run failed（C3 阶梯）；与 lane 两个独立闸（lane 管并发数、限流器管每分钟用量）；参数进 ConfigHub `provider` 段。归位道理：速率限制是供应商合约属性，跟 client 走。

### 2.4 ② entry-contract 重写大纲（确认，E1 开工时成文）
保留：WorkRequest 契约 / 两级拒绝 / 快同步 / 事件不重放 / QUEUED 不继承 run 超时 / 8 条边界不变量（逐条改述）。
砍掉：WorkItem 归一化 / place() 五态回执（换 mailbox 直收+两态 accepted/rejected(reason)）/ 双签发外显（turn_id 由控制器准入签发，P-4）/ ItemPhase 投影（status 直接 fold）。
新增：宿主与渠道关系（挂载式单进程）· 每源默认 queue_mode 表（含 MCP=followup）· session 惰性创建 · 回执语义（收下即进 mailbox，其余看事件流）。

## 3. 已确认排期项核对（无缺失）

| 排期 | 项 |
|---|---|
| A1 前 | steer→langgraph interrupt 对接 · react 节点迁移边界 · 失败语义细则 · compaction 归位确认 |
| A1 时 | LLM 限流器设计 · heartbeat 间隔 · Windows worker 断网验证（防火墙规则） |
| B1 | research preset 化 · fanout 解释器 · DagRuntime 除名落地 |
| C1/C3 | core/study 拆分 · 降级阶梯 |
| P3 前 | T3 第二段（WSL2 bubblewrap+seccomp） |
| E1 后 | 研究域业务四件（D-1 市场摄入 / D-2 结果 schema / D-3 账本 / D-5 身份段）——对真代码谈 |
| 触发线 | 事件库 >2GB 重开删除政策 · 归档 >10GB 或 RSI D3 启动 OLAP · 多租户/多机重开分池 |

## 4. 结论

**可以开工。** E1 第一刀顺序：
1. 重写 `entry-controller-contract.md` 为「协议端点 + mailbox」一页（还 §2.2 的账）
2. `core/orchestration/contracts.py`（WorkRequest 含 queue_mode 五行 + execution 字段位 + 幂等键/deadline）+ mailbox 骨架（lane 串行 + steer 邮箱，仲裁器 v1=steer 转 followup）
3. 一致性 pytest（disposition 全覆盖/两级拒绝/幂等/collect 拒绝/白名单/事件必发）
4. 顺手三小件：chat 预算门（0.5d）· ConfigHub schema 骨架（gateway/mailbox 段）· 契约独立定形（RoundOutcome/BranchContext）

**存量迁移方案（§2.1）✅ 已定案。**

## 5. 二次扫荡（2026-10-26，方法：端到端旅程 trace + 横切面补查）

**八站旅程 trace**（契约/执行者/数据落点三问）：①变工单→②收单→③排队（惰性建库）→④发令（turn_id）→⑤选图→⑥跑图→⑦下放→⑧收尾（fence+登记）——八站无断链、无无主事项。

**横切面补查**：安全（keys 隔离/沙箱/注入风险声明）✅ · 观测（store_size/llm_usage/token 账本）✅ · 升级（键控迁移+PROTOCOL_VERSION）✅ · 备份（恢复三角）✅ · 多研究并行（容量组）✅ · steer 与子 agent（只认 turn）✅。

**新发现 3 条（实现级，无结构问题）**：
1. 预算运行中执行点：准入门已定，run 中途超预算谁砍 → ContextHub 每次模型调用前查余额，超限 emit `budget_exhausted` + CancelScope（**A1 细化**）
2. 制度任务（月度归档/每周校验）执行者 → Gateway scheduler cron 任务（挂载即可）
3. workspace 与代码 repo 位置关系 → ConfigHub `workspace.path` 配置项

**二次结论：维持「可以开工」，无新增讨论项。**

## 6. 三次评估（开工前终审，镜头：YAGNI 审计 · Pre-mortem · Build-vs-Adopt）

### 6.1 YAGNI 审计
约 20 个具名组件逐个追问驱动需求：核心群（Gateway/fence/GraphEngine/executor/分库）各有真痛点驱动，不可砍；ConfigHub 完全体/容量组完整实现/preset 治理/Director/Fanout 在单用户下多为预留。**审计结论：设计面偏大、执行面可控——P0 路径只用一小半。对策：P0 期间每组件实现前必须指认驱动需求，指认不出即推后。**

### 6.2 Pre-mortem（一年后失败的最可能死法）
| 死法 | 概率 | 逃生门 |
|---|---|---|
| 迁移烂尾（E2–E5 新旧并存搞挂） | 中高 | 逐入口独立切换可回退+E1 纯增量；残余风险=单人 17d+ 工期中断 |
| langgraph 反噬（API churn/抽象不匹配 fence/steer） | 中 | 包裹层隔离；A1 前 steer→interrupt 对接讨论探雷（已排期） |
| 全量持久化赌输 | 低中 | 保留政策可后调+per-file 有界+2GB 触发线；后悔成本=改一条路由 |
三条均有逃生门，无「推倒重来」级。

### 6.3 Build-vs-Adopt（补做的根本性检查）
LangGraph Platform/CrewAI：差异化在会话编排规则（steer/fence/home_session）+研究域，通用框架不带或绑死自家模型；且现有管线能跑，**是演进不是重写**。Fork OpenClaw：TS 栈不合+维护跟进成本+渠道冗余。不做 Gateway 只写公共库：steer/排队/fence/automation 结构性无法满足（E1 前置的原始理由）。**结论：自研成立。**

### 6.4 最坏一天适应性
加第二用户（分库/容量组/Postgres 路径已有，auth 需补有重开条件）· 供应商 API 大改（client 层隔离）· 新渠道（哑适配器+collect 留位）· 研究量 100×（归档 OLAP）· 换供应商（preset/provider 解耦）——全部适应。

### 6.5 终审结论
**架构合理，批准开工**，附三句实现期纪律：
1. 设计是平台级的——P0 只实现驱动需求明确的部分（YAGNI 纪律进 E1 验收）
2. 最大风险是迁移执行而非设计——E1 纯增量、逐入口可回退必须死守
3. 代码会修正细节——第一版实现推翻讨论细节属正常健康，**以代码为准，文档随后对齐**
