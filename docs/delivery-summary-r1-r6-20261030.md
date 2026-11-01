# 交付总结：R1–R6 重构 + R7/B1–B3 收尾（2026-10-30）

> 分支：`refactor/v6-gateway` · 起点 `376144a9`（v6.3 架构图 + 路线图）
> 终点 `605996be`（B3）· 22 个提交 · 51 文件 · +8318/−206 行
> 架构图上所有「待建」格子已收口（建成、判定不做、或挂起且有触发条件）。
> 验收标准：`docs/stage-c1-acceptance-20261028.md` 三道门（行为等价 /
> 测试不减 / 失败集不扩大）。

---

## 一、一句话结论

按 `docs/roadmap-next-20261030.md` 的 R1–R6 计划**全部完成**；随后 R7
（自纠技术债）与 B1–B3（interrupt / fold / 状态查询）收尾，架构图上所有
「待建」格子关闭。其中 R3/R5/R6/B3 经勘查**大幅缩减范围**（8 项判定不做、
一项延后），依据全部写入评估文档。每阶段三道门全过：失败集始终 ⊆ 104 条
冻结基线且**零新增**，**0 个测试消失**。本轮共新增 **100 个测试**（12 个
文件）。

> 关于「冻结清单 9839 → 12997」：这不是本轮新增。起点 9839 那条基线
> （`1b7e905e`）本身就不完整——R2.3 重冻时发现约 3000 条**早已存在但
> 从未入基线**的测试（最大一块是 `test_alpha_zoo.py` 的 2302 条）。
> 按门禁指引在同一次提交里重冻（`359a52f3`），使后续 worker 从真实
> 基线出发。本轮真正的新增是 100 个。

---

## 二、交付清单

| 阶段 | 交付 | 提交 | 门禁结果 |
|---|---|---|---|
| **R1** | fence 挂根：`SessionFence` 接进控制器 settle 路径（claim→validate→release）；INV-4 进不变量注册表 | `823c237e` | 99⊆104 · 0 删 |
| **R2** | 常驻消费者 + `run.answer` 事件；`/chat/send` 改 receipt + `?wait=true`（E1=C）；steer 真注入（删降级桩）；优先级老化 | `ee7ac66a` `11d8b8a0` `4d673660` `359a52f3` | 100⊆104 · 0 删 |
| **R3** | 同 session 跨 kind 串行域：fence 域=物理 session id；chat 消费者跳过被占、study 占栏/等待/释放 | `2db83143` `bbd76b72` | 99⊆104 · +5/0 删 |
| **R4** | P-3 工件登记接线（round metrics >64KB → 指针）；PROTOCOL_VERSION 单一来源 + replay 严格校验 | `eaf68451` `ca8a157a` | 98⊆104 · +14/0 删 |
| **R5** | ModelHub：provider/model 解析唯一入口，未知 provider 拒绝不降级 | `153eee1a` `5e88a414` | 98⊆104 · +9/0 删 |
| **R6** | 确定性 Director：停因 → 下一步建议，只发事件不自动执行 | `29cb52b7` `ba0a8678` | 101⊆104 · +18/0 删 |
| **R7** | 技术债修复：sink 累积（重复 SSE）、wait_turn 返旧结果、结果无界 | `0e5b636d` `620d51d6` | 98⊆104 · +10/0 删 |
| **B1** | interrupt 语义：CancelScope 节点边界取消活跃 run | `b4d5af8e` `65025fbb` | 101⊆104 · +10/0 删 |
| **B2** | fold 重建：重启恢复 turn 计数/session 映射/标记中断 run | `949ffe7a` `816b1b71` | 99⊆104 · +7/0 删 |
| **B3** | 状态查询 `session_status`；7 阶段/6 真值判定不做 | `be0f81fe` `605996be` | 99⊆104 · +5/0 删 |

---

## 三、做了什么（按能力）

### 3.1 并发正确性

- **写栅栏（fence）真正上线**：此前 `SessionFence` 建好有测试但零调用方。
  现在控制器的每个 turn：准入 `claim` → 执行 → **`run.settled{ok:True}`
  之前 `validate`** → 通过才 `release`。被超越的 run 发 `run.fenced`，
  陈旧答案不落库。**INV-4「产物落库必过 fence」从文档承诺变成可执行检查**。
- **同 session 跨 kind 串行**：此前 chat turn 与 study round 走两条独立
  队列，**互不感知地并发**读写同一 session。现在 fence 域统一为物理
  session id（剥掉 `chat:`/`automation:` 前缀），两侧互相可见：
  study 跑着 → chat 排队；chat 跑着 → study 不取单。「同实体永不并行」
  首次对全部 kind 成立。
