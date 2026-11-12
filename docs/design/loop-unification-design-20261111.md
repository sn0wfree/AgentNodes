# Loop 统一引擎设计（2026-11-11）

> **性质**：实施设计（决策已全部锁定，待批准后按批执行）
> **上位文档**：`loop-engine-inventory-20261111.md`（现状实测）·
> `loop-engine-industry-survey-20261111.md`（业界证据）
> **纪律依据**：R-014（先方案后动手 + 验收断言写清）· R-015（删接线对象四条件）·
> R-016（先有覆盖所有出口的网再删）· R-013（一批准一 commit，独立可回滚）
> **一句话**：统一引擎 = **skeleton 的骨架与四闸** + **legacy 的四件遗产**
> （出口漏斗 / `budget_exhausted` / token 流 / sync 同引擎）+ **deciders 可组合**
> （AutoGen/Vercel 形状）+ **循环级 HITL 挂点**（LangGraph 形状的库里最小实现）。
> 7 个批次（U1-U6 + H1），先网后删，每批独立可回滚。

---

## §0 目标与非目标

**目标**：`ReActSkeleton` 成为 `AgentLoop` 唯一循环体；`_run_loop_core`（legacy，
246 行）退役；消解双引擎漂移面（现状 3 条已知行为差异中 2 条无网）。

**非目标**（明确不做，见 §9）：kernel 循环图化、内建 checkpoint/durable、CodeAct、
LoopSpec 死代码清理（决策 5）、审批逻辑向 pause/resume 的迁移（H1 只加原语）。

---

## §1 决策记录（2026-11-11，全部锁定）

| # | 决策点 | 锁定值 |
|---|---|---|
| D1 | 统一路线 | **A**：skeleton 为唯一循环体，legacy 退役（否决 B 契约层——V4 已实证失败；否决 C 重写——B3 已建成）|
| D2 | `after_observe` 闸 | **恢复，但为空闸预留**（§4.1）：恢复的是闸位不是判定搬迁——C1/C2 类搬迁违反 U4 行为零变化，留给统一后的独立语义批 |
| D3 | HITL | **单独一批（H1）**，统一批只留挂点 |
| D4 | `run()` 切换方式 | **一次性**（无灰度环境开关）；安全网 = parity 网 + U1 出口网先全绿 |
| D5 | `loop_spec.to_legacy_kwargs` 死代码 | **不动**；U6 完工后再评估 |
| D6 | 流程 | **设计文档先行**（本文档），批准后动 U1 |
| D7 | EXIT 出口补跑 metrics/finalization（legacy 不跑）| **算有意改进**：统一引擎出口收尾是 legacy 的超集（`loop_final`/metrics 在拒绝场景出现）；事件序列网按**统一目标**钉，不按 legacy 钉 |
| D8 | U5 期望值漂移处置 | 引擎无关断言保留；legacy 特有断言迁入 **legacy 遗产网**（`arun(engine="legacy")` 显式钉住，U6 前临时保留，随 U6 删）|
| D9 | 引擎开关退役方式 | 走批 E 先例：读取层删、诊断层留（`audit-legacy` 报"已失效"）|

---

## §2 目标契约总表

| 契约位 | 形状 | 落点批次 |
|---|---|---|
| 循环体 | `ReActSkeleton.run_stream(spec)`（唯一）；`run_to_completion` 保持 drain 语义 | 现状 |
| 闸位 | `precheck / after_reason / after_act / after_observe` 四闸（after_observe 空闸预留）| U4 |
| decider 协议 | `decide(input, ctx) -> (Decision, reason)` 不变；新增 `and_/or_` 组合子 + `ExternalTerminationDecider`（接 halt）| U4 |
| 终态 | **一切出口 → LoopFinalize（幂等）**；EXIT 分支 `return` 改 `break` | U1 |
| 事件 | run_stream = `text_delta/thinking_delta`（转发）+ `error` + `pause`（H1 预留）+ `done`；**零新词表**——delta 事件名已存在（`loop_stream.py` 对每 chunk 发）| U3 |
| HITL | `ctx.pause(reason) -> resume_value` 挂点（Future 形状，复用 approval_event 模式）；审批逻辑迁移不在 H1 | H1 |
| sync | `run() = _run_coro_in_sync(arun())`，签名零变化 | U5 |
| spec | `SkeletonRunSpec` 不变；`LoopSpec` 保留（D5）| — |

