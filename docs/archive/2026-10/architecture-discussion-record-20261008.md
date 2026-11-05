# 架构讨论纪要 — 2026-10

> ⚠ **历史文档**：v2/v3 时代的四轮架构讨论纪要。结论已被 v6.2 取代，保留以记录当时的权衡。

> 用途：**讨论记录 + 决策索引**，后续讨论的起点。
> 性质：不重复实现细节，只记「讨论了什么、达成了什么、还差什么」；细节指向各专项文档。
> 参与：架构师视角 + agent 设计视角 · 借鉴 OpenClaw（392k★）· 与 DSH 对标

---

## 1. 讨论线索（按时间顺序）

| 轮次 | 主题 | 结论一句话 | 详情 |
|---|---|---|---|
| 1 | 六层架构梳理（六遍评审起手） | 分层合理；问题在层内巨文件与层间反向依赖 | `architecture-reeval-20261008.md` |
| 2 | 依赖方向审计 | **3 条 core→api 反向依赖**（event_store→projector / langgraph→bridge / scheduler→web_session） | `architecture-reeval-20261008.md` |
| 3 | SSE 双轨 | 契约应从**事件日志 fold**，不建独立存储；D/C 两处我此前设计有误 | `architecture-reeval2-20261008.md` |
| 4 | Agent 设计（上下文/预算/记忆） | **上下文无仲裁者**（3 套估算、validate() 死契约、记忆不写不读） | `agent-design-reeval-20261008.md` |
| 5 | 目标对齐（自主量化研究 agent） | **自主执行+评估强，缺导演层**（hypothesis 原语有 API 无工具出口） | `goal-alignment-review-20261008.md` |
| 6 | DSH「一切都是插件」 | 借「行为插件化」，**保留**「不变量独立于插件」 | `dsh-plugin-reeval-20261008.md` |
| 7 | 汇总 + RSI | 三层基因组：Tier0 冻结 / Tier1 提案 / Tier2 可自改；**先决条件是 holdout + 不变量层** | `architecture-v2-and-rsi-20261008.md` |
| 8 | 编排层成员边界 | **控制面 3–4 套 → 1 套**；AgentLoop 是「能力」不是「编排」 | `orchestration-layer-membership.md` |
| 9 | 编排层三件套（控制面/策略/环境） | 三者是**一条链 + 一个贯穿环境**；RunContext 是准入产物，Director 产出 = BranchContext | `orchestration-triad-design.md` |
| 10 | **统一入口**（用户提出） | **入口不统一，后面全是空谈**——22 个启动点 / 8 种启动方式 | `unified-entry-design.md` |
| 11 | OpenClaw 对标 | 1 控制面 + N 执行面；执行面可换是它第一原则 | `industry-benchmark-openclaw-20261008.md` |
| 12 | 目标架构 v5 | 管线：接入 → 统一入口 → 控制面 → 策略/环境 → 执行 → 上下文 → 研究域 → 数据 | `architecture-diagram.html`（v5） |
| 13 | queue_mode 语义 + 业界调研（7 家）· 2026-10-24 | API chat=steer 定稿（6/7 业界默认）；collect 留枚举位不实现；Director 产出复用 BranchContext | `queue-mode-research-20261024.md` |
| 14 | 入口×控制面边界契约 · 2026-10-24 | ~~place() 唯一写通道 + AdmissionReceipt 五态~~ ⚠ **被轮次 15（v6）收薄**：mailbox 直收 · 两态回执；WorkItem/五态/双签发外显砍掉 | `entry-controller-contract.md`（待重写） |
| 15 | Gateway 化 + 宿主形态 · 2026-10-24 | **v6**：入口层塌进 Gateway（协议端点薄·无状态），管线 8 段收 4 段；宿主=**挂载式单进程**，业界四模式调研背书 | `gateway-hosting-research-20261024.md` |
| 16 | session 粒度 · 2026-10-24 ⏸ 讨论中 | 草案五条：session=turn 串行域 · study round=chat 会话长 turn · home_session_key · 容量组 · 账本解耦 | `gateway-hosting-research-20261024.md` §5 |
| 17 | Gateway 内部设计 · 2026-10-24 | 依赖图（端点→mailbox→控制器→选择器→runtime；ContextHub 只被 runtime 调）+ 6 不变量 + 生命周期 + 收编映射；中断 run **可配置默认不自动恢复**；关闭等 grace | `gateway-hosting-research-20261024.md` §6 |
| 18 | session 粒度业界调研 · 2026-10-24 ⏸ 待拍板 | 六断言五确证一存疑（Temporal 自动恢复 vs 我们非确定性 run）；吸收四细化：mailbox cap 20+溢出策略 / 优先级老化 / subagent lane 键 / cron 隔离 | `session-granularity-research-20261024.md` |
| 19 | 执行面业界调研 · 2026-10-24 ⏸ 待拍板 | **三层分类法**（T1 进程内循环 / T2 常驻子进程池 / T3 OS 沙箱·远端）；推荐 runtime 进程内 + executor 池 + 沙箱占位；**Windows 无业界沙箱可用，进程边界是当前唯一隔离**；Ray/Dask 不引入（触发条件写死） | `execution-plane-research-20261024.md` |
| 20 | 多 subagent → GraphEngine 统一 · 2026-10-24 ✅ | 两级准入 + 子 run 树七规则 → ExecutionProfile 声明式 → **用户拍板：GraphEngine 唯一 runtime**（包裹 langgraph·支持环）+ 四节点类型（llm/react/cpu/graph，每阶段开/关 ReAct）+ preset 治理分级（research=Tier 0 冻结）；DagRuntime 除名 | `execution-plane-research-20261024.md` §5；架构图 v6.2 |
| 21 | 数据层拆两层 · 2026-10-26 | **平台数据面**（P-1 事件流/P-2 状态库/P-3 工件登记/P-4/5 身份+指针）并入平台虚线框；**研究域独立成 ⑤ 层**（D-1…D-5）；接缝规则=研究域不许私开存储通道；Q1 结果写路径=**方案 B**（parquet 旁挂+指针登记）| 架构图 ⑤ 层 |
| 22 | RuntimeHub 定名 · 2026-10-26 | RuntimeSelector → ProviderHub → **RuntimeHub**（GraphEngine 统一后职责=解析每 turn 的执行体配置：preset 图+provider/模型绑定；"runtime"指执行体配置整体） | 架构图 ② |
| 23 | 配置中心 ConfigHub · 2026-10-26 | **平台组件参数统一收口**：分层装载（代码默认<文件<env）· 治理分级（运维/Tier1/敏感隔离）· 启动校验 fail-fast · 脱敏指纹进事件流；E1 起 schema 骨架各刀补段 | 图右脊 ConfigHub；roadmap 1b |
| 24 | 平台数据面 P-1/P-2 · 2026-10-26 ✅（四次修订） | **最终定案：每 session 一个自包含 SQLite + 全局小库**——session 库=event_log（全量含 delta·永久不清理）+blobs+messages/message_parts（现有 projector 沿用·边界 flush）；全局库=session 登记索引+goal/todo+config 指纹；JSONL 流层取消（event_log 即回放源）；session lane 串行=单文件单写者天然成立；归档=移文件（自包含）；膨胀 per-file 天然有界；跨 session 查询走 P-3 登记索引（唯一代价）；知情偏离业界逐片不持久化惯例，买到零 UI 迁移+回放一切+会话自包含；ClickHouse/DuckDB 分析层延后不变 | `platform-data-plane-discussion-20261026.md` §10（论证 §1–9） |
| 25 | 数据面收尾三件 · 2026-10-26 ✅ | **P-4 定案**：四层派生 id（session uuid→`{sid}#{n}` turn→`{turn}:r{m}` run→子 run/artifact）；**turn:run=1:N 不合并**（用户修订：一轮可多次运行，fence 需要 run 层做同 turn 新旧超越；Temporal WorkflowId:RunId 同构；新增不变量 8=同 turn 至多一活跃 run）；session_key/session_id 分离（文件名=uuid 永不复用）；血统三件套。**P-5 定案**：`{uri,sha256,bytes,schema_v}` 四件套，根=workspace。**D-4 定案**：方案 A 按研究组织 `studies/{study_id}/rounds/R-n/`（账本解耦同构；strategy.py 进 git 面、parquet 排除）。**P-3 收口**：登记行定稿（含 P-4/P-5 字段），kind v1=backtest_result 实装+upload/export/log_bundle 预留 | `p3-p5-data-plane-design.md`（全部 ✅） |
| 26 | 遗留全清 · 2026-10-26 ✅ | **10 项批量拍板，未决台账清零**：session 粒度 2（home_session 定死·发起不问占本会话+toast）+ 执行面 3（executor 单池+闸·T3 两段式·heartbeat A1 定）+ 旧五条（P0 单人顺序·契约独立随 E1·study 拆分 C1·RunContext 放 core/orchestration/·chat 预算门随 E1）。各项附重开条件 | 两调研文档开放点段已改定案 |
| 27 | E1 前最终评估 · 2026-10-26 | 全层扫荡+一致性检查：**无结构性遗漏，可以开工**。三发现：①存量数据迁移策略（建议旧库只读兼容+惰性迁移，⏸ 待点头）②entry-controller-contract 重写（E1 第一步还账）③两个归位（compaction 沿用现状归 ContextHub/A1·LLM 限流器归 ProviderHub client 层）。E1 第一刀顺序已列 | `pre-e1-final-review-20261026.md` |
| 28 | E1 落地 + 目录结构 · 2026-10-26 ✅ | E1 完成：contracts/mailbox/entry_point/ConfigHub/预算门/中立契约，**88 测试全绿**，纯增量零删除。可替换性勘查：3 对「同一概念两套实现」归一（session 队列 A2 / steering inbox A4 / seq 生成器 0.4，净删 ~210 行），预算分层不替换（有 cost_usd），写入限流不同层。**目标目录结构定稿**（engine 与 orchestration 平级 · context 独立 · storage 合并现有）+ 复用账（新代码 20–25%，其余搬运/合并）| `e1-replaceability-assessment-20261026.md` · `target-directory-structure-20261026.md` |
| 29 | E1 后重排 · 2026-10-26 ✅（四轮讨论修订） | **横向分层 → 纵向切片**：原计划前 6 天无产物。修订版 6 子阶段 ~22d：**1a** 纯 LLM 对话切片（2d，不碰 AgentLoop 依赖树）→ **1b** react 节点+工具树（2d）→ **2** 正确性+fence+executor+**A5 不变量层**+**审计门①**（4d）→ **3a** 入口迁移（4d，轻量复查）→ **3b** 数据面（3d）→ **4** 清理+**测试隔离修复**+**审计门②**（7d）。验收=「新增+受影响测试绿」（全量绿在隔离修复后才成立）；显式推迟清单（steer 真注入→阶段 4·B5 记忆→P1·ExecutionProfile→B1）；里程碑 M1 提前到第 2 天 | `replan-after-e1-20261026.md`（修订版） |
| 30 | 规划 v2：能力依赖 · 2026-10-26 ✅ | **失败驱动的重排**：flag 默认开导致 12 个 scheduler 测试失败——根因是 study 执行依赖 research preset（B1），当前引擎只有 llm 节点。**能力依赖矩阵**（chat/CLI 需 llm 节点 ✅ / scheduler 需 research preset ❌）；**双门灰度**（chat 默认开 · study 默认关）；B1 拆 B1-a（GraphEngine 阶段图）+ B1-b（research preset）并前移到 scheduler 迁移之前；**迁移测试方法论：投递成功 ≠ 功能等价**；工期 22d→24d | `replan-v2-capability-dependencies-20261026.md` |

