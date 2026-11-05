# goal 能力族重建方案（2026-11-05）

> **目的**：把减法批次 2/3 删掉的 `goal` / `study` / `autoresearch` 三块能力，
> 合并重建为一个 L2 能力族，并让它给自我进化闭环（RSI）提供细粒度证据源。
>
> **触发**：D1 批次勘查时发现 `tool.goal` 是「注册了但必然失败」的死能力
> （`goal.store` / `goal.models` / `goal.context` 三个 kind 在批次 2 已删，
> 全仓库无注册点）。用户判断「删除不划算」，要求改为**实现出来**，
> 并把 goal / study / autoresearch 三者合并。
>
> **状态**：**本文只做记录与方案设计，尚未执行任何改动。**
> R1–R3 三个阶段待架构讨论结论出来后另行排期。
>
> **方法**：全部结论来自 AST 实测 + `git log` 追溯 + 档案逐文件行数核对，
> 每条断言都能追到具体文件行号或具体提交。

---

## 0. 结论摘要

| # | 判断 | 依据 |
|---|---|---|
| 1 | **autoresearch 不是子系统，是 goal 的一份拓扑配置** | 它的引擎（`GoalWorkflowRunner`）、账本（`GoalStore`）、判据（`GoalCriterion`）全是 goal 的；它自己只剩 9-Agent 拓扑（Python 版 `build_autoresearch_workflow_config()` + YAML 版 `goal_autoresearch_9agent.yaml`）|
| 2 | **恢复 goal 的真正价值不是多一个业务功能，是给 RSI 补上细粒度证据通道** | `kernel/evolution` 的 journal/scoreboard 两个源现在从粗粒度 run 事件派生；goal 侧档案里的 `GoalStore` journal API 与 `LeverScoreboard` 正是它们要的细粒度数据 |
| 3 | **精简的正确边界是「端口化注入」，不是「跨层抽基类」** | `kernel/evolution` 已声明 `JournalSource` / `ScoreboardSource` 两个 Protocol；L3 零改动、L2 提供实现、装配根注入 —— 与项目既有的 `ProjectionSink` / `schema_port` 同一模式 |
| 4 | **可恢复规模远超预期：66 文件 / 19,417 行** | 实现 24 文件 / 8,435 行（含 L1 接口层）+ 测试 42 文件 / 10,982 行，**全在金标准档案里** |
| 5 | **study 与 autoresearch 的源码已不可恢复** | 批次 3（`411a7ee7`）是直接 `D` 删除 37 + 1 个文件，**没有进档案** |

**核心判断**：这三块能力当年是**一个整体被切成三份**，切开后又被分别删除。
恢复的正确形态不是「复活三个子系统」，而是**重建一个能力族**：

```
apps/goal/          一个 L2 能力族
  ├── ledger/       目标 · 判据 · 证据 · 审计 · journal        ← goal 的账本
  ├── orchestration/ DAG 编排引擎 · 钩子 · 分支 DSL · 完成判定  ← goal 的引擎（当年 study 的能力）
  ├── executors/    claims 校准 · 回测桥接（可插拔钩子）        ← 需新建的少量胶水
  └── presets/      拓扑配置（含 9-Agent autoresearch）        ← autoresearch 降级到这里
```

---

## 1. 现状实测

### 1.1 三个子系统的归档真值表

| 子系统 | 处置提交 | 处置方式 | 现在在哪 | 规模 |
|---|---|---|---|---|
| **goal** | `08303f04`（批次 2）| `R100` 重命名 → 档案 | `testcase/golden/apps/goal/` | **19 文件 / 6,147 行** ✅ |
| **hypothesis** | `08303f04` | `R100` 重命名 → 档案 | `testcase/golden/apps/hypothesis/` | 6 文件 / 1,459 行 ✅ |
| **study** | `411a7ee7`（批次 3）| **直接 `D` 删除** | **无处可寻** | 37 文件 / 11,274 行 ❌ |
| **autoresearch** | `411a7ee7` | **直接 `D` 删除** | **无处可寻** | 1 文件 / 1,364 行 ❌ |

