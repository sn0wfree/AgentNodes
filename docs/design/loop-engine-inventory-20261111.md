# Loop 引擎清单与双引擎对比（2026-11-11）

> **性质**：调研（现状实测清单，不改代码；收敛决策的输入文档）
> **触发**：用户要求「梳理 loop 引擎有哪些、每个引擎的调用对象优劣及特性」
> **方法**：全部结论来自当日实测——AST 出口枚举（`tmp/probe/enum_all_exits.py`）、
> decider 返回值 AST 扫描、strategy 槽位消费点正则、`compare_exit.py` 双引擎同场景对照、
> 调用点 grep。行号以当日工作区为准。
> **一句话结论**：默认生产引擎是 skeleton，但**两套引擎没有一套是完整赢家**；
> 真正的风险不是选型，是**双引擎漂移面**——3 条已知行为差异中 2 条无测试网钉住。

---

## §1 全景：1 个类、2 条引擎、1 个共享层

`AgentLoop`（`src/agentnodes/kernel/agent/loop.py`，1437 行）内部有两条执行引擎，
由 `arun()` 的 dispatch 选择（`loop.py:898-913`）：

```
run()  (sync)  ──► _run_coro_in_sync(_run_loop_core(async_mode=False))   恒 legacy，无 dispatch
arun() (async) ──► engine == "skeleton" → _run_via_skeleton()            默认（批次 12 S1 翻转）
                   engine == "legacy"   → _run_loop_core(async_mode=True)
```

回退链（`arun()` 内，:904-910）：

| 输入 | 生效引擎 | 声明处 |
|---|---|---|
| `AGENTNODES_ENGINE=legacy` | legacy | `settings.py:195`（ENV_TABLE，domain: engine）|
| `AGENTNODES_SKELETON=0` | legacy（兼容别名）| `settings.py:197`（旧回退开关）|
| 缺省 / 其他值 | **skeleton** | — |

**只有 `arun()` 有开关；`run()`（sync）恒走 legacy，无 dispatch**（`loop.py:862-864`）。

**共享层**（不是引擎，两套引擎都消费）：`LoopStrategy` 的 9 个 step 槽位
（`strategy/steps/`：`DefaultPreRunStep` / `DefaultCompactionStep` / `DefaultLLMCallStep` /
`DefaultToolExecutionStep` / `BudgetStopStep`(stop) / `DefaultContinuationStep` /
`DefaultResilienceStep` / `DefaultProgressStep` / `DefaultFinalizationStep`，另有 config）。

### 1.1 容易混淆的三个"不是 loop 引擎的东西"

| 名字 | 是什么 | 现状 |
|---|---|---|
| `react.py` 图节点 | 282 行的第二个循环实现（gateway 图内）| **已删**（V3-c，commit `7026b1dc`，+347/−1796）|
| `LoopSpec`（`loop_spec.py`，284 行）| 构造参数聚合（6 组 Spec），不是执行引擎 | 在；`to_legacy_kwargs` 零调用（死代码）|
| `engine.run(state)`（`session_controller.py:289`）| **GraphEngine**（swarm/workflow 编排图）| 与 loop 引擎无关，仅名词撞车 |

---

## §2 引擎一：legacy（`_run_loop_core`）

### 2.1 基本特性

| 特性 | 实测内容 |
|---|---|
| 体量 | **246 行单函数**（AST 实测 L1006-1251）；宿主 `loop.py` 1437 行 |
| 并发模型 | sync/async **同体**：一个函数靠 `async_mode` 旗覆盖 `run()` 与 `arun()` 双端 |
| 循环结构 | for-else：跑满上限走 else 分支（`_handle_max_iter`，`loop_lifecycle.py:41`）|
| 闸门 | 内联 if：预算（:1056-1068）、continuation、injector（order>=100）、文本终答、`_check_no_progress` 审批闸（`loop_progress.py`，280 行）|
| LLM 流式 | `loop_stream.py`（261 行）→ `client.astream/achat/chat`；`stream_mode=True` 默认流式 |
| 终态 | `_handle_stop` / `_handle_max_iter` / `_finalize_metrics`（`loop_lifecycle.py` 48 行 + `loop_endings.py` 128 行）|

