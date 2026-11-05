# Goal Workflow Phase 4 — 端到端打通 + 自定义生效

> 版本：v0.5.1 – v0.5.5
> 周期：W1 – W8
> 状态：W0（设计稿）

## 1. 背景

Phase 3（v0.5.0）完成了 Goal Workflow 子系统的**底层骨架**：

- ✅ `GoalWorkflowRunner` + DAG 调度 + `GoalWorkflowHook`
- ✅ `load_goal_workflow()` + `goal_factor_research.yaml` preset
- ✅ `AgentRunnerRegistry` / `ValidatorRegistry` / `CompletionStrategyFactory`
- ✅ `WorkflowEventBus` + 4 个 Observer
- ✅ `CheckpointStore` + `start_sub_workflow()`
- ✅ 表达式 DSL `ExpressionEvaluator`
- ✅ 597 个测试通过

但代码审计（2026-07-28）发现**端到端仍未打通**：

| 维度 | 现状 | 用户视角 |
|---|---|---|
| CLI 启动 workflow | ❌ 无 `--workflow` 参数 | 跑不起来 |
| TUI 实时面板 | ❌ GoalPanel 收不到 event | 看不动 |
| Ctrl+G 暂停 workflow | ❌ 只暂停裸 goal | 停不下来 |
| 自定义 validator 真生效 | ❌ `_build_controller` 用空 `AgentRegistry` | 接不上 |
| `BranchConfig.condition` 分支 | ❌ 解析了但不求值 | 白写 |
| `resume_from_checkpoint` 续跑 | ❌ 只还原 state 不重放 | 接不上 |
| `pause(immediate=True)` 真中断 | ❌ `should_stop` 不看 `cancelled` | 停不掉 |
| Visual editor | ❌ 只是 viewer，不能编辑 | 改不动 |
| 缺 4 个常用 preset | ❌ `GoalTemplate` 有 YAML 没写 | 用不到 |

**Phase 4 的目标**：把"骨架"变成"用户真能用的产品"。

## 2. 范围

```
P0  端到端打通        v0.5.1 + v0.5.2    2 周    CLI + TUI + async worker
P1  让自定义生效      v0.5.3              2 周    7 项 API 修复
P2  4 preset + 示例   v0.5.4              1 周    YAML + cookbook + demo
P3  可视化编辑器      v0.5.5              3 周    ASCII DAG + 编辑
```

**不做**：跨 workflow 数据血缘分析、远程 workflow 执行、GPU 调度、量化回测集成。

## 3. 设计原则

1. **TDD**：先写测试，再写实现。每个 PR 必须新增 ≥5 测试。
2. **不破坏 API**：`GoalWorkflowRunner.__init__` 死参数在 P1.6 一次性清掉；其他公开签名不变。
3. **小版本兼容**：每个 minor 单独发版，用户可逐步升级。
4. **依赖清晰**：P0 强依赖 `WorkflowEventBus`、`CheckpointStore`、`load_goal_workflow` 已存在 API。
5. **ASCII-first**：P3 渲染不引入新依赖，纯 Unicode box-drawing。

## 4. P0 — 端到端打通（v0.5.1 + v0.5.2）

### 4.1 v0.5.1 CLI 子命令

#### 4.1.1 `cmd_goal_start` 扩展

```python
# cli/commands/slash_goal.py

def cmd_start(args: list[str]) -> str:
    """usage: /goal start <objective> [--template T] [--workflow W] [--workspace PATH]

    Examples:
      /goal start "研究动量因子" --workflow goal_factor_research
      /goal start "评估 Q3 风险" --workflow goal_risk_assessment --workspace ./risk_q3
    """
```

参数解析优先级：

| 标志 | 行为 |
|---|---|
| `--workflow <name>` | 加载 `goal_<name>.yaml` → `GoalWorkflowRunner.start()` |
| `--template <T>` | 现有 `GoalTemplate` 路径（保持兼容） |
| 都不传 | 现有裸 goal 启动（保持兼容） |

实现要点：