- 实现细节修正：fence 最初按 EntryPoint 的 uuid 域 claim，与 scheduler
  持有的物理 id 不是同一命名空间——不统一就会串域。

### 3.2 chat 执行模型

- **常驻消费者**：`Gateway.start_consumer/stop_consumer` + `wait_turn`
  等待原语。由 `qs webui-serve` / `qs api-serve` 宿主启停（带 grace），
  测试/直构路径不启动。
- **`/chat/send` 契约（E1=C，用户拍板）**：默认 submit 即回 receipt、
  答案走 SSE；`?wait=true` 保留同步 reply 给外部脚本用户。**无消费者时
  自动回落原同步路径**——既有 API 测试零改动全绿。
- **答案进事件流**：新增 `run.answer` 事件（过 fence 才发）。消费者模型下
  请求线程看不到 `RunResult`，事件流成为答案唯一通道。
- **steer 真注入**：有活跃 run → 入 steer 队（cap 5，溢出转主队）；
  react 循环边界安全点合批注入（只追加、不新开 run）；turn 收尾滞留项
  晋升主队不丢消息。`steer.downgraded` 临时桩删除。
- **优先级老化**：三级取件 —— 到龄（aged）> interrupt > FIFO。
  普通项等满 `aging_s`（默认 15s）后压过所有后到 interrupt，防 automation
  饿死用户消息；`work.aged` 事件 + ConfigHub 可配。

### 3.3 数据面

- **P-3 工件登记上线**：`ArtifactChannel` 此前完整实现（含 13 个测试）
  但**零调用方**。现在 study round 的 metrics 超过 64KB 时落盘到
  `studies/<id>/rounds/R-n/artifacts/` 并写登记行，`study_round` 事件只带
  指针。小 metrics 事件逐字节不变。**回测结果是第一个真实用户**。
- **PlatformStore 生产接线**：singleton 按 workspace 注入 data_dir，
  `QNODES_PLATFORM_STORE=0` 可关闭，构造失败降级不阻断 chat。
- **PROTOCOL_VERSION**：成为事件信封 `schema_v` 唯一来源；`replay` 遇
  不匹配版本抛 `ProtocolVersionError`（不猜跨版本语义），
  `strict_version=False` 留给迁移。

### 3.4 模型解析

- **ModelHub**：解析序（显式 > profile > provider 默认）集中到一处。
  关键行为修正：`get_provider` 对未知 provider 静默回落 `FallbackAdapter`，
  ModelHub 改为**明确拒绝**（图上红线「不支持则拒绝，不降级」）。
  `auto` 保留为文档定义的合法模式（这条我最初判错，被 4 个既有测试纠正）。

### 3.5 研究自治

- **确定性 Director**：13 个停因 → 4 类动作（continue/rescope/escalate/stop）
  规则表，未知停因一律 escalate。红线三重保证：不 import 预算对象、
  纯函数无副作用、只发 `study.directive` 事件**绝不自动 submit**。
- 命名避开了 `models.py` 里已有的 `StudyDirective`（用户发给 study 的
  指令，方向相反）。

### 3.6 R7：R2 引入的技术债（自纠）

| 缺陷 | 后果 | 修法 |
|---|---|---|
| `install_sink` 闭包链式累积 | 链长随请求数线性增长；更糟的是旧 bridge 捕获旧 message_id，**同一 session 第二次请求出现重复 SSE 文本** | 改集合+锁，`add_sink` 返回注销函数；bridge 收尾事件自动注销，调用方 finally 兜底 |
| `wait_turn` 返旧结果 | `_turn_done` 事件永不 clear，第二次 `?wait=true` 0.000s 拿到上一轮答案 | 改代际比较（`work.enqueued` 推进入队代际，完成推进完成代际，等待前进） |
| `_turn_results` 无界 | 每 session 留含全文的 RunResult | 锁内只保留最近一条 |

实测确认：修复前第二次 wait 耗时 0.000s 返回旧答案；sink 调 5 次是 5 层嵌套。

### 3.7 B1：interrupt 语义补齐

`QueueMode.INTERRUPT` 此前只插队、**不取消正在跑的 turn**。补齐：

- `CancelScope`（单向、线程安全）作为 `state["cancel_scope"]` 传递；
- 引擎在**节点边界**（进入前/结束后）检查，节点抛 `RunCancelled` 也吸收；
- 被取消的 run：发 `run.cancelled` + `settled{cancelled:True}`，**不发
  `run.answer`**（与 R1 的「陈旧产物不落库」同一原则），fence 照常释放；
- 触发：interrupt 工单有活跃 run 时先取消再入队；`Gateway.cancel_session()`。
- **不强杀节点内部**——Python 无法安全杀线程，边界取消已满足体验要求。

