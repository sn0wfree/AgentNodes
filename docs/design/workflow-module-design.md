# 模块化 DAG 工作流设计（Workflow Module Design）

> 状态：已定稿（2026-08-10）
> 前置：`docs/Agentic 设计框架与 Prime Agent 调研报告.md`（§4 Plan-and-Execute）、`docs/archive/undated/agentic-design-application.md`（可用性梳理）
> 决策：不做独立 Plan-and-Execute 引擎，将「动态计划 + 人工确认」能力**合并进现有 DAG 编排**，演进为 Dify 式模块化工作流。

## 一、目标与边界

**做**：
- 可持久化、可自由组合的工作流定义（6 种节点类型）
- 后端执行闭环：段循环（approval 切点 / planner 动态子图 / evaluator 重规划）
- 统一节点输出协议
- 三层可调超参数
- 独立数据库 `workspace/workflows.db`（与 chat session DB 完全隔离）
- 4 个内置工作流模板
- definitions CRUD / start-definition / approve / run 历史 API

**不做（本阶段）**：前端拖拽编辑画布（M3 后置）、goal 集成（后置）、`llm_router` 节点（后续）、definition 镜像进 DB（文件为准）、运行中超参数动态调整（快照语义）。

**不改**：`SwarmRuntime` 执行链核心、`GoalWorkflowRunner` 现有路径、现有 API 端点。

## 二、架构总览

```
templates/workflows/*.json (内置, 只读)   workspace/workflows/*.json (用户, 可写)
        ↓ 加载 + 校验 (workspace 优先)
core/workflow/definition.py: WorkflowDefinition + 校验 + 切割 + to_swarm_preset()
        ↓
core/workflow/executor.py: 段循环 RunState 状态机
  ① 图切割：按 approval 节点切成 [段1][段2]...（approval 不入段）
  ② 段执行：段 → SwarmPreset → SwarmRuntime.execute()（现有引擎，零改动）
     - planner 段输出子图 → 合并进后续段（plan_ 前缀）
     - evaluator 段输出 decision → replan 回环（max_segments 硬上限）
  ③ approval 段间等待：awaiting 持久化 → 审批后 pre_completed 继续
        ↓
core/workflow/store.py: WorkflowStore（SQLite, workspace/workflows.db）
        ↓
统一输出协议（AgentResult 扩展）→ SSE 事件 → 现有前端展示
```

## 三、工作流定义（definition JSON）

### 3.1 格式

```jsonc
// workspace/workflows/<name>.json 或 templates/workflows/<name>.json
{
  "name": "plan_execute_demo",
  "description": "",
  "version": "1.0",
  "budget": {"token": null, "turn": null, "time_seconds": null},
  "llm": {"model": null},
  "params": {
    "llm": {"temperature": null, "max_tokens": null},
    "loop": {"max_iterations": 8},
    "planner": {"max_steps": 6},
    "exec": {"max_segments": 3, "node_timeout_seconds": 300, "node_max_retries": 2},
    "approval": {"timeout": null},
    "summary": {"max_chars": 300}
  },
  "nodes": [
    {"id": "p", "type": "planner",   "label": "生成计划", "config": {"max_steps": 6}},
    {"id": "a", "type": "approval",  "label": "人工确认", "config": {}},
    {"id": "e", "type": "evaluator", "label": "评估结果", "config": {}}
  ],
  "edges": [{"source": "p", "target": "a"}, {"source": "a", "target": "e"}]
}
```

### 3.2 校验规则

- `type` ∈ {llm_agent, planner, evaluator, approval, python, tool}
- id 唯一，匹配 `^[a-zA-Z_][\w-]*$`
- edges 端点必须存在；无环（拓扑排序）；无孤立节点
- `planner` / `evaluator` / `approval` 各最多 1 个
- 必填字段：llm_agent.role、python.function、tool.tool
- params 值域校验（max_steps 3-8、temperature 0-2 等）

## 四、统一输出协议

所有节点输出同一信封（`AgentResult` 扩展 4 个可选字段，`output` 字符串保留兼容）：

```json
{"status": "success|failed|skipped|awaiting",
 "summary": "≤300字", "artifacts": {}, "metrics": {}, "error": null,
 "meta": {"elapsed_s": 0}}
```

## 五、节点类型