```python
# 伪代码
if workflow_name:
    config = load_goal_workflow(workflow_name)
    runner = GoalWorkflowRunner(config, session_id=session_id, store=store)
    runner.subscribe(GoalPanelObserver(panel))   # v0.5.2 会改
    goal_id = await runner.start(objective)
    self.active_runner = runner                  # 存到 session
    return f"✓ workflow '{workflow_name}' started: {goal_id}"
```

#### 4.1.2 `/goal workflows` 子命令

```python
def cmd_workflows(args: list[str]) -> str:
    """usage: /goal workflows [list|show <name>|path <name>]
    """
```

子命令：

- `list` — 表格列出：name / source（builtin/user）/ agents / branches
- `show <name>` — 渲染 YAML + 拓扑 ASCII 图（复用 P3 的 `dag_renderer.py`，先放 stub）
- `path <name>` — 打印 YAML 绝对路径

#### 4.1.3 `/goal checkpoint` 子命令

```python
def cmd_checkpoint(args: list[str]) -> str:
    """usage: /goal checkpoint save|list|resume|delete [goal_id]
    """
```

子命令：

| 命令 | 行为 |
|---|---|
| `save` | 当前 goal 的 workflow state 落盘（无 active runner 时报错） |
| `list [session_id]` | 列出 `~/.quantnodes-research/checkpoints/<session>/<goal>/` |
| `resume [goal_id]` | 默认最近一个；指定时按 goal_id 恢复 |
| `delete <goal_id>` | 删除指定 checkpoint 目录 |

#### 4.1.4 测试

| 文件 | case |
|---|---|
| `tests/test_slash_goal_workflow.py` | `cmd_start --workflow` 加载 preset / `--workflow not_found` 报错 / 现有 template 兼容 |
| `tests/test_slash_goal_workflows.py` | `list` 列出 5 个 preset / `show` 输出含 agents 数 / `path` 绝对路径 |
| `tests/test_slash_goal_checkpoint.py` | save/list/resume/delete 4 个 round-trip / 无 active runner 报错 |

### 4.2 v0.5.2 TUI 集成

#### 4.2.1 async worker

新增 `cli/tui/workers/workflow_worker.py`：

```python
class WorkflowWorker:
    """Background asyncio.Task wrapper for GoalWorkflowRunner.start().

    Lifecycle:
        worker = WorkflowWorker(runner, app)
        task = asyncio.create_task(worker.run())
        # ...
        await worker.cancel()    # graceful: sets runner._state.cancelled
    """

    def __init__(self, runner: GoalWorkflowRunner, app: ResearchApp) -> None: ...
    async def run(self) -> None: ...
    async def cancel(self, *, immediate: bool = False) -> None: ...
    @property
    def is_running(self) -> bool: ...
```

要点：

- `asyncio.create_task` 而非 `asyncio.run` — 与 Textual event loop 共存
- 异常透传到 `app.notify(severity="error")`
- `cancel()` 调 `runner.pause()`，由 P1.2 让 `pause` 真中断

#### 4.2.2 Ctrl+G 暂停

修改 `cli/tui/app.py:608-612`：

```python
async def action_goal_continuation(self) -> None:
    # 优先暂停 workflow
    worker = self._workflow_worker
    if worker and worker.is_running:
        await worker.cancel()
        self.notify("⏸ workflow paused (Ctrl+G to resume)")
        return
    # 回退：暂停裸 goal continuation
    self._state.goal_continuation_paused = not ...
```

#### 4.2.3 GoalPanel 订阅

修改 `cli/tui/app.py:614-644`：

```python
def on_mount(self) -> None:
    # ... 现有代码
    self._state.active_runner: GoalWorkflowRunner | None = None

def start_workflow(self, runner: GoalWorkflowRunner) -> None:
    self._state.active_runner = runner
    observer = GoalPanelObserver(self.query_one(GoalPanel))
    runner.subscribe(observer)
    # 同时挂 MetricsObserver / LoggerObserver
```

#### 4.2.4 测试

| 文件 | case |
|---|---|
| `tests/test_workflow_worker.py` | run 正常完成 / cancel 中断 / 异常透传 / 双 cancel 幂等 |
| `tests/test_tui_workflow_integration.py` | Ctrl+G 真暂停 / GoalPanel.on_workflow_event 触发 / 多 worker 切换 |

