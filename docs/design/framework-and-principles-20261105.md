# 框架与理念（草案 v0.1 · 2026-11-05）

> **地位**：约束性草案。**本文档本身不开任何代码**；§2.5 的工作项全部
> 待 §3 决策点拍板后才排期。
>
> **决策进度**：D1 已拍板（2026-11-05）→ 见 §3.1 决策记录；
> D2 随 D1 采纳理念三默认处置；剩余 D3–D6 待拍。
>
> 输入：GEP-RSI v1.1（语义架构）· 四层物理架构（layered-skeleton-design）·
> 一致性扫描（`e7a6e868` + `d2e962df`）· goal 重建方案（`bd743d99`）。
>
> 本文档回答：这个「自我进化的研究 agent 平台」的理念是什么、框架长什么样、
> 已排期的 R1–R3 与扫描出的缺口如何装进同一个框架。

---

## §0 一句话

**物理轴（L1–L4）管「谁可以 import 谁」，语义轴（G/E/P）管「谁可以改变谁」；
两轴在 Tier 0/1/2 相交；三条反馈回路按分钟 / 小时 / 天分时标运行；
评估器与变异白名单的守护在进程外。**

---

## §1 理念（六条）

体例：每条理念 = 一个实测张力 + 一条规则 + 一个可执行检查。
**没有检查的理念不进本文**——这是本仓库自己的门禁文化（layer_matrix、
ratchet、path_literals、diagram sync 全是这个文化的产物）。

### 理念一 · 双轴正交：物理轴管依赖，语义轴管变异

**张力**（扫描 §8.0）：GEP-RSI 的全部缺口在 import 图上恒为 0——
物理门禁全绿，而 P 层没有成本维度、循环没有驱动。两套维度互不推导。

**规则**：
- 物理四层 L1→L2→L3→L4，只允许向下 import（AST 门禁，已有）
- 语义三层 G→E→P，只允许「P 的信号经验证写回 G」
  （Tier 白名单 + ValidationGate，已有）
- 两轴的桥接点是 **Tier 0/1/2**：每个可变基因同时有一个物理地址（住在哪层）
  和一个变异权限（能不能被提案）

| | 物理轴 | 语义轴 |
|---|---|---|
| 约束对象 | import 语句 | 变更行为 |
| 违规形态 | 上行 import | 越权变异 / 未验证生效 |
| 执行者 | 包外 AST 门禁 | 运行时 Tier 白名单 + Gate |
| 各自的盲区 | 语义缺口、资产路径耦合 | 物理错位 |

**检查**：物理轴 = `check_dangling_imports --layers` + `test_layer_matrix`（已有）；
语义轴 = 理念四/五的新检查（待建，W4）。

### 理念二 · 变更三关口：提案 → 验证 → 留痕

**张力**：自我修改系统最大的风险是「能直接改配置的 agent 总能把指标调好看」
（`genome_proposals.py` 头注原话）。

**规则**（三条全部已有，保持不动）：
1. **提案**：`GenomeProposal.new()` 是唯一变更入口，构造期拒绝 Tier 0
2. **验证**：`ValidationGate` 是唯一生效入口——隔离回放 + 基线对比 + ±20% 双侧
3. **留痕**：每次生效 / 回滚写事件 + 版本链 append-only（frozen dataclass + 游标）

**Tier 2 的特别规则**：研究产物不经验证闸，但**必须经版本链留痕**
（`set_tier2(key, value, by=...)`）。

**缺口**：`set_tier2` 全仓零调用（扫描 §8）——Tier 2 是设计了没接线的层。
R1 恢复 goal 后，goal 产物应成为第一个 Tier 2 写入方（决策点 D6）。

### 理念三 · 注册即可运行，或不可见

**张力**（扫描 §4，全场最深）：26 个业务工具注册成功、调用必然
`CapabilityNotFound`。端口化把失败从编译期推迟到运行期，且注册层
分不清「包装存在」与「能力可用」。

**规则**：

> kind 注册进 registry 的前提：其全部依赖 kind 在同一装配语境可解析。
> 不满足 → 整组不注册 + `capability.skipped` 事件；**绝不注册必然失败的壳**。