---

## §3 结构细节

### 3.1 四闸与 after_observe（D2）

```
PRECHECK ──► REASON ──► DEC①after_reason ──► ACT ──► DEC②after_act ──► OBSERVE ──► DEC③after_observe ─┐
   ▲                                                                                                   │
   └────────────────────────────────── 下一轮 ◄────────────────────────────────────────────────────────┘
```

- after_observe **默认零 decider**（空闸）：结构性打通 + 单测钉「存在且为空」。
- 现有 GoalContinuation / InjectorContinuation **留在 after_reason 不动**（搬迁 =
  判定时点改变 = 行为变更，违反 U4 零变化；若将来要做，独立语义批 + parity 重跑）。

### 3.2 出口漏斗与收尾幂等（U1 核心）

skeleton 全部 9 个出口 → 统一漏斗到 LoopFinalize：

| # | 出口 | 位置 | U1 动作 |
|---|---|---|---|
| 1 | precheck STOP | break | 不变（回归钉）|
| 2 | precheck EXIT | L176-181 `return` | **删除分支**（J-1 已查证：`test_deciders.py:183` 在 decider 单元级测 EXIT，未向 skeleton 注入 precheck EXIT decider；留注释说明 EXIT 仅 after_act 可产生）|
| 3 | reasoner error | break | 不变 |
| 4 | after_reason STOP | break | 不变 |
| 5 | actor error | break | 不变 |
| 6 | **after_act EXIT** | **L256-265 `return`** | **改 `break`**（唯一可达的无收尾出口）|
| 7 | after_act STOP | break | 不变 |
| 8 | max_iter（for-else）| 落 FINALIZE | 不变 |
| 9 | 顶层异常 | except → 落 FINALIZE | 不变 |

**幂等坑与解法**：after_act EXIT 改 break 后，审批闸（`_check_no_progress`，
loop_progress.py:227-233）已发过 `loop_end`/`assistant_message`/`iter_end` 三件套，
LoopFinalize 再发 = **重复事件**。解法沿仓库已有先例
（`_after_run_fired` 标记，skeleton_adapters.py:272-286）：`NoProgressDecider.decide`
在 gate 返回 True 时置 `ctx._ending_emitted = True`（decider 拿得到 ctx），LoopFinalize
见标记跳过三件套、**仍执行** finalization step + git + trace close（D7：统一出口收尾是
legacy 超集）。附带效果（已查证可接受）：EXIT 路径将出现 `done` 事件——`run_stream`
的直接消费方只有 `run_to_completion`（不依赖 done），src/ 无其他消费方。
E3（发射所有权迁进 FINALIZE）**U6 前不可行**（闸与 legacy 共享），列为 U6 后可选优化。

### 3.3 token 流（U3）

- `LoopReasoner` adapter 已在 loop 的响应获取路径上；把 `text_delta`/`thinking_delta`
  事件**转发**进 `run_stream` 即可，**零新词表、零新事件类型**。
- `stream_mode=False` 时 delta 自然缺失（与 legacy 一致）；契约测试用 streaming 场景。

### 3.4 deciders 可组合（U4）

| 能力 | 新增 | 对照 |
|---|---|---|
| 组合 | `Decider.and_(other)` / `.or_(other)`（短路语义与现有"首个非 PROCEED 生效"一致）| AutoGen `&`/`\|`、Vercel stopWhen 数组 |
| 外部终止 | `ExternalTerminationDecider`——读现有全局 halt 状态，命中返回 EXIT | AutoGen ExternalTermination |
| 安全网惯例 | max 计数与语义条件**必须组合**使用 | AutoGen 官方 pitfall #5869 |
| after_observe | 空闸登记进 `modes.deciders_for`（默认 `[]`）| D2 |

**U4 实施细节（2026-11-12 盘查后钉定）**：

- **组合子语义**：`and_()` = 两个 decide 都 PROCEED 才 PROCEED，否则取**第一个非
  PROCEED** 的结果（与闸内"首个非 PROCEED 生效"一致）；`or_()` = 第一个非 PROCEED
  即返回，都 PROCEED 才 PROCEED。组合子返回包装对象，`name` = `"(a and b)"` 形态。
