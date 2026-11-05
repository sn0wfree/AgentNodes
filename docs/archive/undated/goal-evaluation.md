# Goal 子系统评估报告

> 调研时间：2026-08-02。本文档系统化梳理 goal 功能（Ledger + Workflow + Agent + 集成层）的实现与执行现状，
> 记录发现的死代码/未接线功能/配置字段问题，供后续修复排期。

## 一、架构全貌（三层）

```
┌─ Goal Ledger 层 ── GoalStore(SQLite goals.db) + GoalStatus 生命周期 + 证据/标准/审计
│   models.py / store.py / policy.py / context.py / completion_strategy.py
│
├─ Workflow 层 ── GoalWorkflowRunner → SwarmRuntime.execute (DAG 逐层并行)
│   workflow.py / workflow_config.py / workflow_hook.py / checkpoint_store.py
│   5 个内置 preset YAML (goal_*.yaml)
│
├─ Agent 层 ── WorkflowController.execute_agent → SwarmWorker (mini-ReAct, 20 iters)
│   (不走 role_factory.build_agent_loop；那套是 autoresearch 流水线)
│
└─ 集成层 ── 5 个 goal 工具 / API 路由(goal.py, workflow.py SSE) / TUI GoalPanel /
             React GoalTab / AgentLoop 目标上下文注入 + 续跑注入
```

### 数据模型要点

- `GoalStatus` 12 态：active/paused/waiting_user/needs_refresh/insufficient_evidence/
  compliance_blocked/blocked/budget_limited/usage_limited/complete/cancelled/superseded
- `GoalRecord`：goal_id, session_id, status, objective, ui_summary, source, protocol,
  risk_tier, token/turn/time budgets + used, progress_percent, parent_goal_id, workflow_id
- 每 goal 1+ 必选标准（`goal_criteria`，`protocol_step` 标记步骤）、N 条证据（`goal_evidence`，
  可关联 criterion_id/claim_id）、thesis claim 自动创建、完成审计（`goal_audits`）
- `progress_percent` = 必选标准覆盖比例 `covered/required_total * 100`，clamp [0,100]，
  每次变更后重算；零必选标准返回 100
- 存储：`~/.agentnodes/goals.db`（env `AGENTNODES_GOAL_DB_PATH` 可覆盖）；
  5 表 + WAL + `threading.RLock` + `BEGIN IMMEDIATE` 写事务；
  partial unique index `idx_goals_one_current_per_session` 强制每会话单活跃目标

### 完成策略（CompletionStrategy 模式）

| 模式 | 行为 |
|------|------|
| `auto`（默认） | 每个必选标准生成 `AuditRow(satisfied)` → `update_status(COMPLETE, audit)`；要求 verified 证据 |
| `lite` | `complete_lite`：仅需每个必选标准有 ≥1 条关联证据，无需 audit/verified |
| `manual` | no-op，等待用户显式 `/goal complete` |

### 上下文注入（AgentLoop）

- 每次 run 前注入 `<current-research-goal>` 块（`_get_goal_context`）
- 每次无工具调用的 LLM 回复后检查续跑（`_check_goal_continuation`）：status ∈
  {active, needs_refresh, insufficient_evidence} 时追加 `<goal-continuation>` 提示
- TUI Ctrl+G 可暂停续跑

### 测试现状

- 核心 goal 测试（tools/e2e/store/models/policy/context/panel）：**196 passed**
- `tests/test_api_goal_router.py`：**15 failed（预存）**——stub `auth_header()` 用未签名
  base64 token，而 `api/auth_tokens.py` 已改 HMAC-SHA256 签名。goal 路由真实代码无回归保护。

---

## 二、发现的问题（按严重度）

### 🔴 P0 — 死代码 / 未接线功能（文档宣称但实际不生效）