## 5. P1 — 让自定义生效（v0.5.3）

7 项修复，依赖图：

```
P1.1 registry      ──┐
P1.2 cancel        ──┤
P1.3 resume        ──┼─▶ v0.5.3
P1.4 expression    ──┤
P1.5 prompt        ──┤
P1.6 cleanup       ──┤
P1.7 workflow_id   ──┘
```

### 5.1 P1.1 真接 AgentRegistry + ValidatorRegistry

**现状**：`workflow.py:496-507 _build_controller` 用空 `AgentRegistry()`。

**修复**：

```python
# core/goal/workflow.py
def _build_controller(self) -> WorkflowController:
    registry = AgentRegistry()
    # 反查所有 YAML 中声明的 agent，从 ValidatorRegistry 拿 validator
    for agent_id in self._config.dag:
        validator = ValidatorRegistry.get(agent_id)
        if validator:
            registry.register(_ValidatingExecutor(agent_id, validator))
    return WorkflowController(registry=registry, adj={...}, ...)
```

新增 `_ValidatingExecutor`（`core/goal/executors.py`）：

```python
class _ValidatingExecutor:
    def __init__(self, agent_id: str, validator: AgentValidator) -> None:
        self.name = agent_id
        self._validator = validator

    async def run(self, *, agent_name, prompt, tools, context) -> dict:
        # 走 SwarmWorker 拿结果
        result = await SwarmWorker.run_agent(...)
        # 校验
        self._validator.validate(result)
        return result
```

### 5.2 P1.2 pause 真中断

**现状**：`GoalWorkflowHook.should_stop` 只看 `_completed`。

**修复**：

```python
# core/goal/workflow_hook.py:152
def should_stop(self) -> bool:
    runner_state = getattr(self._runner, "_state", None)
    if runner_state and runner_state.cancelled:
        return True
    return self._completed
```

### 5.3 P1.3 resume 重放

**现状**：`checkpoint()` 传空 `layer_results={}`（workflow.py:549），`resume_from_checkpoint` 只还原 state。

**修复**：

```python
# core/goal/workflow.py
def checkpoint(self) -> None:
    state = self._state
    layer_results = self._hook.layer_results      # 修：不再传空 dict
    CheckpointStore().save(
        session_id=self._session_id,
        goal_id=state.goal_id,
        state=state,
        layer_results=layer_results,
    )

@classmethod
def resume_from_checkpoint(cls, session_id, goal_id, ...) -> "GoalWorkflowRunner":
    data = CheckpointStore().load(session_id, goal_id)
    runner = cls(config, session_id=session_id, store=store, workspace=workspace)
    runner._state = data["state"]
    runner._hook._layer_results = data["layer_results"]    # 注入
    runner._hook._completed_layers = set(                  # 标记跳过
        k for k, v in data["layer_results"].items() if v.get("_complete")
    )
    return runner
```

并修改 `GoalWorkflowHook._run_layers`：跳过 `_completed_layers` 中的节点。

### 5.4 P1.4 表达式 DSL 真求值

**现状**：`BranchConfig.condition` 解析后未求值。

**修复**：

```python
# core/goal/workflow_hook.py
def on_layer_complete(self, layer_idx: int) -> None:
    for branch in self._branches:
        cond = evaluate_condition(branch.condition, self._layer_results)
        if cond:
            if branch.action == "skip":
                self._skip_agents.update(branch.target)
            elif branch.action == "retry":
                self._retry_agents.update(branch.target)
            elif branch.action == "redirect":
                self._redirect_agent(branch.target, branch.reason)
```

DSL 增强（`expression_evaluator.py`）：

- 支持 `and` / `or` / `not`（优先级 `not > and > or`）
- 函数 `len(x)` / `contains(x, y)` / `min(a, b)` / `max(a, b)`

### 5.5 P1.5 SwarmRuntime 用 PromptBuilder

**现状**：`runtime.py:267-284` 用内联 f-string。

**修复**：

```python
# core/swarm/runtime.py:267
async def _execute_agent(self, call, upstream_outputs):
    from core.workflow.prompt import PromptBuilder
    prompt = PromptBuilder.build_prompt(
        system_template=call.system_prompt,
        user_template=call.task,
        upstream_results=upstream_outputs,
    )
    return await SwarmWorker.run_agent(...)
```