- **ExternalTerminationDecider 用注入，不用直读**（R-001 分层纪律）：halt 实现在
  `interfaces/cli/halt.py`（L4），kernel（L2）**不得 import 它**——首版实现直读
  `is_halted()`，被 `check_dangling_imports --layers` 的 `L3>L1` 违规提示暴露
  （U4 实测，见下）。改为构造注入 `halt_check: Callable[[], bool] | None`：
  - `None`（默认）= 无外部终止源，恒 `PROCEED`（**运行时零变化**，满足本批约束）；
  - 装配层（L3/L4 或测试）传入 `is_halted` 即启用。
  这与仓库既有模式一致：`_make_approval_gate`（`modes.py:61`）也是闭包注入而非
  kernel 直读 interfaces。
- **节流边界**：`is_halted()` 有跨进程 mtime 节流（0.5s）——同一进程内 trip 后
  0.5s 内立读可能 False。生产语义正确（跨进程 trip 靠文件可见性）；单测注入替身
  （不实测节流窗口）。conftest 的 `_reset_halt`（autouse）保证测试间无污染。
- **闸位**：ExternalTerminationDecider 挂 **precheck**（每轮开头 + for-else 兜底前的
  最后检查点；halt 是全局信号，不该等 REASON/ACT 浪费一轮 LLM 调用）。
- **EXIT 语义 vs STOP**：halt 命中返回 **EXIT**——与 no-progress 拒绝同型（用户/外部
  明确要求终止整个 run），经 U1 漏斗走 FINALIZE（finished_reason 落 "exit"）。
  注意 Decision.EXIT 注释（deciders.py:56）"跳过主 finalize" 已被 U1 改写
  （EXIT 也走 FINALIZE），实施时同步修正该注释。
- **测试更新清单**：`test_deciders.py:233` 三闸断言 → 四闸（+`after_observe` 为空列表）；
  `test_deciders.py:17` 的 EXIT 注释同步。

### 3.5 HITL 挂点（H1）

- 形状：gate/decider 可调 `ctx.pause(reason) -> resume_value`；
  skeleton yield `{"type": "pause", ...}` 并 await ctx 上的 Future。
- 复用 `approval_event` 模式（loop_progress.py:212-219 的 clear→查→wait 三步序，
  防"提前到达的决策被丢弃"）。
- **验收红线**：resume **不得隐式重放** pause 前的副作用（LangGraph double-execution 坑，
  检索证据见 survey §2 LangGraph 行）。
- 现有工具级 ASK 通道（permission gateway）**保留不动**；审批逻辑迁移是 U6 后可选。

### 3.6 sync 同引擎（U5）

- `run()` 内部改为 `_run_coro_in_sync(self.arun(task, context=context, history=history))`；
  签名、返回类型零变化。业界先例：OpenAI `run_sync` = 同一 Runner 的包装（10/10 框架无双引擎分域）。
- 回归面核验清单（U5 前置）：`executor.py:428`（workflow）、`role_factory.py:273`
  （answer 必须合法 JSON）、`run_evals_llm.py:93`（scorecard 字段）、
  `workflow/executors.py:23`；**最大工作量在 `test_agent_loop*.py` 的期望值迁移**（D8 规则处置）。

---

## §4 组件去向清单

| 现有组件 | 行数 | 统一后去向 | 批 |
|---|---|---|---|
| `_run_loop_core` | 246 | **退役** | U6 |
| `loop.py` 其余（~1190）| — | 保留瘦身：装配薄壳 + 事件/trace/git 基建 | 分批 |
| `loop_stream.py` | 261 | **吸收**：delta 事件进 run_stream；文件转为 Reasoner 流式实现 | U3 |
| `loop_lifecycle.py` | 48 | 语义已在 LoopFinalize 等价物；U6 随 legacy 评估 | U6 |
| `loop_endings.py` | 128 | **保留**（metrics/claim validation 实现）| — |
| `loop_progress.py` | 280 | **保留**；审批闸为 H1 pause/resume 的第一消费者候选 | H1 后评估 |
| `loop_tools.py` | 414 | **保留不动** | — |
| `loop_spec.py` | 284 | 保留；`to_legacy_kwargs` **不动**（D5）| — |
| `skeleton.py` | 339 | 保留 + EXIT 改 break + 四闸 | U1/U4 |
| `skeleton_adapters.py` | 371 | 保留 + Reasoner delta 转发 | U3 |
| `deciders.py` | 281 | 保留 + 组合子 + ExternalTermination | U4 |
| `modes.py` | 159 | 保留 + after_observe 空闸登记 | U4 |
| `AGENTNODES_ENGINE/SKELETON` | — | U5 起仅诊断；U6 走 D9 退役 | U6 |
| `test_skeleton_equivalence.py`（对偶）| 201 | **U1 转绝对值断言**（R-009 反对偶；V4-4a 先例）| U1 |
| `test_engine_parity.py`（绝对值 6 项）| 422 | **保留并扩展**为单引擎能力网 | — |
| `_check_budget` | — | **在用**（loop.py:1115，2026-11-11 核验），保留至 U6 | — |

