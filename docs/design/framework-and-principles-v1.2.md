# 框架与理念（v1.2 · 2026-11-16）

> **性质**：框架文档（理念 / 结构 / 能力 / 决策）· **上位源**：本文件（HTML 版是它的呈现）
> **基线**：`main` @ 合并 `ui-workbench`（534 commit）后的状态
> **与 v1.1 的关系**：v1.1（`framework-and-principles-20261105.md`）**保留不动**。
> 本版是**重做叙述 + 回填实测**，不是增量修补 —— 原因见 §6。
> **口径**：本文件所有数字均为 2026-11-16 实跑所得（`git ls-tree` / AST / 门禁实跑 /
> pytest），非文档转述。层编号统一采用**门禁口径**（见 §2.0）。
> **配套**：架构图 `docs/architecture-layered.html`（同源能力集 + 层级 + 依赖边 +
> 已知架构债）；会话生命周期下沉的迁移方案见
> `docs/design/session-lifecycle-kernel-migration-20261116.md`（R-014，**未实施**）。

---

## §0 一句话

**物理轴（L1–L4）管「谁可以 import 谁」，语义轴（G/E/P）管「谁可以改变谁」**；
两轴在 **Tier 0/1/2** 相交；**三条反馈回路**按分钟 / 小时 / 天分时标运行；
**评估器与变异白名单的守护在进程外**。

v1.2 追加一句：**执行面已收敛为单引擎（LoopEngine）**，结构门禁从「手动跑」变为
「pytest + CI 双闸」，`kernel/` 的业务名词归零已由自动化守住。

---

## §2.0 层编号口径（v1.2 新增 · 必读）

本仓存在**两套互相矛盾**的层编号，读任何文档前必须先确认口径：

| 来源 | 编号 |
|---|---|
| `scripts/arch/layer_matrix.py:5`（自称「唯一事实源」）| **L1 interfaces / L2 apps / L3 kernel / L4 foundation** |
| `.rules/rules.md` §1.1 | 「foundation（L1）← kernel（L2）← apps（L3）← interfaces（L4）」|

**实测判定 = 门禁口径正确**。判据是 `check_dangling_imports.py --layers` 的输出矩阵：

```
      L1    L2    L3    L4
  L1   118    59   121    83
  L2     0    42    33    11
  L3     0     0   195    71
  L4     0     0     0    63
```

- **L4 行只有对角 63** → L4 不 import 任何人 → L4 = 最底层 = `foundation`
- **L3 行 import L4 共 71 处** → kernel→foundation 合法 → L3 = `kernel`
- **L2 行 import L3/L4 共 33/11** → apps→kernel/foundation 合法 → L2 = `apps`
- **L1 行 import 全栈 59/121/83** → interfaces 消费一切 → L1 = `interfaces`

⇒ **本文件全部采用门禁口径**。凡出现「L3→L2」= kernel 不 import apps = 0。

> `.rules/rules.md` §1.1 的编号已记为待修项（进度评估 §5 遗留 #12）。

### 2.0.1 旁挂评估带：`evals/` 不属于四层

四层之外还有一处**不属于任何层**的构件：`src/agentnodes/evals/`。这不是疏漏，是**结构事实**。

**为什么不能按层归属**（2026-11-16 评估，三条判据两条不成立）：

| 判据 | 结果 | 实测 |
|---|---|---|
| 概念 | ❌ | `foundation` 的定位是「零业务依赖的基础设施」；evals 是**自进化的 P 层信号源**，不是基础设施 |
| 结构 | ❌ | evals 有 **5 条 L3 依赖**（`run_evals_llm.py:54-57` ×4 指向 `kernel/agent/*`、`golden_sets.py:449` 指向 `kernel/evolution/golden_eval`）——下沉 L4 会全部变成上行违规 |
| 职责 | ❌ | `run_evals_llm.py:71-93` **在进程内构造 `AgentLoop` 并真实调用 `loop.run()`** —— 它的职责就是驱动被测对象 |

**它现在的形态**：跨层读取（L3 ×5 + L4 ×3），零入边（没有任何生产代码 import evals），只被 `agentnodes` 顶层 re-export 触及。

**记账处理**：`layer_matrix.py` 把它登记为 L3 是**「不报违规」的记账位，不是语义判定**（见该文件 L66-77 的注释）。语义上它是**旁挂评估带**：

```
        L1 interfaces
             ↓
        L2 apps
             ↓
        L3 kernel   ←─────┐
             ↓            │  evals（旁挂带）
        L4 foundation ←───┘   跨层读取被测对象，零入边
```

