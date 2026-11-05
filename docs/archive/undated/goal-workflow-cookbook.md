# Goal Workflow Cookbook

> Phase 4 v0.5.4 — 5 分钟写一个自定义 workflow

## 快速开始

### 1. 查看现有 presets

```bash
# 列出所有内置 + 用户 workflow
/goal workflows list

# 查看某个 preset 的详情
/goal workflows show goal_factor_research
```

Python API：

```python
from strategy_research.core.goal.workflow_config import list_goal_workflows

for wf in list_goal_workflows():
    print(f"{wf['name']}: {wf['description']}")
```

### 2. 从 preset 复制

```bash
# 复制到用户目录
mkdir -p ~/.quantnodes-research/workflows
cp src/strategy_research/core/swarm/presets/goal_factor_research.yaml \
   ~/.quantnodes-research/workflows/my_strategy.yaml
```

### 3. 编辑 YAML

```yaml
# ~/.quantnodes-research/workflows/my_strategy.yaml
name: my_strategy
description: 我的自定义策略研究流程
version: "1.0"

goal:
  default_criteria:
    - "完成因子分析"
    - "通过风险审查"
    - "生成结构化报告"
  risk_tier: research_general

agents:
  - id: my_analyst
    prompt_file: .prompts/my_analyst.md
    tools: [analysis, run_backtest]
    input_from: []
    evidence_criterion: 0
    timeout: 300
    max_retries: 3

  - id: my_reviewer
    prompt_file: .prompts/my_reviewer.md
    tools: [analysis]
    input_from: [my_analyst]
    evidence_criterion: 1
    timeout: 120
    max_retries: 2

  - id: my_writer
    prompt_file: .prompts/report_writer.md
    tools: [markdown]
    input_from: [my_analyst, my_reviewer]
    evidence_criterion: 2
    timeout: 120
    max_retries: 2

dag:
  my_analyst: []
  my_reviewer: [my_analyst]
  my_writer: [my_analyst, my_reviewer]

completion:
  mode: auto
  auto_audit: true
  require_all_evidence: true
```

### 4. 验证并运行

```bash
# 验证 YAML
/goal workflows show my_strategy

# 运行
/goal start "研究我的策略" --workflow my_strategy

# 或 Python API
python -c "
import asyncio
from strategy_research.core.goal.workflow_config import load_goal_workflow
from strategy_research.core.goal.workflow import GoalWorkflowRunner

config = load_goal_workflow('my_strategy')
runner = GoalWorkflowRunner(config, session_id='test')
goal_id = asyncio.run(runner.start('研究我的策略'))
print(f'goal_id={goal_id}')
"
```

## 内置 Presets

| Preset | Agents | 用途 |
|---|---|---|
| `goal_factor_research` | 4 | 因子研究：定义→数据→分析→风控 |
| `goal_market_analysis` | 3 | 市场扫描→regime 分类→报告 |
| `goal_risk_assessment` | 4 | 仓位审计→压力测试→风险报告 |
| `goal_strategy_review` | 5 | PnL 归因→衰减→基准→总结 |
| `goal_portfolio_review` | 4 | 组合构建→集中度→风险→报告 |

## 自定义 Agent

### 添加自定义 validator

```python
from strategy_research.core.goal.validator_registry import ValidatorRegistry

class MyValidator:
    def validate(self, result):
        if not isinstance(result, dict):
            raise ValueError("Result must be dict")
        if "answer" not in result:
            raise ValueError("Missing 'answer' key")

ValidatorRegistry.register("my_analyst", MyValidator())
```

### 添加自定义 completion 策略

```python
from strategy_research.core.goal.completion_strategy import CompletionStrategyFactory

class MyCompletion:
    def is_complete(self, evidence_count, criteria_count):
        return evidence_count >= criteria_count * 0.8

CompletionStrategyFactory.register("my_mode", MyCompletion())
```

## Checkpoint 管理

```bash
# 保存当前状态
/goal checkpoint save

# 列出所有 checkpoint
/goal checkpoint list

# 恢复
/goal checkpoint resume

# 指定恢复
/goal checkpoint resume <goal_id>

# 删除
/goal checkpoint delete <goal_id>
```

## Branch 条件 (v0.5.3 P1.4)

YAML 中可以定义条件分支（**已接线生效**，P0-1）：

```yaml
branches:
  - condition: 'risk_controller.output.max_drawdown < -0.2'
    action: skip
    target: portfolio_construction
    reason: '回撤过大，跳过组合构建'
```

支持的语法：
- 比较：`<`, `<=`, `>`, `>=`, `==`, `!=`
- 布尔逻辑：`and`, `or`, `not`
- 字段访问：`agent_id.output.field`

已实现的动作：
- `skip`：条件命中时从后续层移除 target
- `retry`：条件命中时 target 在下一层重跑一次

> **未实现**：`redirect` 动作（改道）——解析被接受但不生效，属未来工作。

> **字段解析**：`agent_id.output.field` 从该 agent 的 worker 输出 JSON 解析；
> 若输出 `answer` 本身是 JSON，其键会合并到 `output` 下，因此
> `risk_controller.output.max_drawdown` 可直接引用（无需 `answer.` 前缀）。

## Checkpoint（P1.3 / P1.8）

`/goal checkpoint save|list|resume|delete` 可保存/列出/恢复/删除运行状态。

P1.8 已实现真断点续跑：

- `runner.checkpoint()` 保存 **layer_results**（已执行 agent 的输出 JSON）
- `GoalWorkflowRunner.resume_and_continue()` 从 checkpoint 恢复：
  - 复用现有 goal_id（**不创建新 goal**，保留 evidence/progress）
  - 跳过已执行层（`SwarmRuntime.pre_completed` + `start_layer`）
  - 下游 agent 通过 `_gather_upstream` 看到恢复的输出
- 适用场景：进程崩溃/重启后从断点继续；不需要重新执行上游 agent

CLI 用法：

```python
from strategy_research.core.goal.workflow import GoalWorkflowRunner

runner = GoalWorkflowRunner(config, session_id="s", goal_id="g")
await runner.resume_and_continue()
```

> 限制：resume 必须找到对应 goal_id 的 checkpoint（`FileNotFoundError`
> 若缺失）；checkpoint 文件不跨机器迁移。

## Troubleshooting

### YAML 加载失败

```
Workflow not found: my_strategy
```

→ 检查文件名是否匹配 `goal_*.yaml` 或 `*.yaml`
→ 检查 `~/.quantnodes-research/workflows/` 目录是否存在

### DAG 有环

```
ValueError: DAG has a cycle
```

→ 检查 `dag` 定义中是否有循环依赖

### evidence_criterion 越界

```
Agent X evidence_criterion N exceeds criteria count M
```

→ 确保 `evidence_criterion` 在 `[0, len(default_criteria))` 范围内