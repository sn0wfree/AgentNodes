# Workflow 层设计文档

> 本文档记录 Project-Driven Workflow 的完整设计。
> 核心目标: 将控制权从平台 (OpenCode) 反转到项目本身。

---

## 1. 背景与问题

### 1.1 之前的问题: 平台驱动项目

```
┌─────────────────────────────────────────────────────┐
│  OpenCode 平台 (大脑)                                │
│  - 决定何时 spawn Agent                             │
│  - 决定 prompt 内容                                 │
│  - Agent 输出由 LLM 决定                           │
│  - 平台无法验证 Agent 输出                          │
│  - 平台无法处理幻觉                                 │
└─────────────────────────────────────────────────────┘
        │
        ▼ (被动)
┌─────────────────────────────────────────────────────┐
│  项目 (手)                                          │
│  - 等待平台调用                                     │
│  - 接受 LLM 输出 (可能有幻觉)                      │
│  - 无法验证结果                                     │
│  - 无法恢复错误                                     │
└─────────────────────────────────────────────────────┘
```

**导致的问题**:
- **幻觉**: LLM 输出未经验证
- **重复**: Agent 重复之前的错误建议
- **失控**: 平台无法监控 Agent 行为
- **脆弱**: 一个 Agent 失败导致整体失败
- **不可追溯**: 没有完整决策记录

### 1.2 现在的目标: 项目驱动平台

```
┌─────────────────────────────────────────────────────┐
│  项目 (大脑)                                        │
│  - 决定 Agent 调用时机                              │
│  - 构造动态 prompt                                  │
│  - 验证 Agent 输出                                  │
│  - 控制信息流                                       │
│  - 记录决策                                         │
│  - 处理错误                                         │
└─────────────────────────────────────────────────────┘
        │
        ▼ (控制)
┌─────────────────────────────────────────────────────┐
│  平台 (手)                                          │
│  - 执行具体操作                                     │
│  - 调用 LLM                                         │
│  - 读写文件                                         │
│  - 可以被替换                                       │
└─────────────────────────────────────────────────────┘
```

### 1.3 之前的实验真相

通过 OpenCode session 数据库确认:

| 证据 | 说明 |
|------|------|
| 8 个 subagent session | 真实 spawn 记录 |
| 88743 input tokens | Factor Analyst 真实工作量 |
| 真实 IC/IR 值 | `-0.0038 < 0.03` 不是硬编码 |
| 真实回退决策 | "已回退" 是 LLM 推理结果 |
| `general` agent type | OpenCode Task tool 调用 |

**结论**: 之前的实验是**真实 LLM 调用**,不是 stub。

---

## 2. 架构设计

### 2.1 三层架构

```
┌────────────────────────────────────────────────────────────┐
│  Workflow 层 (我的工作)                                     │
│  - WorkflowController (控制器)                             │
│  - AgentExecutor Protocol (接口)                           │
│  - Agent Validators (验证)                                 │
│  - Error Recovery (恢复)                                   │
│  - Python API (给外部工具)                                 │
│  - CLI JSON (给 Codex/OpenClaw)                            │
└────────────────────────────────────────────────────────────┘
        │
        ▼ (调用)
┌────────────────────────────────────────────────────────────┐
│  Agent 层 (另一个 Agent 的工作)                             │
│  - AgentLoop (ReAct 循环)                                  │
│  - BaseTool + ToolRegistry                                 │
│  - 10 个 Agent 算法                                        │
│  - 沙箱 / 追踪 / 记忆                                      │
└────────────────────────────────────────────────────────────┘
```

### 2.2 职责划分

| 我的工作 (Workflow 层) | 另一个 Agent 的工作 (Agent 层) |
|----------------------|------------------------------|
| WorkflowController (控制器) | AgentLoop (ReAct 循环) |
| AgentExecutor Protocol (接口) | BaseTool + ToolRegistry |
| Agent Validators (验证) | 具体 Agent 算法 |
| Error Recovery (恢复) | 工具实现 |
| Python API | 沙箱 (AST guard) |
| CLI JSON | 追踪 (JSONL trace) |

### 2.3 保留现有功能