### 2.2 出口与收尾（AST 枚举：全函数只有 2 个 return）

| 出口 | 位置 | 收尾动作 |
|---|---|---|
| no-progress 拒绝/超时 | L1226 | `after_run` ✅ + trace close ✅ + `trace_path` ✅；**跳过** finalization step 与 `_git_commit` |
| 正常（含 STOP/max_iter/异常 break 后落此）| L1251 | finalization step + `_git_commit` + trace close + `trace_path` ✅ |

**已知缺口**：异常收尾（预算停止 / LLM 抛错）**不发 `loop_end`/`iter_end`**
（V4-1 实测的 3 条引擎差异之一，随 V4 回退保持未修——见 `.rules/lessons.md`）。

### 2.3 优 / 劣

| 优（每条有依据）| 劣（每条有依据）|
|---|---|
| 出口少且全可见：2 个 return，收尾语义一致 | 循环体焊死：9 个槽位内联调用，换策略要动 1437 行文件 |
| sync+async 同体，双端行为同一份代码保证 | 异常出口不发 `loop_end`/`iter_end`（V4-1，未修）|
| 生产验证时间最长，4 个 sync 调用方在跑 | 只有 sync 路径在用它 → 与 async 主路径长期并存漂移 |
| `budget_exhausted` 事件**唯一来源** | 闸门内联 → 单测要拉起整个 core |

---

## §3 引擎二：LoopEngine

### 3.1 基本特性

| 特性 | 实测内容 |
|---|---|
| 体量 | `loop_engine.py` 339（本体）+ `loop_engine_adapters.py` 371（6 个 adapter）+ `deciders.py` 281（8 个 decider）+ `modes.py` 159（闸位映射）= **1150 行** |
| 并发模型 | async-only；`run_stream()` 是 async 事件生成器，`run_to_completion()` drain 它 |
| 循环结构 | 显式状态机：PRECHECK → REASON → DECIDE①(after_reason) → ACT → DECIDE②(after_act) → OBSERVE |
| 闸门 | `Decision` 四值枚举（CONTINUE/STOP/EXIT/PROCEED）× 3 闸位 × 8 decider |
| adapter | 6 个：Prepare / Reasoner / Actor / Hooks / Finalize / IterationStart——引擎不拥有任何业务，全部转发回 `AgentLoop` |
| 流式 | LLM token 流与 legacy **同层**（经 LoopReasoner）；但 `run_stream` 只 yield 4 处：error ×3（L189/236/286）+ done ×1（L296），**无 token 增量事件** |

### 3.2 闸位 → decider 映射（`modes.deciders_for`，:139-159）

| 闸位 | decider | 包装的 strategy 槽位 |
|---|---|---|
| `precheck` | MaxIterationsDecider / BudgetDecider / CircuitBreakerDecider | stop / resilience |
| `after_reason` | BudgetDecider / GoalContinuationDecider / InjectorContinuationDecider / StopSignalDecider / TextFinalDecider | stop / continuation |
| `after_act` | NoProgressDecider（+审批 gate = `loop._check_no_progress`）| progress |

**EXIT 只有 `NoProgressDecider` 能产生**（8 个 decider 的 `decide()` 返回值 AST 扫描，
2026-11-11）——skeleton 的 precheck EXIT 分支（`loop_engine.py:176-181`）当前是**不可达死代码**。

### 3.3 出口与收尾

| 出口 | 位置 | 收尾动作 |
|---|---|---|
| STOP / max_iter / 异常 break | 落 L288 FINALIZE | `LoopFinalize`：`loop_end`/`assistant_message`/`iter_end` + finalization step + git + trace close + `trace_path` ✅ |
| **after_act EXIT**（no-progress 拒绝/超时）| L256-265 提前 return | `after_run` ✅；**不关 trace、`trace_path=None`、metrics/`loop_final` 缺** ❌ |
| precheck EXIT | L176-181 | 同上，但当前不可达（死代码）|