---

## 2. 关键发现（每条附证据）

### 2.1 结构债（Layer & Structure）

| # | 发现 | 证据 | 影响 |
|---|---|---|---|
| **①** | **3 条 core→api 反向依赖** | `event_store→projector`（惰性 import）· `langgraph_engine→bridge_v2`（SSE 双轨）· `scheduler→web_session._get_db`（私有符号泄漏） | 分层叙事失效 |
| **②** | **SSE 双轨** | 旧 `SSEEventBuffer` + 原生 `subscribe()` 并存；bridge_v2 是半截迁移的胶水 | 消除双轨 + 消 ① 中两条 |
| **③** | **持久化无统一契约** | 11 个 store/db 模块 | 契约文档 + 一致性测试（0.4） |
| **④** | **上下文无仲裁者** | 3 套 token 估算（`//4` / `3.0` / `token_utils`）· `validate()` 0 调用 | ContextHub 独占装配与预算 |
| **⑤** | **记忆断裂** | `PersistentMemory.add` 调用方只有人工 CLI / MCP；agent 不写；注入只 pre-run 一次且拼进 task 字符串 | 记忆回路断（G2） |
| **⑥** | **导演层缺失** | `HypothesisStore.derive/contradicts` 有 API 无工具出口；`propose` = 0 | 自主性缺一块（D1/D2） |
| **⑦** | **引擎层反向依赖 AgentLoop** | `AgentExecutor`×5 · `executor`×13 | AgentLoop 是能力不是编排 |
| **⑧** | **无 write fence** | `fence` 命中 35 处全在无关文件；取代的 run 可提交陈旧数据 | 并发正确性缺口 |
| **⑨** | **入口 8 种启动方式** | 22 实例化点 / 15 文件；CLI=同步 / API=asyncio.Queue / scheduler=Semaphore / MCP=无 | 「各做各的」的根源 |