> ⚠️ **代码注释与事实不符**：`src/agentnodes/apps/capabilities.py:63-64` 写着
> 「apps/study + apps/autoresearch 全部归档到 `testcase/golden/apps/`」，
> 但 `testcase/golden/apps/` 下只有 `alpha_zoo` / `export` / `goal` /
> `hypothesis` / `portfolio` / `validation` 六个目录，**没有 study**。
> 这条注释需要一并修正。

### 1.2 L1 接口层也在档案里（R2 不用从零写）

批次 2 一并归档了 goal 的接口层：

| 文件 | 行 | 职责 |
|---|---:|---|
| `testcase/golden/interfaces/cli/slash_goal.py` | 879 | `/goal` slash 命令组 |
| `testcase/golden/interfaces/routers/goal.py` | 288 | goal API 路由 |
| `testcase/golden/interfaces/routers/workflow.py` | 731 | 工作流 API 路由 |
| `testcase/golden/interfaces/tui/dag_view.py` | 177 | TUI DAG 视图 |
| `testcase/golden/interfaces/tui/workflow_worker.py` | 213 | TUI 工作流 worker |
| **小计** | **2,288** | |

> ⚠️ 归档时路径被压平：`interfaces/api/routers/` → `interfaces/routers/`，
> `interfaces/cli/tui/widgets/` → `interfaces/tui/`。恢复时需补回原层级
> （`interfaces/api/routers/` / `interfaces/cli/tui/widgets/`）。

> 这修正了「R2 要新写 CLI / TUI / Web SSE」的估计——**它们是恢复，不是新写**。

### 1.3 测试资产

`testcase/tests/` 里与 goal / workflow 相关的测试共 **42 文件 / 10,982 行**：

```
test_goal_store.py 575   test_goal_tools.py 558   test_api_goal_router.py 574
test_goal_e2e.py 434     test_goal_workflow_phase3.py 404   test_goal_context.py 378
test_goal_cli.py 360     test_goal_events.py 297   test_goal_hook_p3d.py 309
test_goal_workflow_v052.py 306   test_goal_workflow.py 295   test_workflow_graph_shadow.py 294
test_goal.py 321        test_goal_p3b.py 283    test_workflow_hook.py 274
test_goal_account_usage.py 227  test_goal_scoping.py 205   test_workflow_approval_api.py 346
test_goal_event_bus.py 194   test_goal_models.py 325   test_goal_workflow_v053.py 265
test_workflow_control_api.py 215  test_workflow_ownership.py 262  test_workflow_config.py 222
test_goal_panel.py 243  test_workflow_checkpoint_resume.py 196  test_workflow_sse_stream.py 199
test_goal_workflow_state.py 152  test_workflow_graph_api.py 87   test_workflow_resume_from_store.py 175
test_goal_policy.py 140   test_workflow_monitor_and_skills.py 170   test_dag_view_widget.py 141
test_goal_injection_guard.py 133  test_goal_v2_session_decouple.py 162   test_cli_goal_cmd.py 171
test_goal_evidence_retry_idempotency.py 77   test_tool_display_goal.py 100
test_hypothesis_goal_linking.py 253   test_goal_workflow_v051.py 222
test_goal_workflow_phase3b.py 247   test_goal_workflow_v054.py 191
```

**可恢复总计：66 文件 / 19,417 行**（实现 24 / 8,435 + 测试 42 / 10,982）。

### 1.4 kernel 侧一直为 goal 工作流引擎留着接口

`src/agentnodes/kernel/swarm/types.py:94-127` 的 `SwarmPreset` 至今保留：

```python
goal: dict[str, Any] | None = None          # ← goal 工作流的判据
completion: dict[str, Any] | None = None     # ← 完成判定配置
branches: list[dict[str, Any]] = field(default_factory=list)
budget_token / budget_turn / budget_time_seconds
```

docstring 明写：「The goal workflow loader still produces the richer
`GoalWorkflowConfig` for YAML parsing, but converts it to `SwarmPreset`
before passing to SwarmRuntime.」

**结论：kernel 侧从未拆除 goal 的接入点，缺的只有 L2 那个包本身。**

### 1.5 当前 `resolve("goal.*")` 的实测行为

