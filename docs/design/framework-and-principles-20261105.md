# 框架与理念（v1.1 · 2026-11-06）

> **地位**：**v1.1**。v1.0（2026-11-05 六项决策 D1–D6，记录见 §3.1）经
> **三轮 agent-team 评审**（4 评审员独立评审 → 交叉质证 → 元评审 + 独立盲评，
> **5/5 GO 附条件**）后应用修正案升版。评审全文见 `framework-review-20261106.md`；
> GO 条件 C1–C14 见 §3.4。**W1 自本版起具备开工条件**（批次 1）。
>
> 输入：GEP-RSI v1.1（语义架构）· 四层物理架构（layered-skeleton-design）·
> 一致性扫描（`e7a6e868` + `d2e962df`）· goal 重建方案（`bd743d99`）·
> 三轮评审（`415bc663` + `a1bb0a8c`）。
>
> 本文档回答：这个「自我进化的研究 agent 平台」的理念是什么、框架长什么样、
> 已排期的 R1–R3 与扫描出的缺口如何装进同一个框架。

---

## §0 一句话

**物理轴（L1–L4）管「谁可以 import 谁」，语义轴（G/E/P）管「谁可以改变谁」；
两轴在 Tier 0/1/2 相交；三条反馈回路按分钟 / 小时 / 天分时标运行；
评估器与变异白名单的守护在进程外；检查信号必须经由被检方不可控的通道产生。**

---

## §0.5 概念对照：Loop Engineering × Graph Engineering

> **概念出处**：《Loop Engineering 概念解析、思考与实践》（本仓库作者著）。
> 本节把这对概念与本框架的实现逐项对映；七条理念是它的展开。

**定义**：
- **Loop Engineering**——在 Agent 执行过程中加入**过程循环**，以目标驱动的方式
  保障执行效果，让 Agent 越练越好。
- **Graph Engineering**——约束 Loop 的结构性工程，防止 Loop 过拟合目标、
  卷分数、越练越死板。**边界（2026-11-06 拍板）：包含执行图与监管图两部分**——
  执行图（GraphEngine、SwarmRuntime 编译器）给 Loop 提供结构化、可中断、
  可恢复的轨道；监管图（Tier 分层、双门、指纹、原则七）裁决 Loop 是否真的变好。

**动静之分，不是文件之分**：Loop 侧是**行为**（跑在图上的目标驱动循环），
Graph 侧是**结构与监管**（图本身 + 闸门）。`AgentLoop` 既是 Loop 的载体（行为），
其代码结构也是 Graph 的一部分（结构）——同一段代码在两个概念里各有一个投影。

### 对映表

| 概念 | 本仓库实例 |
|---|---|
| Loop · 执行内循环 | `AgentLoop` 迭代 + 回路 1 goal continuation（判据未满足 → 注入续跑，分钟）|
| Loop · 任务级改进循环 | 回路 2 goal journal（判据达成/未达成 + 证据，小时）|
| Loop · 自我改进循环 | 回路 3 RSI cycle（诊断 → 提案 → 验证 → 生效，天）|
| Graph · 执行图 | GraphEngine（langgraph，唯一执行核）+ SwarmRuntime（SwarmPreset → 层图编译器）+ WorkflowGraphCompiler（definition → 图）|
| Graph · 监管图 | Tier 0/1 白名单（变异面）· ValidationGate 双侧 ±20% · held-in/held-out 双集（泛化性检测）· 评估指纹 · 原则七 · 终止层级 |

**三句关系**：图**承载**循环（没有 GraphEngine，回路 3 无处可跑）；图**约束**循环
（双门、指纹、白名单决定一次"变好"算不算数）；循环的合法成果**写回**图
（Tier 1 activate 改 harness——下一轮 Loop 在新的轨道上跑）。双时间尺度：
Loop 小尺度内变好，Graph 大尺度上约束 + 吸收——「越练越好」而不「越练越死板」的
机制就是：**练的是轨道上的跑法，改的是轨道本身，而轨道的每一次改道都要过闸。**

### 三条合法信道（v1.1 · Loop ↔ Graph 接口最小化）

对下面 0.5.1 的病理表逐行检验后，可以收拢成一条定理：

> **除以下三条信道外，Loop 与 Graph 之间不存在任何其他读写。**
> 任何新增的读写，要么封掉，要么改设计成三条之一。

| 信道 | 方向 | 承载者 | 管它的条款 |
|---|---|---|---|
| ① 执行 | Loop 在图上跑 | GraphEngine / AgentLoop | Loop Engineering 领地——结构自由，行为受预算与闸 |
| ② 闸控写 | Loop 的产出经屏障校验写回图 | GenomeProposal → ValidationGate → activate | 写入内容过闸：判据强度门 / tier2 value 约束 / F1 装配契约 |
| ③ 裁决 | 图对 Loop 出具裁决 | golden eval / goal 判据 | 信号必须走被检方不可控通道——**原则七就是本信道的细则** |