**为什么这恰恰是对的**：理念四要求「评估器在循环外」。给它一个层级归属，等于把观察者塞进被观察的结构里 —— 要么违反 DAG，要么假装同层。**「不属于任何层」是它的正确位置**，架构图里以独立泳道呈现（`docs/architecture-layered.html`）。

**已评估但未采纳的替代路线**（记录以备未来重估）：

| 路线 | 内容 | 否决理由 |
|---|---|---|
| B 协议下沉 | 把 `GoldenTask`/`RunRecord` 协议搬到 L4 | 不解决层级问题（evals 仍要 import kernel 才能跑）|
| C evals 拆分 | `scorecard.py`（280 行、零 kernel 依赖）下沉 L4，`run_evals*.py` 留原处 | 收益仅「scorecard 在 L4」，代价是 `evals/` 作为包消失、报告与 CI 要多看一处 |

---

## §0.5 概念对照：Loop Engineering × Graph Engineering（v1.2 新增）

> **概念出处**：《Loop Engineering 概念解析、思考与实践》（本仓库作者著）。
> 本节在 v1.1 的 md 底稿里已存在（`442b6ab5` / `1a9050f2`），但**从未进入 HTML 版**
> —— 这是 v1.1 HTML 落后其 md 底稿 3 个 commit 的主要缺口。v1.2 首次把它纳入呈现层。

**定义**：

- **Loop Engineering** —— 在 Agent 执行过程中加入**过程循环**，以目标驱动的方式保障执行效果，让 Agent 越练越好。
- **Graph Engineering** —— 约束 Loop 的结构性工程，防止 Loop 过拟合目标、卷分数、越练越死板。**边界（2026-11-06 拍板）：包含执行图与监管图两部分** —— 执行图给 Loop 提供结构化、可中断、可恢复的轨道；监管图（Tier 分层、双门、指纹、原则七）裁决 Loop 是否真的变好。

**动静之分，不是文件之分**：Loop 侧是**行为**（跑在图上的目标驱动循环），Graph 侧是**结构与监管**。同一段代码在两个概念里各有一个投影。

### 对映表

| 概念 | 本仓库实例 | v1.2 状态 |
|---|---|---|
| Loop · 执行内循环 | `AgentLoop` 迭代 + 回路 1 goal continuation（分钟）| ✅ 活着 |
| Loop · 任务级改进循环 | 回路 2 goal journal（小时）| ⚠️ 台账有、端口未接 |
| Loop · 自我改进循环 | 回路 3 RSI cycle（天）| ⚠️ 零件齐、驱动半通 |
| Graph · 执行图 | GraphEngine + SwarmRuntime + WorkflowGraphCompiler | ✅ 在 |
| Graph · 监管图 | Tier 0/1 白名单 · ValidationGate · held-in/out 双集 · 评估指纹 · 原则七 · 终止层级 | ✅ **8 道门禁 + CI job + 28 条 pytest 断言** |

**三句关系**：图**承载**循环；图**约束**循环；循环的合法成果**写回**图。
双时间尺度：Loop 小尺度内变好，Graph 大尺度上约束 + 吸收。

### 三条合法信道（Loop ↔ Graph 接口最小化）

> **除以下三条信道外，Loop 与 Graph 之间不存在任何其他读写。**
> 任何新增的读写，要么封掉，要么改设计成三条之一。

| 信道 | 方向 | 承载者 | 管它的条款 | v1.2 状态 |
|---|---|---|---|---|
| ① 执行 | Loop 在图上跑 | GraphEngine / AgentLoop | Loop 领地——结构自由，行为受预算与闸 | ✅ |
| ② 闸控写 | Loop 产出经屏障写回图 | GenomeProposal → ValidationGate → activate | 判据强度门 / tier2 value 约束 / F1 装配契约 | ❌ **`set_tier2` 生产 0 调用（信道从未接线）** |
| ③ 裁决 | 图对 Loop 出具裁决 | golden eval / goal 判据 | 信号必须走被检方不可控通道（= 原则七）| ✅ held-out 包外持有 |

**8 行病理表据此归位**：5 行未授权写 · 1 行未授权读 · **1 行信道缺失（②从未接线：`set_tier2` 零调用 —— v1.2 实测仍未修）** · 1 行 Graph 内部缺陷。

**可测试的推论**：新关口组件接入时只问一句 —— **它与 Loop 之间开了几条信道？不在三条之内的，封掉或改设计。**

### 0.5.1 病理 ↔ 闸门对照表（v1.2 状态回填）