### 5.6 P1.6 清死参数

`GoalWorkflowRunner.__init__` 当前接受但忽略：

- `agent_runner`
- `agent_runner_type`
- `runner_kwargs`
- `use_validators`

**修复方案**：保留兼容 1 个版本（v0.5.3 deprecation warning），v0.6.0 删除。

```python
def __init__(self, config, *, session_id, store, workspace=None,
             agent_runner=None, agent_runner_type=None,
             runner_kwargs=None, use_validators=True):
    if agent_runner or agent_runner_type or runner_kwargs:
        warnings.warn(
            "agent_runner/agent_runner_type/runner_kwargs are deprecated "
            "in v0.5.3 and will be removed in v0.6.0. "
            "Use AgentRunnerRegistry.register() instead.",
            DeprecationWarning, stacklevel=2,
        )
    # ... 构造逻辑保持
```

### 5.7 P1.7 workflow_id 落库

**现状**：`goals.workflow_id` 列存在（store.py:180）但未写入。

**修复**：

```python
# core/goal/workflow.py
async def start(self, objective: str) -> str:
    # 1. 创建 goal
    goal_id = self._store.create_goal(
        objective=objective,
        workflow_id=self._config.name,    # 新增
        ...
    )
    # ... 后续
```

### 5.8 测试

`tests/test_goal_workflow_phase4.py` — ≥25 个 case：

- 自定义 YAML 端到端跑通（mock SwarmWorker）
- 自定义 validator 在 agent 输出不合规时报错
- BranchConfig.condition 真分支
- checkpoint 恢复后跳过已完成 layer
- pause 立刻中断（mock hook）
- DSL `and`/`or`/`not` 求值
- PromptBuilder 替换内联 prompt
- 死参数触发 DeprecationWarning
- workflow_id 写入 goal 行

## 6. P2 — 4 preset + 示例（v0.5.4）

### 6.1 新增 YAML

```
src/agentnodes/core/swarm/presets/
  goal_market_analysis.yaml        # 3 agents
  goal_risk_assessment.yaml        # 4 agents
  goal_strategy_review.yaml        # 5 agents
  goal_portfolio_review.yaml       # 4 agents
```

### 6.2 YAML schema 示例（market_analysis）

```yaml
# goal_market_analysis.yaml
name: goal_market_analysis
description: 市场状态扫描 + regime 分类 + 报告生成
goal:
  default_criteria:
    - "已识别当前市场 regime"
    - "已列出至少 5 个观察指标"
    - "已生成结构化报告"
  risk_tier: low
completion:
  mode: lite
  auto_audit: true
  require_all_evidence: false
agents:
  market_scanner:
    prompt_file: prompts/market_scanner.md
    tools: [tushare, akshare]
    timeout: 300
    max_retries: 2
  regime_classifier:
    prompt_file: prompts/regime_classifier.md
    tools: [analysis]
    input_from: [market_scanner]
  report_writer:
    prompt_file: prompts/report_writer.md
    tools: [markdown]
    input_from: [market_scanner, regime_classifier]
dag:
  market_scanner: []
  regime_classifier: [market_scanner]
  report_writer: [market_scanner, regime_classifier]
```

### 6.3 Cookbook

`docs/archive/undated/goal-workflow-cookbook.md` — 5 分钟教程：

1. 复制 builtin preset 到用户目录
2. 修改 agent / dag / criteria
3. `/goal workflows list` 验证
4. `/goal start "..." --workflow my_custom`
5. 自定义 validator（Python）

### 6.4 Demo

`examples/goal_workflow_demo.py`：

```python
"""演示：自定义 goal workflow 端到端跑通。"""
import asyncio
from agentnodes.core.goal.workflow_config import load_goal_workflow
from agentnodes.core.goal.workflow import GoalWorkflowRunner
from agentnodes.core.goal.store import GoalStore
from agentnodes.core.goal.event_bus import CollectingObserver

async def main():
    config = load_goal_workflow("goal_market_analysis")
    store = GoalStore(db_path="./demo.db")
    runner = GoalWorkflowRunner(config, session_id="demo", store=store)

    events = CollectingObserver()
    runner.subscribe(events)

    goal_id = await runner.start("扫描 Q3 市场状态")
    print(f"goal {goal_id} done")
    print(f"events: {len(events.events)}")

if __name__ == "__main__":
    asyncio.run(main())
```

