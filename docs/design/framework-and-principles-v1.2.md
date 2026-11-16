# 框架与理念（v1.2 · 2026-11-16）

> **性质**：框架文档（理念 / 结构 / 决策）· **上位源**：本文件（HTML 版是它的呈现）
> **基线**：`main` @ 合并 `ui-workbench`（534 commit）后的状态
> **与 v1.1 的关系**：v1.1（`framework-and-principles-20261105.md`）**保留不动**。
> 本版是**重做叙述 + 回填实测**，不是增量修补 —— 原因见 §5。
> **口径**：本文件所有数字均为 2026-11-16 实跑所得（`git ls-tree` / AST / 门禁实跑 /
> pytest），非文档转述。层编号统一采用**门禁口径**（见 §2.0）。

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
| L1 | `interfaces/` | 118 | 27,337 | 渠道层 + 组装根（哑适配器 + 唯一知道 apps 的位置）|
| L2 | `apps/` | 42 | 9,660 | 业务插件层（goal / chat / agent_plugins / swarm_presets / scheduled_research / builtin_tools / **algo**）|
| L3 | `kernel/` | 195 | 42,266 | 平台机制（agent / workflow / gateway / orchestration / events / session / evolution / swarm / …）|
| L4 | `foundation/` | 63 | 12,077 | 零业务原语 |
| — | **合计** | **418** | **91,340** | 上行违规 **0** |

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
| P | 冻结基准 + scorecard | `evals/`（70 条 golden）| L3 | ✅ 已有（缺指纹）| ✅ 已有 **+ 指纹已补** |
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

## §3 决策点（六项已拍板 · 附「后续落点」）

| # | 决策 | 拍板 | **v1.2 落点** |
|---|---|---|---|
| D1 | L2 的定位 | (c) 插件槽 + R1 | ✅ R1 完成；L2 = 42 文件 / 9,660 行（goal/chat/agent_plugins/swarm_presets/scheduled_research/builtin_tools/algo）|
| D2 | 缺依赖工具 | 不注册 + WARN | ✅ `check_capability_requires` 门禁化 |
| D3 | 成本/延迟进表型 | B 并列维度 + 双闸 | ✅ W2 完成 |
| D4 | RSI 循环驱动 | E 分层混合（两阶段）| ⚠️ 阶段 1 只落了一半（缺 cron 兜底）|
| D5 | hash chain 现在做吗 | P2 | ❌ 未做（符合拍板）|
| D6 | Tier 2 接线 | R1 时接 | ❌ **未接**（`set_tier2` 生产 0 调用）|

---

## §5 v1.1 → v1.2 变化总账（本版核心）

> 这是 v1.2 相对 v1.1 的**全部实质变化**，逐行可核。HTML 版的 §10 与本表同源。

### 5.1 分层体量

| 层 | v1.1（2026-11-06）| v1.2（2026-11-16）| Δ |
|---|---:|---:|---:|
| L1 `interfaces/` | 109 | **118** | +9 |
| L2 `apps/` | 15 | **42** | **+27** |
| L3 `kernel/` | 221 | **195** | **−26** |
| L4 `foundation/` | 66 | **63** | −3 |
| `src/` 总数 | 473 | **525** | +52 |

**一句话读法**：**L2 涨、L3 降** —— 业务内容从 kernel 迁往 apps（R-001「kernel 零业务名词」的方向兑现）。

### 5.2 子包增删

| 变化 | 子包 | 依据 |
|---|---|---|
| kernel ➕ | `algo/` | `6f3e727c` —— **但它是红线来源，已于 2026-11-16 迁出** |
| kernel ➖ | `scheduled_research/` | 批次 8（`93fc98e4`）迁 apps |
| kernel ➖ | `algo/` | 2026-11-16 迁 `apps/algo`（业务名词破 nouns 门禁）|
| apps ➕ | `agent_plugins/` · `goal/` · `scheduled_research/` · `swarm_presets/` · `algo/` | 全部是 v1.1 时代**不存在**的 |

### 5.3 新增架构级构件（v1.1 时代不存在）

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

### 5.4 工作项完成度（v1.1 全部「待开工」→ v1.2 实测）

| 项 | v1.1 | v1.2 |
|---|---|---|
| W1 / W2 / W3 | 待开工 | ✅ 完成 / ✅ 完成 / ✅ 完成 |
| W4 | 待开工 | ⚠️ 部分（cron 兜底缺）|
| W5 / W6 | 待开工 | ❌ 未做 / ❌ 未做（D5 拍板 P2）|
| R1 / R2 | 待恢复 / 待接线 | ✅ 完成 / ✅ 完成 |
| R3 | 待接线 | ❌ 未做 |

### 5.5 门禁体系

| 项 | v1.1 | v1.2 |
|---|---|---|
| 结构门禁 | 「物理轴：AST 门禁（已有）」 | **8 道**（dangling+layers / path / capability / semantic / tool_refs / nouns / env / diagram）|
| pytest 覆盖 | 无 arch 门禁用例 | `test_scripts_arch.py` **26 用例**（含 nouns 3 条 + R-019 反向检验）|
| CI | 只跑 ruff / pyright / pytest | ➕ **`architecture-gates` job（8 步）** |
| 红线状态 | — | ✅ `batch_gate` **ALL GREEN**（2026-11-16 修）|

### 5.6 引擎形态

| 项 | v1.1 | v1.2 |
|---|---|---|
| 执行面 | 三套并存（AgentLoop 22.5k / GraphEngine+workflow 4.8k / SwarmRuntime 1.5k）| **双路径收敛中**：统一引擎（LoopEngine + Decider + Step）已是 `run()` 默认；legacy 循环体仍在（`u6-2-terminal-cleanup` 分支 5 commit **未合并**）|

### 5.7 文档一致性

| 缺陷 | v1.1 | v1.2 |
|---|---|---|
| CSS 变量 | **0 个**（30 个 hex 硬编码）| **已变量化**（`--layer-g/e/p` / `--barrier` / `--bridge` / `--phys-l1..l4` / `--ok/warn/bad`）|
| 真 heading | `<h1>` ×1、`<h2>` ×0 | `<h2>` ×10（分节可锚点跳转）|
| 关联文档链接 | 4 条中 **3 条失效** | 全部可解析 |
| 体量数字 | 硬编码快照（已被推翻 4 处）| **由 `check_framework_numbers.py` 钉在代码上** |

---

## §6 本文档不决定的事

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