病理表 8 行据此归位：**5 行未授权写**（基线投毒 / 判据恒真 / 伪事件 / 监管面触达 /
F1 塑形）、**1 行未授权读**（卷分数：loop 读考卷）、**1 行信道缺失**（②从未接线：
`set_tier2` 零调用——成果不可追溯）、**1 行 Graph 内部缺陷**（F2 回滚错靶——闸门
自身打错，属 Gate 测试职责而非信道问题）。

**可测试的推论**：新关口组件接入时只问一句——**它与 Loop 之间开了几条信道？
不在三条之内的，封掉或改设计。** W1 自检、W4 指纹与增量门验真、W2 基线硬条款
全部是这个问题的实例；原则七、held-out 外持、判据强度门分别是三条信道的细则。

### 0.5.1 病理 ↔ 闸门对照表（Graph 侧条款的验收理由）

「越练越死板」在本仓库不是理论风险，是三轮评审逐条实证的病理。每条 Graph 侧
验收条款对应一条已实证病理——**这些闸门不是官僚主义**：

| 病理 | 实测病灶 | 闸门（条款 → 落点）| 状态 |
|---|---|---|---|
| 卷分数通道（确定性预计算 + 带内爬升）| seed 固定抽样可预计算；±20% 单集双侧=每代 +19% 合规通过 | held-out 双集 + **进程外持有** + seed 进程外注入 → W3（C9）| 排期 批次 3 |
| 监考被收买（基线投毒）| `golden_eval.py:196-198` 0.0 进均值 + `rsi_wiring.py:356-359` 无条件缓存（代码链闭环实证）| 基线硬条款：单任务异常剔除 + 最小有效样本数 + 滚动中位数 + 报错率健康度 → W2（C10）| 排期 批次 3 |
| 目标本身退化（判据恒真）| `evaluate("true")==True`；自写 evidence 自判完成 | 判据强度门（单一实现 kernel/swarm + apps/goal 创建路径校验）+ goal 创建限频 → R1（C7）| 排期 批次 2 |
| 考勤自报（事件伪签名）| report payload 无 task_signature；补字段后可定向投毒（N1 合取）| kernel 侧 TicketStore 计算签名 + 确定性降维 + 配额去重 + 增量门验真 → W4（K/L）| 排期 批次 2 |
| 监管面被 Loop 触达 | `DEGRADATION_THRESHOLD` 进程内可改；pin 集原只 2 文件；`ctx.write_roots` 可整体覆盖白名单；halt 进程内布尔 | pin 全集 + 常量包外比对 + 密封子进程自验指纹 + ctx.write_roots 封口 + halt 持久化 → W4（N/C5/C11）| 排期 批次 2 |
| 退化不可逆（回滚错靶）| F2：三连败 `cursor−1` 回退掉**上一个已批准的好版本** | anchor 式回滚（激活前记锚，废除盲目 cursor−1）→ W4（C13）| 排期 批次 2 |
| 成果不可追溯 | `set_tier2` 全仓零调用；replay 留 `replay:*` 幽灵版本；事件库无 hash chain | D6 三细则 + 幽灵版本隔离 + W6 hash chain（P2）| R1 / P2 |
| 轨道僵化的另一面（假基因堆积）| `deactivate` 零调用；从未激活组件无清扫 | W5 周期性假基因清理 | 批次 4 |

> 元规则（议题 C 的裁定预告）：**Graph 的边只在 Loop 踩过的地方画**——上表每一行
> 都有 file:line 出处；不为想象中的攻击加闸，已有闸一年零触发即评估退役。

---

## §1 理念（七条）

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

**机制（W1 · v1.1 拍板包，评审 4/4 收敛）**：工厂声明 `requires: frozenset[str]`
（依赖 kind 清单）；装配期按依赖拓扑序解析，缺一个整组跳过。
替代方案（注册时 dry-run 一次）开销大且不可静态分析，不推荐。

**W1 机制拍板包五条（v1.1 · 三轮评审收敛，缺一即假门禁）**：