### 3.8 B2：fold 重建（重启恢复状态）

此前重启即丢 turn 计数（**turn_id 撞号**，P-4 身份被破坏）、session 映射
（事件流分叉）、in-flight run（无影无踪）。现在 `Gateway.recover()`：

- turn 计数从 `turn.admitted` 取 max → 续号不撞号（端到端：3 turn 后重启
  → 新 turn `#4`）；
- session_key→uuid 映射恢复（同 key 落回同一 aggregate）；
- `run.started` 无 terminal 配对 → `run.interrupted{reason:"restart"}`，幂等；
- **auto_resume=false 兑现**：mailbox 待办不重建（定稿「重试靠重提」）；
- 宿主顺序改为 `recover()` → `start_consumer()`。
- 过程中发现 `list_sessions()` 依赖的 `session_registry` **零调用方**（永远
  为空）——改为 `iter_session_ids()` 直接扫存储层。

### 3.9 B3：状态查询 + 两个死概念的判定

- **「7 阶段状态机」**（OpenClaw 对标：idle/admitting/…/mutating）与现有
  事件生命周期**一一同构**，且控制器单线程串行（R3），阶段机要管理的并发
  状态转换不存在 → ⏹ 不做；
- **「6 正交真值」从未被定义**——全库只有一行注释引用，枚举值不存在，
  所属的 RunHandle 形态已被 v6 取代 → ⏹ 死概念关闭；
- **真缺口**：receipt 契约没有非阻塞 poll 端（唯一原语 `wait_turn` 是
  阻塞的）。补 `Gateway.session_status(session_key)`：active/queued/
  last_turn_id/last_ok/last_answer，全读已有内存状态，与 B2 语义一致。

---

## 四、判定「不做」的项（含依据与重开条件）

| 项 | 判定 | 依据 | 重开条件 |
|---|---|---|---|
| **cpu 节点** | ⏹ 不做 | 动机已被满足两次：回测支持 `background=True` → 子进程；fanout-t2 已接 ExecutorBridge 进程池。再建是第三条等价路径 | 出现非回测的 CPU 密集步骤需要嵌图内并行 |
| **graph 子图** | ⏹ 不做 | 零用户——所有图都是扁平 `add_node` | 出现「阶段内部本身是有环/多分支的子流程」 |
| **subagent lane** | ⏹ 不做 | subagent 当前同步串行 + 每 turn 上限 5，不存在并发打爆 provider | subagent 改为并行执行时 |
| **preset Tier 0 治理** | ⏹ 不做 | 冻结机制要保护「可被外部修改的 preset 文件」，当前不存在（research preset 在代码里，swarm preset 全扁平） | preset 真正外置为可编辑文件时 |
| **RunContext 包对象** | ⏹ 不做 | 图上 6 项约束已全部以散装 state 键存在（run_budget/sink/cancel Event/steer_inbox/run_id/fence_domain） | 出现第 7 个需贯穿的约束时 |
| **7 阶段状态机** | ⏹ 不做 | 与现有事件生命周期一一同构（admitted/started/settled/cancelled/fenced）；单线程串行下无并发状态转换可管理 | 控制器引入并发准入时 |
| **6 正交真值** | ⏹ 关闭 | **从未被定义**（全库仅一行注释引用，枚举值不存在），所属 RunHandle 形态已被 v6 取代 | ——（死概念） |
| **interrupt 级联子 run 树** | ⏹ 不做 | 无子图节点；fanout 分支级联已实现 | graph 子图落地时 |

**延后一项**：方法论归位 `core/study/method/`。前置条件：先解
`phase_engine ↔ runner` **循环导入**（36KB ↔ 54KB）——那是独立的一刀，
不属于「换目录」。

---

## 五、R3 勘误（重要）

v6.3 架构图审计时我判定「CLI/MCP 入口适配器无调用方」——**对 CLI 是错的**。
R3 勘查发现 `ChatSession._run_via_gateway` 早已接 Gateway（`QNODES_NEW_ENTRY`
默认开，有 3 个测试）。同时 study 也早已走 `automation:{session}` mailbox
（阶段 3c/4 完成）。因此 R3 的原计划三分之二已存在，真实缺口只剩跨 kind
串行域。图已更正。

---

## 六、过程中修掉的真实缺陷