| 病理 | 实测病灶 | 闸门 | v1.2 状态 |
|---|---|---|---|
| 卷分数通道 | seed 固定抽样可预计算；±20% 单集双侧 | held-out 双集 + 进程外持有 → W3 | ✅ **已完成** |
| 监考被收买 | `golden_eval` 0.0 进均值 + 无条件缓存 | 基线硬条款 → W2 | ✅ **已完成** |
| 目标本身退化 | `evaluate("true")==True` | 判据强度门 → R1 | ✅ R1 完成；**恒真形态仍是已知残差** |
| 考勤自报 | report payload 无签名 | kernel 侧签名 + 增量门验真 → W4 | ⚠️ **部分**（cron 兜底缺）|
| 监管面被触达 | `DEGRADATION_THRESHOLD` 进程内可改 | pin 全集 + 指纹 → W4 | ✅ **指纹已完成** |
| 退化不可逆（回滚错靶）| F2：三连败回退掉上一个好版本 | anchor 式回滚 → W4 | ✅ **已完成** |
| 成果不可追溯 | `set_tier2` 零调用；无 hash chain | D6 三细则 + W6 | ❌ **仍未接（D6）** · P2 |
| 轨道僵化（假基因）| `deactivate` 零调用 | W5 周期清理 | ❌ **未做** |

> **元规则**：**Graph 的边只在 Loop 踩过的地方画** —— 每行都有 file:line 出处；不为想象中的攻击加闸。
> **退役机制可参数化**：每条闸门维护触发计数，「零触发多久进退役评审」是可调参数（缺省 12 个月）。

---

## §1 理念（七条 · 检查栏已回填 v1.2 实测）

### 理念一 · 双轴正交：物理轴管依赖，语义轴管变异

| 栏 | 内容 |
|---|---|
| 张力 | GEP-RSI 的全部缺口在 import 图上**恒为 0** —— 物理门禁全绿 ≠ 架构对 |
| 规则 | 两轴不互相推导也不合并；桥接点是 Tier 0/1/2 |
| **检查（v1.2 实测）** | 物理轴：**8 道结构门禁齐备**（`scripts/arch/` 14 文件），已接 pytest（`test_scripts_arch.py` 26 用例）+ CI（`architecture-gates` job）。语义轴：Tier 0 三重冻结在 `proposals`/`validation_gate`/`skill_memory` 三处 ✅ |

### 理念二 · 变更三关口：提案 → 验证 → 留痕

| 栏 | 内容 |
|---|---|
| 张力 | 能直接改配置的 agent 总能把指标调好看 |
| 规则 | GenomeProposal 唯一入口（构造期拒 Tier 0）→ ValidationGate 唯一生效入口 → 事件 + 版本链 append-only |
| **检查（v1.2 实测）** | 三道已成立 ✅。**残差**：`set_tier2` 生产调用 **0 处**（Tier 2 设计了没接线，D6 拍了板但未落地）|

### 理念三 · 注册即可运行，或不可见

| 栏 | 内容 |
|---|---|
| 张力 | 业务工具注册成功、调用必败（CapabilityNotFound）—— 端口化把失败从编译期推到运行期 |
| 规则 | kind 注册的前提 = 全部依赖 kind 可解析；不满足 → 整组不注册 + WARN，绝不注册必然失败的壳 |
| **检查（v1.2 实测）** | ✅ **已落地**：`requires` 声明 95 处；`apps/capabilities.py` 注册带 requires；`check_capability_requires.py` consistent |

### 理念四 · 评估器在循环外，终止层级在进程外

| 栏 | 内容 |
|---|---|
| 张力 | 评估器可被循环改写 = 自进化退化为自欺骗；「永远不可变」又不可实现 |
| 规则 | 评估器不在 Tier 1 白名单（闭合集合）；修改唯一路径 = PR + 人审 + 包外门禁；终止层级 = `scripts/arch` + 测试 + 人 |
| **检查（v1.2 实测）** | 评估完整性指纹 **已落地**（`evals/golden_sets.py` 相关 114 处）；终止层级 = 8 道门禁 + 26 条 pytest 断言 + CI job ✅ |

### 理念五 · 两条反馈回路不合并，goal 是桥

| 栏 | 内容 |
|---|---|
| 张力 | 合并会把两种噪声混在一起，并让「把判据写松」成为指标攻击面 |
| 规则 | 能力反馈（golden eval，冻结基准）喂第 5 步双门；任务反馈（goal journal，判据证据）喂第 1 步诊断；端口方向恒为 L2 实现 → L3 消费 |
| **检查（v1.2 实测）** | ⚠️ **端口未接**：`JournalSource`/`ScoreboardSource` 协议已声明，但 `apps/goal/journal_source.py` / `scoreboard_source.py` **不存在**；**`interfaces/rsi_wiring.py:219` 四源仍同指** `EventStoreReportSource`。R3 未做 |

### 理念六 · 没有驱动的循环不存在