### 3.4 优 / 劣

| 优（每条有依据）| 劣（每条有依据）|
|---|---|
| 闸位可测可插拔：8 个 decider 独立类，映射表一页可读 | **规模倒挂**：1150 行 vs 被替代的 246 行（4.7×）|
| async-first，生产主路径默认走它 | 6 个 adapter 全是转发——引擎没有"接管"循环，只是把循环体搬进接线层 |
| 换闸/换策略 = 注入，不动大文件 | **EXIT 出口不收尾**（trace 不关、`trace_path=None`）|
| 异常出口事件齐全（经 LoopFinalize）| `run()`（sync）不可用；`budget_exhausted` 不发（V4-1b 随 V4 回退撤销）|
| | `_finalize`「两处都不能删」——抽象成本落在记忆上（adapters :356-359 注释自认）|

---

## §4 调用对象对比表

| 方向 | legacy | skeleton |
|---|---|---|
| **上游调用方** | `run()`：`executor.py:428`、`role_factory.py:273`、`run_evals_llm.py:93`、`workflow/executors.py:23`；`arun(engine="legacy")` 仅回退开关 | `arun()`：`service.py:1008`（API 主路径）、`agent_loop_node.py:154`（web chat 图节点，V3-b）、`fanout.py:118` |
| **strategy 槽位消费** | **9 个全内联**（AST 实测：compaction/config/continuation/finalization/llm_call/progress/resilience/stop/tool_execution）| 4 个经 adapter（compaction/finalization/llm_call/tool_execution）+ 4 个经 decider 包装（stop/continuation/resilience/progress）|
| **LLM I/O** | `loop_stream.py` | **同一层**（LoopReasoner 转发，无独立实现）|
| **工具执行** | `loop_tools.py`（414 行）直调 | LoopActor → 同一 `loop_tools` |
| **审批闸** | `_check_no_progress` 内联 | 同一函数，经 `_make_approval_gate` 闭包注入 NoProgressDecider（`modes.py:61-94`）|
| **hooks** | `_afire_hooks`（5 事件：before_run/before_iteration/after_iteration/after_run/on_error）| `LoopHooks` adapter → 同一组事件 |

**关键结论**：两引擎的**业务能力同源**（同一批 strategy steps / 同一个 `loop_tools` /
同一个审批闸），分叉只在「循环体怎么编排、闸门怎么判定」。

---

## §5 双引擎同场景对照（2026-11-11 实测，`compare_exit.py`）

场景：no-progress 触发 → 审批超时拒绝（`approval_timeout=0.01, on_timeout="reject"`），
`trace_dir` 开启，两引擎各跑一遍：

| 项 | legacy | skeleton | 一致？ |
|---|---|---|---|
| `finished_reason` | `approval_timeout_rejected` | 同 | ✅ |
| `iterations` | 2 | 2 | ✅ |
| `success` | False | False | ✅ |
| `answer` 长度 | 74 | 74 | ✅ |
| trace 有 `loop_end` | True | True | ✅ |
| trace 有 `loop_final` | False | False | ✅（两引擎都跳过 metrics）|
| 事件序列 | 15 条 | 15 条，逐条相同 | ✅ |
| **`trace_path`** | 设置 | **None** | ❌ |
| **`stop_reason`** | None | `no_progress` | ❌ |

---

## §6 已知行为差异清单（漂移面）

| # | 差异 | 生产影响 | 钉住它的网 |
|---|---|---|---|
| 1 | EXIT 收尾：skeleton 缺 `trace_path`/trace close/`loop_final`；legacy 异常出口缺 `loop_end`/`iter_end` | 诊断口径不同；skeleton 主路径上 trace 文件靠 `__del__` 兜底关闭 | **无**（一次性实测）|
| 2 | `budget_exhausted` 只有 legacy 发 | 「因预算停止」在生产主路径（skeleton）**无事件信号** | **无**（lessons.md 文字记录）|
| 3 | `stop_reason` 措辞：skeleton 用 decider 标签，legacy 用 None/`_handle_stop` reason | 调用方精确匹配会踩 | 对偶测试豁免措辞（弱）|

