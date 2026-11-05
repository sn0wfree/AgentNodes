# P1-1 Loop 策略抽象

> **Status:** Completed (branch `p1-1-loop-strategy-abstraction`, merged to `main` as `94482c6`)
> **承接:** P0-1/P0-2/P0-3 完成事件源 + capability seams。本步把
> `AgentLoop._run_loop_core` 中的硬编码决策点抽出为 Step Protocol，
> 由 `LoopStrategy` 组合 Step 实现不同循环策略。

## 完成状态

| 阶段 | 标题 | 状态 | 提交 |
|------|------|------|------|
| 母文档 | 设计 | ✅ | `4c7d732` |
| L1 | LoopContext | ✅ | `0f0e7ec` |
| L2 | 9 个 Step Protocol | ✅ | `0f0e7ec` |
| L3 | 默认 Step 实现 | ✅ | `0f0e7ec` |
| L4 | LoopStrategy + LoopConfig | ✅ | `0f0e7ec` |
| L5 | Factory + ReActStrategy | ✅ | `0f0e7ec` |
| L6 | CustomStrategy | ✅ | `0f0e7ec` |
| L7-L8 | AgentLoop 实际迁移 | ⏭ 后续迭代 | - |
| 合并 | merge commit | ✅ | `94482c6` |

**测试**：20 个 P1-1 新增测试 + 240 个回归（合计 260）全绿。
**ruff**：P1-1 新增文件 0 错误。

## v0.1 范围说明

P1-1 提交的是**基础设施**（types + factory + no-op Step stubs）。
`AgentLoop._run_loop_core` 实际迁移到驱动 `LoopStrategy` 的
work 是**后续迭代**（设计文档中的 L7-L8 步骤）：
- L7：`_run_loop_core` 重写为 `for iteration in ...: ctx = strategy.<step>.execute(ctx)`
- L8：把现有 ReAct 行为逐一迁入对应 Step（CompactionStep、ProgressStep 等已有真实逻辑）

P1-1 完成的是"所有脚手架就位 + 测试验证组合语义 + CustomStrategy
override 可用"。`StrategyFactory.create("react")` 当前等价于
直接调 `ReActStrategyFactory.create()`。

## 后续（P1-2/3/4 候选）

- 实际替换 `AgentLoop._run_loop_core`（L7）
- 迁移 ReAct 行为到各 Step（L8）
- 新增 ExplorerStrategy / ValidatorStrategy / MinimalStrategy（每个是 P1-2/3/4 一周）
- Profile 集成（`LoopStrategy` 通过 Profile YAML 配置）

## 目标

将 `AgentLoop` 中硬编码的 ReAct 循环改为可插拔的 `LoopStrategy`：

```python
LoopStrategy (组合)
├── PreRunStep        # 预运行准备（hypothesis / goal / context 注入）
├── LLMCallStep       # LLM 调用模式（流式/批量 + 降级）
├── CompactionStep    # 上下文压缩
├── StopStep          # 停止条件
├── ContinuationStep  # 目标续写
├── ProgressStep      # 无进展检测
├── ResilienceStep    # 断路器
├── ToolExecutionStep # 工具执行
└── FinalizationStep  # 后置处理

预设策略：
├── ReActStrategy       # 默认（当前行为）
├── ExplorerStrategy    # 探索模式（高迭代、宽松检测）
├── ValidatorStrategy   # 验证模式（低迭代、严格 claim validation）
├── MinimalStrategy     # 最小模式（只读工具、一次性）
└── CustomStrategy      # 用户自定义（继承 + 覆盖 step）
```

## 关键设计

### Step 协议

每个 Step 实现特定决策点。Step 接口：

```python
class Step(Protocol):
    """单个决策点协议。"""
    name: str
    
    def should_run(self, ctx: LoopContext) -> bool: ...
    def execute(self, ctx: LoopContext, *, async_mode: bool) -> LoopContext: ...
```

### LoopContext：共享状态

`LoopContext` 在循环内传递所有 step 需要的共享状态：

```python
@dataclass
class LoopContext:
    # 输入
    task: str
    context: str | None
    history: list[dict[str, Any]] | None
    
    # 运行时
    messages: list[dict[str, Any]] = field(default_factory=list)
    result: LoopResult = field(default_factory=LoopResult)
    iteration: int = 0
    t0: float = 0.0
    
    # 工具状态
    recent_hashes: list[str] = field(default_factory=list)
    tool_calls_made: int = 0
    previous_summary: str | None = None
    
    # 停止信号
    should_stop: bool = False
    stop_reason: str | None = None
    
    # 扩展
    metadata: dict[str, Any] = field(default_factory=dict)
```