| 栏 | 内容 |
|---|---|
| 张力 | 五个零件各有测试，`build_rsi_loop()` 生产零调用 —— 七步之外缺第 0 步：谁拉起一轮 |
| 规则 | 主驱动 run.settled 收尾钩子 + cron 兜底 + 每 session 至多 1 cycle（复用容量闸）；阶段 2 前置 = 判据强度门 + 事件写入方鉴别 |
| **检查（v1.2 实测）** | ⚠️ **部分**：`build_rsi_loop` 12 处引用、`rsi.cycle_started`/`cycle_skipped` 12 处已有；**cron 兜底 0 处**（阶段 1 只落了一半）|

### 理念七 · 检查信号必须经由被检方不可控的通道产生（v1.1 新增）

| 栏 | 内容 |
|---|---|
| 张力 | 全部「检查」的通过信号都由被检查方自己产出 —— 自报 requires / 自报事件 / 自写 evidence / 同进程可投毒的 golden eval。不是四个 bug，是缺一条原则 |
| 规则 | 检查信号通道三选一：**kernel 侧计算** / **进程外持有**（held-out 由包外脚本持有）/ **不可伪造源**（append-only + 未来 hash chain）|
| **检查（v1.2 实测）** | held-out 包外持有 ✅（469 处相关）；append-only 10 处 ✅；**hash chain 0 处**（D5 拍板延到 P2，未做）。**残差诚实声明**：`evaluate("true")==True` 的恒真判据形态仍是已知攻击面（`interfaces/goal_settled_consumer.py:18,34` 有注释记录）|

---

## §2 框架结构

### 2.1 双轴总图

**语义轴（G → E → P）**：变异面 ⊂ 表达面 ⊂ 表型面

| 层 | 角色 | 构件（现状）|
|---|---|---|
| **G 基因组** | 变异发生地 | `GenomeProposal` · 版本链 + 游标（`kernel/orchestration/genome_version.py`）· `default_policy` |
| **E 表达** | 选择执行地 | `skill_memory` · `harness_resolver` · `isolated_replay` |
| **P 表型** | 选择裁决地 | `evals/scorecard` · `goal journal` · `event_log` |

**桥接 Tier 0/1/2**：每个可变基因同时有一个**物理地址**（住在哪层）和一个**变异权限**（能不能被提案）。

**物理轴（门禁口径）**：

| 层 | 目录 | 文件 | 行数 | 职责 |
|---|---|---:|---:|---|
| L1 | `interfaces/` | 118 | 22,070 | 渠道层 + 组装根（哑适配器 + 唯一知道 apps 的位置）|
| L2 | `apps/` | 42 | 9,660 | 业务插件层（goal / chat / agent_plugins / swarm_presets / scheduled_research / builtin_tools / **algo**）|
| L3 | `kernel/` | 208 | 48,136 | 平台机制（agent / workflow / gateway / orchestration / events / session / evolution / swarm / …）|
| L4 | `foundation/` | 63 | 12,091 | 零业务原语 |
| — | **合计** | **431** | **91,957** | 上行违规 **0** |

### 2.2 构件落位表（语义 × 物理 · 状态已回填）

| 语义层 | 构件 | 物理落点 | 层 | v1.1 状态 | **v1.2 状态** |
|---|---|---|---|---|---|
| 屏障 | Tier 0 冻结快照 | `interfaces/tier0_snapshot.py` | L1 | ✅ 已有 | ✅ 已有 |
| G | 基因组版本链 | `kernel/orchestration/genome_version.py` | L3 | ✅ 已有 | ✅ 已有（`deactivate` 仍零生产调用）|
| G | 提案语法 + Tier 白名单 | `kernel/evolution/genome_proposals.py` | L3 | ✅ 已有 | ✅ 已有 |
| G | 提案生成（确定性 policy）| `kernel/evolution/default_policy.py` | L3 | ✅ 已有 | ✅ 已有 |
| E | 选择性转录 | `kernel/evolution/skill_memory.py` + `agent/harness_resolver.py` | L3 | ✅ 已有 | ✅ 已有 |
| E | 隔离回放 + 回滚锚点 | `kernel/evolution/isolated_replay.py` + `rsi_wiring` | L1/L3 | ✅ 已有 | ✅ 已有 |
| E | **循环驱动（第 0 步）** | `run.settled` 钩子 + `interfaces/rsi_cli.py` | L1/L3 | ❌ W4 | ⚠️ **部分**（CLI + 事件有，cron 兜底无）|
| P | 冻结基准 + scorecard | `evals/`（70 条 golden）| **旁挂带** | ✅ 已有（缺指纹）| ✅ 已有 **+ 指纹已补** |
| P | 执行轨迹（append-only）| `kernel/events` + `foundation/storage` | L3/L4 | ✅ 已有 | ✅ 已有（hash chain 仍 P2）|
| P | 任务级判据证据 | `apps/goal/` journal（经端口）| L2 | ⚠ 待恢复 | ✅ **R1 已恢复** / ❌ **R3 端口未接** |
| 屏障 | Tier 0 三重冻结 | `proposals` / `validation_gate` / `skill_memory` | L3 | ✅ 已有 | ✅ 已有 |
| 屏障 | 物理门禁 + 评估指纹 | `scripts/arch/`（进程外）| — | ✅ 门禁 · ❌ 指纹 | ✅ **门禁 8 步 + CI job · 指纹已补** |

