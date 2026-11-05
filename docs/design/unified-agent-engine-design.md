# 统一 Agent 执行引擎设计（Unified Agent Engine）

> 状态: 实施中
> 日期: 2026-08-18
> 前置讨论: study 动态 DAG 生成 + 编排功能完整复用

## 1. 问题

代码库存在**两条平行的 agent 执行路径**，做同一件事（spawn agent → ReAct → collect output）：

| 维度 | Study 路径 | 编排路径 |
|------|-----------|---------|
| 入口 | `autoresearch.spawn_agent()` | `SwarmRuntime._execute_agent()` |
| 执行器 | `AgentLoop` (loop.py, 2383 行) | `SwarmWorker` (worker.py, 404 行) |
| Prompt | `PromptBuilderFactory` + principles.md common 层 + `{tool_list}`/`{workspace}` 注入 | `PromptBuilder`（裸拼接，无 common 层/注入） |
| 分发 | `role_factory._ROLE_*` 两张表 | `AgentCall.context["executor_type"]` |
| 调用方 | study runner 3-phase / WorkflowRunner / SubAgentTool 之外的所有角色 | SwarmRuntime（LLM 路径）/ SubAgentTool |

后果：
- 同一 agent 在两条路径下 prompt 不同、迭代上限不同（8 vs 20）、能力不同（memory/compaction/approval gate 仅 AgentLoop 有）
- 三份重复的 agent 定义（`_ROLE_PROMPT_FILES`、`_ROLE_TOOL_WHITELIST`、YAML presets）
- 新 agent 需要在多处注册；修 bug 需要改两处引擎

## 2. 目标

**单一执行引擎**：`AgentLoop` 吸收 `SwarmWorker` 的独有能力后成为唯一 LLM 执行器；
`AgentExecutor` 作为唯一分发层（llm / python / evaluator）；
`AgentPlugin` + `AgentPluginRegistry` 作为唯一 agent 定义来源；
`AgentDAGConfig` 作为唯一 DAG 配置格式（graph.json / YAML / SwarmPreset 均可互转）。

Study（round loop）与编排（DAG layers）都是这套积木的消费方，差异只在**调度方式**，不在执行引擎。

```
AgentPlugin ──→ AgentPluginRegistry
                      │
              AgentExecutor (唯一分发)
              ├─ "llm"       → AgentLoop (唯一引擎, 吸收 3 项 SwarmWorker 能力)
              ├─ "python"    → python 函数注册表 (run_backtest_script 等)
              └─ "evaluator" → evaluator 注册表 (decide 等)
                      │
    ┌────────┬────────┼────────┬──────────┐
    ▼        ▼        ▼        ▼          ▼
Study Runner  SwarmRuntime  SubAgentTool  WorkflowRunner
(round loop)  (DAG layers)  (子 agent)    (segment loop, 已是 AgentLoop)
```

## 3. 分阶段实施（8 个 Phase）

### Phase 1 — AgentLoop 吸收 SwarmWorker 能力（向后兼容）

`core/agent/loop.py` 新增，默认行为不变：

| 能力 | 来源 | 实现 |
|------|------|------|
| `iteration_timeout_s: float \| None = None` | SwarmWorker `timeout_s` | `asyncio.wait_for` 包 LLM 调用；超时 → `finished_reason="timeout"` |
| Wrap-up nudge | SwarmWorker 0.8×max_iter 注入 | LoopStrategy 新 step `WrapUpNudgeStep`（可关） |
| 最后一轮 tools=None | SwarmWorker 强制文本收尾 | strategy `llm_call` step 增强：`iteration == max_iterations` 时不带 tools |

新增测试 `tests/test_agent_loop_swarm_parity.py`。

### Phase 2 — AgentPlugin 体系（纯新增）

```
core/agent/
├── plugin.py           # AgentPlugin 冻结数据类
├── builtin_plugins.py  # BUILTIN_PLUGINS（合并 _ROLE_* 两张表 + YAML presets 的 agent 定义）
├── registry.py         # AgentPluginRegistry: get/list/register/complete_dependencies
└── dag_config.py       # AgentDAGConfig + AgentNodeConfig + 与 StudyGraph/YAML/SwarmPreset 互转
```

`AgentPlugin` 字段：id/name/category/description/prompt_file/tools/requires/provides/
executor_type/python_function/default_timeout/default_max_iterations/default_max_retries/
optional/keywords。

`complete_dependencies(selected)`：按 requires 闭包补全必选 agent（如选 strategist 自动补 dq+fa）。

### Phase 3 — AgentExecutor 统一分发

```
core/agent/executor.py  # AgentExecutor
core/agent/exec_registry.py  # python/evaluator 函数注册表（从 SwarmRuntime._python_executors 上提）
```