| # | 拍板 | 理由（评审实证） |
|---|---|---|
| A | **自检闸住 `CapabilityRegistry.register` 内核侧**——import 副作用、填充器钩子、懒补注册路径 B 三条注册路径全部经此一处 | 路径 B（`capability/__init__.py:95-104`）直读 CAPABILITIES 逐条注册，可绕过任何 apps 侧检查 |
| B | **requires 语义 = factory 执行期闭包**；方法级双向边（`store.py:1347` ↔ `context.py:185`）明注为已接受例外 | 按「方法路径闭包」语义 store↔context 成环，整组拒绝——口径必须先拍板 |
| C | **校验 = 对 kind 实现模块集 AST 提取 resolve 字面量集，与声明求双向差集**；kind 名必须字符串字面量（动态构造即违规）；动态 kind（如 `f"evolution.{name}"`）列豁免清单 | 红队原案「扫 factory body」对全部真实案例漏报（`tool.goal` 的 body 只有 import+构造）——照落地会造出「检查永远绿」的假门禁 |
| D | **两阶段装配**：全清单收集 → 计算 skip 集 → 提交；新 kind 注册时**重算 skip 集（域限定 CAPABILITIES 清单）**；`capability.skipped` 定义兜底 sink | 注册发生在 import 副作用期，顺序依赖 + skipped 无 sink = 「不允许静默」落空 |
| E | **W1 测试破坏面 4 处改断言方向不归档**：`test_agent_loop.py:489,532`、`test_builtin_tools_comprehensive.py:188`、`test_mcp.py:89` | 它们测的语义（allowed_tools=None、readonly 过滤、MCP 默认面）仍有效 |

**检查**：装配后 `registry.list_kinds()` 与「声明可运行的 kind 集合」做一致性断言
（测试门禁）；运行期缺口 emit 事件。**W1 开工日自举验证**：用 AST 差集提取器对现存
kind 跑一遍，产出**带 resolve 调用点证据的 requires 全表**（GO 条件 C3）——声明表
本身错误率若高，自检越严错得越自信；差异项人工裁决后才启用自检。

### 理念四 · 评估器在循环外，终止层级在进程外

**张力**（GEP-RSI 校准 02 + 扫描 §8.4）：评估器若可被循环改写，
自进化退化为自欺骗；而「永远不可变」的绝对命题又不可实现（MetaRSI 的教训）。

**规则**（前三条已成立；第四、五条 v1.1 补齐）：
1. 评估器（`evals/`、golden_tasks.jsonl、scorecard）**不在 Tier 1 白名单内**
   ——白名单是闭合集合，构造期即封死（已验证：未知组件 `TierViolation`）
2. 评估器修改的唯一路径 = 循环外治理：PR + 人审 + 包外门禁。
   运行时进程没有改 `genome_proposals.py`（白名单本体）的合法入口
3. **终止层级** = 包外 `scripts/arch/` 门禁 + 测试套件 + 人。进程重启带不走它
4. **评估完整性指纹（W4 · v1.1 pin 集扩全）**：pin 集 = **evals/ 全部 + kernel/evolution/
   全部 + rsi_wiring.py**（v1.0 只 pin 2 文件——阈值 / 白名单本体 / 抽样 / 回放全裸露）；
   **关键常量启动时与包外基线比对**（`DEGRADATION_THRESHOLD` / TIER0/TIER1 /
   `DEFAULT_SAMPLE_SIZE` / select_tasks——进程内属性补丁可让任何文件级指纹失明）；
   **评估链跑在密封子进程**：子进程自行重算哈希、拒收父进程传值、eval 期 agent 工具
   白名单为受限集、解释器钉死；manifest 住 `scripts/arch/`（进程外），更新走
   PR 人审 + 基线纪元重置记录（防「pin 疲劳」死锁）；**pin 时点 = strategy_research
   根改写人审之后、通电之前**
5. **运行期写盘防线（v1.1 · 两轴之间的第三类行为）**：运行期进程用 file/shell 工具改
   包内文件，既非上行 import 也非合法变异——两轴门禁同时失明。防线 = `ctx.write_roots`
   禁止覆盖出包目录（**红队修正**：默认写根本不含 src/，真洞是 ctx 运行时整体覆盖默认
   白名单，不是「给白名单加拒绝」）+ **halt 持久化**（落盘 + 启动读取——进程内布尔对
   cron 夜间新进程无效，D4 护栏「kill switch 对 RSI 生效」以此为准）

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
- **skip 聚合升级（v1.1）**：skip 事件必须有读者——滚动 24h 计数按 reason 分类
  （`empty_holdout` / `no_new_evidence` / …），单类超阈（建议 3）→ escalate 人工；
  escalate 本身限频（防振荡刷屏）、须定义送达通道；`capability.skipped` 同样适用此条
- **D4 阶段 2 的启动前置（v1.1）**：判据强度门（见理念七/簇三）+ 事件写入方鉴别——
  无此二件，阶段 2 的 goal.settled / run.settled 驱动是可投毒的输入通道

---

### 理念七 · 检查信号必须经由被检方不可控的通道产生（v1.1 新增）

**张力**（红队元发现，三轮评审收敛为设计原则）：框架的全部「检查」的通过信号
都由被检查方自己的代码路径产出——W1 自检靠自报 requires；证据增量门靠自报事件；
goal 判据靠自写 evidence（`complete_lite` + `evaluate("true")==True` 实测恒真）；
基线与候选分靠同一进程内可被降级 / 投毒的 golden eval
（`golden_eval.py:196-198` 单任务异常记 0.0 进均值 → `rsi_wiring.py:356-359`
无条件缓存——基线投毒通道闭环实证）。**这不是四个 bug，是缺一条原则。**