| 调用点 | kind | 行为 |
|---|---|---|
| `kernel/agent/loop.py:100` | `goal.store` | **无 try/except** → 异常直接冒出 |
| `kernel/agent/context_injector.py:120` | `goal.context` | `except Exception` + `logger.debug` → **静默降级** |
| `kernel/agent/context_injector.py:228` | `goal.context` | 同上 |
| `apps/builtin_tools/goal_tools.py:30/131/224/523` | `goal.store`/`goal.context`/`goal.models` | 工具被调用时抛 `CapabilityNotFound` |

`tool.goal` kind **仍在 `apps/capabilities.py:187` 的 `CAPABILITIES` 表里**
（`_tool_goal` 工厂在 `:166`），因此：

- 5 个 goal 工具（`CreateGoalTool` / `AddEvidenceTool` / `CompleteGoalTool` /
  `GetGoalStatusTool` / `ListGoalsTool`）**对外可见**（`/help` 列得出），
  **但每次调用必然失败**；
- `GoalContextInjector`（`context_injector.py:95`）与
  `GoalContinuationInjector`（`:173`）**永远走不到自己的逻辑**，
  因为它们唯一的动作就是 `resolve("goal.context")`。

> `apps/capabilities.py:59-61` 记录了当时的意图：「kind 消失后运行时优雅降级为
> no-op，不影响框架其他功能」——**降级本身是设计好的**，但「对外宣告 5 个必然
> 报错的工具」不是。

### 1.6 当前层体量（2026-11-05 实测）

```
L1  108 文件  23,024 行      L3  189 文件  36,402 行
L2   15 文件   4,489 行      L4   59 文件  10,030 行
                        总计  371 文件  73,945 行
上行违规：L2→L1 · L3→L1 · L3→L2 · L4→* 全为 0
```

---

## 2. 三个子系统的历史关系

### 2.1 演进链

```
① apps/autoresearch.py          独立 9-Agent 串行链（1,364 行）
        ↓  改造成挂在 goal 的 DAG 引擎上
② apps/goal/workflow.py         GoalWorkflowRunner —— DAG 研究编排引擎
        ↓  study 域从 goal 域里「切出去」
③ apps/study/                   runner / phase_engine / rounds / artifacts
                                / review_loop / budget / method/* / graph …
        ↓  批次 2：goal 被删，其两块被搬进 study
④ apps/study/scoreboard.py      ← goal/scoreboard.py
   apps/study/objective_policy.py ← goal/policy.py
        ↓  批次 3：study + autoresearch 被直接删除
⑤ 现状                          goal 在档案 / study 与 autoresearch 彻底消失
```

**证据链**：

| 环节 | 证据 |
|---|---|
| ①→② | `kernel/swarm/presets/goal_autoresearch_9agent.yaml:4`：「复用 autoresearch.py 的 9-agent 串行链，**通过 Goal Workflow DAG 引擎执行**」|
| ②→③ | 批次 2（`08303f04`）**新增**了 `apps/study/scoreboard.py` 与 `apps/study/objective_policy.py` —— 即 goal 的两块被搬到 study |
| ③→④ | 批次 3（`411a7ee7`）删除 study 全部 37 文件 + `apps/autoresearch.py` |
| goal 吸收了 study 的能力 | `apps/goal/workflow.py`（981 行）自带 `GoalWorkflowRunner`（start / resume / pause / checkpoint / start_sub_workflow / 证据收集 / Agent 事件桥）、`apps/goal/workflow_hook.py`（452 行）SwarmRuntime 钩子、`apps/goal/expression_evaluator.py`（255 行）分支 DSL、`apps/goal/completion_strategy.py`（170 行）完成判定、`apps/goal/checkpoint_store.py`（174 行）崩溃恢复 |

### 2.2 结论

**goal 是整合后的新版，study 是被切出去的旧版。** 合并 = 保留 goal 的整合形态，
不复活 study 的拆分形态。

autoresearch 则进一步退化为**一份拓扑配置**：

| autoresearch 的内容 | 归属 |
|---|---|
| 9-Agent 拓扑（Python 版 `workflow_config.py::build_autoresearch_workflow_config()`）| 与 YAML 重复，可删（见 §6 R3）|
| 9-Agent 拓扑（YAML 版 `goal_autoresearch_9agent.yaml`，138 行）| 唯一事实源 |
| DAG 执行引擎 | `apps/goal/workflow.py` |
| 账本 / 判据 / 完成判定 | `apps/goal/store.py` · `apps/goal/models.py` · `apps/goal/completion_strategy.py` |