### 6.5 测试

`tests/test_goal_workflow_presets.py` — ≥10 case：

- 4 个 preset YAML 加载无错
- 每个 preset 的 DAG 是合法的（无环、无未引用 agent）
- preset 的 prompt_file 全部存在
- demo 脚本能 import + 加载

## 7. P3 — 可视化编辑器（v0.5.5）

### 7.1 目标

让用户在 TUI 里**看到** + **编辑** + **运行** workflow：

```
┌─ DAG: goal_factor_research ──────────────┐
│ ┌─────────┐   ┌──────────────┐           │
│ │researcher│──▶│data_quality  │           │
│ │   ✓     │   │     ✓        │           │
│ └─────────┘   └──────────────┘           │
│      │             │                     │
│      ▼             ▼                     │
│ ┌──────────┐  ┌──────────────┐           │
│ │ factor_  │─▶│ risk_        │           │
│ │ analyst  │  │ controller   │           │
│ │   ⏳     │  │              │           │
│ └──────────┘  └──────────────┘           │
│                                          │
│ Layer 2/3 · 67% complete                 │
│ Selected: factor_analyst (Enter to edit) │
└──────────────────────────────────────────┘
```

### 7.2 ASCII 渲染算法

`core/goal/dag_renderer.py`：

```python
def render_dag(
    dag: dict[str, list[str]],
    *,
    status: dict[str, NodeStatus] | None = None,
    selected: str | None = None,
    width: int = 60,
) -> str:
    """Render DAG as ASCII with Unicode box-drawing.

    Algorithm:
    1. topological_layers() → assign each node to a layer index
    2. For each layer, list nodes top-to-bottom
    3. Draw edges as │ / ─ ▶ between layers
    4. Add status icon (✓ ⏳ ✗ ○) prefix
    5. Highlight selected node with ▸ marker

    Limitations:
    - DAG with > 20 nodes may overflow terminal width
    - Long node names truncated to 12 chars
    """
```

#### 7.2.1 Layout 算法

```
Layer 0   Layer 1        Layer 2
researcher ─▶ data_quality ─▶ factor_analyst
                                │
                                ▼
                            risk_controller
```

1. 计算 `topological_layers(dag)`（已有 `core/workflow/dag.py:6`）
2. 同层节点垂直对齐
3. 跨层节点用 `─▶` 直线连接（无交叉优化 v0.5.5 不做）
4. 节点内 12 字符截断 + 状态 icon

### 7.3 TUI Widget

新增 `cli/tui/widgets/dag_view.py`：

```python
class DAGView(Static):
    """Display ASCII DAG with selection state."""

    BINDINGS = [
        ("j", "select_next", "Next node"),
        ("k", "select_prev", "Prev node"),
        ("h", "select_parent", "Parent"),
        ("l", "select_child", "Child"),
        ("enter", "edit_node", "Edit"),
        ("e", "edit_yaml", "Edit YAML"),
        (":", "command_mode", "Command"),
    ]

    def __init__(self, dag: dict[str, list[str]]) -> None: ...
    def update_status(self, status: dict[str, NodeStatus]) -> None: ...
    def action_select_next(self) -> None: ...
    def action_edit_node(self) -> None: ...
```

### 7.4 编辑模式

`:e` 进入 YAML 编辑（`textual-suggestion` 或 `prompt_toolkit`）：

```yaml
# 编辑中的 buffer
agent: factor_analyst
prompt_file: prompts/factor_analyst.md    # 可改
tools: [analysis, tushare]                 # 可改
timeout: 300                               # 可改
max_retries: 2                             # 可改
input_from: [researcher, data_quality]     # 可改
evidence_criterion: factor_analyst.output.ic > 0.05   # 可改
---
:w 保存  :q 退出  :! 放弃
```