### 2.2 结构债（规模与重复）

| 项 | 数值 |
|---|---|
| >800 行文件 | 25 个 / 28k 行 = 21% src（最大：goal/store 1773 · study 路由 1642 · study store 1619 · session service 1450） |
| AgentLoop 拆解轴 | 7 mixin × 9 step **6 组一一重叠**（step 是薄壳） |
| 注册表 | 5 套并存（plugin registry / TOOLSETS / provider `_REGISTRY` / data_store_registry / MCP register） |
| 调度 | 3 处（service / runner / scheduler） |
| 控制面 | 3–4 套（SessionService / StudyRunner / SwarmRuntime / Workflow=工具） |
| `PROTOCOL_VERSION` | **0 处**（协议无版本） |

### 2.3 我们的资产（不可动）

| 资产 | 业界对标 |
|---|---|
| **研究方法论**：假设 → 派生 → 证伪(claims) → AEGIS 新颖性/回归门 → 两阶段 journal | **无直接对标物** |
| **70 题 golden eval + nightly 跟幅 + pyright 回归门禁** | 多数项目没有可比的门禁资产 |
| **类型债治理机制**（分批 + triage + 门禁） | 罕见 |
| **hook 红线**（只读不改控制流） | 与 OpenClaw 一致 ✅ |
| **Tier 0 不变量冻结** | 比 OpenClaw 更硬（他们 sandbox 默认关） |