---

## 3. 自我进化闭环的信号缺口

### 3.1 RSI 闭环的现状

`kernel/evolution/` 共 **11 文件 / 1,930 行**，机制完整：

```
holdout.py            只读验证轨迹集（H5）+ 确定性抽样
genome_proposals.py   GenomeProposal / Tier 0-1-2 划分 / 状态机
validation_gate.py    验证、±20% 阈值、连续 3 次失败回滚
skill_memory.py       装配期解析生效的 Tier 1 配置
evolution_agent.py    只读证据 → 单个提案
golden_eval.py / holdout_replay.py / isolated_replay.py / default_policy.py / tools.py
```

装配根 `src/agentnodes/interfaces/rsi_wiring.py:211-214`：

```python
agent = EvolutionAgent.with_capabilities(
    journal=source, scoreboard=source, validation=source, holdout=source,
    policy=default_policy,
)
```

**四个源全部指向同一个对象** `EventStoreReportSource(store)`（`:197`）。

### 3.2 四个源的实测粒度

| 源 | 实现位置 | 数据来源 | 粒度 |
|---|---|---|---|
| `journal` | `kernel/evolution/holdout.py:220` | `run.answer` / `run.settled` / `run.failed` / **`study.round_done`** | **粗**：一轮跑完没、失败没 |
| `scoreboard` | `holdout.py:233` | `assignment.report` 按 status 计数 | **粗**：只有 `counts` / `total` / `ready_rate` |
| `validation` | `holdout.py:247` | `genome.rollback` / `genome.activated` / `rsi.validated` | 够用 |
| `holdout` | `holdout.py` | 事件流派生的轨迹 | 够用 |

**两处具体的缺口**：

1. **`study.round_done` 永不发射** —— 全仓库检索只有 `holdout.py:222` 一处提到它
   （study 已在批次 3 删除）。journal 的四个事件 kind 里有一个恒空。
2. **`run.*` 事件确实在发射**，但是 gateway 层的粗粒度生命周期：
   `kernel/gateway/session_controller.py:285/286/307/330/347/354`
   （`run.failed` / `run.settled` / `run.answer`）。

### 3.3 goal 侧正好补上这两个缺口

`testcase/golden/apps/goal/store.py` 的 journal API：

```
append_journal_entry          list_journal_entries        get_latest_journal_entry
build_journal_context         check_novelty               check_regression
archive_rejected_edit         fill_journal_attribution    fill_journal_prediction_outcome
```

**每条 journal 条目带 `predicted_affected`（预测影响）、`outcome`（实际结果）、
`round_num`** —— 这正是「杠杆有效性」统计需要的字段。

`testcase/golden/apps/goal/scoreboard.py` 的 `LeverScoreboard`：

```
update / get_scoreboard / get_best_lever / is_lever_fatigued / build_scoreboard_context
LeverStats: raw_precision / posterior_mean / effective_n
```

其 docstring 直言：**「tracks which modification types are most effective」**
—— **它就是 evolution 需要的 scoreboard**，只是当年住在 goal 里，
批次 2 被搬到 study，批次 3 又随 study 一起被删。

> **结论**：RSI 闭环原本的证据源就在 goal 里，被减法批次切断。
> 恢复 goal = 把自我进化的细粒度信号接回去。

### 3.4 为什么「怎么接」有明确答案

`kernel/evolution/evolution_agent.py:42-55` 已经声明了两个 Protocol：

```python
class JournalSource(Protocol):
    def read_journal(self, *, limit: int = 50) -> list[dict[str, Any]]: ...

class ScoreboardSource(Protocol):
    def read_scoreboard(self) -> dict[str, Any]: ...
```

**端口声明在内侧（kernel/L3），实现在外侧（apps/L2）** —— 方向天然正确。
只需在装配根把这两个槽位从 `EventStoreReportSource` 换成 goal 的实现，
**L3 一行不改**。这与项目已用过的 `ProjectionSink`（`kernel/agent/projection_port.py`）
和 `schema_port`（`foundation/storage/schema_port.py`）是同一个模式。