| # | 问题 | 证据 | 影响 |
|---|------|------|------|
| 1 | **分支条件 DSL 完全未接线** | `expression_evaluator.py` 声明 "NOT wired"；`evaluate_condition` 零生产调用；`SwarmRuntime` 不读 `preset.branches` | cookbook 教的 `branches:` 配置**无效**；skip/retry/redirect 不会触发 |
| 2 | **checkpoint 无法真正恢复执行** | `_layer_results` 运行期从未写入（hook 初始化后无回调写它）→ 存盘 `layer_results={}`；`resume_from_checkpoint` 只恢复 state 供查看，`start()` 仍新建 goal | `/goal checkpoint resume` 名不副实；断点续跑未实现 |
| 3 | **前端 goal_* SSE 事件链路是死的** | `webui/frontend/src/hooks/sse/metaHandlers.ts` 明说 "TODO(feature): dead chain end-to-end today. No backend emitter"；后端无 `emit(goal_...)` | GoalTab/CriteriaList/GoalTimeline **只能渲染空态**；前端 goal 面板仅靠 `/state` 回填存活 |
| 4 | **子工作流取消检测缺失** | `start_sub_workflow` 的 `GoalWorkflowHook(...)` 未传 `runner=self`（`workflow.py:499`） | 子工作流无法响应取消 |
| 5 | **`get_progress` 字段注水** | `current_layer`/`agents_completed`/`agent_statuses` 运行期从不更新（恒 0/{}） | status 接口给前端假数据 |

### 🟠 P1 — 配置字段被声明但不生效

| # | 字段 | 现状 |
|---|------|------|
| 6 | `GoalAgentConfig.timeout/max_retries` | 打包进 `AgentCall.context` 但 SwarmWorker 路径不读（用 `ControllerConfig.timeout_seconds`）；retry 只在遗留 `execute_round` |
| 7 | `GoalAgentConfig.input_from` | 被 DAG 边取代，永远不读 |
| 8 | `CompletionConfig.require_all_evidence/auto_audit` | 序列化进 preset 但零读取；覆盖检查恒"要求全部" |
| 9 | API `/goal/start` 的 `market` 参数 | 接受但丢弃（代码 TODO，`goal.py:17-19`） |
| 10 | API `/goal/evidence` 的 `hypothesis_id` | 不转发到 `EvidenceInput` |

### 🟡 P2 — 测试 / 文档债

| # | 问题 |
|---|------|
| 11 | `test_api_goal_router.py` **15 个失败（预存）**：auth fixture 用未签名 token，goal 路由无回归保护 |
| 12 | 文档 vs 现实：cookbook 教 `branches:`/checkpoint resume，但功能未接线（问题 1/2） |
| 13 | `AgentLoopRunner`（build_agent_loop 连接器）dormant 且已损坏（kwargs 签名不匹配）；`GoalWorkflowRunner` 仍收 `agent_runner`/`agent_runner_type`/`runner_kwargs` 废弃参数并发 DeprecationWarning |

---

## 三、评估结论

### 质量不错的部分

- 数据模型完善、12 态生命周期完整、证据/标准/审计全持久化
- 并发安全：RLock + BEGIN IMMEDIATE + WAL + 单会话单活跃目标约束
- 防串会话：`session_id` 注入 + `StaleGoalError` 陈旧写保护
- 完成策略模式 + ValidatorRegistry 设计清晰
- 安全护栏：`reject_live_execution_objective`（中英文交易意图正则）、RiskTier 分级
- UI 三层覆盖：TUI GoalPanel + React GoalTab + SSE workflow 流
- 核心测试扎实（300+ 通过）

### 判定：真问题 vs 文档超前

**真正该修（用户可见影响）**：

| 优先级 | 问题 | 建议方案 |
|--------|------|----------|
| P0-2 | 前端 goal 空态（问题 3） | 二选一：后端补 `goal_*` SSE 事件（符合现有 metaHandlers 契约），或前端轮询 `/api/goal/status` |
| P1-1 | API 测试全挂（问题 11） | 修 `auth_header()` fixture 用 HMAC-SHA256 签名 token，成本低 |
| P1-2 | progress 假数据（问题 5） | hook 回调里真实维护 `current_layer`/`agents_completed`/`agent_statuses`；或从返回去掉 |