| # | 缺陷 | 发现方式 |
|---|---|---|
| 1 | `_fence_domain` 被误插进 `@dataclass` 装饰器与类之间 → 全模块 import 崩 | 测试收集报 `'function' object has no attribute '__mro__'` |
| 2 | R4 给 runner 加 `artifacts=` 参数，破坏鸭子类型测试替身 | **门禁②捕获**：`test_stage3c_scheduler_migration` 不在基线 → 修实现不改测试 |
| 3 | ModelHub 把 `auto` 误判为非法 provider | 4 个既有 singleton 测试失败 |
| 4 | 老化初版把 aged 与 interrupt 同优先级扫描 → 后到 interrupt 仍抢先 | 调试脚本显示 `first: u2` 而非 aged 项 |
| 5 | fence 域最初用 EntryPoint uuid，与 scheduler 物理 id 不同空间 | 设计时发现（未上线） |
| 6 | B2 的 recover 用 `list_sessions()` 枚举——其依赖的 `session_registry` **零调用方**（永远为空），会静默找不到 session | 实现时核实调用方发现 → 改 `iter_session_ids()` 扫存储层 |

---

## 七、对外契约变化（需知会使用方）

| 变化 | 影响 | 兼容 |
|---|---|---|
| `/chat/send` 默认回 receipt 不回 `reply` | WebUI/CLI/TUI **无感**（前端 `send` 响应类型本就是 `unknown`，渲染走 SSE；TUI 走进程内流式） | `?wait=true` 保留旧行为；无消费者时自动回落 |
| `steer.downgraded` 事件 → `steer.became_turn` / `steer.queued` / `steer.applied` | 消费该事件的代码（本仓库内 8 处测试已改） | 无外部消费方 |
| 新增事件：`run.answer`、`run.fenced`、`work.aged`、`artifact.registered`、`study.directive`、`run.cancelled`、`run.interrupted`、`turn.admitted`(+session_key) | 旧消费方对未知 kind 天然忽略 | 向后兼容 |
| 未知 provider 从「静默降级」变「明确报错」 | 配错 provider 的部署会在启动时报错 | 这是有意的修复 |
| INTERRUPT 工单现在**会取消活跃 run**（此前只插队） | automation 提交的工单在 chat turn 跑着时会打断它——这是定稿语义的兑现，不是回归 | 无 |
| 修复重复 SSE：同一 session 连续请求不再叠加旧 bridge | 第二次请求起事件恢复单次推送（此前是 bug） | 修复 |

---

## 八、当前架构状态（v6.3 图已同步）

**已实线（✅）**：Gateway 单进程内核 · WorkRequest 两态回执 · mailbox
（lane 串行 / cap20+摘要 / steer 注入 / 优先级老化 / deadline / interrupt
取消）· SessionController（准入 + P-4 身份 + fence + 跨 kind 串行 +
常驻消费者 + 取消 + 状态查询）· GraphEngine 唯一编排（llm/react/
fanout-t1/t2 + 节点边界取消）· ContextHub（collect→arbitrate→render +
预算仲裁 + 压缩同源）· 不变量 ×6 · 平台数据面（统一库事件化 + P-3 登记
+ P-5 指针 + PROTOCOL_VERSION + fold 重建）· ModelHub · Director
（确定性）· CLI/TUI/API/schedule 四渠道统一入口。

**判定不做（⏹，有据可查）**：cpu 节点 · graph 子图 · subagent lane ·
preset Tier 0 · RunContext 包对象 · 7 阶段状态机 · 6 正交真值 · interrupt
级联子 run 树（全表见 §四）。

**仍开（⏳/⏸）**：容量组闸语义 · 独立宿主进程（qs gateway）· 每 session
分库（D2 延后）· 全局 session 登记 · 方法论归位（⏸ 前置=解循环导入）·
P5-1c 删旧 chat 分支（⏸ 触发条件已定）。

---

## 九、下一步（按优先级）

> **2026-11-01 更新**：原 #2（104 条基线）与 #3（循环导入）已完成——基线
> 现存 61 条在跑、43 条自然通过；循环导入已解（`study/contracts.py`）。
> 当前真实待办见下表。

| # | 事项 | 前置 / 触发 | 状态 |
|---|---|---|---|
| 1 | **P5-1c 删旧 chat 分支** | R2 于 10-30 上线，按「一周无回归」**11-06 到期**。入口 `docs/todo-remove-legacy-chat-branch-20261027.md` §0.3，第一步 ⓿ 删死代码 `_run_agent_loop_background` | ⏸ 待触发 |
| 2 | **D2 提案通道消费方** | 无前置。Director 已发 `study.directive` 事件但全库零消费方（唯一 emit 在 `study/runner.py:227`）——自主闭环差最后一环。估 1–2d | ⏳ 空白 |
| 3 | **架构图 v6.5 定稿** | 已完成（2026-11-01）：48 个 ⏳ 逐格复核，翻格已完成项、修 1 个嵌套标签 bug | ✅ |
| 4 | **L2 统一注册 / CapabilityRegistry** | 无前置但性价比低。实测 **11 套 Registry 类**（图上曾误写 5 套）。建议维持「触碰到时顺带收敛」 | ⏳ 建议不做 |
| 5 | 每 session 分库 | 重开条件：单库 >2GB 或出现归档需求 | ⏳ D2 延后 |
| 6 | 合回 `main` | **用户已明确：暂不合，保持 `refactor/v6-gateway` 分支** | ⏸ 挂起 |