---

## 4. chat 耦合面

### 4.1 已经预埋好的 5 处

| 接点 | 位置 | 状态 |
|---|---|---|
| chat 循环参数 | `kernel/agent/chat_loop.py:41` `enable_goal_injection: bool = False` | ✅ 已有 |
| API 会话开启 goal 注入 | `interfaces/api/session/service.py:942` `enable_goal_injection=True,  # long-horizon: continue until goal criteria met` | ✅ 已有 |
| TUI 续跑开关 | `interfaces/cli/tui/session.py:371` `toggle_goal_continuation()`（ctrl+G，keybindings.py:27）| ✅ 已有 |
| goal 工具集 | `apps/capabilities.py:187` `tool.goal` → `_tool_goal` | ✅ 已注册（但必然失败）|
| 上下文注入器 | `kernel/agent/context_injector.py:95/173` | ✅ 已实现（但必然空转）|

### 4.2 缺的 3 处 —— 都在档案里

| 缺口 | 档案位置 | 行 |
|---|---|---:|
| `/goal` slash 命令 | `testcase/golden/interfaces/cli/slash_goal.py` | 879 |
| goal / workflow API 路由 | `testcase/golden/interfaces/routers/{goal,workflow}.py` | 288 + 731 |
| TUI DAG 视图与工作流 worker | `testcase/golden/interfaces/tui/{dag_view,workflow_worker}.py` | 177 + 213 |

### 4.3 chat 驱动 goal 的完整闭环（恢复后自动成立）

```
用户提需求
  → LLM 调 create_goal 建目标 + 验收判据          （tool.goal）
  → GoalContextInjector 每轮把 <current-research-goal> 块注入 task
                                                  （kernel/agent/context_injector.py:95）
  → LLM 干活后调 add_goal_evidence 挂到具体判据    （tool.goal）
  → GoalContinuationInjector 判断必选判据未覆盖则强制续跑
                                                  （context_injector.py:173）
  → 全覆盖后审计并置为 complete                    （GoalStore.update_status）
```

**这条链现在只差 store / models / context 三个实现。**

---

## 5. 合并后的目标形态

### 5.1 目录结构

```
src/agentnodes/apps/goal/                    ← L2，一个能力族
├── __init__.py                    106   能力门面
├── models.py                      200   GoalStatus/RiskTier/GoalRecord/GoalCriterion/
│                                       EvidenceInput/EvidenceRecord/AuditRow/JournalEntry
├── store.py                      1,773   GoalStore —— 目标/判据/证据/审计/journal/子目标
├── store_utils.py                  69   GoalStore 纯函数助手
├── context.py                     190   ← goal.context 的实现
├── policy.py                       48   研究目标边界（拒绝实盘执行）
├── scoreboard.py                  206   LeverScoreboard ← RSI 证据源
├── validator_registry.py          105   ValidatorRegistry
├── completion_strategy.py         170   Auto/Lite/Manual 完成判定
├── expression_evaluator.py        255   分支条件 DSL
├── workflow.py                    981   GoalWorkflowRunner —— DAG 编排引擎
├── workflow_config.py             470   YAML 拓扑加载
├── workflow_hook.py               452   SwarmRuntime 钩子（证据收集 + 自动完成）
├── checkpoint_store.py            174   崩溃恢复
├── event_bus.py / events.py       287   工作流状态事件 + SSE 载荷
├── dag_renderer.py                165   ASCII DAG 渲染
├── templates.py                   125   目标模板
├── cli.py                         371   goal 命令组
├── claims.py                      NEW   summarize_prediction_accuracy（重建）
├── executors.py                   NEW   meets_metric_targets / run_backtest_script 钩子
└── presets/                       NEW   拓扑 YAML（autoresearch 在这里）
```

### 5.2 capability kind 注册表

| kind | factory 返回 | kernel 消费点 | 现状 |
|---|---|---|---|
| `goal.store` | `GoalStore` **类** | `loop.py:100` → `GoalStore()` | 缺失 |
| `goal.models` | models 模块 | `goal_tools.py:224/523` → `.EvidenceInput` / `.GoalStatus` | 缺失 |
| `goal.context` | context 模块 | `context_injector.py:120/228` → `.get_current_goal_context` 等 | 缺失 |
| `goal.workflow` | workflow 模块（新增） | DAG 编排入口（CLI / API 用）| 缺失 |