### 2.3 三条反馈回路（时标分层 · 存活状态已回填）

| 回路 | 时标 | 名称 | v1.2 状态 |
|---|---|---|---|
| 1 | 分钟 · 会话内 | Goal Continuation | ✅ **活着** —— `GoalContinuationInjector` 在默认注入链，chat / API 全路径生效 |
| 2 | 小时 · 任务级 | Goal Journal → 表型诊断 | ⚠️ **半通** —— goal 台账已恢复（R1 ✅），但端口未接（R3 ❌，四源同指）|
| 3 | 天 · 能力级 | RSI Cycle | ⚠️ **零件齐、驱动半通** —— 缺 cron 兜底、缺假基因清理（W5）|

### 2.4 目录形态（v1.2 现状）

```
src/agentnodes/                                      418 文件 / 91,340 行
├── interfaces/          L1  渠道 + 组装根（rsi_wiring / gateway_wiring / tier0_snapshot）
├── apps/                L2  业务插件
│   ├── goal/            18 文件 / 5,975 行（R1 已恢复：判据 / 台账 / DAG 工作流）
│   ├── chat/            产品策略
│   ├── agent_plugins/   量化插件（quant.py / quant_validators.py / quant_display.py）
│   ├── swarm_presets/   37 个拓扑 preset
│   ├── scheduled_research/  定时研究（批次 8 自 kernel 迁入）
│   ├── builtin_tools/   业务工具
│   └── algo/            无前视调仓回测（2026-11-16 自 kernel/algo 迁入）
├── kernel/              L3  平台机制
│   ├── agent/           LoopEngine / LoopSpec / loop_assembly / guardrails / deciders
│   ├── workflow/        contract.py（B1 节点产物契约）+ 编译器/执行器
│   ├── evolution/       G/E/P 循环机制（holdout / validation_gate / evolution_agent）
│   └── gateway/ orchestration/ events/ session/ swarm/ permission/ memory/ skills/
├── foundation/          L4  零业务原语
├── evals/               冻结基准（含完整性指纹）
├── templates/           资产（.prompts 17 / .skills 26 —— 归属另议）
└── scripts/             （1 个历史遗留）
```

### 2.5 工作项总表（完成度回填）

| 项 | 内容 | **v1.2 实测状态** | 判据 |
|---|---|---|---|
| **W1** | 装配期能力自检 | ✅ **完成** | `requires` 95 处 + `check_capability_requires` consistent |
| **W2** | P 层成本/延迟并列维度 + 双闸 | ✅ **完成** | cost 双闸 43 处 · latency 采集 5 处 |
| **W3** | held-in / held-out 双集双门 | ✅ **完成** | `kernel/evolution/holdout.py` 存在 · holdout 313 处 |
| **W4** | 完整性指纹 + 循环驱动链 | ⚠️ **部分** | 指纹 114 处 ✅ · `build_rsi_loop` 12 处 ✅ · **cron 兜底 0 处** ❌ |
| **W5** | 假基因清理 | ❌ **未做** | `deactivate(` 生产调用 **0 处** |
| **W6** | 事件 hash chain | ❌ **未做**（P2，符合 D5 拍板）| hash chain **0 处** |
| **R1** | 恢复 `apps/goal` | ✅ **完成** | 18 文件 / 5,975 行 · goal.* kind 67 处 · `goal.settled` 14 处 |
| **R2** | chat 接线 | ✅ **完成** | slash_goal 32 处 · GoalPanel 74 处 · goal SSE 48 处 · goal API 路由 |
| **R3** | journal/scoreboard 端口 + preset 化 | ❌ **未做** | `journal_source`/`scoreboard_source` **0 处** · `rsi_wiring.py:219` 四源仍同指 |

---

## §3 能力集与架构的接口（本次讨论新增的定位说明）