**规则**：任何「检查」的信号，产生通道必须是以下三选一：
1. **kernel 侧计算**——不经事件发起方之手（如 task_signature 由 TicketStore 在
   admit/settle 时计算注入，而非工具/agent 自填）
2. **进程外持有**——如 held-out 集由 `scripts/arch/` 包外脚本持有，agent 不可读；
   评估指纹基线住包外
3. **不可伪造源**——如 append-only 事件库 + 未来 hash chain

**已知残差（诚实声明）**：W1 的 AST 差集抓运行期**动态** resolve（kind 名非常量）
不可能——原则七在静态检查层只兑现一半；agent 措辞 objective 聚进高失败率签名的
「输入构造」攻击不被 kernel 侧签名防住（被配额与 golden eval 双重兜底，风险有界）。

**检查**：每加一个新关口组件，先答「这个信号谁产生、被检方能不能控制该通道」；
答不出即不得上线（理念二关口组件 × 穿透通道 × 封堵机制清单，随理念二维护）。

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

### 2.5 工作项总表（v1.1 · 验收条款以 §3.4 GO 条件为准）

| 项 | 内容 | 来源 | 前置 |
|---|---|---|---|
| W1 | 装配期能力自检（理念三拍板包 A–E + 自举验证 requires 全表 + **batch_gate 门禁固化为附带交付**） | 理念三 · C1–C3 | ✅ |
| R1 | 恢复 apps/goal（**删 goal.expression kind**）+ goal.* kind 自证注册 + **4 阻断点逐一处置** + `goal.settled` kind（schema 预留写入方鉴别字段）+ Tier 2 接线三细则 + **判据强度门** + **GOAL_UPDATED 投影链复活** + 测试按 **collect 通过数**搬回（方向性锚；档 E 随 R2 同生共死、档 D 归档 + skip(reason)、核心域最小集下限） | bd743d99 + D1/D4/D6 + 评审簇一 | **W1** |
| R2 | chat 接线（slash / GoalPanel / SSE / API 路由）；**前置：修 A5 双头环境变量**（`AN_GOAL_DB_PATH` vs `AGENTNODES_GOAL_DB_PATH`） | bd743d99 + 评审 F10 | R1 |
| R3 | journal/scoreboard 端口接入 + autoresearch preset 化；**前置：6 个 goal_*.yaml 判据强度审查** | bd743d99 + 评审簇三 | R1 |
| W2 | P 层成本/延迟维度（**并列维度 + 双闸**：total 五项不动；cost 分子维度分别过闸或统一归一化——**禁混合均值**；`ReplayOutcome(metric, cost)` 向后兼容；**基线硬条款**：单任务异常剔除 + 最小有效样本数 + 滚动中位数 + 报错率健康度；latency 透传自既有 elapsed_s） | 理念五 · 校准 01 · D3 | ✅ 已拍板 |
| W3 | held-in / held-out 双集；**held-out 进程外持有（agent 不可读）**；分歧 kill-switch 并入本项 | GEP-RSI 第 5 步 | **W4**（原 W2，评审 Q）|
| W4 | 评估完整性指纹（**pin 全集 + 常量包外比对 + 密封子进程 + 更新协议**）+ 循环驱动链（阶段 1：CLI + cron + 证据增量门**验真** + 预算闸 + skip 聚合升级）+ **F1/F2/F7 三地雷修复**（F1 必须在驱动上线前）+ **回放状态隔离** | 理念四 / 六 · 评审簇二 | ✅ D4 已拍板 |
| W5 | 假基因清理（周期性 deactivate 从未激活组件） | GEP-RSI 第 7 步 | R1 |
| W6 | 事件 hash chain（P2；折中先行：失败计数落盘 + 事件写入方鉴别提前至 W4） | 屏障 | ✅ D5 已拍板 |

---

## §3 决策点（六项全部已拍板 · 2026-11-05）

| # | 问题 | 选项 | 推荐 |
|---|---|---|---|
| D1 ✅ | L2 的定位 | (a) 恢复 goal 重新充实 / (b) 取消 L2 / (c) 插件槽 + 自检 | **已拍板：(c) + R1**（见 §3.1）|
| D2 ✅ | 缺依赖工具的处置 | 不注册 / 注册 + degraded 标注 | **随 D1 采纳理念三默认：不注册 + WARN**（可推翻）|
| D3 ✅ | 成本/延迟如何进表型 | A 进总分加权 / B 并列维度+双闸 / A0 门槛收紧 | **已拍板：B**（见 §3.1 / §3.2）|
| D4 ✅ | 循环驱动方式 | A run.settled / B cron / C CLI / D goal.settled / E 分层混合 | **已拍板：E 分层混合，分两阶段**（见 §3.1）|
| D5 ✅ | hash chain 现在做吗 | 现在做 / P2 | **已拍板：P2**（评估指纹先行，见 §3.1）|
| D6 ✅ | Tier 2 现在接线吗 | R1 时接 / 以后 | **已拍板：R1 时接**（见 §3.1）|