`tool.goal` 已在表中，**无需改动**。

### 5.3 kernel 零改动的论证

现有三处 `resolve("goal.*")` 的 kind 名与上表逐字对上；
`SwarmPreset` 的 `goal`/`completion`/`branches`/`budget_*` 字段齐备；
`kernel/swarm/types.py` 的 `AgentCall` 支持 `context` 里带
`executor_type` / `python_function`（正是 `GoalAgentConfig` 需要的）。

**因此 R1 不需要碰 `kernel/` 任何一行。**

### 5.4 4 个悬空引用（被删代码留下的洞）

| 引用 | 位置 | 失败后果 | 处置 |
|---|---|---|---|
| `apps.study.method.claims.summarize_prediction_accuracy` | `store.py:1649` | **硬依赖**，缺则 `build_journal_context()` 抛 ImportError | **重建**（~50 行，字段从调用点反推：`n_predictions` / `n_validated` / `direction_hit_rate` / `mean_abs_error`）|
| `apps.study.runner.meets_metric_targets` | `workflow.py:402` | 在 try/except 内 → 降级为 warning | **重建**（~40 行）|
| `apps.backtest.run_backtest_script` | `workflow.py:425` | 在 try/except 内 → 降级 | **做成可插拔钩子**：`apps/backtest` 已随批次 4 删除且未进档案，命中时抛明确的「未注册」错误而非 ModuleNotFoundError |
| `apps.hypothesis.HypothesisRegistry` | `store.py:1037` | 在 try/except 内 → 可选 | **本轮不恢复**（`apps/hypothesis` 6 文件 / 1,459 行留在档案，后续按需）|

### 5.5 presets 的落位

`kernel/swarm/presets/` 现有 6 个 `goal_*.yaml`：

```
goal_autoresearch_9agent.yaml (138)   goal_factor_research.yaml (55)
goal_market_analysis.yaml (45)        goal_portfolio_review.yaml (55)
goal_risk_assessment.yaml (55)         goal_strategy_review.yaml (65)
```

preset 引用的 9 个提示词**全部已在包里**
（`src/agentnodes/templates/.prompts/{researcher,data_quality,factor_analyst,
strategist,portfolio_construction,risk_controller,attribution_analyst,
anti_overfit_analyst,backtest_diagnostics}.md`）；
引用的工具（`factor_analysis` / `get_market_data` / `compute_factor` /
`run_backtest` / `list_data_sources` / `search_symbol`）在
`apps/builtin_tools/` 有对应实现 —— **需实测对齐注册名**。

---

## 6. 三阶段方案

> 每阶段独立验收：门禁 + 架构图回写 + 全量回归 + 收集数差分。

### R1 · 恢复 `apps/goal/`，让能力闭环成立（L2）

| 步 | 动作 | 备注 |
|---|---|---|
| 1 | `git mv testcase/golden/apps/goal → src/agentnodes/apps/goal` | 19 文件 / 6,147 行，`git mv` 保留历史 |
| 2 | import 改写 `strategy_research.` → `agentnodes.` | 19 文件；注意 `workflow_config.py:28` 的 `import ...kernel.swarm as _swarm_mod` 与 `workflow.py:21` 的 `kernel.swarm.types` |
| 3 | 新增 `apps/goal/claims.py` | `summarize_prediction_accuracy`（~50 行）|
| 4 | 新增 `apps/goal/executors.py` | `meets_metric_targets` + `run_backtest_script` 钩子（~80 行）|
| 5 | `apps/capabilities.py` 注册 4 个 kind | `goal.store` / `goal.models` / `goal.context` / `goal.workflow` |
| 6 | `scripts/arch/layer_matrix.py` 增 `agentnodes.apps.goal: L2` | |
| 7 | 修正 `apps/capabilities.py:63-64` 关于 study 归档位置的错误注释 | |
| 8 | 测试搬回 | `testcase/tests/test_goal_*.py`（26 个）→ `tests/`，import 改写 |