**机制（W1，机制本身待拍板）**：工厂声明 `requires: frozenset[str]`
（依赖 kind 清单）；装配期按依赖拓扑序解析，缺一个整组跳过。
替代方案（注册时 dry-run 一次）开销大且不可静态分析，不推荐。

**检查**：装配后 `registry.list_kinds()` 与「声明可运行的 kind 集合」
做一致性断言（测试门禁）；运行期出现缺口时 emit 事件，不允许静默。

### 理念四 · 评估器在循环外，终止层级在进程外

**张力**（GEP-RSI 校准 02 + 扫描 §8.4）：评估器若可被循环改写，
自进化退化为自欺骗；而「永远不可变」的绝对命题又不可实现（MetaRSI 的教训）。

**规则**（前三条已成立，第四条待建）：
1. 评估器（`evals/`、golden_tasks.jsonl、scorecard）**不在 Tier 1 白名单内**
   ——白名单是闭合集合，构造期即封死（已验证：未知组件 `TierViolation`）
2. 评估器修改的唯一路径 = 循环外治理：PR + 人审 + 包外门禁。
   运行时进程没有改 `genome_proposals.py`（白名单本体）的合法入口
3. **终止层级** = 包外 `scripts/arch/` 门禁 + 测试套件 + 人。进程重启带不走它
4. **评估完整性指纹（W4）**：golden_tasks.jsonl 与 scorecard.py 的 hash 由包外
   脚本 pin 住；评测前校验，不符 → escalate 而非放行

### 理念五 · 两条反馈回路不合并，goal 是桥

**张力**：goal 的「判据达成」与 eval 的「能力退化」是两类信号。
合并会把两种噪声混在一起，并让「把判据写松」成为指标攻击面。

**规则**：

| | 能力反馈 | 任务反馈 |
|---|---|---|
| 回答的问题 | 系统会不会 | 这件事做没做成 |
| 信号 | golden eval（冻结基准，70 条） | goal journal（判据 + 证据） |
| 喂给循环 | 第 5 步 · 双门评估 | 第 1 步 · 表型诊断 |
| 时标 | 天（cycle） | 时（每任务） |
| 归属 | L3 `evals/`（冻结） | L2 `apps/goal`（经端口） |

端口方向恒为 **L2 实现 → L3 消费**：`JournalSource` / `ScoreboardSource`
（`kernel/evolution/evolution_agent.py:42-55` 已声明）。
`rsi_wiring.py:211` 四源同指一个对象是待修 bug，不是设计。

### 理念六 · 没有驱动的循环不存在

**张力**（扫描 A4）：五个零件各有测试，`build_rsi_loop()` 生产零调用——
GEP-RSI 七步之外还缺一个**第 0 步：谁拉起一轮 cycle**。

**规则（驱动契约，W4）**：
- 主驱动：`run.settled` 事件后异步拉起 `run_once(...)`（会话收尾钩子，L1 组装根接线）
- 兜底驱动：cron（scheduled_research 域已有）
- 频率预算：每 session 至多 1 cycle（复用 `gateway.capacity` 闸）
- 可观测：`rsi.cycle_started` / `rsi.cycle_skipped(reason=...)`——
  「没有驱动」也必须以 skipped 事件现形，**不允许静默**

---

## §2 框架结构

### 2.1 双轴总图

```
语义轴（谁可以改变谁 · GEP-RSI）

    G 基因组 ──选择性转录+编译──▶ E 表达 ──执行轨迹+证据包──▶ P 表型
      ▲                                                        │
      └──────────── 选择信号（经验证 · 过屏障）◀────────────────┘

    变异面 ⊂ 表达面 ⊂ 表型面；评估器在变异面外；屏障在进程外

物理轴（谁可以 import 谁 · 四层）

    L1 interfaces ──▶ L2 apps ──▶ L3 kernel ──▶ L4 foundation
   （chat/api/mcp/   （goal·chat·    （loop·workflow·     （llm·storage·
     组装根）          工具插件）      evolution·events）    observability）
    依赖只向下；上行违规 = 0（棘轮锁定）
```