### 3.1 决策记录

**D1 · 已拍板（2026-11-05）：L2 = 业务插件槽 + R1 恢复 goal，goal 必须过理念三自检。**

锁定四件事：

1. **L2 的存在理由改写**：不再是「研究 IP 层」（layered-skeleton-design 的旧口径），
   而是**业务插件槽**——每个 kind 是一个自带依赖声明的插件，注册即自证可运行。
   `apps/capabilities.py` 的角色从「kind 表」升级为「插件清单」。
2. **批次顺序改变**：W1（自检机制）提前为 R1 的前置——没有自检机制，
   「goal 过自检」无从谈起。新顺序：**W1 → R1 → R2 → R3**。
3. **R1 验收标准具体化**（插件槽口径下的 goal 依赖链；**v1.1 修正**：删 goal.expression 行
   ——4/4 实证零消费者，`kernel/swarm/runtime.py:793-796` 已改本地副本；恢复它 =
   注册无人 resolve 的 kind + 双份实现。**判据求值器单一实现 = kernel/swarm**）：

   | kind | requires | 说明 |
   |---|---|---|
   | `goal.store` | ∅ | 台账本体（依赖是 import 不是 kind）|
   | `goal.models` | ∅ | 判据 / 证据 schema |
   | `goal.context` | `goal.store` | 上下文渲染读台账 |
   | `tool.goal`（5 工具）| `goal.store, goal.models, goal.context` | 工具组 |

   装配期按拓扑序解析：`store / models → context → tool.goal`；
   缺一个 → 整组不注册 + `capability.skipped` 事件。
   **v1.1 追加三条 R1 验收**（评审簇一）：① **4 个运行期阻断点逐一处置并声明降级语义**
   （`store.py:1037/1649`、`workflow.py:402/425`——全是函数级惰性 import，
   「按依赖链自证注册」对它们假通过；`store.py:1649` 无 try，在 goal.context 渲染链上，
   R1 恢复即暴露）；② 测试搬回口径 = **collect 通过数（方向性锚）**，分档处置成文
   （档 E 依赖 R2 接口层的随 R2 同生共死；档 D 依赖已删包的归档 + skip(reason)；
   goal 核心域测试为不可裁剪最小集）；③ **GOAL_UPDATED 死投影链复活验证**
   （~100 行，R2 的 GoalPanel/SSE 依赖它）。
4. **现存 5 个坏工具组的命运进入 D2 议程**：W1 落地时，
   `tool.factor / tool.data / tool.backtest / tool.backtest_analysis / tool.data_clean`
   因依赖的 `db.* / datasource.* / config.runner / data.*` 已删而**全部过不了自检**。
   其模块文件（约 3,300 行）是删是归档，在 W1 验证通过后单独议。

**D2 · 随 D1 采纳理念三默认处置**：缺依赖 = 整组不注册 + WARN 事件。
（按 D1 的「必须过理念三自检」视为已采纳；若要 degraded 标注，明确推翻即可。）

**D4 · 已拍板（2026-11-05）：E —— 分层混合驱动，分两阶段落地。**

四个驱动源全部收敛到**单一入队点**，统一过预算闸，统一 skip 词表：

| 阶段 | 驱动 | 触发点 | 门 |
|---|---|---|---|
| 阶段 1（= W4 范围）| C · 手动 CLI | `agentnodes rsi run --seed N`（可复现，调链路用）| 人工 |
| 阶段 1（= W4 范围）| B · cron 定时 | scheduled_research 域（基建已有），夜间 | **证据增量门**：自上次 cycle 后无新 `assignment.report` / journal → `skip(no_new_evidence)` |
| 阶段 2（R1 后）| D · goal.settled | goal 判据达成/失败事件——信号密度最高，直喂第 1 步表型诊断 | （随 R1 就位）|
| 阶段 2（R1 后）| A · run.settled | run 收尾事件，异步入队 | **去重门**：冷却窗口 ≥ 数小时，或证据增量 ≥ N 条 |

全程护栏（理念六硬要求，非选项）：每 session ≤ 1 cycle + 每日 ≤ N cycle（N 待定，
建议 3–5）；cron 隔离池（capacity 车道，现默认 2 槽）；**halt 持久化**（落盘 + 启动
读取——进程内布尔对 cron 新进程无效，v1.1 修正）；成本核算基准 = **一轮 ≈ 20 次
golden 评测**（候选 10 + 基线 10，`DEFAULT_SAMPLE_SIZE=10`）。