---

## 十、证据索引

| 内容 | 位置 |
|---|---|
| 阶段设计/评估文档 | `docs/stage-r1-fence-wiring-design-20261030.md` · `stage-r2-consumer-steer-aging-design-20261030.md` · `stage-r3-session-domain-design-20261030.md` · `stage-r4-data-plane-design-20261030.md` · `stage-r5-execution-plane-assessment-20261030.md` · `stage-r6-research-domain-assessment-20261030.md` · `stage-r7-r2-techdebt-design-20261030.md` · `stage-b1-interrupt-design-20261030.md` · `stage-b2-fold-recovery-design-20261030.md` · `stage-b3-controller-assessment-20261030.md` |
| 深度统一设计 | `docs/deep-unification-graph-engine-design-20261031.md`（B1/B2/B3）· `docs/plan-retry-fix-and-workflow-migration-20261031.md`（R-A / R-B0–R-B4，含二次评估勘误） |
| 路线图（本轮的输入） | `docs/roadmap-next-20261030.md` |
| 架构图（已同步） | `docs/architecture-diagram.html` / `.svg`（**v6.5，2026-11-01**） |
| 验证矩阵 | `scripts/run_test_matrix.py`（分 lane + 分片并行）· `docs/test-marker-lanes-design.md` §9 |
| 验收工具 | `scripts/c1_gate.py`（失败集）· `scripts/c1_inventory.py`（测试数）· `scripts/cov_gate.py`（覆盖率三档） |
| 冻结基线 | `tests/baselines/pr-lane-failures.json`（104）· `pr-lane-nodeids.json`（12997） |
| 新增测试（共 100） | `tests/test_r1_fence_wiring.py`(7) · `test_r2_consumer.py`(9) · `test_r2_steer.py`(6) · `test_r2_aging.py`(5) · `test_r3_session_domain.py`(5) · `test_r4_data_plane.py`(9) · `test_r5_model_hub.py`(9) · `test_r6_director.py`(18) · `test_r7_sink_and_wait.py`(10) · `test_b1_interrupt.py`(10) · `test_b2_fold.py`(7) · `test_b3_status.py`(5) |

---

## 十一、覆盖率冲刺收官（2026-10-31 补充）

目标「单元测试覆盖率提升至 90% 以上」已达成，口径为**活跃核心**
（A 平台内核 + B 研究域 + C 能力层，排除 CLI 交互向导与外部数据加载器，
依据 `docs/coverage-90-plan-20261030.md`）：

```
scripts/cov_gate.py --min 90 → 全绿（a5f40255）
A 平台内核 91.2% · B 研究域 90.1% · C 能力层 90.1% · 合计 90.3%
（21,994 语句 / 2,131 未覆盖）
```

配套验收：

- `scripts/c1_gate.py` 对冻结基线（104 条）**无失败集扩张**；
- 重型 e2e 组首次全绿（P0 SSE 假死修复后）：sse 4/4 · webui 7/7 ·
  full_flow 10/10 · playwright 8/8 · gated 10/10（诊断记录
  `docs/known-bug-sse-send-deadlock-20261030.md`）。

覆盖率工作副产物——两个此前不可见的**真实产品缺陷**：

| 缺陷 | 症状 | 修复 |
|---|---|---|
| `study/dag_planner.py` 一级相对导入写错（`.agent` 应为 `..agent`） | LLM 规划路径自项目引入起**静默死亡**，永远走关键词兜底 | 改正导入；LLM 规划路径恢复可达 |
| `agent/context_injector.py` GoalContinuation 对非 str content 未归一 | 测试替身/异常 provider 提前抛 AttributeError，未走文档承诺的 TypeError 容忍路径 | `str()` 归一后进入容忍分支 |

新增测试 84 个（checkpoint 恢复 / monitor / 流式聚合 / combo / 数据工具
校验 / continuation guard / AgentLoop 集成等），提交 `16a3c010` + `a5f40255`。

遗留（不在本目标内）：API/CLI 档（D/E）未做 90% 断言；104 条历史基线
失败清偿维持原计划（阶段收尾顺手清）。

---