**R1 内部建议分两个 commit**：`ledger`（models/store/context/policy）
与 `orchestration`（workflow/config/hook/expression/completion），
各自跑一次定向回归 —— 6,147 行一次搬完，出问题难定位。

**风险**：`store.py` 有 40+ 方法与多张表；DB 路径需与 `~/.agentnodes` 统一
（`ticket_store.py:18` 提到「与 StudyStore 同库（goals.db，`AGENTNODES_GOAL_DB_PATH`）」）。

### R2 · chat 接线（恢复 L1 接口层）

| 步 | 动作 | 档案来源 |
|---|---|---|
| 1 | `/goal` slash 命令 | `testcase/golden/interfaces/cli/slash_goal.py`（879）→ `interfaces/cli/commands/` |
| 2 | goal + workflow API 路由 | `testcase/golden/interfaces/routers/{goal,workflow}.py`（288+731）→ `interfaces/api/routers/` |
| 3 | TUI DAG 视图 + 工作流 worker | `testcase/golden/interfaces/tui/{dag_view,workflow_worker}.py`（177+213）→ `interfaces/cli/tui/` |
| 4 | Web SSE | `apps/goal/events.py::build_goal_updated_payload`（108）→ 接进 `interfaces/api/routers/chat.py` 现有 SSE 流 |
| 5 | goal schema | `interfaces/api/schemas/study.py`（400 行 / 39 符号）**仍归档** —— study 不再是独立 API 域；其 `GoalSnapshotModel` 概念并入新 goal schema |
| 6 | 验证 chat 侧行为 | `chat_loop.py:41` / `service.py:942` 的 `enable_goal_injection` 已存在，R1 完成后自动生效 —— 本阶段只需验证行为正确 |

### R3 · autoresearch preset 化 + RSI 证据源接入

| 步 | 动作 | 备注 |
|---|---|---|
| 1 | 6 个 `goal_*.yaml` 从 `kernel/swarm/presets/` → `apps/goal/presets/` | `workflow_config.py` 的 `_PRESETS_DIR` 改为：先 `apps/goal/presets/` → 再兼容 `kernel/swarm/presets/` → 再 `~/.agentnodes/workflows/` |
| 2 | **删掉 `build_autoresearch_workflow_config()`**（~90 行）| YAML 成唯一事实源。`GoalWorkflowConfig` 的字段与 YAML schema 1:1 对应（`goal`/`agents[]`/`dag`/`completion`/`branches`/`budget_*`），无信息损失 |
| 3 | 新增 `apps/goal/journal_source.py` | 实现 `read_journal(limit)` → `GoalStore.list_journal_entries` |
| 4 | 新增 `apps/goal/scoreboard_source.py` | 实现 `read_scoreboard()` → `LeverScoreboard.get_scoreboard` |
| 5 | `interfaces/rsi_wiring.py:211` 换槽位 | `journal=goal_journal, scoreboard=goal_scoreboard`；**L3 零改动** |
| 6 | `kernel/evolution/holdout.py:222` 去掉死 kind | 删 `study.round_done`（永不发射）|
| 7 | （可选，后续）`kernel/evolution/default_policy.py` 阈值参数化 | 失败率 > 0.5 → 窄化 tool_set 是硬编码；减法路线图批次 8 的遗留项 |

---

## 7. 原批次处置的修正

| 原批次 | 原判定 | 现在的处置 |
|---|---|---|
| **D1** `tool.goal` 死能力 | 删除 | **反转 → 实现**（R1）|
| **A2** `foundation/errors.py` | 删 `BacktestError` / `GoalError` | `BacktestError` 仍删（回测域已删）；**`GoalError` 保留/恢复**（goal 回来了）|
| **D2** `interfaces/api/schemas/study.py` | 归档 | **仍归档**（study 不再是独立 API 域），但 `GoalSnapshotModel` 概念并入新 goal schema |
| **B1** `kernel/workflow/validator.py` + `grounding.py` | 清理研究语义 | **不变**，可在 R1 之后并行做 |

---

## 8. 风险与已知代价