**建议收编/接线（标注现状，别宣称）**：

| 优先级 | 问题 | 建议方案 |
|--------|------|----------|
| P0-1 | 分支 DSL 未接线（问题 1） | 接线：在 `GoalWorkflowHook.on_layer_complete` 评估 branches（skip/retry/redirect）+ 填充 `_layer_results`。表达式评估器已实现，只差调用点 |
| P0-3 | checkpoint 无法续跑（问题 2） | 完整断点续跑是较大工程；先文档标注局限（仅恢复状态，非续跑），真续跑另立任务 |

**低优先**：问题 6-10 配置字段（加"未生效"标注）、问题 4（子工作流补 `runner=self`）、
问题 13（清理废弃参数）。

---

## 四、待定决策 → 已定

| 决策项 | 结论 |
|--------|------|
| 1. P0-1 接线分支 DSL | **做**。redirect 语义取 C：**暂不实现**，只上 skip + retry；redirect 在文档标注"未实现" |
| 2. P0-2 前端 goal 面板 | **方案 B（轮询）**：扩展 `/api/goal/status` 返回完整快照（复用 `_build_goal_snapshot`）+ GoalTab 轮询。理由：单一真相源、覆盖三路径、B13 `/state` 已铺路；SSE 需三处发射源同步，死链复燃风险高 |
| 3. 范围 | P0 + P1 + P2 文档同步全部做 |

### 执行状态

- [x] P0-1 分支 DSL 接线：SwarmRuntime 每层后构建 layer_results → evaluate branches → skip/retry（redirect 未实现）；hook 填充 `_layer_results`（顺带修 checkpoint 空数据）
- [x] P1-2 `get_progress` 真实更新（hook 维护 current_layer / agents_completed / agent_statuses）
- [x] P1-3a 子工作流 hook 补 `runner=self`
- [x] P0-2 前端轮询：扩展 `/api/goal/status` + GoalTab 轮询
- [x] P1-1 修 `test_api_goal_router.py` auth fixture（HMAC-SHA256）
- [x] **P1-2 get_progress 前端 agent_statuses 展示接通**：后端 workflow SSE 改命名事件（progress / dag_update）+ 前端 progress handler 同步 agent_statuses
- [x] **P1-8 真断点续跑**：SwarmRuntime 支持 `pre_completed` + `start_layer`；`GoalWorkflowRunner.resume_and_continue()` 复用现有 goal_id 从 checkpoint 继续执行
- [x] P2 文档同步：cookbook branches 标注已生效 + checkpoint 局限；design §5.4 标注已接线

---

## 相关文件

- `src/agentnodes/core/goal/`：models/store/policy/context/completion_strategy/
  validator_registry/expression_evaluator/workflow/workflow_config/workflow_hook/
  checkpoint_store/event_bus/cli/templates/dag_renderer
- `src/agentnodes/core/swarm/runtime.py`：DAG 执行器（branches 未消费）
- `src/agentnodes/api/routers/goal.py`、`api/routers/workflow.py`（SSE）
- `src/agentnodes/api/routers/web_session.py`：`_build_goal_snapshot`/`_shape_goal_for_frontend`
- `src/agentnodes/core/agent/builtin_tools/goal_tools.py`（5 工具）
- `src/agentnodes/core/agent/loop.py`：目标上下文/续跑注入（L474-496, L559-583, L1503-1540）
- `webui/frontend/src/`：stores/goal.ts、components/goal/*、hooks/sse/metaHandlers.ts
- `src/agentnodes/cli/tui/widgets/goal_panel.py`、`cli/commands/slash_goal.py`
- `docs/archive/undated/goal-design.md`、`docs/archive/undated/goal-workflow-design.md`、`docs/archive/undated/goal-workflow-cookbook.md`