**连锁**：
- **W4 验收标准 = 阶段 1 范围 + 评审簇二**：CLI + cron + 证据增量门**验真** +
  预算闸 + `rsi.cycle_started / cycle_skipped(reason)` 事件 + **task_signature
  进 report payload（kernel 侧 TicketStore 计算）+ 确定性降维（role+toolset，
  不用 LLM 意图分类）+ 配额去重 + A3 路径修复（`parents[1]`）+ 指纹 pin 全集 +
  密封子进程 + skip 聚合升级 + F1/F2/F7 三地雷修复 + 回放状态隔离（N/S 合并设计，
  一切子进程以显式 genome 版本 id 重建状态）**。阶段 1 跑通的判据 =
  **端到端反测试**：真实 emit 链 → holdout 非空 → 真实 golden 数值 ≠ 代理分。
- **阶段 2 启动前置（v1.1）**：判据强度门（R1 交付）+ 事件写入方鉴别（W4 验收
  组成部分，非阶段 2 才做——阶段 1 的 holdout 采样源同样吃事件流）。
- **R1 新增一条小交付**：补 `goal.settled` 事件 kind（阶段 2 驱动 D 的前置；
  现在只有 `goal_updated`，且其 emit 方已随批次 2 删除）。
- **批次 2 工作量预警（v1.1）**：W4 范围较 v1.0 翻倍以上——周期按两倍预留，
  或把回放/评估子进程化拆到批次 3（避免「批次 2 卡住 = 框架失败」的误判）。

**D5 · 已拍板（2026-11-05）：P2。** hash chain 是「必要非充分」（GEP-RSI v1.1 原话），
先落评估完整性指纹（W4）——同样的审计目标，收益/成本比高一个量级。
hash chain 待指纹机制跑稳后作为 W6 排入 P2。

**D6 · 已拍板（2026-11-05）：R1 时接。** goal 恢复后其产物（策略文件 / 研究记录）
经 `set_tier2(key, value, by=...)` 写入版本链——补上全仓唯一缺失的留痕闭环
（`set_tier2` 至今零调用，扫描 §8）。R1 验收随之 +1：至少一条 Tier 2 写入路径连通
（事件与版本链各留一条记录）。
**v1.1 三细则（评审收敛）**：① 接线写死 **apps/goal journal 写入路径内**（不放装配根，
避免与 W4 双头编辑 rsi_wiring.py）；② 失败 emit `tier2.write_failed`，**不许静默**；
③ **tier2 写入不得推 `activated_at`**——防污染锚语义归 Tier 1 变异（推荐 holdout
锚改锚「最后一次 Tier 1 activate」），否则高频 tier2 写入会把 holdout 防污染窗口
归零（D6 与证据供给隐藏互毁，`genome_version.py:280` 实证）。

**R1 范围累计变更**：+ `goal.settled` 事件 kind（D4）+ Tier 2 接线（D6）。
**D3 · 已拍板（2026-11-05）：B —— 并列维度（向量）+ 双闸。**
- `total` 五项权重不动（历史基线继续可比）；`TaskScore` 新增独立 `cost` 维度
- 成本闸：平均 token 成本退化 > 30% → **硬拒**（无论能力涨多少）；
  能力升但成本暴涨超上限 → escalate 人工审（复用「暴涨 escalate」先例）
- 归一化：`cost = 1 − min(1, tokens / budget_max)`；无 `budget_max` 的任务 cost 记 None，只参与能力闸
- 采集前提：latency **透传自既有 elapsed_s**（v1.1 更正：kernel 侧已广泛采集，
  原「全库零采集」陈述有误；缺的只是 evals RunRecord/TaskScore 透传；
  tokens→cost 管线改动面 5 源文件不变）
- 修正记录：v0.1 原推荐「进总分」**作废**——权重=汇率是自我评分交换面，与理念二冲突
- **v1.1 修正（评审 P 条 + C10）**：budget_max 实测仅覆盖 7/70 任务、seed=0 抽样
  仅 2 条——原 None fallback「只参与能力闸」= **90% 语料成本免检**；
  且绝对 tokens 与 cost∈[0,1] 混入同一均值会让 budget 任务退化信号被稀释到
  噪声以下。修正为：**无 budget_max 任务记 tokens 绝对量，分子维度分别过闸
  或统一按任务基线归一——禁止混合均值**；None/有效样本数口径写死
  （不足最小样本 → 成本闸降级 escalate，不装 0 分）
- **v1.1 基线硬条款（分歧 3 已由代码链定论关闭）**：`golden_eval.py:196-198`
  单任务异常记 0.0 进均值 + `rsi_wiring.py:356-359` 无条件缓存 = 基线投毒通道
  闭环实证。修复进 W2：单任务异常剔除 + 最小有效样本数（不足整轮 skipped）+
  基线滚动中位数 + 报错率健康度