### LoopStrategy

`LoopStrategy` 是 Step 的组合容器：

```python
@dataclass
class LoopStrategy:
    name: str
    description: str
    
    pre_run: PreRunStep
    llm_call: LLMCallStep
    compaction: CompactionStep
    stop: StopStep
    continuation: ContinuationStep
    progress: ProgressStep
    resilience: ResilienceStep
    tool_execution: ToolExecutionStep
    finalization: FinalizationStep
    
    config: LoopConfig = field(default_factory=LoopConfig)
    
    def should_continue(self, ctx: LoopContext) -> bool: ...
```

### 集成到 AgentLoop

`AgentLoop.__init__` 接受可选 `strategy` 参数；默认 `ReActStrategy()`：

```python
class AgentLoop:
    def __init__(self, ..., strategy: LoopStrategy | str | None = None):
        if isinstance(strategy, str):
            self._strategy = StrategyFactory.create(strategy)
        elif strategy is None:
            self._strategy = ReActStrategyFactory.create()
        else:
            self._strategy = strategy
        # 从策略配置覆盖默认值
        self.max_iterations = self._strategy.config.max_iterations
        self.no_progress_window = self._strategy.config.no_progress_window
        ...
```

`_run_loop_core` 改为驱动 strategy：

```python
async def _run_loop_core(self, task, context, history, *, async_mode):
    # ... trace context 注入 ...
    ctx = LoopContext(task=task, context=context, history=history)
    ctx = self._strategy.pre_run.execute(ctx)
    
    for iteration in range(1, self.max_iterations + 1):
        ctx.iteration = iteration
        # 压缩（如果策略允许）
        ctx = await self._strategy.compaction.execute(ctx, async_mode=async_mode)
        # LLM 调用
        response = await self._strategy.llm_call.execute(ctx, async_mode)
        if response is None:
            break
        # 核心决策
        if not self._strategy.should_continue(ctx):
            break
        # 工具执行
        ctx = await self._strategy.tool_execution.execute(ctx, response)
    
    ctx = await self._strategy.finalization.execute(ctx, async_mode)
    return ctx.result
```

## 实施步骤

| 步骤 | 内容 | 验证 |
|------|------|------|
| L1 | `core/agent/strategy/loop_context.py` — `LoopContext` dataclass | dataclass 创建 + 默认值 |
| L2 | `core/agent/strategy/protocol.py` — `Step` + 9 个 Step Protocol | runtime_checkable |
| L3 | `core/agent/strategy/steps/` — 9 个 Step 默认实现（从 AgentLoop 提取） | 单元测试 |
| L4 | `core/agent/strategy/loop_strategy.py` — `LoopStrategy` 组合 | dataclass |
| L5 | `core/agent/strategy/factory.py` — `StrategyFactory` + 4 个预设 | registry |
| L6 | `core/agent/strategy/custom.py` — `CustomStrategy` 继承基类 | 简单 test |
| L7 | `AgentLoop.__init__` 接受 strategy + `_run_loop_core` 驱动 | 默认 = ReAct 现有行为 |
| L8 | 测试 15+ + AgentLoop 集成测试 | 现有 112+ 测试全绿 |

## 风险

| 风险 | 缓解 |
|------|------|
| AgentLoop 集成破坏现有 112+ 测试 | `ReActStrategy` 100% 复刻当前行为；默认 `strategy=None` 走 ReAct |
| 9 个 Step Protocol 抽象粒度过细 | 每个 Step 单一职责；组合通过 `LoopStrategy` |
| 同步/异步路径分裂 | Step 接口 `execute(ctx, *, async_mode)` 参数化 |
| `LoopContext` 数据结构膨胀 | 默认值清晰；扩展走 `metadata` dict |

## 不在 P1-1 范围

- 实际替换 `AgentLoop._run_loop_core`（L7 只做骨架 + 接口）— L8 后续迭代
- 新增 LoopStrategy 子类（ExplorerStrategy / ValidatorStrategy / MinimalStrategy
  在后续 P1-2/3 提供）
- LoopStrategy 持久化（profile 集成 — P1-2 范围）