| type | 执行体 | config | 输出 |
|---|---|---|---|
| `llm_agent` | `run_agent_via_llm(role, ...)`（role_factory.py） | role、prompt_text?、tools?（覆盖白名单）、max_iterations | summary=answer 截断 |
| `planner` | 同上（role=planner）+ `StructuredOutputParser` 解析 + 1 重试 + 5 步 fallback | max_steps(3-8) | artifacts.plan=子图 |
| `evaluator` | 同上（role=evaluator）+ 规则层兜底 | — | artifacts.decision={verdict: continue/replan/stop, reason} |
| `approval` | 不执行，图切点 | timeout（null=永久） | approved / edits |
| `python` | `PythonExecutor` 注册函数（workflow/executors.py:27） | function、params | 函数输出封装 |
| `tool` | 注册工具调用（走 python_executor 路径） | tool、params | 工具输出封装 |

工具白名单：`build_agent_loop` 新增可选参数 `tools_override: list[str] | None = None`（默认 None=角色白名单，向后兼容）。

planner fallback：解析失败 → 5 步标准流水线（假设→数据→回测→验证→报告）。
evaluator 规则层：连续 2 段失败 → stop；预算超 150% → stop；默认 continue。

## 六、执行循环（executor.py）

### 6.1 RunState

`run_id / definition_name / session_id / status(pending|running|awaiting|completed|failed) / segment_idx / segments[] / pre_completed / findings / failures / params_snapshot`

### 6.2 切割算法（纯图）

拓扑排序 → 遇 approval 节点切段；approval 在首位的段自动跳过；无 approval = 单段。approval 不入任何段，其 `inputs`（上游段）执行完毕即为挂起点。

### 6.3 段循环主流程

1. `start-definition` → 建 run → 状态机
2. 每段：段内节点 → `SwarmPreset`（llm_agent/planner/evaluator 走 llm 执行，python/tool 走 `executor_type="python_executor"`）+ `pre_completed` → `SwarmRuntime.execute()`
3. planner 段结束 → 子图校验（`plan_` 前缀、无环、节点数 ≤ max_steps）→ 合并进当前段尾部追加执行
4. evaluator 段结束 → decision：
   - `continue` → 下一段
   - `replan` → 回 planner（输入=旧计划+reason+findings+pre_completed），重生成子图
   - `stop` → run completed，写最终 summary
5. 硬上限：`max_segments=3`、budget 三项、规则层前置检查

### 6.4 approval 等待

段间挂起 → status=awaiting → SSE 事件 → `POST /approve`：通过 → pre_completed 累计 + 下一段；拒绝 → 回 planner replan；超时（`approval.timeout`，null=永久）**保持 awaiting 无动作**。

## 七、超参数三层体系

```
运行请求覆盖（start-definition 携带 params，不落盘）
  > 节点 config（config 内覆盖）
    > definition.params（全局默认）
      > 代码默认值（LLMConfig.load() 等）
```

生效点：`loop_factory` 构造时 `LLMConfig.with_config(**llm)` → `run_agent_via_llm(llm_config=...)`；budget/max_segments 在 executor 状态机前置检查。start 时冻结生效参数快照入 `runs.params_snapshot`（可审计）；运行中改超参数不支持（快照语义，`directive` 机制已提供提示级干预）。

## 八、数据存储

| 数据 | 位置 |
|---|---|
| definition | `workspace/workflows/*.json`（用户）/ `templates/workflows/*.json`（内置只读） |
| 运行域数据 | **独立 DB `workspace/workflows.db`**（与 goals.db 同级，与 chat session DB 完全隔离） |

### 8.1 workflows.db 表

| 表 | 关键字段 |
|---|---|
| `runs` | run_id(PK), definition_name, session_id, objective, status, segment_idx, params_snapshot(JSON), findings(JSON), failures(JSON), created_at, updated_at |
| `run_segments` | run_id, segment_idx, nodes(JSON), status, elapsed_s, error |
| `node_outputs` | run_id, segment_idx, node_id, status, summary, artifacts(JSON), metrics(JSON), error, elapsed_s |
| `approvals` | run_id, node_id, status, edits(JSON), created_at, responded_at |
| `run_events` | run_id, seq, event_type, data(JSON), time |

### 8.2 机制

- `core/workflow/store.py`：`WorkflowStore`，照抄 `SQLiteStore` 模式（memory_manager.py:118）：`_ensure_conn` / `_init_schema` / `threading_lock` / `health_check` / `auto_repair`
- 挂起恢复从 DB 读（段索引 + pre_completed + findings）
- SSE 实时照发 + 落 `run_events`
- 隔离保证：工作流代码不 import session DB 模块；测试断言 session DB 无污染

## 九、内置 workflow（4 个）