`:w` 触发 `save_goal_workflow()`（见 7.5）。

### 7.5 Save-back

新增 `core/goal/workflow_config.py:save_goal_workflow`：

```python
def save_goal_workflow(
    path: Path,
    config: GoalWorkflowConfig,
    *,
    backup: bool = True,
    validate: bool = True,
) -> None:
    """Atomic write YAML with optional backup and validation.

    Steps:
    1. Serialize config to YAML
    2. If backup=True and path exists, rename to <path>.bak
    3. Write to <path>.tmp
    4. If validate=True, run validate_dag() — on failure, restore from .bak
    5. os.replace(tmp, path)

    Raises:
        WorkflowValidationError: if DAG invalid
        WorkflowSaveError: if write fails
    """
```

### 7.6 多 Tab

Buffer-style 工作流切换：

- `:bnext` / `:bprev` 在 builtin / 用户 / 历史之间切换
- 当前正在跑的 workflow 不能编辑（lock icon）

### 7.7 测试

`tests/test_dag_renderer.py` ≥15 case：

- 1/2/5 节点渲染
- 跨层边渲染
- status icon 正确
- selected 高亮
- 截断长名字
- 校验 cycle / unreachable
- 宽度边界（< node 数）

`tests/test_dag_view_widget.py` ≥10 case：

- 按键导航 j/k/h/l
- Enter 触发 edit
- :e 进入 YAML 编辑
- :w 保存 + reload 一致
- :w 校验失败回滚

## 8. 发布与依赖

### 8.1 发布日历

| 版本 | 周次 | 内容 | 新增测试 |
|---|---|---|---|
| **v0.5.1** | W1 | P0 CLI 子命令 | +12 |
| **v0.5.2** | W2 | P0 TUI + async worker | +15 |
| **v0.5.3** | W3-W4 | P1 全部 | +25 |
| **v0.5.4** | W5 | P2 preset + cookbook | +10 |
| **v0.5.5** | W6-W8 | P3 可视化编辑器 | +30 |
| **合计** | 8 周 | — | **+92 测试** |

### 8.2 依赖关系

```
v0.5.1 ──▶ v0.5.2 ──▶ v0.5.3 ──▶ v0.5.4 ──▶ v0.5.5
   │          │          │          │          │
   │          │          │          │          └─ 依赖 v0.5.3 的
   │          │          │          │             workflow_id 列
   │          │          │          └─ 依赖 v0.5.3 的
   │          │          │             validator 接通
   │          │          └─ 依赖 v0.5.2 的
   │          │             async worker
   │          └─ 依赖 v0.5.1 的
   │             workflow_id 列（store.py:180 已存在）
   └─ 不依赖（仅用现成 API）
```

### 8.3 风险

| 风险 | 缓解 |
|---|---|
| v0.5.3 P1.3 resume hook 状态序列化 | 增量交付：先做 state，再做 layer_results |
| v0.5.5 ASCII 渲染 > 20 节点拥挤 | 分页（`--page` 标志）+ 折叠子图 |
| v0.5.2 async worker 与 Textual event loop 交互 | 用 `app.call_from_thread` 而非直接 await |
| v0.5.3 P1.6 deprecation 触发老用户报错 | 1 版本软警告（v0.6.0 真删） |
| P1.4 DSL `and/or/not` 实现复杂度 | 复用 Python `ast.parse(mode="eval")` + 自定义节点 visitor |

## 9. 不做（Out of Scope）

- 跨 workflow 数据血缘 / lineage 图
- 远程 / 分布式 workflow 执行
- GPU 调度、并行度自动调优
- 量化回测与 workflow 深度集成（已有 autoresearch 入口）
- Workflow 版本控制（git 外部）
- 权限系统（多用户）

## 10. 参考

- `docs/archive/undated/goal-workflow-design.md`：Phase 3 完整设计（735 行）
- `docs/archive/undated/goal-design.md`：Goal 子系统设计
- `docs/archive/undated/vibe-trading-core-patterns.md`：借鉴的 8 个组件
- `core/goal/workflow.py`：当前实现（v0.5.0）
- `cli/commands/slash_goal.py`：当前 CLI（19 测试）