> **§3 是编号占位与定位说明**：能力集（§4）的每一项都要落到具体包上，而包的归属
> 由 §2 的四层骨架决定。两者的接口是：
>
> | 方向 | 约束 |
> |---|---|
> | 能力 → 包 | 每项能力列出实现者集合（§4.2–§4.4），实现者必须是 §2 物理轴上真实存在的包 |
> | 包 → 层 | 包的层归属由 `scripts/arch/layer_matrix.py` 判定，门禁 `check_dangling_imports.py --layers` 强制（上行违规 0） |
> | 能力 → 语义轴 | C1–C28 大多落在 E（表达）/ P（表型）面；C11（工具权限门）与 C12（停止条件）属**屏障**语义（不可被 agent 自行修改）|
>
> **一条重要的观察**：能力的粒度比包细，比函数粗。C8「历史持久化」横跨
> `foundation/storage`（存储）与 `kernel/gateway`（会话标识）；C9「工具定义与调用」
> 横跨 `kernel/agent`（协议）与 `apps/builtin_tools`（工具实现）。
> **能力的粒度不与包的粒度对齐 —— 这是能力集能独立于架构分层存在的原因。**

## §4 MVP 三层能力集（2026-11-16 新增）

> **本节回答一个此前没被明确的问题**：这个项目**最小能交付什么**。
> v1.1 只定义了「理念 / 结构 / 决策」，没有定义「产品边界」；架构图上的红黄绿三层
> 最早也是按「代码路径经过的包」划的（见 §4.5 末尾的方法学说明）。
>
> **口径**：能力集是**能力**（用户可见 / 系统可验证的功能），不是包。
> 每个能力都要能指出**由哪些包实现** —— 实现者集合才是包级分层的依据。

### 4.1 三层定义

| 层 | 名称 | 能力 | 判据（删掉它会怎样）|
|---|---|---|---|
| 🔴 **A 红** | **最小可对话** | C1–C12（12 项）| 接一句话 → agent 调工具 → 流式回复，历史可存。删掉任一项，用户要么连不上、收不到、存不下，要么 agent 只能空谈 |
| 🟡 **B 黄** | **最小可干活** | C13–C15（3 项）| 加上多节点工作流、产物契约、上下文注入。删掉只能单轮单节点干活 |
| 🟢 **C 绿** | **全能力** | C16–C28（13 项）| 加上长程任务、自进化、评估、量化插件、定时研究、预算闸、事件溯源、记忆、技能、三个交付通道 |

**包含关系**：C ⊃ B ⊃ A（绿层含黄层含红层的能力）。
**包级对应**：红 9 包 / 黄 3 包 / 绿 24 包（合计 36 = 全仓包数）。

### 4.2 红的 12 项能力（C1–C12）

| # | 能力 | 最小形态 | 实现者 |
|---|---|---|---|
| **C1** | 交互接入 | HTTP 端点收一条消息（+ 可选 session_id）| `interfaces/api` |
| **C2** | 人格设定 | system prompt —— agent 的角色 | `kernel/agent` |
| **C3** | 请求组装 | system + 历史 + 本轮消息 | `kernel/agent` |
| **C4** | 模型出站 | 兼容协议 + 认证 + 超时 | `foundation/llm` |
| **C5** | 结果解析 | 取文本 / 取增量 | `foundation/llm` · `interfaces/api` |
| **C6** | 流式回传 | SSE 逐 delta | `interfaces/api` · `kernel/events` |
| **C7** | 会话标识 | 多轮的载体 | `kernel/gateway` |
| **C8** | **历史持久化** | 会话落盘 + 取回（重启不丢）| `kernel/session` · `foundation/storage`（SQLite 后端） |
| **C9** | **工具定义与调用** | function calling 协议 | `kernel/agent` · `apps/builtin_tools` |
| **C10** | **工具执行与回灌** | tool loop：请求→执行→回灌→再请求 | `kernel/agent` |
| **C11** | **工具权限门** | 哪些工具 / 哪些路径 / 要不要人确认 | `kernel/permission` |
| **C12** | **停止条件** | 最大轮数 / token 预算 / 时间预算 | `kernel/agent` |

> **加粗的四项是 2026-11-16 讨论新增的**：工具闭环（C9/C10/C11/C12）与历史持久化（C8）。
> 此前默认「最小可对话」不含工具，实际它才是 agent 与聊天框的分界。

### 4.3 黄的 3 项能力（C13–C15）

| # | 能力 | 最小形态 | 实现者 |
|---|---|---|---|
| C13 | 多节点工作流 | DAG 编排 | `kernel/workflow` |
| C14 | 节点产物契约 | 结构化校验 | `kernel/workflow` |
| C15 | 任务级上下文注入 | 判据续跑 / workspace | `kernel/context` |

### 4.4 绿的 13 项能力（C16–C28）