| 功能 | 保留 | 说明 |
|------|------|------|
| OpenCode Task tool | ✅ | 作为执行方式之一 |
| AgentLoop (ReAct) | ✅ | 作为执行方式之一 |
| .prompts/*.md | ✅ | 作为 prompt 基础 |
| .skills/*.md | ✅ | 作为方法论参考 |

---

## 3. 核心接口

### 3.1 AgentExecutor Protocol

**借鉴**: vibe-trading 的 ToolRegistry 模式。

**定义**: 每个 Agent 是一个可执行单元,通过统一接口调用。

**接口**:
- `name`: Agent 名称
- `description`: Agent 描述
- `run(prompt, context)`: 执行 Agent,返回输出

**职责**:
- 接收 prompt 和上下文
- 执行 Agent 逻辑
- 返回标准化输出

### 3.2 AgentStatus

**借鉴**: vibe-trading 的 WorkerStatus。

**状态**:
- `pending`: 等待执行
- `in_progress`: 正在执行
- `completed`: 成功完成
- `failed`: 执行失败
- `timeout`: 超时
- `incomplete`: 输出无效
- `validation_failed`: 验证失败

### 3.3 AgentCall

**借鉴**: vibe-trading 的 SwarmTask。

**包含**:
- `agent`: Agent 名称
- `prompt`: 动态生成的 prompt
- `depends_on`: 依赖的 Agent 列表
- `status`: 执行状态
- `output`: 执行结果
- `error`: 错误信息
- `duration_ms`: 执行耗时

### 3.4 AgentOutput

**定义**: Agent 输出的标准格式。

**包含**:
- `agent_name`: Agent 名称
- `action`: 动作类型
- `hypothesis`: 假设
- `reason`: 原因
- `factor_direction`: 因子方向
- Agent 特定字段
- 元数据

---

## 4. WorkflowController

### 4.1 职责

- 管理 Agent 执行顺序 (串行链)
- 构造动态 prompt
- 传递 Agent 间输出
- 验证每个 Agent 输出
- 处理错误和重试
- 记录决策

### 4.2 执行流程

```
1. 构造 Agent 串行链
2. 按顺序执行每个 Agent
3. 检查依赖是否满足
4. 构造动态 prompt
5. 调用执行器
6. 验证输出
7. 保存记录
8. 运行回测
9. 决策 keep/discard
```

### 4.3 Agent 串行链

```
Researcher → Data Quality → Factor Analyst → Strategist →
Portfolio Construction → Risk Controller → Attribution Analyst →
Anti-overfit Analyst → Backtest Diagnostics
```

### 4.4 Prompt 构造

- 基础 prompt: 从 .prompts/*.md 读取
- 动态注入: 当前状态 (Calmar/Sharpe/...)
- 上游输出: 前一个 Agent 的输出

---

## 5. Agent Validators

### 5.1 验证策略

**策略**: 每个 Agent 都验证。

### 5.2 验证层次

1. **Schema 验证**: 输出格式是否符合 schema
2. **逻辑验证**: 输出内容是否合理
3. **历史验证**: 是否重复之前的建议

### 5.3 Agent 特定验证

| Agent | 验证内容 |
|-------|---------|
| Researcher | action/factor_direction 是否有效 |
| Factor Analyst | IC/IR 范围是否合理 |
| Strategist | 回测验证策略修改是否有效 |
| Risk Controller | 风险指标是否达标 |
| Anti-overfit | 6 种方法是否通过 |

---

## 6. Error Recovery

### 6.1 恢复策略

**策略**: 回滚重试。

### 6.2 恢复步骤

1. Agent 执行失败 → 记录错误
2. 回滚策略修改 (如有)
3. 重试 (最多 3 次)
4. 达到最大重试次数 → 终止

---

## 7. Python API

### 7.1 高层 API

- `run_research_loop()`: 运行 N 轮研究
- `run_one_round()`: 运行单轮研究

### 7.2 中层 API

- `spawn_agent()`: 调用单个 Agent
- `apply_strategy_change()`: 应用策略修改
- `decide_keep_discard()`: 决策 keep/discard

### 7.3 低层 API

- `read_state()`: 读取当前状态
- `save_agent_record()`: 保存 Agent 记录
- `chat()`: 调用 LLM

---

## 8. CLI JSON

### 8.1 命令

- `quantnodes-research workflow round`: 单轮 (JSON)
- `quantnodes-research workflow loop`: 多轮 (JSONL)
- `quantnodes-research workflow chat`: 单 Agent 调用 (JSON)

### 8.2 输出格式

- stdout: JSON 结果
- stderr: 进度信息
- exit code: 0=成功, 1=业务错误, 2=参数错误

---

## 9. 集成方式

### 9.1 与其他 Agent 的集成

**方式**: Protocol + Registry

**流程**:
1. 另一个 Agent 实现 AgentExecutor 接口
2. 另一个 Agent 调用注册函数注册
3. 我的 Workflow 层从注册表获取执行器
4. 我的 Workflow 层调用执行器的 run 方法

### 9.2 执行方式

**四种执行方式并存**:
1. AgentLoop: 使用其他 Agent 的 AgentLoop
2. Task tool: 使用 OpenCode Task tool (保留现有)
3. Python: 使用 Python 函数调用
4. CLI: 使用 CLI 子进程

**自动检测逻辑**:
- 有注册的 AgentLoop → 用 AgentLoop
- 在 OpenCode 环境 → 用 Task tool
- 有 Python 实现 → 用 Python
- 默认 → 用 CLI

---

## 10. 实施路径

### Phase 1: 核心接口

| 文件 | 内容 |
|------|------|
| `core/workflow/__init__.py` | 导出 |
| `core/workflow/types.py` | Pydantic models |
| `core/workflow/agents.py` | Agent Protocol + Registry |

### Phase 2: WorkflowController

| 文件 | 内容 |
|------|------|
| `core/workflow/controller.py` | WorkflowController |
| `tests/test_workflow_controller.py` | 10+ pytest |

### Phase 3: Agent Validators

| 文件 | 内容 |
|------|------|
| `core/workflow/validator.py` | Agent Validators |
| `tests/test_workflow_validators.py` | 20+ pytest |

### Phase 4: Error Recovery

| 文件 | 内容 |
|------|------|
| `core/workflow/recovery.py` | Error Recovery |
| `tests/test_workflow_recovery.py` | 10+ pytest |

### Phase 5: Python API

| 文件 | 内容 |
|------|------|
| `core/workflow/api.py` | Python API |
| `tests/test_workflow_api.py` | 10+ pytest |

### Phase 6: CLI JSON

| 文件 | 内容 |
|------|------|
| `cli.py` | cmd_workflow_round(), cmd_workflow_loop() |
| `tests/test_workflow_cli.py` | 10+ pytest |

---

## 11. 关键设计决策

| 决策点 | 选择 |
|--------|------|
| 控制权 | 项目控制 (项目决定调用时机、验证输出、处理错误) |
| 验证深度 | 每个 Agent 都验证 (Schema + 逻辑 + 历史) |
| 错误恢复 | 回滚重试 (最多 3 次) |
| 集成方式 | Protocol + Registry (借鉴 vibe-trading) |
| 执行方式 | AgentLoop + Task tool + Python + CLI (四种并存) |
| 通信方式 | Agent 串行链 (简化 DAG) |

---

## 12. 文件结构

```
src/strategy_research/
├── core/
│   ├── workflow/                    # 新增
│   │   ├── __init__.py
│   │   ├── types.py
│   │   ├── controller.py
│   │   ├── agents.py
│   │   ├── validator.py
│   │   ├── recovery.py
│   │   ├── state.py
│   │   ├── backtest.py
│   │   ├── llm.py
│   │   ├── round.py
│   │   ├── decision.py
│   │   └── prompt.py
│   ├── agent/                       # 已存在 (另一个 Agent)
│   ├── llm/                         # 已存在
│   └── ...
├── cli.py                           # 修改
└── tests/
    ├── test_workflow_controller.py
    ├── test_workflow_validators.py
    ├── test_workflow_recovery.py
    └── ...
```

---

## 13. 参考资料

- `docs/archive/undated/autoresearch-design.md`: 完整设计
- `docs/archive/undated/vibe-trading-survey.md`: vibe-trading 架构
- `docs/archive/undated/enhancement.md`: 借鉴方案
- OpenCode session 数据库: 实验真相