---

## 3. 已拍板的决策（按时间）

| # | 决策 | 决定 | 理由 / 影响 |
|---|---|---|---|
| 1 | 控制面是否统一为一个 | ✅ **接受** | chat 与 study 共用 SessionController；最大一刀，也是最大简化 |
| 2 | SwarmDAG 归属 | ✅ **编排层内一格**（标注 DAG 驱动） | 决策 2 |
| 3 | Director 归属 | ✅ **编排层内一格**（策略，不是循环） | 决策 3 |
| 4 | AgentLoop 归属 | ✅ **移入能力层**（问题 ⑦ 已决） | 新切 0.11 |
| 5 | 统一入口优先级 | ✅ **E1 插到 A1 之前** | 用户判断：入口不统一后面全是空谈 |
| 6 | P2 研究域强化时机 | ✅ **等控制面 OK 后再动** | 避免同时改 `phase_engine.py` / `runner.py` |
| 7 | P0 是否两人并行 | ✅ **单人顺序执行**（10-26 线索 26） | 团队扩员时重开 |
| 8 | 契约（RoundOutcome/BranchContext）是否独立 | ✅ **做，随 E1 定形** | — |
| 9 | `core/study/` 拆 method/ + engine/ | ✅ **C1 时做**（等控制面 OK） | — |
| 10 | RunContext 放 `core/orchestration/` | ✅ **定案：中立层** | — |
| 11 | chat 路径无预算门是否立刻补 | ✅ **随 E1 立刻补**（0.5d） | — |
| 12 | E1 `queue_mode` 语义（§7 问题①）· 10-24 | ✅ **API chat=steer**，细则五条；collect 留枚举位 | 7 家业界调研背书：6/7 默认 steer；`queue-mode-research-20261024.md` |
| 13 | Director 产出是否复用 `BranchContext`（§7 问题②）· 10-24 | ✅ **复用** | 纯数据契约贯通入口→控制器→runtime；真要分家等 C2（成本更低） |
| 14 | Gateway 宿主形态（「单进程」= 哪个进程）· 10-24 | ✅ **挂载式单进程** | `qs gateway` 为进程主人，FastAPI/MCP/scheduler 为挂载渠道，CLI=HTTP 客户端；三前置（workers=1 自检 · core/gateway 零 FastAPI import · 状态 fold 可重建）+ 重执行下放 executor 子进程；automation 需求强制 C 家族（业界先例：Jupyter 切分 · Temporal 恢复 · OpenClaw 薄客户端） |
| 15 | Gateway 内部设计与生命周期 · 10-24 | ✅ 依赖图+6 不变量+启动 fold/关闭 grace；**中断 run 恢复=可配置，默认不自动恢复**；关闭默认等 grace ~5min | ContextHub 只被 runtime 调用；Director/Scheduler 无特权路径；EventStore 唯一共享态；恢复状态不恢复执行；详见 `gateway-hosting-research-20261024.md` §6 |
| 16 | 执行面统一：GraphEngine · 10-24 | ✅ **唯一 runtime = GraphEngine**（包裹 langgraph·支持环）+ 节点类型 llm/react/cpu/graph + preset 治理分级；research preset **Tier 0 冻结**（方法论进化 = preset 版本化+D2 门禁）；DagRuntime 除名（形状即数据）；红线不入配置（图根=turn · cpu 子 run 不得为 study round · 取消连坐 · fence 挂根） | A1=引擎骨架+两节点类型+ExecutorBridge；B1=research preset 化；多 subagent 七规则见执行面文档 §5.2 |
| 17 | 平台配置统一收口 ConfigHub · 10-26 | ✅ 所有平台组件参数唯一来源=ConfigHub：分层装载（代码默认<文件<env）· 治理分级（运维可调 / Tier1 提案=D2 落地处 / 敏感隔离——key 永不进事件流日志）· 启动校验 fail-fast · 脱敏指纹进事件流（配 fold 重建）| E1 起 schema 骨架（gateway/mailbox 段），各刀补自己段；组件构造时收只读切片，不做全局 import |