| # | 能力 | 实现者 | # | 能力 | 实现者 |
|---|---|---|---|---|---|
| C16 | 长程任务（判据/台账/journal）| `apps/goal` | C23 | 事件溯源与轨迹投影 | `kernel/events` |
| C17 | 自进化（提案→验证→激活）| `kernel/evolution` | C24 | 记忆 | `kernel/memory` |
| C18 | 评估基准与打分 | `evals` | C25 | 技能 | `kernel/skills` |
| C19 | Swarm 拓扑执行 | `kernel/swarm` · `apps/swarm_presets` | C26 | CLI 通道 | `interfaces/cli` |
| C20 | 量化插件 | `apps/agent_plugins` | C27 | TUI 通道 | `interfaces/cli/tui` |
| C21 | 定时研究 | `apps/scheduled_research` | C28 | MCP 通道 | `interfaces/mcp` |
| C22 | 编排与预算闸 | `kernel/orchestration` | | | |

### 4.5 归属判据：为什么不用 import 闭包

初版按**依赖闭包**机械划分，实测结果不可用：

```
红色层闭包 = 32/36 个包 / 1,945 符号   ← 几乎全仓
黄色层闭包 =  0 包
```

原因：`interfaces/api` 与 `kernel/agent` **传递性 import 了几乎整个仓库** —— 从它们出发做传递闭包，得到的不是「最小集」而是「全仓」。

⇒ 改用**运行时必要性**：**删掉这个包，对应能力还成不成立**。这是判断，不是推导 —— 所以每项能力的「实现者」都列在表上，可复审。

### 4.6 与架构图的关系

架构图 `docs/architecture-layered.html` 用同一套能力集：

- 图例按层显示「N 项能力 · M 包」，**点色块可虚化该层**（虚影复用悬停聚焦的 dim 样式，不另造一套）
- 每个包的 tooltip 显示它**贡献哪几项能力**（如 `kernel/agent` → C2 C3 C9 C10 C12）
- 图上另附**「最小可对话能力集」清单块**（28 行）与**「已知架构债」块**（D1–D3）


## §5 决策点（六项已拍板 · 附「后续落点」）

| # | 决策 | 拍板 | **v1.2 落点** |
|---|---|---|---|
| D1 | L2 的定位 | (c) 插件槽 + R1 | ✅ R1 完成；L2 = 42 文件 / 9,660 行（goal/chat/agent_plugins/swarm_presets/scheduled_research/builtin_tools/algo）|
| D2 | 缺依赖工具 | 不注册 + WARN | ✅ `check_capability_requires` 门禁化 |
| D3 | 成本/延迟进表型 | B 并列维度 + 双闸 | ✅ W2 完成 |
| D4 | RSI 循环驱动 | E 分层混合（两阶段）| ⚠️ 阶段 1 只落了一半（缺 cron 兜底）|
| D5 | hash chain 现在做吗 | P2 | ❌ 未做（符合拍板）|
| D6 | Tier 2 接线 | R1 时接 | ❌ **未接**（`set_tier2` 生产 0 调用）|

---

## §6 v1.1 → v1.2 变化总账（本版核心）

> 这是 v1.2 相对 v1.1 的**全部实质变化**，逐行可核。架构图 `docs/architecture-layered.html` 的「v1.1 → v1.2 架构变化总账」与本表同源。

### 6.1 分层体量

| 层 | v1.1（2026-11-06）| v1.2（2026-11-16）| Δ |
|---|---:|---:|---:|
| L1 `interfaces/` | 109 | **118** | +9 |
| L2 `apps/` | 15 | **42** | **+27** |
| L3 `kernel/` | 221 | **195** | **−26** |
| L4 `foundation/` | 66 | **63** | −3 |
| `src/` 总数 | 473 | **525** | +52 |

**一句话读法**：**L2 涨、L3 降** —— 业务内容从 kernel 迁往 apps（R-001「kernel 零业务名词」的方向兑现）。

### 6.2 子包增删

| 变化 | 子包 | 依据 |
|---|---|---|
| kernel ➕ | `algo/` | `6f3e727c` —— **但它是红线来源，已于 2026-11-16 迁出** |
| kernel ➖ | `scheduled_research/` | 批次 8（`93fc98e4`）迁 apps |
| kernel ➖ | `algo/` | 2026-11-16 迁 `apps/algo`（业务名词破 nouns 门禁）|
| apps ➕ | `agent_plugins/` · `goal/` · `scheduled_research/` · `swarm_presets/` · `algo/` | 全部是 v1.1 时代**不存在**的 |

### 6.3 新增架构级构件（v1.1 时代不存在）