- `execute(plugin, task, workspace, *, context, upstream_outputs, ...)` → 统一 `AgentResult`
- `_exec_llm`：统一 prompt 路径（common layers + 角色 prompt + `{tool_list}`/`{workspace}` 注入 + upstream
  outputs section）→ `AgentLoop`。即合并 `role_factory.build_agent_loop` 与 `workflow/prompt.py`
- `role_factory.run_agent_via_llm` 保留签名，内部转发 AgentExecutor（调用方无感）
- `SwarmRuntime.register_python_executor` 保留为兼容 shim 转发到 exec_registry

### Phase 4 — SwarmRuntime / SubAgentTool 改线 + 旧代码 deprecated

- `SwarmRuntime._execute_agent` LLM 路径：`PromptBuilder → WorkflowController → SwarmWorker`
  整链替换为 `AgentExecutor.execute`
- `SubAgentTool` 子 agent 同样改用 AgentExecutor（保留 no-nesting、timeout 语义）
- `WorkflowController` / `SwarmWorker` / `workflow/prompt.py` / `workflow/agent_runner.py`
  标记 deprecated（DocWarning + 转发），测试迁移后 Phase 8 物理删除
- `GoalWorkflowRunner._build_controller` 的 stub 注册表路径保留（用于测试）

### Phase 5 — Study DAG 驱动执行（先串行）

- `graph.py` 增加 `StudyGraph ↔ AgentDAGConfig` 互转
- `runner._run_one_round_impl`：硬编码 3-phase 替换为按 `topological_layers()` 串行调
  `AgentExecutor`；行为开关 `SR_STUDY_DAG_ENGINE=1`（默认 0 走旧路径，灰度切换）
- `StudySwarmHook`：agent 完成 → `save_agent_record` + 实时 `study_graph_node` SSE
  （替代 round 结束后一次性 emit topology）
- AEGIS round 级检查（novelty/regression/attribution）保留在 runner 层，不进 hook
- parse_failed 重试保留：输出 JSON 解析失败 → 带 `pre_completed` 重跑失败节点

### Phase 6 — AI 编排生成 DAG

- `core/study/dag_planner.py`：AgentLoop + 专用 planner prompt 分析 objective →
  从 BUILTIN_PLUGINS 选择 → 依赖补全 → `AgentDAGConfig` → graph.json；
  36 个 YAML presets 摘要作为 few-shot 候选注入 prompt
- API：`GET /api/study/agents`、`POST /api/study/plan-dag`、`GET /api/study/presets`
- `bootstrap.init_study_dir(auto_compose=True)`：走 planner，任何失败 fallback
  `DEFAULT_STANDARD_GRAPH`

### Phase 7 — 前端

- Study 创建页：AI 编排 tab（复用 `OrchestratorChat`[session=`study:compose:{id}`] +
  `WorkflowDAG` 预览 + agent 勾选面板 `StudyAgentPalette`）
- Study 详情页 Flow tab：复用 `WorkflowDAG` 实时节点状态；paused/interrupted 显示
  "编辑 DAG" → 复用 `WorkflowEditor` → `PUT /{id}/graph`

### Phase 8 — 清理 + 全量回归

- 物理删除：`workflow/worker.py`、`workflow/controller.py`、`workflow/prompt.py`、
  `workflow/agent_runner.py`；迁移 `test_swarm_worker.py`(27)、`test_workflow_controller.py`(11)
  等到 AgentExecutor 测试
- 回归：后端 pytest 全量 + 前端 vitest + build；`SR_STUDY_DAG_ENGINE` 灰度后默认开启并删开关

## 4. 兼容性与风险

| 风险 | 缓解 |
|------|------|
| AgentLoop 行为变化 | Phase 1 三项能力默认关闭（`iteration_timeout_s=None` 等），仅 DAG 路径显式开启 |
| SwarmRuntime 改线影响 goal workflow | Phase 4 前先跑全量 `test_goal_workflow_*` / `test_swarm*` 基线 |
| Study prompt 兼容 | Phase 5 串行 + 灰度开关；对比迁移前后 agent 输出 |
| SubAgentTool 嵌套 | 保留 no-nesting 语义，仅换底层引擎 |
| 旧 API 调用方 | `register_python_executor` 等保留 shim 转发 |

## 5. 明确不做

- 不保留双模式（full/lightweight）— 单一引擎
- 不新增第二套 DAG 执行器 — SwarmRuntime 继续负责 layer 调度，只是节点执行换 AgentExecutor
- 不在本设计中处理 workflow/definition.py 的 WorkflowDefinition 节点类型系统（已可表达为
  AgentDAGConfig 的超集，Phase 8 后再评估收敛）