---

## 4. 架构演进（v1 → v5.1）

| 版本 | 核心表述 | 变化 |
|---|---|---|
| **v1** | 六层 + 四中枢 | 分层合理；中枢是「工作集 / 供给 / 事实 / 底线」 |
| **v2** | 三编排嵌套 + mixin↔step 表 | ⑦ 发现（引擎反向依赖 AgentLoop）；goal 归为账本 |
| **v3** | AgentLoop → 能力层 + 3 驱动循环 + Director | 用户三条决策落地 |
| **v4** | 1 控制面 + N 执行面 + 上下文独占 + 不变量层 | OpenClaw 借鉴；总纲 `architecture-v3-openclaw-inspired.md` |
| **v5** | **管线**：接入 → 统一入口 → 控制面 → 策略/环境 → 执行 → 上下文 → 研究域 → 数据 | 用户提出统一入口；E1 前置 |
| **v5.1** | v5 落边界：入口×控制面 `place()` 唯一写通道 + 双签发；queue_mode 定稿落图 | 10-24 轮次 13/14 拍板；详见 `entry-controller-contract.md` |
| **v6** | **Gateway 化**：入口层塌进 Gateway（协议端点薄·无状态），管线 8 段收 4 段；mailbox 直收 · 两态回执 | 10-24 用户判「仍混乱」→ 直借 OpenClaw 骨架 |
| **v6.1** | v6 落细则：mailbox 先序后闸/cap20/老化 · 容量组闸横带 · home_session · 执行面 T1/T2/T3 · 框头生命周期 | 10-24 轮次 15–19；图 html+svg 已同步 |

---

## 5. 落地计划（合并后唯一起手顺序）

### P0 脊柱（每步可独立合入）

| 序 | 刀 | 内容 | 估工 |
|---|---|---|---|
| 1 | **E1 统一入口协议** | `WorkRequest` / `RunHandle` / `WorkEntry` | 1d |
| 2 | **0.1–0.4 结构可信** | scheduler 泄漏 · SSE 双轨 · 去反向 · 持久化契约 | 4d |
| 3 | **A3 write fence** | activeRunId + 提交校验 | 1d |
| 4 | **A1 runtime 协议** | AgentRuntime + 选择器 + 两实现 | 2d |
| 5 | **A2 SessionController** | 7 阶段 + 队列模式 + 6 真值（等价迁移） | 2d |
| 6 | **A4 ContextHub** | collect/arbitrate/render + 1 provider 试点 | 1.5d |
| 7 | **A5 不变量层** | 2 静态 + 1 运行时不变量 | 1d |
| 8 | **E2–E4 入口迁移** | SessionService / scheduler / CLI / MCP 逐个走 submit() | 3.5d |
| 9 | **E5 幂等键** | 副作用方法可安全重试 | 0.5d |
| 10 | **0.5 门禁** + **0.6–0.7 接缝** | CI 门禁 · 统一注册 · 一致性套件 | 2.5d |

### P1 收敛（3–4 周）