### 2.2 构件落位表（语义 × 物理）

| 语义构件 | 物理落点 | 层 | 现状 |
|---|---|---|---|
| Tier 0 冻结快照 | `interfaces/tier0_snapshot.py` | L1 | ✅ |
| 基因组版本链 | `kernel/orchestration/genome_version.py` | L3 | ✅（含 deactivate / set_tier2）|
| 提案语法 + Tier 白名单 | `kernel/evolution/genome_proposals.py` | L3 | ✅（缺证伪条件字段）|
| 提案生成 | `kernel/evolution/default_policy.py` | L3 | ✅ 确定性；LLM policy 后续替换 |
| 选择性转录 | `kernel/evolution/skill_memory.py` + `kernel/agent/harness_resolver.py` | L3 | ✅（缺结构化转录本）|
| 隔离回放 | `kernel/evolution/isolated_replay.py` | L3 | ✅ |
| **循环驱动** | `run.settled` 钩子（待建）+ scheduled_research | L1/L3 | ❌ W4 |
| 冻结基准 + scorecard | `evals/` | L3 | ✅（缺指纹、缺成本维度）|
| 执行轨迹 | `kernel/events` + `foundation/storage` | L3/L4 | ✅ append-only |
| 任务级判据证据 | `apps/goal` journal（R1 恢复） | L2 | ⚠ 待恢复，经端口接入 |
| 屏障 · Tier 0 三重冻结 | proposals / gate / skill_memory | L3 | ✅ |
| 屏障 · 物理门禁 | `scripts/arch/`（包外） | 进程外 | ✅ |
| 屏障 · 评估指纹 | 包外脚本 | 进程外 | ❌ W4 |

### 2.3 三条反馈回路（时标分层）

```
回路 1 · 会话内（分钟）  goal continuation
    判据未满足 → 注入续跑提示（GoalContinuationInjector，已有，活着）
回路 2 · 任务级（小时）  goal journal
    判据达成/未达成 + 证据 → P 层表型诊断的原料（缺 goal = R1，缺端口 = R3）
回路 3 · 能力级（天）    RSI cycle
    holdout 诊断 → 提案 → 隔离回放 → 双门 → activate / rollback
    （零件齐全；缺驱动 W4、缺成本维度 W2、缺双集 W3）
```

回路 1 活着；回路 2 断在 goal 与端口；回路 3 断在驱动与 P 层。

### 2.4 落地后的目录形态（R1–R3 之后）

```
src/agentnodes/
├── interfaces/          L1  渠道 + 组装根（R2：/goal slash、GoalPanel、SSE 恢复接线）
├── apps/                L2  业务插件
│   ├── goal/            R1 恢复（19 文件：判据 / 台账 / DAG 工作流）
│   ├── chat/            产品策略（222 行，维持）
│   └── builtin_tools/   业务工具（W1 自检后只剩可运行的组）
├── kernel/              L3  平台机制
│   ├── evolution/       G/E/P 循环机制（R3：journal/scoreboard 端口接入）
│   └── agent/ workflow/ gateway/ orchestration/ events/ ...（维持）
├── foundation/          L4  零业务原语
├── evals/               冻结基准（W4 指纹）
└── templates/           资产（归属另议——扫描议题 6）
```

### 2.5 工作项总表（**拍板前不排期**）

| 项 | 内容 | 来源 | 前置 |
|---|---|---|---|
| W1 | 装配期能力自检（`requires` 声明 + 拓扑解析） | 理念三 | ✅ D1 已拍板 |
| R1 | 恢复 apps/goal + goal.* kind + 测试搬回（按 §3.1 依赖链自证注册） | bd743d99 + D1 | **W1** |
| R2 | chat 接线（slash / GoalPanel / SSE / API 路由） | bd743d99 | R1 |
| R3 | journal/scoreboard 端口接入 + autoresearch preset 化 | bd743d99 | R1 |
| W2 | P 层成本/延迟维度（TaskScore 加列） | 理念五 · 校准 01 | D3 |
| W3 | held-in / held-out 双集 | GEP-RSI 第 5 步 | W2 |
| W4 | 评估完整性指纹 + 循环驱动链 | 理念四 / 六 | D4 |
| W5 | 假基因清理（周期性 deactivate 从未激活组件） | GEP-RSI 第 7 步 | R1 |
| W6 | 事件 hash chain | 屏障 | D5（可选）|