---

## §5 迁移批次（每批独立可回滚）

| 批 | 内容 | 验收断言（红→绿 / 零→有）| 回滚方式 | 依赖 |
|---|---|---|---|---|
| **U1** | 出口网（§3.2 矩阵 9 出口 × 6 断言：`trace_path` 非空 / trace 已关 / `loop_end`·`iter_end`·`after_run` 各恰一次 / done 语义正确）+ after_act EXIT→break + `_ending_emitted` 幂等 + precheck EXIT 分支删除 + 对偶测试转绝对值 | **已完成（2026-11-12）**：网 5/5 绿（先红 xfail → 修后 XPASS → 摘 marker）；全量 7172 passed / 0 failed | revert 单 commit（break 改动还原为 return；标记为纯增量）| — |
| **U2** | `budget_exhausted` 补发 + no-progress 事件序列网（compare_exit 固化） | **已完成（2026-11-12）**：红网（2 xfail）→ LoopFinalize 补发（budget 分支，载荷与 legacy 同形）→ XPASS 摘 marker 4/4 绿；序列网 skeleton 绝对值 + legacy 对照 2/2 绿；全量 7178 passed / 0 failed | revert | U1 |
| **U3** | run_stream 转发 `text_delta/thinking_delta` | **已完成（2026-11-12）**：红网（流式场景 run_stream 只 yield done）→ 事件桥（`AgentLoop._make_event_bridge` + 骨架 `event_source` + 步骤间隙 drain）→ 4/4 绿（正向 2 / 负向 1 / 工具共存 1）；全量 7181 passed / 0 failed | revert | U1 |
| **U4** | deciders `and_/or_` + ExternalTerminationDecider + after_observe 空闸 | **已完成（2026-11-12）**：组合子 6 条 + 外部终止 5 条 + 分层纪律 1 条单测；闸位断言按四闸更新（`test_deciders.py` 三闸→四闸 + `after_observe == []`）；**运行时零变化**（既有 loop 网 29 条全绿）；全量 7195 passed / 0 failed。**执行中发现真实分层违规**（见 §3.4 与 §5.2）| revert | 无（实际串行）|
| **H1** | `ctx.pause/resume` 原语（§3.5）| e2e：pause 事件可见 → resume 值进循环 → **无副作用重放**；超时走既有 approval_timeout 语义 | revert（纯增量）| U1 |
| **U5** | `run()` 切统一引擎（§3.6）| 表 G 核验完 + D8 迁移完 + parity/出口网全绿 + 全量 `-n 12` 绿 | revert 一处 dispatch（legacy 路径仍在）| U1-U3 |
| **U6** | 删 `_run_loop_core` + legacy 遗产网 + 引擎开关（D9）；E3 所有权迁移（可选）| 只读 audit 命令独立报 legacy 残留（R-004）；collected 锚点更新；全量绿 | revert（内容在 git 历史）| U5 + 网全绿 |

**顺序原则**：先网后删（R-016）；U4 与 U1 都动 `skeleton.py`，实际串行执行；U5 前 U1-U3 必须全绿（D4）。

**存档分支复用**（2026-11-11 评估补）：U1 的网**从 `archive/loop-unify-20261111` 改编而非重写**——
`test_skeleton_capabilities.py`（能力绝对值断言 + subprocess 模式）、`test_engine_capabilities.py`、
`tests/llm_client_mock.py`（chat/achat 双入口替身，V4-2 教训的实现）都在存档上，V4 的测试资产中
**网的部分是好的**（lessons.md 判其"照模板写"的是 LoopProtocol 契约测试，不是能力网）。