## 十二、深度统一：执行核收敛 + checkpoint 收口（2026-10-31 补充）

背景：checkpoint 盘点发现「两套执行核 + 两套恢复机制」。用户拍板
**方案 B（深度统一）**，设计见
`docs/deep-unification-graph-engine-design-20261031.md`。

**B1 · SwarmRuntime 层循环退役**（提交 7dc07b35）：`execute()` 变为
SwarmPreset → GraphEngine 编译器——每层 `(split → fanout → join)` 链，
层屏障语义保真；契约（SwarmPreset/AgentResult/SwarmHook/cancel）零改动，
三个消费面（goal workflow ×3 / CLI swarm ×2 / MCP ×1）一行未动。
引擎侧仅加 opt-in `include_result`（branch.settled 事件携带结果，
完成序逐条交付桥接 on_agent_complete）。至此**全系统执行核收敛为
GraphEngine 一个**：chat / study / fanout 原生 + goal/CLI/MCP swarm 经编译器。

**B2 · checkpoint 收口**：层间自动存档（`GoalWorkflowHook.on_layer_complete
→ runner.checkpoint()`，此前 checkpoint() 零调用方、崩溃即丢）；
`/goal checkpoint resume` 改调 `resume_from_checkpoint + resume_and_continue`
——从断点层真续跑（pre_completed + 引擎原语 `run(start=layer_k)`），
替换 v0.5.1 的「只展示」。恢复原语自此与 study 轮恢复（plan A）同源。

验收：行为等价（受影响 24 套件 568 测试**零修改**全绿）+ 15 个新语义锁
测试（hook 线程/完成序、should_stop 每层、预算层边界中断、未注册 agent
静默排除 vs stub、resume 重放、CLI 端到端续跑）。

附带修正的认知：旧 `_execute_layer` 的 retry 分支实际是**空转**（重试
agent 已有结果即被跳过）——本刀按「行为等价」原样保留并在设计文档 §3
决定 B 注明，未顺手"修"（等价优先，修语义属另一个决策）。

边界：core/workflow 定义系统（webui workflow 特性）未迁——同模式可将来
另开一刀；study plan A 不动；忠实 DAG 边（多后继）留作引擎演进项。

---

## 十三、workflow 定义系统迁引擎（R-B0…R-B4，2026-10-31）

上一节的边界项已完成。设计见
`docs/plan-retry-fix-and-workflow-migration-20261031.md`（含二次评估勘误）。

| 刀 | 内容 | 提交 |
|---|---|---|
| **R-B0** | `node_outputs` 增列 `output`（老库自动 ALTER、幂等）+ `rebuild_agent_results` | bcb49500 |
| **R-B1** | 新建 `WorkflowGraphCompiler`（definition → GraphEngine），影子验证 7/7 | b1a15f04 |
| **R-B2** | `WorkflowRunner` 主路径切编译器；`resume_from_store` 关掉「重启即卡死」 | e8b19445 |
| **R-B3** | 编译器内 replan 环；删旧段循环（executor.py 708 → 476 行） | 62a831c6 |

**关键修正**（二次评估推翻初版方案）：WorkflowStore 只存 summary 不存
output，且引擎 state 含不可序列化对象——「state 落库」不成立，改为
**run 级重建**（补 output 列 + 从关系数据重建 pre_completed）。

**顺手修掉的产品缺陷**：`/approve` 原先依赖内存里的 runner 实例，
进程重启即 404 永久卡死；现在任何实例都能从 store 重建并续跑。

**验收**：40 个 R-B 测试全绿（store 列 / 影子对比 / resume / 段循环 20 例，
其中 3 个此前失败的 replan 用例现全部通过）；完整 workflow 回归 293 passed，
仅剩 4 个冻结基线内的 CLIExecutor 失败。

### R-A · retry 空转修复

`branches[].action=retry` 此前是**空转**：动作把目标加回剩余层，但 split 节点
的「已有结果即跳过」过滤又把它丢掉——而 retry 的前置条件恰恰是「目标已跑过」。
现改为显式 `retry_queue` 穿过图状态：split 接受「无结果 **或** 在重跑队列」
者，队列项在准入时消费（一次命中一次重跑），由新字段
`SwarmPreset.branch_max_retries`（默认 1）封顶防无限重跑；重跑结果覆盖旧值、
插入序保留，`on_branch_retry` 钩子可观测。6 个测试锁定。提交 d589f777。

**优先级说明**：全库 4 个 workflow 模板 + 6 个 goal preset **零 branches 配置**，
属"有承诺无用户"，故定为 P2 并排在主线之后。

### 最终门禁