**剩余待拍**：无。六项全部落定；v1.1 应用三轮评审修正案，GO 条件见 §3.4。

### 3.2 D3 方案分析（已拍板：方案 B）

**事实底座**（全部实测；v1.1 更正 latency 表述）：
- 现有总分：`total = 0.40·tool_recall + 0.20·无违规 + 0.15·budget_ok + 0.15·content_ok + 0.10·completed`（`evals/scorecard.py:112`）
- `tokens_used` 已采集（`RunRecord`），但只做 0/1 门槛（`budget_max_tokens`）
- latency：kernel 侧已广泛采集 elapsed_s/elapsed_ms（复现：`git grep -c elapsed`），
  **缺的只是 evals RunRecord/TaskScore 透传**（v1.0「全库零采集」陈述有误，已更正）
- budget_max 覆盖实测 **7/70**、seed=0 抽样 10 条中仅 2 条带预算
  （`golden_tasks.jsonl`，评审两组独立探针同数）
- ValidationGate 已有「暴涨同样 escalate」的先例（`validation_gate.py:193-205`：
  退化 < −20% 判 reward-hacking），双闸有语义先例可循

#### 方案 A：进总分加权（本文件 v0.1 的原推荐）

给 cost 一个权重（如 0.15），现有五项等比缩放，仍然输出单一 `total`。

| 优 | 劣 |
|---|---|
| 对比逻辑零改动（±20% 直接复用） | **权重 = 汇率**：0.15 意味着「花 1 单位成本分可以买 0.15 单位能力分」——这正是 GEP-RSI 警惕的自我评分交换面，agent 会学会在汇率有利处做交易 |
| 实现最小 | **交易方向不可见**：能力 +5% / 成本 +80% 可能总分持平，裁决者看不出交易发生过 |
| | **改 total 语义 → 历史基线全失效**：已存 scorecard、tests 里的总分对比全部要重校 |
| | 归一化难：cost 0..1 相对什么？相对基线噪声大，相对预算粒度粗 |

#### 方案 B：并列维度（向量 + 双闸）—— 修正推荐

`total` 保持五项不动；`TaskScore` 加 `cost` 维度；`ValidationGate` 升级为双闸：
- 能力闸：`total` 退化 > 20% → 拒（现状不变）
- 成本闸：平均 token 成本退化 > 阈值（建议 30% 起步，防噪声误杀）→ **直接拒，无论能力涨多少**；
  能力升但成本暴涨超上限 → escalate 人工审（复用「暴涨 escalate」先例）

| 优 | 劣 |
|---|---|
| **total 语义不变 → 历史基线、已存 scorecard 全部继续可比** | 双闸多一点代码（仍在 Gate 内部，不新增模块）|
| **交换率由治理定（阈值），不由 agent 学**——成本退化是硬拒，无汇率可钻 | 仍需选成本度量与归一化（见下）|
| 交易方向可见：能力与成本分开读，「变强了但贵了 3 倍」一览无余 | latency 需先补采集（真实工作量，但不复杂）|
| 校准 01 原文是「**并列**的硬维度」——向量才是原意，加权和是曲解 | |
| 为 HARNESSPRO 式帕累托选择铺路（成本-性能前沿） | |

#### 归一化子选项（两方案共用）

| 子选项 | 说明 | 判定 |
|---|---|---|
| b1 相对基线运行成本 | 同任务基线 run 的 token 比率 | 噪声大（同任务 run 间方差大），不推荐 |
| **b2 相对 budget_max_tokens** | `cost = 1 − min(1, tokens / budget_max)`；超预算 = 0，与现有 budget_ok 无缝衔接 | **推荐**：复用任务作者已表达的字段，零新增配置 |
| b3 全语料分位数 | 复杂，暂不 | 暂不 |

无 `budget_max` 的任务（`budget_ok` 恒 True 的那部分）：cost 维度记 None，
只参与能力闸——缺失即不参与，不装作 0 分。

#### 采集前提（无论选哪个都要做）

1. `RunRecord` 加 `duration_ms`，`golden_eval` 执行处 `time.monotonic` 打点
2. `TaskScore` 加 `cost`（b2 归一化）与 `duration_ms` 透传
3. 先 tokens 后 latency：tokens 已在，latency 打点是唯一新增采集

**修正说明**：本文件 v0.1 的 D3 推荐（进总分）经分析后修正为 B——
「加权和」会让交换率内生于优化目标，与理念二「评分不经自我」冲突；
校准 01 的「并列」一词指的就是向量。

### 3.3 v1.1 执行序

四条泳道收敛为两列：泳道内串行、泳道间并行。每批次完成后全量验证
（**batch_gate：collect 方向性锚 `collected ≥ 上批值` 且 `errors==5`（playwright 项）
不变 + 层门禁全绿 + graphify 符号级复扫**——batch_gate 固化是 W1 附带交付）。