`B1` 编排层收敛 · `B2` 缓存边界 + 分层预算 + 归属 · `B3` 协议版本化 + CI 门禁 · `B4` 身份分离 · `B5` 记忆三改 + 写回

### P2 研究域（等控制面 OK）

`C1` 方法论归位 `core/study/method/` · `C2` Director 落地（propose_next）· `C3` 失败降级阶梯 · `C4` 记忆清除边界 · `C5` 单一调度器收敛

### P3 RSI（前置全满足）

`D1` holdout 集 · `D2` Tier 1 提案通道 · `D3` 晋级门禁 · `D4` 观察期与自动回滚

---

## 6. 文档索引

| 文档 | 内容 |
|---|---|
| **`architecture-v3-openclaw-inspired.md`** | ★ 目标架构总纲（1 控制面 + N 执行面 + 上下文独占） |
| `architecture-diagram.html` | v5 管线图（对照讨论用）· 同 `.svg` |
| `unified-entry-design.md` | 统一入口（E1–E5） |
| `queue-mode-research-20261024.md` | queue_mode 业界调研（7 家）+ steer 细则五条 + 定稿表 |
| **`entry-controller-contract.md`** | ⚠ v5.1 边界契约（被 v6 收薄中，待 session 粒度定案后重写为「协议端点 + mailbox」一页） |
| **`gateway-hosting-research-20261024.md`** | ★ Gateway 宿主形态调研与定案：三方案 / 业界四模式 / 先例对照 / 定案清单 / session 粒度草案 |
| `session-granularity-research-20261024.md` | session 粒度业界调研：六断言判定 + 四条吸收细化 |
| `execution-plane-research-20261024.md` | 执行面业界调研：三层分类法 + Windows 约束 + executor 契约 + 沙箱位 |
| `roadmap-plain-20261026.md` | 实施步骤大白话版 + 执行层剩余讨论清单（编号规则说明） |
| `platform-data-plane-discussion-20261026.md` | ⏸ P-1/P-2 讨论稿：联合评估方案 A+四防护，未定案 |
| `platform-data-plane-industry-research-20261026.md` | 数据面业界调研：六路证据 + 五悬点校准 + pending_writes 等额外收获 |
| `p3-p5-data-plane-design.md` | 数据面收尾三件：P-4 身份定案（turn:run=1:N）+ P-3 登记 + P-5 指针 |
| `pre-e1-final-review-20261026.md` | E1 前最终评估：一致性检查 + 三发现 + 开工清单 |
| `e1-replaceability-assessment-20261026.md` | E1 可替换性评估：3 对重复概念 + 替换刀号 + 净删 ~210 行 |
| `target-directory-structure-20261026.md` | ★ 目标代码目录结构 + 现有文件迁移映射（每刀落位检查单） |
| **`replan-after-e1-20261026.md`** | ★ E1 后重新规划：纵向切片优先，5 阶段 20d，6 个可演示里程碑 |
| `orchestration-triad-design.md` | 控制面 / 策略 / 环境 三件套边界 |
| `orchestration-layer-membership.md` | 编排层成员（含 3 条决策） |
| `industry-benchmark-openclaw-20261008.md` | OpenClaw 逐维度对标 |
| `openclaw-architecture.html` | OpenClaw 原生架构图 + 同构对照 |
| `architecture-reeval-20261008.md` · `architecture-reeval2-20261008.md` | 依赖方向 · SSE 双轨 |
| `agent-design-reeval-20261008.md` · `goal-alignment-review-20261008.md` | 上下文无仲裁 · 导演层缺失 |
| `dsh-plugin-reeval-20261008.md` | DSH 插件模式借鉴与保留 |
| `refactor-master-plan-20261008.md` | ★ 唯一起手顺序与护栏（P0–P3 + 具体刀号） |
| `context-hub-discussion.md` · `context-hub-membership-rule.md` | ContextHub 职责与成员边界 |
| `plug-in-contract-design.md` · `agent-g1-g2-design.md` | 接缝规范 · ContextPlan / 记忆闭环 |

---

## 7. 下一步

（2026-10-24 更新：原两条待定问题均已拍板，见决策 12/13 与 `queue-mode-research-20261024.md`。）

**下一步：起草 E1（统一入口协议，1d）**——`WorkRequest`（含 queue_mode 枚举 + steer 细则五条）/ `RunHandle` / `WorkEntry` 三契约 + 一致性 pytest；顺手补 chat 预算门（决策 11，0.5d，运行时风险）。