### 5.1 U1 执行记录（2026-11-12）

| 步骤 | 结果 |
|---|---|
| 网：`tests/test_loop_exit_net.py`（5 场景 × 6 断言）| 先 4 绿 + 1 **xfail 钉住 EXIT 洞**（红网就位）|
| 修：after_act EXIT `return`→`break` + precheck EXIT 分支删除 + `_ending_emitted` 幂等 | XPASS(strict) → 摘 marker → **5/5 绿** |
| 修中发现：幂等标记必须**两处配合**（skeleton EXIT 分支置位 + LoopFinalize 读标记跳三件套）——首版只置位不读，全量事件重复（loop_end×2）被网当场抓住 | **网的有效性实证** |
| 对偶转绝对值（D8）：`test_skeleton_equivalence.py` repetitive 场景改精确语义 + 勘误注释（"skeleton 落 stop"是过时记载，批次 12 S1 已收编审批闸）| 7 passed |
| 附带修复 1：`llm_client_mock.py` 从存档恢复 + 新增 `patch_llm_client_full`（4 入口，R-008 修正）| — |
| 附带修复 2：`ExecutorBridge` 跨进程取消竞态（`dispatch_epoch` 信封取代裸 Event 判定）+ collect timeout 3→10s（满载下 spawn 冷启动假超时）| 单跑 10 passed；探针 20 次 0 失败；混合负载 6 连绿 |
| 场景构造踩坑（写进网注释）| ① `no_progress_window`/`max_iterations` 必须经 strategy config 传（react preset 自带 max_iterations=10 会静默覆盖构造参数）；② shell/echo 在 Windows 沙箱下会 error——用 list_history 构造多轮场景 |
| 全量 | **7172 passed / 114 skipped / 0 failed**（`-n 12`，105s）；batch_gate ALL GREEN（7286 ≥ 7178）|

---

## §6 legacy 遗产网（D8 的落地形态）

- 新文件 `tests/test_legacy_engine_net.py`：把 `test_agent_loop*.py` 中
  **legacy 特有断言**（无法引擎无关化的）显式改走 `arun(engine="legacy")` 钉住。
- 生存期：U5 创建/迁移 → U6 与 legacy 一起删。
- 判据：断言依赖 legacy 特有行为（如异常出口不发 `loop_end`——统一引擎**故意**
  与之不同，D7）→ 进遗产网；断言是引擎无关行为（answer/success/iterations）→ 留原地。

---

## §7 风险与缓解

| # | 风险 | 缓解 |
|---|---|---|
| 1 | U1 改 break 后事件重复（表 E 坑）| `_ending_emitted` 幂等（有 `_after_run_fired` 先例）；U1 网断言"恰一次"直接抓 |
| 2 | U5 `test_agent_loop*.py` 大面积期望值漂移 | D8 遗产网规则；迁移逐文件进行，每文件全量绿再下一个 |
| 3 | after_observe 被误用为搬迁目标（破坏零变化）| §3.1 写死：默认空闸；搬迁需独立语义批 |
| 4 | resume 重放副作用（LangGraph 坑）| H1 验收红线含"无副作用重放"断言 |
| 5 | 批次过大重演 V4 | 每批一个 commit、独立可回滚（§5 回滚列）；U4/H1 与 U1-U3 并行降单批体积 |
| 6 | 删 legacy 后发现网没盖住的出口 | U1 网先于 U6 全绿 + 只读 audit（R-004）+ R-015 四条件跟踪（§8）|

---

## §8 R-015 四条件跟踪（删 legacy 的门槛）

| # | 条件 | 现状 | 由谁满足 |
|---|---|---|---|
| ① | 规范已定 | ✅ 本文档（待批准）| D1-D9 |
| ② | 接线完成 | 部分（arun 默认 skeleton；run 走 legacy）| U5 |
| ③ | 完整测试（绝对值断言）| 部分（parity 6 项有；出口/事件序列无）| U1 / U2 / U3 + 遗产网 |
| ④ | 审计能力独立 | ❌ 无 | U6 前建只读 audit 命令 |

**四条全绿前，U6 不开工。**

---

## §9 明确不做