| 批次 | 泳道 A · 能力恢复 | 泳道 B · P 层与屏障 |
|---|---|---|
| 1 | **W1** 能力自检（理念三拍板包 A–E；幽灵工具清零 44→18；自举 requires 全表；**batch_gate 固化**） | — |
| 2（周期按两倍预留） | **R1** goal 恢复（删 goal.expression）+ 4 阻断点处置 + goal.settled kind + set_tier2 三细则 + 判据强度门 + GOAL_UPDATED 复活 + 测试分档搬回 | **W4 阶段 1** CLI + cron + 证据增量门验真 + 预算闸 + 指纹 pin 全集 + 密封子进程 + skip 聚合 + **F1/F2/F7 地雷修复** + 回放状态隔离 |
| 3 | **R2** chat 接线（前置：A5 双头环境变量） | **W2** cost 并列维度 + 双闸 + 基线硬条款 ∥ **W3** 双集（**held-out 进程外持有**）+ kill-switch |
| 4 | **R3** 端口接入 + preset 化（前置：6 preset 判据强度审查） | **W5** 假基因清理 |
| P2 | — | **W6** 事件 hash chain |

另议（W1 验证通过后）：5 个过不了自检的工具组模块文件（约 3,300 行）去留——
**删 + 决策记录留 commit hash 考古锚**（批次 3/4/5 直删无档案的教训，评审合并案）。

### 3.4 v1.1 GO 条件（C1–C14，三轮评审 5/5 收敛；全文见 framework-review-20261106.md §7.4）

| 组 | 条件 | 阻塞对象 |
|---|---|---|
| C1 | 簇一应用五缺口：档 D 处置成文 / 2 个路径漂移测试文件 / kind 名字面量规范 / factory 签名不变明文 / 成本基线提供方 + batch_gate 固化 | 批次 1 |
| C2 | C 条实施写死：AST 提取（非 import 反射）+ 动态 kind 豁免清单 + 「实现模块集」定义 + 重算触发域限定 | 批次 1 |
| C3 | **C 提取器自举验证**：产出带 resolve 调用点证据的 requires 全表，差异人工裁决后才启用自检 | 批次 1 |
| C4 | 报告文本修正三条（已随本版应用） | — |
| C5 | W6 折中落条款（失败计数落盘 + 事件写入方鉴别）+ halt 持久化 | 批次 2 |
| C6 | pin 时点（根改写人审后、通电前）+ 指纹更新协议（PR 人审 + 基线纪元重置）+ manifest 住 scripts/arch/ | 批次 2 |
| C7 | tier2 value 格式约束（仅指针 + hash、key 命名空间受控）；判据求值器单一实现；goal.settled schema 预留写入方鉴别字段 | 批次 2 |
| C8 | collect 锚加下限（核心域最小集）+ A5 双头环境变量挂 R2 前置 | 批次 2 |
| C9 | **A3 反窥探三件套**（held-out 进程外持有 / must_contain 评测期解密 / seed 进程外注入） | 批次 2 泳道 B / 批次 3 |
| C10 | cost 口径禁混合均值 + 基线硬条款（分歧 3 关闭） | 批次 3 |
| C11 | 写盘防线 = ctx.write_roots 封口断言；N+S 进程隔离合并设计（子进程自验指纹 + eval 工具白名单 + 解释器钉死） | 批次 2 泳道 B |
| C12 | skip 告警按 reason 分类 + 限频 + 送达通道；capability.skipped 聚合读者 | 批次 2 泳道 B |
| C13 | **F1/F2/F7 地雷进 W4 验收**（F1 必须在驱动上线前修复） | 批次 2 泳道 B |
| C14 | R3 前 preset 判据强度审查；批次 2 周期两倍预留或子进程化拆批次 3 | 批次 3 |

---

## §4 本文档不决定的事

- `templates/` 资产层的归属（扫描议题 6）——**v1.1 注**：裁决时必须带上
  `tier0_snapshot.py:15` 的 methodology 快照消费方（资产归属直接牵动 Tier 0 冻结值采集）
- 执行面三套并存（AgentLoop 22.5k / GraphEngine+workflow 4.8k / SwarmRuntime 1.5k）
  的收敛——独立议题，不属理念层
- `kernel/agent` 是否拆分——结构/性能话题，不属理念层
- **校准 03 的「行为轨迹特征」表型维度**（轨迹已采集未进任何闸）——范围裁剪的显式
  声明，非遗忘；GEP-RSI 剩余远期缺口：失败签名 (c,q,m) 聚类、分布外/跨模型维度、
  双轨审计器
- 语义轴「构件→层」落位映射的机器可读静态断言——backlog（评审 P2-8）