| 构件 | 路径 | 行数 | 引入 |
|---|---|---:|---|
| B1 节点产物契约 | `kernel/workflow/contract.py` | 317 | `a15280b0` |
| LoopEngine 统一引擎 | `kernel/agent/loop_engine.py` | 562 | 批次 11/12 |
| LoopSpec 聚合 | `kernel/agent/loop_spec.py` | 284 | 批次 12 |
| loop_assembly 双工厂收口 | `kernel/agent/loop_assembly.py` | 89 | phase3 |
| guardrails 护栏 | `kernel/agent/guardrails.py` | 114 | phase5 |
| StepHandler 协议 | `kernel/agent/_core_types.py` | — | 批次 9 |
| ctx_hash_id 上下文指纹 | `kernel/agent/agent_telemetry.py::compose_ctx_hash_id` | — | `7e68ac77` |
| 架构门禁全套 | `scripts/arch/`（14 文件）| — | 批次 4d / 8 |
| 层级矩阵唯一事实源 | `scripts/arch/layer_matrix.py` | 207 | `layered-skeleton-design-20261103` |
| 架构图同步器 | `scripts/arch/sync_architecture_diagram.py` | 390 | 批次 4d |

### 6.4 工作项完成度（v1.1 全部「待开工」→ v1.2 实测）

| 项 | v1.1 | v1.2 |
|---|---|---|
| W1 / W2 / W3 | 待开工 | ✅ 完成 / ✅ 完成 / ✅ 完成 |
| W4 | 待开工 | ⚠️ 部分（cron 兜底缺）|
| W5 / W6 | 待开工 | ❌ 未做 / ❌ 未做（D5 拍板 P2）|
| R1 / R2 | 待恢复 / 待接线 | ✅ 完成 / ✅ 完成 |
| R3 | 待接线 | ❌ 未做 |

### 6.5 门禁体系

| 项 | v1.1 | v1.2 |
|---|---|---|
| 结构门禁 | 「物理轴：AST 门禁（已有）」 | **8 道**（dangling+layers / path / capability / semantic / tool_refs / nouns / env / diagram）|
| pytest 覆盖 | 无 arch 门禁用例 | `test_scripts_arch.py` **26 用例**（含 nouns 3 条 + R-019 反向检验）|
| CI | 只跑 ruff / pyright / pytest | ➕ **`architecture-gates` job（8 步）** |
| 红线状态 | — | ✅ `batch_gate` **ALL GREEN**（2026-11-16 修）|

### 6.6 引擎形态

| 项 | v1.1 | v1.2 |
|---|---|---|
| 执行面 | 三套并存（AgentLoop 22.5k / GraphEngine+workflow 4.8k / SwarmRuntime 1.5k）| **双路径收敛中**：统一引擎（LoopEngine + Decider + Step）已是 `run()` 默认；legacy 循环体仍在（`u6-2-terminal-cleanup` 分支 5 commit **未合并**）|

### 6.7 文档一致性

| 缺陷 | v1.1 | v1.2 |
|---|---|---|
| CSS 变量 | **0 个**（30 个 hex 硬编码）| **已变量化**（`--layer-g/e/p` / `--barrier` / `--bridge` / `--phys-l1..l4` / `--ok/warn/bad`）|
| 真 heading | `<h1>` ×1、`<h2>` ×0 | `<h2>` ×10（分节可锚点跳转）|
| 关联文档链接 | 4 条中 **3 条失效** | 全部可解析 |
| 体量数字 | 硬编码快照（已被推翻 4 处）| **由 `check_framework_numbers.py` 钉在代码上** |

---

## §7 本文档不决定的事

- `templates/` 资产层归属（`.prompts` 17 / `.skills` 26 仍在）—— 已决议延期至第三步
- 三套执行面的**最终**收敛（`u6-2-terminal-cleanup` 5 commit 未合并，双引擎仍在）
- `kernel/agent` 是否拆分（`loop.py` 1,581 行，目标 ≤650）
- 校准 03 的「行为轨迹特征」表型维度（轨迹已采集未进任何闸）
- phase1 D1 的 C6/C7（`file_tools.py` **无 edit 工具** → 编辑唯一性 / 读后写保护物理上不存在）

---

## 附录：证据索引

| 断言 | 命令 |
|---|---|
| 层体量 | `python scripts/arch/sync_architecture_diagram.py` |
| 层方向矩阵 | `python scripts/arch/check_dangling_imports.py --layers` |
| kernel 名词归零 | `python scripts/arch/check_kernel_nouns.py` |
| 全部 8 道门禁 | `scripts\batch_gate.cmd` |
| W/R 状态 | `python tmp/probe/wr_status.py`（复核脚本，随用随弃）|
| 体量数字核对 | `python scripts/arch/check_framework_numbers.py`（本版新增）|
| 前视性质 | `python -m pytest tests/test_backtest_no_lookahead.py -q` |