---

## §7 测试网现状

| 网 | 形式 | 强度（R-009 判据）|
|---|---|---|
| `test_loop_engine_equivalence.py`（5 场景）| `legacy == skeleton` 对偶（比 LoopResult 5 字段）| **弱**：两边同错同过；这正是「空集合不是通过」的重演案例 |
| `test_engine_parity.py`（批次 13 S3-a）| 6 项能力**绝对值**断言：预算记账 / wrap_up_nudge / guardrail / max_iter 文案 / hook 计数 / injector+goal 续跑顺序 | 较强：单侧退化即红 |
| `test_agent_loop*.py` | legacy 单侧功能测试（sync 为主）| legacy 侧厚、skeleton 侧薄 |
| 出口收尾网 | **不存在** | R-016 点名的洞（V4 教训的直接残留）|

---

## §8 汇总评分表

| 维度 | legacy | skeleton | 胜方 |
|---|---|---|---|
| 代码规模效率 | 246 行 | 1150 行（4.7×）| legacy |
| 可插拔性 | 焊死 | 注入即换 | skeleton |
| sync 支持 | ✅ | ❌ | legacy |
| 事件完整性 | 缺异常收尾 2 事件；`budget_exhausted` 唯一来源 | 缺 `budget_exhausted`；异常出口齐全 | 平手（互补缺陷）|
| 出口收尾一致性 | 2 出口清晰 | EXIT 双出口漏收尾 | legacy |
| 生产在用面 | sync 4 调用方 | async 主路径（API/web/fanout）| skeleton |
| 可测性 | 闸门内联 | decider 单类可测 | skeleton |
| 等价性网 | 被对照侧 | 6 项绝对值 + 5 场景对偶 | — |

---

## §9 结论与收敛前提

1. **当前形态**：默认生产路径是 skeleton（API/web chat）；legacy 靠 4 个 sync 调用方和
   回退开关续命。两套引擎没有一套是完整赢家。
2. **最实际的风险不在选型，在漂移面**：§6 的 3 条差异中 2 条无网；`test_skeleton_equivalence`
   是对偶断言，两边同错同过。
3. **收敛（删 legacy）的前置条件**（R-015 四条件，逐条对照现状）：

| # | 条件 | 现状 | 缺口 |
|---|---|---|---|
| ① | 规范已定（谁接管、边界在哪）| ❌ 未定 | 本文档 §8 只列事实，不代替决策 |
| ② | 接线完成 | 部分：skeleton 是 arun 默认，但 `run()` 无 dispatch | sync 4 个调用方要迁 |
| ③ | 完整测试（绝对值断言）| 部分：parity 6 项有；**出口收尾、budget_exhausted、事件序列无网** | 需先建 R-016 要求的「所有出口」网 |
| ④ | 审计能力独立于被删侧 | ❌ 无 | 引擎选择器/行为差异审计命令不存在 |

**在 ③④ 补齐之前，任何"删 legacy"的提案都不应被批准**（V4 的直接教训）。

---

## 附：证据清单

| 事实 | 证据 |
|---|---|
| `_run_loop_core` 246 行 / 2 个 return | AST 枚举（当日 tmp/probe/enum_all_exits.py）|
| 8 个 decider 的 decide() 返回值 | AST 扫描（同日 probe）|
| strategy 槽位消费点 | 正则 `self._strategy\.(\w+)` / adapters / modes |
| no-progress 场景双引擎对照 | tmp/probe/compare_exit.py（15 事件逐条比对）|
| 调用点清单 | grep `.run(` / `.arun(`（src/ 全量）|
| parity 6 项 | `tests/test_engine_parity.py` 模块 docstring |
| `budget_exhausted` / 异常收尾差异 | `.rules/lessons.md` V4-1 条（实测存档）|