| 不做 | 理由 |
|---|---|
| kernel 循环图化 / 内建 checkpoint / durable | survey §4 建议 7；编排已有 workflow 层 |
| CodeAct 动作空间 | 与 loop 引擎正交 |
| `to_legacy_kwargs` 清理 | D5（U6 后评估）|
| 审批逻辑迁移到 pause/resume | H1 只加原语；迁移是 U6 后可选 |
| E3 发射所有权迁移 | U6 前不可行（闸与 legacy 共享）；U6 后可选 |
| GoalContinuation/Injector 搬到 after_observe | 行为变更，独立语义批 |

---

## §10 验证命令（每批必跑，R-007）

```bat
:: 全量（并行默认）
C:\Users\1\AppData\Local\Programs\Python\Python313\python.exe -m pytest tests -q -p no:hypothesispytest -p no:hypothesis -n 12 --dist loadgroup
:: 门禁
scripts\batch_gate.cmd
:: 架构图（动了 src/ 行数必跑）
D:\tools\python312\python.exe scripts\arch\sync_architecture_diagram.py --write
```

---

## 附：证据链

| 设计决定 | 证据 |
|---|---|
| 路线 A 方向 | survey §1 模式 A（OpenAI/Anthropic/AWS 生产形态）|
| 出口漏斗 | inventory §3.3（EXIT 实测）+ survey 建议 2（LangGraph END 漏斗）|
| 幂等标记 | `skeleton_adapters.py:272-286`（`_after_run_fired` 先例及其理由注释）|
| token 流零新词表 | `src/agentnodes/foundation/observability/event_vocab.py` 模块注释（delta 事件已存在）|
| deciders 组合 | survey §2 AutoGen/Vercel 行 + pitfall #5869 |
| HITL 形状 | survey §2 LangGraph/pydantic-ai 行 + double-execution 坑；库内 `loop_progress.py:212-219` 三步序 |
| 出口矩阵 9 项 | 2026-11-11 AST 枚举 + `compare_exit.py` 实测 |
| `_check_budget` 在用 | loop.py:1115（2026-11-11 核验，纠正前次"死代码"误记）|

### 5.2 U4 执行记录（2026-11-12）

| 步骤 | 结果 |
|---|---|
| `Decider.and_()` / `.or_()` 组合子 | 6 条单测（短路语义 / 双 PROCEED / name 可读）|
| `ExternalTerminationDecider` | 5 条单测（halt→EXIT / 未 halt→PROCEED / 无源→PROCEED / 故障 fail-open / 经 modes 注入生效）|
| `after_observe` 第四闸 | `modes.deciders_for` 登记空列表；`skeleton.run_stream` OBSERVE 后调闸（空列表 → 直接 PROCEED）＝运行时零变化 |
| 闸位断言更新 | `test_deciders.py` 三闸 → 四闸 + `after_observe == []`（预期中的文本变更，非行为漂移）|
| **执行中发现的真实缺陷** | **首版 `ExternalTerminationDecider` 直读 `interfaces/cli/halt.is_halted`（L4）→ `check_dangling_imports --layers` 报 `L3>L1: 1`**。根因是 kernel（L2）反向依赖 interfaces（L4），违反 R-001。改为**构造注入 `halt_check`**（None = 无源 = 恒 PROCEED），`deciders_for(halt_check=...)` 由装配层传入——与仓库既有 `_make_approval_gate` 闭包注入同模式。修复后门禁 `L3>L1: 0` |
| 回归钉 | 新增单测 `test_deciders_module_does_not_import_interfaces`（读源码断言无 `interfaces.cli` / `from agentnodes.interfaces`），防回归 |
| 运行时零变化验证 | 既有 loop 网 29 条（出口/预算/事件序列/双通道/parity/对偶）全绿 |
| 全量 | **7195 passed / 114 skipped / 0 failed**（`-n 12`，100s）；batch_gate ALL GREEN（7309 ≥ 7178）|

**流程教训**：U4 的验收标准原写「纯增量，既有断言零改动」——实测必改
（闸位集合断言）。已在 §5 表格修正为「运行时零变化 + 闸位断言按四闸更新」。
U4 还暴露了「直读跨层实现」的惯性：halt 就在 `interfaces/cli`，但 kernel
不得 import——门禁当场抓住。这是 gate 有效的又一实证（R-019 的正面案例）。