| 模板 | 节点图 | 用途 |
|---|---|---|
| `plan_execute_auto` | planner → (动态子图) → evaluator → replan 循环 | 全自动目标驱动研究 |
| `plan_execute_approval` | planner → approval → (动态子图) → evaluator | 同上 + 人工确认 |
| `alpha_research` | llm_agent(假设) → tool(check_data) → tool(run_backtest) → evaluator | 静态流水线示例 |
| `data_quality_audit` | tool(check_data) → llm_agent(诊断报告) | 最小单链示例 |

优先级：workspace（用户，可写）> templates（内置，只读）；同名 workspace 覆盖；内置 DELETE 拒绝（422），编辑需 `POST /definitions/{name}/copy`。

## 十、API

| 端点 | 说明 |
|---|---|
| `POST /api/goal/workflow/definitions` | 创建/覆盖（校验失败 422） |
| `GET /api/goal/workflow/definitions` | 列表（含 source: builtin/user） |
| `GET /api/goal/workflow/definitions/{name}` | 详情 |
| `DELETE /api/goal/workflow/definitions/{name}` | 删除（内置拒绝） |
| `POST /api/goal/workflow/definitions/{name}/copy` | 内置 → 用户复制 |
| `GET /api/goal/workflow/definitions/{name}/graph` | nodes+edges（现有 WorkflowDAG 消费） |
| `POST /api/goal/workflow/start-definition` | `{session_id, definition_name, objective, params?}` → run_id |
| `POST /api/goal/workflow/approve` | `{run_id, approved, edits?}` |
| `GET /api/goal/workflow/run/{run_id}/status` | 段级状态 |
| `GET /api/goal/workflow/run/{run_id}` | run 详情（含段/节点输出） |
| `GET /api/goal/workflow/run/{run_id}/events` | 事件历史 |
| `DELETE /api/goal/workflow/run/{run_id}` | 删除历史 |
| SSE 扩展 | `segment_started / segment_completed / awaiting_approval / plan_created / plan_replan / run_completed / run_failed` |

## 十一、文件清单

新增：
- `core/workflow/definition.py` — 模型 + 校验 + 切割 + to_swarm_preset()
- `core/workflow/node_types.py` — 节点注册表（元数据）+ 执行分派
- `core/workflow/executor.py` — RunState 状态机 + 段循环 + 挂起恢复
- `core/workflow/store.py` — WorkflowStore（SQLite）
- `core/workflow/builtin.py` — 内置模板加载器
- `core/goal/planner.py`、`core/goal/evaluator.py` — 角色实现
- `templates/workflows/*.json`（4 个）+ `templates/.prompts/planner.md`、`evaluator.md`
- 测试：`test_workflow_definition.py`、`test_workflow_node_types.py`、`test_workflow_segment_loop.py`、`test_workflow_approval_api.py`、`test_workflow_store.py`

修改：
- `core/workflow/types.py` — AgentResult +4 可选字段
- `core/agent/role_factory.py` — 注册 planner/evaluator 角色 + `tools_override` 参数
- `api/routers/workflow.py` — 新端点（现有端点不动）

## 十二、提交拆分

| Commit | 内容 |
|---|---|
| 0 | 本文档 |
| 1 | definition 模型+校验+切割+转换器 + AgentResult 扩展 + 测试 |
| 2 | node_types 注册表 + planner/evaluator 角色 + executor 段循环 + store + 内置模板 + 测试 |
| 3 | approval + definitions CRUD/start-definition/approve API + SSE 事件 + 集成测试 + 全套回归 |

## 十三、风险与对策

| 风险 | 等级 | 对策 |
|---|---|---|
| approval 进 DAG 核心 | 🔴 | 段间切点，SwarmRuntime 零改动 |
| replan 失控循环 | 🟠 | max_segments=3 + budget + 规则层 + evaluator 兜底 continue |
| 子图 id 冲突 | 🟠 | `plan_` 前缀 + 校验 |
| 挂起后重启丢状态 | 🟠 | workflows.db 持久化 + 恢复入口 |
| 无 key 环境无法测 LLM 节点 | 🟡 | loop_factory 注入 stub |
| AgentResult 扩展破坏旧消费方 | 🟡 | 全可选字段 + 全套回归 |

## 十四、关键复用点

- `run_agent_via_llm`（role_factory.py:163）— 单角色 LLM 任务执行
- `StructuredOutputParser`（structured_output.py:38）— JSON 三层解析
- `PythonExecutor`（workflow/executors.py:27）— 注册函数机制
- `SwarmRuntime.execute`（swarm/runtime.py:130）— 段执行引擎（含 pre_completed）
- `SQLiteStore` 模式（memory_manager.py:118）— WorkflowStore 模板
- `_active_runners` + TTL（api/routers/workflow.py:26）— runner 生命周期
- `templates/.prompts|.skills/` 惯例 — 内置模板位置
