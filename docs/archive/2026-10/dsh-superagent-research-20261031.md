# DeepSeek Super-agent 调研：文章、开源原型与实现核对

> 调研时间：2026-10-31 / 2026-11-01
> 代码基线：`deepseek-ai/dsh-superagent-prototype` @ `6337fc8`（2026-10-31 20:05 +0800）
> 二手来源：[搜狐·深维DimDeep](https://www.sohu.com/a/1095614133_123032853)（2026-11-01）
> 原文：《Super-agent 设计探索：迈向更自主的 Agent 系统》，DeepSeek Harness 团队 / 署名「DeepSeek 产品部」，2026-10-31 晚间发布于官方公众号
> 配图：`dsh-superagent-engine-architecture.svg` · `dsh-superagent-admission-gate.svg`

---

## 0. 结论速览

1. **文章是二手报道，主力产物是开源原型。** 搜狐那篇没有给出任何代码地址；真正的对应仓库是 `deepseek-ai/dsh-superagent-prototype`，MIT、TypeScript、单一 commit 一次性发布。
2. **文章不是产品说明，是架构探讨。** 原文自己写明"原型并非正式产品"，README 更进一步：*"Some designs discussed in the article are directions for future development and are not implemented in this prototype."*
3. **双层 Router/Worker、事件驱动、异步全双工、多信道是"真货"**，构成仓库主干；**存算分离、外部 Watchdog、链式召回（Chain of Recall）是"PPT 部分"**，代码里 grep 不到。
4. **那组 12.4 / 15.7 分钟的消融数字没有可复现的基准代码**，仓库内不存在对应实验脚本。
5. **值得学的是领域建模和工程约束**，尤其 `docs/` 那份编号文档体系与 Engine 的单写者模型；打折扣的是架构宣传本身。

---

## 1. 仓库基本盘

| 项 | 值 |
|---|---|
| 仓库 | `deepseek-ai/deepseek-harness` 组织下另有本体；原型为 `deepseek-ai/dsh-superagent-prototype` |
| 描述 | Towards a More Autonomous Agent System |
| Star / Fork | 4,675 / 434（创建于 2026-10-31T09:32Z，约一天数据） |
| 语言 / 协议 | TypeScript / MIT |
| 体积 | 775 KB，**1 个 commit**，无 tag、无 release、无 discussions，issue 数为 0 |
| 提交者 | `DeepSeek Harness Team <team@dsh.dev>` |
| 依赖 | `@deepseek-ai/dsh-*` 0.2.1-alpha.2 一整套（peer 约 25 个），`engines.dsh` 要求 `^0.2.1-alpha.2` |
| 安装方式 | `package.json` 为 `private: true`、`version: 0.1.0`，**不能 npm 安装**；只能 `dsh plugin --profile superagent add "link:$PWD"` 挂进独立 DSH profile |

**目录结构**

| 目录 | 内容 |
|---|---|
| `src/` | 111 个文件（110 个 .ts/.tsx），含 `core` / `app` / `client` / `executor` / `sources` / `infra` |
| `tests/` | 65 个测试文件 |
| `docs/` | 9 份编号文档（000 哲学 → 008 cookbook） |
| `prompts/` | 13 个提示词 md，含 `team/` 三个分节 |
| `skills/` | 2 个 Source skill（GitHub / Feishu） |

全仓 177 个 TS/TSX 文件。核心 `src/core` 只依赖 `node:crypto`，不做任何 I/O。

---

## 2. 文章讲了什么（原文结构）

原文共 8 节，主张是「把计算机系统的经典工程思想搬到 Agent 构建上」，认为瓶颈正从人的注意力转向系统架构。

### 2.1 四大特征

连续（跨 IM / GitHub 评论的单一 Agent 感）、高效（并行、不阻塞）、主动（感知环境事件自主推进）、持久（长期在线、异常自恢复）。文章认为**单个 ReAct Loop 撑不住这四点**。

### 2.2 双层架构

| 层 | 职责 | 实现要点 |
|---|---|---|
| Router | 常驻全局路由层，承接全部人类交互，汇总跨任务信息与决策，充当全局序点、协调点、记忆 | 单个常驻 LLM Session，轻量模型即可，**不执行任务，甚至可不配 Bash** |
| Worker | 上下文隔离，挂载完整工具，专注单一任务 | 通常配最强模型；不直接与人交互，只向 Router 汇报；Worker 间通常不直接通信（冲突多在共享 PR 等环境，靠订阅同一 Thread 自行解决） |

积压 Trigger 下一轮批量合并，靠 Prefill 并行摊薄单请求耗时。文章的核心表达是：**Session 应是"系统的实现细节，而不应是用户的认知负担"**。

权衡原文也讲清楚了：全事件同一 Session → 上下文最连续但串行受限；每事件新开 Session → 吞吐最高但缺全局上下文、结果割裂、多 Session 各自回复造成混乱。

### 2.3 事件驱动

- **Trigger**：群聊、邮件日程、代码审查评论、CI 结果等半结构化环境事件。
- **Fast / Slow Path**：程序层按来源、群组、白名单确定性筛除；语义层由 LLM 按策略判断，同时充当自然语言扩展点。
- **Thread 路由**：已订阅 → Fast Path 直通 Worker，不经 Router；未订阅 → Slow Path，Router 只负责新事件分派。
- **消融数据**：去掉直通后完成时间基本不变，但 Router 决定数 +30%，读缓存 token 从 2,323 万增至 3,177 万（+37%）。

**上下文即缓存**：Inline（近期高概率信息内联进窗口）+ Spill（超预算信息留引用/摘要）。**链式召回（Chain of Recall）**：给 Trigger / Thread / Worker Session 唯一 ID，注入时内联下一跳 ID，形成可逐跳检索的引用图。

### 2.4 异步多信道

把同步 `run()` 改为 `start()` / `stop()` 全双工：`start()` 立即返回 Started，后台完成作为新事件投回会话。多信道隔离：IM 硬性规定单次回复**不超过 100 词**，GitHub / 邮件侧重结构化高密度交付。

### 2.5 存算分离

- 计算与状态分离：Agent Loop 进云端无状态弹性容器，Session 状态进数据库。
- 计算与执行环境分离：端侧部署不保存会话状态的轻量 daemon，只执行命令返回结果；支持 BYOM（Bring Your Own Machine）、云端托管、按需创建；可跨设备切换。文章自陈这与 Anthropic 的「脑手分离」思路一致，区别在优先接入用户日常、可能间歇在线的个人机器。

### 2.6 Watchdog

主系统外一个独立 Watchdog Session，定时消息驱动，构成外部 Control Loop。除处理基础设施故障，还用模型判断 Worker 是否有实质进展、方向是否偏离；本身具备完整工具能力，可用自然语言询问原因或在线增删提示词、调整触发规则。

### 2.7 实验数据

约 300 条消息（约 20 条工作请求，其余闲聊/跟进/叫停/CI），编码工作用只耗时的工具代替，每方案重复 5 次。

| 方案 | p50 | p95 | 备注 |
|---|---|---|---|
| Super-agent | 12.4 min | 15.7 min | — |
| 单 Session + Subagent（中性提示） | — | 29.5 min | 模型 5 次均未调用 Subagent |
| 提示优先委派 | 慢于 Super-agent | — | 5 次中 4 次委派仍更慢，剩 1 次自执行形成长尾 |

另有 Router 批处理实验：Trigger 到达速率从约 1 条/分钟提到约 40 条/分钟，Router 用真实模型、Worker 用 Mock。

### 2.8 开放问题

双层架构如何扩展（Router 何时分片、Worker 何时直接通信、系统与模型 Co-design）；脱离 Session 后的人机协作形态；上下文边界与 A2A；安全与隐私（云端被入侵或提示词注入会危及全部端侧设备；需让 Agent 不接触凭据即可使用凭据）。

---

## 3. 文章 vs 代码：哪些真落地

按原文各节逐项 grep 核实。

| 文章设计 | 代码状态 | 证据 |
|---|---|---|
| Router / Worker 双层、两个 preset、批量合并 Eval | **已实现（主干）** | `cordis.patch.yml` 定义 `dsh-superagent-router` / `dsh-superagent-worker`；主 Session 14 工具、Executor 6 工具 |
| 事件驱动、Fast/Slow Path、Thread 订阅直通 | **已实现** | `Source → Trigger → Thread → Eval`，gate（确定性）+ assessment（模型）两层 |
| 异步 start/stop 全双工 | **已实现** | `DshExecutor` + `executor/background.ts`（`ctx.jobs` / `ctx.subagents`）+ `turnEnded` |
| 多信道（100 词 IM / 结构化 GitHub） | **已实现** | `src/infra/text.ts:3` `IM_WORDS = 100`，`AcknowledgementSchema` 在 schema 层强制 |
| Inline / Spill 上下文管理 | **部分实现** | `sources/admission-context.ts` 的 `threadWindow`：前 5 条 + Trigger 前后各 5 条，400/1200 字符截断，其余给 ID 存根 |
| 链式召回 Chain of Recall | **未实现** | grep 无命中；只有普通 ID 资源分页 |
| 存算分离（云端无状态 + 端侧 daemon + BYOM） | **未实现** | 单机 SQLite（`<stateDir>/orchestrator.sqlite`）+ 本地 worktree |
| 外部 Watchdog Session | **未实现** | grep 无命中。有 `HealthBoard`、`retry-health`、运维小时巡检脚本，但那不是「带工具、用模型判断 Worker 是否跑偏」的独立 Session |
| 提前行动 / 静默复现修复 | **部分** | `observe` 模式 + `dropped` report + 提示词静默修复约定，无投机执行调度 |
| 12.4/15.7 分钟消融、40 trigger/min 压测 | **仓库内无对应代码** | 无 benchmark 脚本 |

**结论**：双层 + 事件驱动 + 全双工 + 多信道是实打实的；存算分离、Watchdog、链式召回是文章卖点但代码里没有。二手报道在这点上说得含糊，容易让人误以为开源仓库就是文章描述的系统。

---

## 4. 授权模型核对：准入表逐条对照

这是本次核对的重点。结论：**核心语义高度吻合，偏差集中在 4 处未文档化分支和 1 处兼容旁路**。

### 4.1 实现结构

准入门闸在 `src/policy.ts` 的 `settingsPolicy(settings, sources).gate()`（第 191–211 行），分两条路径：

```ts
if (!trigger.human) return machineGate(trigger)   // bot / CI
```

人写的消息则走六道判定（详见 `dsh-superagent-admission-gate.svg`），**首个成立者生效**：

| 序 | 条件 | 结果 |
|---|---|---|
| 1 | `allowedSender && trigger.addressed` | explicit · via addressed |
| 2 | `admission === 'explicit'` | explicit · via admission |
| 3 | `¬(addressed==='direct' && !allowed) && (allowed \|\| mode≠off) && participating(thread, tickets)` | allowed ? explicit : inferred · via followup |
| 4 | `admission === 'inferred' && mode ≠ 'off'` | inferred · via admission |
| 5 | 以上皆否 | 拒绝 |

### 4.2 准入表三格

`admissionOf()` 的取值是 `表[row][cell] ?? ADMISSION_DEFAULTS[cell]`：

| 发送者 × 是否 @ | cell | 默认 | 可选 |
|---|---|---|---|
| 在名单 · 未 @ | `listedUnaddressed` | inferred | inferred / ignore |
| 不在名单 · @ | `unlistedAddressed` | ignore | explicit / inferred / ignore |
| 不在名单 · 未 @ | `unlistedUnaddressed` | ignore | inferred / ignore |

未 @ 的消息只有在 `receivable = addressed || (listened && observed)` 成立时才查表，否则直接 `ignore`。

### 4.3 与文档的偏差

| # | 级别 | 位置 | 结论 |
|---|---|---|---|
| **F1** | 低 | `policy.ts:206` | **未文档化的闸门规则。** 不在名单的人**私信**智能体时被显式排除在 follow-up 强度之外，只能走准入表。`002-authorization.md` 全文没有这条。**这是唯一会改变判定结果的差异**：未名单用户私信、且该 Thread 上已有工单时，代码给出拒绝（表默认 ignore），而按 002 对 follow-up 的描述读者会预期 inferred。 |
| **F2** | 中 | `policy.ts:40-61` | **002 单独读不完整。** `assessmentOutcome` 除「至少一条匹配规则」外，还要求 `direction`（`implementation_requested` 为真、`target_basis ≠ undetermined`、`!requires_design_decision`）和 `intent_evidence`。这道 direction 门只在 `007-architecture.md:145` 一句话和 `prompts/admission.md` 里出现。拿 002 当授权权威文档会严重低估 accept 门槛。 |
| **F3** | 低 | `model.ts:116-123` | **007 的 `via` 表少一个值。** 文档列 4 个（addressed / followup / admission / bot），代码有 5 个，多出 `listed`（早期 Mandate 未记录 basis 时的兼容值），另带 `authority` 旧字段。 |
| **F4** | 低 | `policy.ts:210` | **拒绝原因的选取顺序未文档化。** `!allowed → not_a_member`；`receivable && admission==='ignore' → admission_ignored`；否则 `no_mention`。002 零散提到这几个码但没给顺序，运维无法从文档反推。 |
| **F5** | 低 | `policy.ts:217-218` | **老记录少一道复验。** 「Source 是否仍监听该 Thread」只在 `basis.source && basis.scope` 都存在时才检查；002 写成无条件要求。schema-10 迁移来的 Mandate 会跳过。 |
| **F6** | 一致 | `policy.ts:212-240` | **复验链与文档逐条吻合。** followup 显式直接通过；admission 按 `STRENGTH`（ignore 0 < inferred 1 < explicit 2）单调比较；被列入名单的人从 `unlistedUnaddressed` 迁到 `listedUnaddressed`；表单元格 weakening 会吊销显式 Mandate；`observation` 存在时必须仍是 auto 且匹配规则全启用。 |

### 4.4 另外两处一致点

- `policyVersion` 只哈希 rules + accept mode + sources，所以改并发、改超时、暂停都**不作废**旧决定（与 007 一致）。
- `evals` 健康项用 1 小时窗口，面板提示未完成 Decision 用 24 小时窗口（`overview.ts` 的 `dayAgo()`），两者与 002 表述完全对应。

---

## 5. Engine 架构

见 `dsh-superagent-engine-architecture.svg`。

### 5.1 单写者与单向依赖

`Engine` 是 Triggers / Threads / Decisions / Tickets / Assignments 的唯一写入者。所有方法同步，每条命令一个事务。外部副作用一律先入队（`Effects`，六种 kind 各自带重试退避和健康项），**提交后才执行**。

它自己不写逻辑，委托五个 part，依赖严格单向：

```
Replies（无依赖）
  ↑
Tickets ──→ Replies
  ↑
Assignments ──→ Tickets, Replies
Evals ──→ Tickets
Intake ──→ Tickets
```

所以 Engine 构造时先建 `Replies`，再由它往上搭；Executor / Assessor 回调 Engine，Runtime 用 getter 而非构造注入来打破循环。

### 5.2 Ticket 状态机

关键设计：**stage 不是被设置的，而是每次从数据推导**（`src/core/stage.ts` 的 `ticketStage()`），自上而下首个匹配生效：

| stage | 条件 | 面板文案 |
|---|---|---|
| person/delivery | Input 投递失败 | Input undelivered |
| person/answer | 有 attention | Awaiting answer |
| person/review | 最新 report = ready | Awaiting review |
| person/blocked, failed | 最新 report 为 blocked/failed 且此后无人的消息 | Blocked / Failed |
| working/queued | 无 Assignment 或 pending | Queued |
| idle | report=waiting 或 Assignment 结束 | Waiting for a Trigger |
| working | 其余 | Working |

推论：「在等人」永远优先于「在干活」——一个既投递失败又有 ready 报告的工单显示为「输入未投递」。

`working` 与 `idle` 之间没有显式转移：`idle` 是「turn 结束且无待办」，只要 Trigger 到达订阅的 Thread，`Intake` 就把它变成 Input 并 `wake`。`ended` 回到 `queued` 需要**人消息 + Mandate 复验通过**，是唯一复活路径。

### 5.3 并发与隔离（容易踩的点）

- **WorkerSlots**：launch / delivery 前同步预留槽位；人的消息可额外占一个槽（`by === 'person'`）并优先于新 launch，所以人不会被 Agent 排队挡住。
- **一单一批 Session + 一个目录**，`worker-<ticket id>`，前台命令超 5 秒转后台 job（`cordis.patch.yml` 里 shell 和 job wait 都钉在 5000ms）。
- **隔离很弱**：Executor 以操作员 OS 用户、`danger-full-access` 运行；`gh-agent` / `lark-bot` 这些 wrapper 只选凭据，不是安全边界。文档自己承认「worker 可以按绝对路径执行程序、可以读你的 home 目录」，真隔离要开独立 OS 账户。
- **前置依赖偏重**：Node 22.19+/24+、pnpm、git 2.32+、tmux、Python 3.8+、gh 2.48+，可选 lark-cli 1.0.90，且要先装 `@deepseek-ai/dsh@0.2.1-rc.3`。
- **只支持 github.com 单仓库**，不支持 GitHub Enterprise；agent 不能是 GitHub App（要靠账号通知），必须是独立 user account。

---

## 6. 值得借鉴的部分

对量化策略研究这类项目，可直接迁移的是**结构与约束**，不是它的业务语义：

1. **一个写入者 + 效果队列。** 命令同步改聚合、副作用持久化入队、提交后执行——这套在三处都很有用：回测任务调度、研究轮次的 artifact 落盘、LLM 调用。它换来了「事务回滚不会留下半个副作用」。
2. **状态推导而非状态存储。** `ticketStage()` 每次从数据算 stage，避免状态机与事实不一致。同理可用于 study round / workflow run 的状态展示。
3. **准入分两层：确定性闸门 + 语义判断。** 先把「谁可以发起工作」用纯函数判定（可测试、可审计），再交给模型判「这件事要不要做」。这是 HITL 设计里最容易被做糊的地方。
4. **文档即规格，且有测试守文档。** `tests/docs.spec.ts` 会检查编号文档只链接更早的文档、每个本地链接可解析、无合并冲突标记；`tests/model-surface.spec.ts` 检查工具/提示词/skill 的漂移。这个思路比「写完就烂」强得多。
5. **"少即是多"的哲学文档。** `docs/000-philosophy.md` 把「每个人类注意力都是有成本的，每个通知都要值回票价」变成硬性评审标准，并写清了 operator 的责任（start narrow / tune for less noise / 不要用来产出量）。

---

## 7. 需要注意的取舍

- **原型的 self-description 是可信的**：作者明说 much of the engineering done by coding agents、研究代码、可能有 bug、风格不一致。单 commit 无历史印证了这是一次性整理发布，不是长期迭代项目。
- **周边生态热度不能算到这个原型头上**：`deepseek-harness` 本体 26.8 万 star，第三方 `dsh-desktop` 3.4 万、`awesome-dsh-plugin` 2.2 万，插件生态很热，但与 `dsh-superagent-prototype` 是两回事。
- **文章的实验没有可复现性**：300 条消息、5 次重复、编码工具用「只耗时」替代——这是一次方向性验证，不是基准测试。引用它的数字时应注明这一点。

---

## 8. 复用与验证建议

若要在本项目借鉴，建议按以下顺序小步验证：

1. **先只抄 `ticketStage()` 那种「推导式状态」**，用在一个已有子系统的状态展示上，观察是否消除了状态不一致 bug。
2. **再抄「确定性闸门 + 语义判断」两层结构**，落在某个人工确认点（如自动提交回测、自动改策略参数）上，先跑 `observe` 模式只记录不执行。
3. **最后才考虑「单写者 + 效果队列」**，因为它要求把现有 IO 都改成可入队的形态，成本最高，收益在长任务（回测/研究轮次）场景才明显。

不建议照搬的：`danger-full-access` 的执行模型、单机 SQLite 的持久化方式、仅支持单 GitHub 仓库的 Source 设计。

---

## 附：代码索引（本报告引用的位置）

| 主题 | 路径 |
|---|---|
| 准入门闸 | `src/policy.ts:167-242` |
| 评估结果校验（direction / intent_evidence） | `src/policy.ts:40-61` |
| 准入表默认值 | `src/admission.ts:9-13` |
| Mandate 与 basis 类型 | `src/core/model.ts:96-133` |
| 复验 | `src/policy.ts:213-240` |
| 策略版本哈希 | `src/policy.ts:111-118` |
| Engine 入口 | `src/core/engine.ts:24-258` |
| 五个 part | `src/core/{intake,evals,tickets,assignments,replies}.ts` |
| stage 推导 | `src/core/stage.ts:9-26` |
| 共享谓词（waitsForPerson / settled / followupOwners） | `src/core/model.ts:75-96, 349-353` |
| 端口定义 | `src/core/ports.ts` |
| 效果队列 | `src/app/effects.ts` |
| 窗口上下文 | `src/sources/admission-context.ts:6-42` |
| 100 词限制 | `src/infra/text.ts:3`、`src/app/tools/tool.ts:16-17` |
| 状态机与并发 | `src/executor/dsh.ts:168-232` |
| 24 小时窗口 | `src/app/views/overview.ts:42`、`src/app/views/records.ts:15-16` |