| 门 | 结果 |
|---|---|
| 行为等价 | workflow 全量回归 293 passed + swarm 82 passed |
| 覆盖率 | cov_gate A 91.1% / B 90.1% / **C 90.5%**（合计 90.5%）✅ |
| 失败集 | c1_gate **clean**（98/104 基线内，无扩张）✅ |
| 测试库存 | c1_inventory **+178 增 / 0 删** ✅ |
| 代码量 | executor.py **708 → 391 行**（-45%），旧段循环彻底出清 |

**一次时序失败 → 追下去是产品缺陷（已修）**：最终全量里
`test_p0_loop_fixes::test_rejected_no_progress_exits` 失败过一次。
起初判为测试脆弱，但确定性复现证明是**产品竞态**：

`_check_no_progress` 在等待用户决策前无条件 `clear()` 事件并把
`_approval_response` 置 None。生产里用户可能在 agent 还没走到审批门时
就点了批准/拒绝（人机交互的正常时序，不是边缘情况）——决策被静默丢弃，
然后阻塞到 `approval_timeout`（**默认 1800 秒**）。测试的 `sleep(0.2)`
只是在负载下恰好落进这个窗口。

修复：等待顺序改为 `clear → 查 response → wait`，提前到达的决策被立即
消费；消费后复位，供下一轮使用（第三个别有用心的测试锁住「上一轮决策
不得泄漏到下一轮」）。新增 `tests/test_loop_approval_race.py` 三个
**确定性**用例（不靠 sleep 竞速），重复 5 次全稳，耗时从 11.3s 降到 1.8s。
提交 686b2200。

**同一轮追查又揪出一个更严重的缺陷（JWT 密钥写坏，已修）**：
继续查测试稳定性时发现 `test_persisted_file_round_trip` 间歇失败，
定位到两处真实缺陷：

| 缺陷 | 后果 |
|---|---|
| `os.open()` 未加 `O_BINARY`，Windows 文本模式把密钥里的每个 `0x0A` 写成 `0x0D 0x0A` | 文件存 33–34 字节而缓存 32 字节 → **重启后所有已签发 JWT 立即失效**，新签发的又与磁盘不一致 |
| `read_bytes().strip()` | 随机密钥首/尾的合法空白字节被剥掉，缓存值与磁盘值不一致 |

随机密钥含 `0x0A` 的概率约 1/8，所以这个缺陷长期以「偶发失败」的形态
潜伏。修复：写盘加 `O_BINARY`；只把「全空白文件」视为缺失，返回完整字节。
新增 2 个**确定性**守护测试（把 `token_bytes` 钉成含 `0x0A` 的值，
回退修复即变红）。提交 225468cd。

**这两个缺陷都修完后，冻结基线里 2 条（`test_empty_file_is_treated_as_missing`、
`test_persisted_file_round_trip`）已不再失败。**

---

## 十四、文档同步与现状定稿（2026-11-01）

### 架构图升 v6.5

架构图此前停在 v6.4（10-30），48 个 ⏳ 标记与代码脱节。本次对照代码
**逐格复核**后更新：

| 变更 | 依据 |
|---|---|
| 执行面「唯一编排 runtime」→「**全路径收敛**」 | B1（goal/CLI/MCP swarm 经编译器）+ R-B（webui workflow 经编译器），两套自研调度器已删除 |
| PlatformStore「已建未接线」→「**生产接线**」 | `gateway.py:91`、`event_store.py:114` 实测注入 |
| 脱敏指纹 ⏳ → ✅ | `gateway.emit_config_fingerprint()` + `server.py:39` |
| 「5 套注册表并存」→「**11 套** Registry 类并存」 | 原数据是错的，实测 11 个 `class *Registry` |
| 落地速览 | 改为 v6.5 现状：单一引擎、恢复原语统一、覆盖率三档 ≥90%、分片验证 7.5min；未建项与挂起项分开列 |

同步修掉一个**原有的** HTML 结构 bug：`PROTOCOL_VERSION` 那行缺
`</text>` 闭合，导致下一行被嵌套（渲染时视觉上会重叠）。HTML 与 SVG
两份都已修，并加了结构校验（`text` 开闭数相等、无跨标签嵌套）。

### 保留为真实未完成（不虚报）

逐项核实后确认这些**确实没做**，标记维持 ⏳：

- **稳定前缀**：全库无 `stable_prefix` 实现（ContextHub 只做了 collect/arbitrate/render）
- **hypothesis provider**：无任何 HypothesisProvider 代码
- **subagent lane**：无实现（R5 判定「无并发问题」不做）
- **容量组闸**：无 CapacityGroup（等待真实并发压力触发）
- **每 session 分库**：仍是统一单库（D2 延后）
- **cpu 节点 / graph 子图**：R5 判定不做（等价物已有 / 零用户）
- **L2 统一注册**：11 套并存

