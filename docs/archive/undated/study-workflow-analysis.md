# Study 工作流梳理

## 当前状态：两套并行执行系统

### 系统 1：Study（当前活跃）

```
用户: /study start "研究动量因子" --strategy blue_chip_momentum
  │
  ▼
chat.py → GoalStore.replace_goal() + StudyStore.create_study(QUEUED)
  │
  ▼
scheduler.submit() → _session_loop() → _run_one_study()
  │  等待 chat slot → claim slot → mark RUNNING
  ▼
executor.run() → _run_loop()
  │
  ├── ROUND 1:
  │   ├── asyncio.to_thread(_run_one_round)
  │   │     │
  │   │     ▼
  │   │   run_research_round()  ← 硬编码 9-agent 串行链
  │   │     ├── 1. read_current_state()  → strategy.py + results.tsv
  │   │     ├── 2. spawn("researcher")          → LLM 或 stub
  │   │     ├── 3. spawn("data_quality")        → LLM 或 stub
  │   │     ├── 4. spawn("factor_analyst")      → LLM 或 stub
  │   │     ├── 5. spawn("strategist")          → LLM 或 stub
  │   │     ├── 6. spawn("portfolio_construction") → LLM 或 stub
  │   │     ├── 7. run_backtest_script()        → python strategy.py
  │   │     ├── 8. spawn("risk_controller")     → LLM 或 stub
  │   │     ├── 9. spawn("attribution_analyst") → LLM 或 stub
  │   │     ├── 10. spawn("anti_overfit_analyst") → LLM 或 stub
  │   │     ├── 11. spawn("backtest_diagnostics") → LLM 或 stub
  │   │     └── 12. decide(metrics, aoa_verdict) → keep/discard
  │   │
  │   ├── 记账（budget, metrics, heartbeat）
  │   ├── 检查终止条件：targets_met / budget / stagnation / max_rounds
  │   └── cooldown sleep
  │
  ├── ROUND 2: ...
  └── 完成 → monitor 后台任务（如果设置了 interval）
```

**特点**：
- 9-agent 流水线**硬编码**在 `autoresearch.py` 中
- Agent 间数据流**隐式**（上一个 agent 的输出 dict 传给下一个）
- 无 DAG 支持，无 YAML 配置，无并行
- Agent 通过 `spawn_agent()` → `AgentLoop`（LLM）或 stub 执行
- 决策通过 `strategy_acceptance.decide()`（硬阈值 + LLM verdict）

### 系统 2：Goal Workflow（已构建但未接入）

```
用户: /goal workflow start goal_factor_research
  │
  ▼
GoalWorkflowRunner.start()
  │  创建 goal → 构建 GoalWorkflowHook → 转换为 SwarmPreset
  ▼
SwarmRuntime.execute(preset, workspace, task, hooks=[GoalWorkflowHook])
  │
  ├── topological_layers(dag) → 计算执行层
  │
  ├── Layer 0: [researcher]  ← 并行执行同层 agent
  │   ├── ThreadPoolExecutor → _execute_agent("researcher")
  │   │     └── WorkflowController.execute_agent()
  │   │           └── SwarmWorker.run(task)  ← mini-ReAct + tools
  │   └── GoalWorkflowHook.on_agent_complete() → 证据收集
  │
  ├── Layer 1: [data_quality]
  │   └── ...
  │
  ├── Layer N: [backtest_diagnostics]
  │   └── ...
  │
  ├── _apply_branches() → 条件跳过/重试
  └── GoalWorkflowHook.should_stop() → 提前终止
```

**特点**：
- YAML 驱动的 DAG（36 个预设配置）
- 支持层内并行（`ThreadPoolExecutor`）
- 支持条件分支（`skip`/`retry`，`redirect` 未实现）
- 支持 checkpoint/resume
- Agent 通过 `SwarmWorker`（mini-ReAct loop with tools）执行
- 证据通过 `GoalWorkflowHook` 自动收集
- 有 API、CLI、前端（部分）

## 两套系统的对比

| 维度 | Study（活跃） | Goal Workflow（休眠） |
|------|-------------|---------------------|
| 入口 | `/study start` | `/goal workflow start` |
| 执行引擎 | `AutoresearchExecutor` | `SwarmRuntime` |
| Agent 执行 | `AgentLoop`（完整 ReAct） | `SwarmWorker`（mini-ReAct） |
| Pipeline 定义 | 硬编码（Python） | YAML DAG |
| 并行 | 无（串行链） | 层内并行 |
| 条件分支 | 无 | skip/retry（redirect 未实现） |
| Checkpoint | 无 | 有 |
| 证据收集 | 手动（`_study_append_evidence`） | 自动（`GoalWorkflowHook`） |
| API | `/api/study/*` | `/api/goal/workflow/*` |
| 前端 | StudyTab（完整） | Workflow store（部分） |

## 核心问题

**两套系统做同一件事，但互不连接**：

1. Study 的 9-agent 流水线可以被 Goal Workflow 的 DAG 引擎替代
2. Goal Workflow 的证据收集可以替代 Study 的手动证据追加
3. Goal Workflow 的 checkpoint 可以替代 Study 的无状态重跑
4. 两套系统各自有自己的 API、前端、状态管理

## 设计决策

根据框架哲学（workflow = DAG），应该将 Study 的执行迁移到 Goal Workflow 的 DAG 引擎上。

**选项 A：Study executor 委托给 SwarmRuntime**
- `AutoresearchExecutor._run_one_round()` 内部调用 `SwarmRuntime.execute()`
- 保留 Study 的调度器/槽位管理
- 最小改动，渐进迁移

**选项 B：统一为 Goal Workflow**
- `/study start` 改为调用 `GoalWorkflowRunner.start()`
- Study 的 scheduler + executor 被 Goal Workflow 的 runner 替代
- 大改动，但最终架构更清晰

**选项 C：Study 保留，Goal Workflow 作为高级模式**
- Study 用于简单场景（9-agent 固定流水线）
- Goal Workflow 用于复杂场景（自定义 DAG）
- 两套系统并存

## 待确认

1. 选哪个选项？
2. 9-agent 流水线是否需要保留为"默认 DAG"？
3. 是否需要支持用户自定义 DAG（YAML 配置）？
4. Study 的 scheduler/槽位管理是否需要保留？

---

*基于以上梳理，规划 Study 工作流的 DAG 化方案。*