---

## §3 决策点（拍板后本文升 v1.0，才开代码）

| # | 问题 | 选项 | 推荐 |
|---|---|---|---|
| D1 ✅ | L2 的定位 | (a) 恢复 goal 重新充实 / (b) 取消 L2 / (c) 插件槽 + 自检 | **已拍板：(c) + R1**（见 §3.1）|
| D2 ✅ | 缺依赖工具的处置 | 不注册 / 注册 + degraded 标注 | **随 D1 采纳理念三默认：不注册 + WARN**（可推翻）|
| D3 | 成本/延迟进不进总分 | 进总分加权 / 只做 0/1 门槛 | **进总分**（校准 01：与完成率并列硬维度；权重另定）|
| D4 | 循环驱动方式 | run.settled 自动 / 仅 cron / 手动 CLI | **run.settled 自动 + cron 兜底 + 频率闸** |
| D5 | hash chain 现在做吗 | 现在做 / P2 | **P2**：先上评估指纹（W4），hash chain 收益/成本比低 |
| D6 | Tier 2 现在接线吗 | R1 时接 / 以后 | **R1 时接**：goal 产物写 `set_tier2`，留痕闭环 |

### 3.1 决策记录

**D1 · 已拍板（2026-11-05）：L2 = 业务插件槽 + R1 恢复 goal，goal 必须过理念三自检。**

锁定四件事：

1. **L2 的存在理由改写**：不再是「研究 IP 层」（layered-skeleton-design 的旧口径），
   而是**业务插件槽**——每个 kind 是一个自带依赖声明的插件，注册即自证可运行。
   `apps/capabilities.py` 的角色从「kind 表」升级为「插件清单」。
2. **批次顺序改变**：W1（自检机制）提前为 R1 的前置——没有自检机制，
   「goal 过自检」无从谈起。新顺序：**W1 → R1 → R2 → R3**。
3. **R1 验收标准具体化**（插件槽口径下的 goal 依赖链）：

   | kind | requires | 说明 |
   |---|---|---|
   | `goal.store` | ∅ | 台账本体（依赖是 import 不是 kind）|
   | `goal.models` | ∅ | 判据 / 证据 schema |
   | `goal.expression` | ∅ | 纯表达式求值 |
   | `goal.context` | `goal.store` | 上下文渲染读台账 |
   | `tool.goal`（5 工具）| `goal.store, goal.models, goal.context` | 工具组 |

   装配期按拓扑序解析：`store / models / expression → context → tool.goal`；
   缺一个 → 整组不注册 + `capability.skipped` 事件。
4. **现存 5 个坏工具组的命运进入 D2 议程**：W1 落地时，
   `tool.factor / tool.data / tool.backtest / tool.backtest_analysis / tool.data_clean`
   因依赖的 `db.* / datasource.* / config.runner / data.*` 已删而**全部过不了自检**。
   其模块文件（约 3,300 行）是删是归档，在 W1 验证通过后单独议。

**D2 · 随 D1 采纳理念三默认处置**：缺依赖 = 整组不注册 + WARN 事件。
（按 D1 的「必须过理念三自检」视为已采纳；若要 degraded 标注，明确推翻即可。）

**剩余待拍**：D3（成本进总分）· D4（驱动方式）· D5（hash chain）· D6（Tier 2 接线）。

---

## §4 本文档不决定的事

- `templates/` 资产层的归属（扫描议题 6）——等 D1 定了再议
- 执行面三套并存（AgentLoop 22.5k / GraphEngine+workflow 4.8k / SwarmRuntime 1.5k）
  的收敛——独立议题，不属理念层
- `kernel/agent` 是否拆分——结构/性能话题，不属理念层