### 当前质量门（2026-11-01 实测）

| 门 | 结果 |
|---|---|
| 覆盖率（A/B/C 三档） | A 91.1% / B 90.2% / C 90.5% ✅ |
| 失败集（c1_gate） | clean——61 条在跑，全部落在冻结基线 104 内 ✅ |
| 测试库存（c1_inventory） | +178 增 / 0 删 ✅ |
| e2e lane | 84 passed / 0 failed ✅ |
| browser lane | 10 passed / 0 skipped ✅ |
| 完整三层验证耗时 | 约 7.5 分钟（分片并行） |

基线里 **43 条已自然通过**（含 JWT 修复治好的 2 条），说明「清偿历史账」
这个方向在按 D4 原则缓慢收敛，无需单独立项。

---

## 十五、覆盖率口径修正与 H 档补测（2026-11-01 续）

### 承认一个测量错误

上一轮我说「覆盖率三档全绿 90.4%」。这个数字没错，但**口径有偏**：
`cov_gate.py` 只断言 A/B/C（22,244 行），而 `core/` 顶层的单文件模块
（db / backtest / compute_factor / factor_validate / data_readiness /
data_import / config_runner）没有子包前缀，三个档位一个都匹配不到。
3,966 行研究工具核心就这样落在缝里，只有 66%——而 `db.py` 单文件就有
18 处生产调用。

这是我按「目录重要性」划线而非「业务关键性」造成的选择偏差。

### 修正：新增第四档 H

`cov_gate.py` 现在有四档，H 消费 A/B/C 剩余的行；数据加载与回测引擎
（`data_source` / `backtest_engine` / `alpha_zoo` / `portfolio` 等）改为
显式的 `EXTERNAL_HEAVY` 排除，不再混在「其他」里假装被覆盖。

修正后的真实数字：

| 档 | 语句 | 覆盖率 | 判定 |
|---|---:|---:|---|
| A 平台内核 | 4,855 | 91.1% | ✅ |
| B 研究域 | 7,159 | 90.2% | ✅ |
| C 能力层 | 10,235 | 90.5% | ✅ |
| **H 研究工具核心** | **8,541** | **79.2%** | ❌ 未达标 |
| 门禁合计 | 30,790 | 87.4% | |

H 档报红是**故意保留的**——它就该红到补完为止，不能用三档掩盖。

### H 档补测进展（+301 个测试）

| 文件 | 起点 | 现在 | 增量 |
|---|---:|---:|---:|
| `core/data_readiness.py` | 36.8% | **79.4%** | +42.6 |
| `core/factor_validate.py` | 67.2% | **79.0%** | +11.8 |
| `core/data_import.py` | 21.0% | **43.3%** | +22.3 |
| `core/backtest.py` | 47.1% | **55.7%** | +8.6 |
| `core/db.py` | 40.5% | **59.9%** | +19.4 |
| **H 档整体** | 75.2% | **79.2%** | +4.0 |

都是纯本地逻辑（DuckDB CRUD、长/宽表转换、评分数值、TSV 契约），
不存在「外部依赖没法测」的理由。`data_import` 剩余缺口是远程 loader
（tushare/ifind/fred/akshare），需要外部 SDK mock，明确不追。

### 顺带修掉的真实缺陷（由补测暴露）

**`get_connection(read_only=True)` 在空 workspace 上抛异常而非返回 None。**
DuckDB 对不存在的库抛 `IOException: database does not exist`，但 20 个
调用点（db.py 内部 + data_readiness + config_runner + preflight + cli）
都约定「None = 无数据」。

实际后果：`data_readiness` 文档承诺 C0「数据库不可用 → fail 检查」，
但它在构造该检查之前就崩了——整个 pre-flight 挂掉，而不是报告「没有
数据」。写 C0 的测试才发现。

修复：只读连接遇库不存在/打开失败一律返回 None；写模式仍抛出（写入
失败必须响亮）。

**另一处已记录的局限**：`compute_data_fingerprint` 只哈希
（列名、行数、日期范围），不哈希数值。能检出「换了资产/换了区间」，
检不出「同区间数值被修订」。若将来用于缓存失效判定需要补数值摘要。
已用测试显式钉住该行为，不再让它是个隐含假设。

### 删除的死代码

`core/hooks/bundled/` 整个包（87 行 `CommandLoggerHook`）——只被
`__init__.py` re-export，全库零实例化、零测试引用。`alpha_zoo_convert.py`
（549 行）虽有零生产调用方，但有两个专门的测试文件在跑它，属一次性迁移
工具仍被验证，保留不动。