| # | 风险 | 量级 | 缓解 |
|---|---|---|---|
| 1 | **量化味回流**：`default_goal_criteria` 默认判据提到 "symbol universe" / "market or benchmark evidence"；9-Agent preset 引用 factor/data/backtest 工具 | 中 | 判据文本是数据可改、preset 是数据可换、工具本来就已在 L2 活着。**这是「平台 + 插件」定位下的插件内容，不是内核污染**——`kernel/` 一行不改 |
| 2 | **6,147 行单批次恢复**，回归出问题难定位 | 中 | R1 内部分两个 commit，各自定向回归 |
| 3 | **DB 路径冲突**：`GoalStore` 自带 `_init_db`，与 `~/.agentnodes` 下现有 `foundation/storage` 的表可能重名 | 中 | 恢复前先核对 `AGENTNODES_GOAL_DB_PATH` 与 `session_paths.py::resolve_session_db_path` 的落点 |
| 4 | **`schema_port` 退化 DDL 交互**：`foundation/storage/schema_port.py` 是 session DB 的退化 schema，goal store 自带建表 | 低 | 实测确认两者不共用连接 |
| 5 | **42 个测试文件搬回**，可能有 import 之外的 API 漂移 | 中 | 搬回后先跑 `--collect-only` 看收集期错误，再跑全量 |
| 6 | **RSI 证据源换槽位后行为变化**：journal 从粗粒度换成细粒度，`default_policy` 的启发式阈值可能给出不同的提案 | 中 | R3 单独验证；`default_policy` 的参数化同时做 |

---

## 9. 未做的事（明确记录）

| 项 | 原因 |
|---|---|
| **不把 goal 的账本模型提成 L3 公共基类给 evolution 共用** | 跨层大重构，会动到 `kernel/evolution` 的 H3（Tier 0 冻结的三重结构性保证：`genome_proposals.py` 构造时拒 + `validation_gate.py` 反序列化再拒 + `skill_memory.py` 丢弃 Tier 0 键）。风险远大于收益。**正确接法是端口化注入**：L3 声明 Protocol、L2 实现、装配根注入 |
| **不复活 `apps/study` 的拆分形态** | study 是被 goal 整合掉的旧版；复活它是把已经解决的问题再引入一遍 |
| **不恢复 `apps/autoresearch.py`（1,364 行）** | 源码已被批次 3 删除且未进档案，不可恢复；其拓扑已由 YAML 承载 |
| **不改 `kernel/` 任何一行** | R1–R3 全部靠既有 capability 端口与装配根注入完成 |
| **本轮不执行任何改动** | 本文只做记录与设计，待架构讨论结论出来后另行排期 |

---

## 附：证据索引

| 结论 | 证据位置 |
|---|---|
| goal 在档案、study/autoresearch 被删 | `git show --name-status 08303f04` / `411a7ee7` |
| study 删除 37 文件 / 11,274 行 | `git show 411a7ee7^:<file>` 逐文件行数求和 |
| autoresearch.py 1,364 行 | `git show 411a7ee7^:src/strategy_research/apps/autoresearch.py` |
| L1 接口层在档案 | `git show --name-status 08303f04` 的 `R100` 记录 |
| SwarmPreset 保留 goal 字段 | `src/agentnodes/kernel/swarm/types.py:94-127` |
| resolve 三处必然抛错 | `kernel/agent/loop.py:100`、`kernel/agent/context_injector.py:120/228`、`apps/builtin_tools/goal_tools.py:30/131/224/523` |
| `tool.goal` 已注册 | `src/agentnodes/apps/capabilities.py:166/187` |
| RSI 四源同指 | `src/agentnodes/interfaces/rsi_wiring.py:197/211-214` |
| `study.round_done` 永空 | 全仓库检索只有 `kernel/evolution/holdout.py:222` |
| `run.*` 事件在发射 | `src/agentnodes/kernel/gateway/session_controller.py:285/286/307/330/347/354` |
| journal/scoreboard API 在 goal | `testcase/golden/apps/goal/store.py`、`scoreboard.py` |
| evolution 已声明两个 Protocol | `kernel/evolution/evolution_agent.py:42-55` |
| 批次 2 搬走 scoreboard/policy | `git show --name-status 08303f04` 中 `A apps/study/{scoreboard,objective_policy}.py` |
| 批次 7 回滚 | `git show bd3f6009` |
