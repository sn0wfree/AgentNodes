# P1-2/3/4 — 三个一策略实现

> **Status:** Completed (branch `p1-2-3-4-strategies`, merged to `main` as `a9a3c1c`)
> **承接:** P1-1 LoopStrategy 基础设施。每个策略是一个 `LoopStrategy`
> 子类 + 重写对应 Step + 在 factory 注册。

## 目标

| 策略 | 文件 | 设计重点 |
|------|------|----------|
| **ExplorerStrategy** | `core/agent/strategy/explorer.py` | 高迭代上限（50）、宽松进展检测（窗口=5）、不强制 claim validation |
| **ValidatorStrategy** | `core/agent/strategy/validator.py` | 低迭代上限（5）、严格进展检测（窗口=2）、最终化强制 claim validation |
| **MinimalStrategy** | `core/agent/strategy/minimal.py` | 单次 LLM 调用、只读工具、不调用 LLM 增量 |

三个策略都继承 `CustomStrategy`（来自 P1-1 factory.py），只重写需要不同行为的 Step。

## ExplorerStrategy（P1-2）

设计：探索模式适合大型研究任务，需要：
- 高迭代上限（默认 50）
- 宽松无进展检测（窗口=5）
- LLM call：单次更长的 prompt 而非反复
- 不强制 goal continuation

实现要点：
```python
class ExplorerStrategy(CustomStrategy):
    def __init__(self):
        super().__init__(
            name="explorer",
            base_strategy=ReActStrategyFactory.create(
                LoopConfig(max_iterations=50, no_progress_window=5)
            ),
        )
```

## ValidatorStrategy（P1-3）

设计：验证模式适合 claim-heavy 任务：
- 低迭代上限（默认 5）
- 严格进展检测（窗口=2）
- Finalization 强制跑 claim validation
- StopStep 在残留 todo 时强制继续

实现要点：
- FinalizationStep 重写为：调 `_run_claim_validation` 并设置 `result.claim_validation_passed`
- LoopConfig 收紧 `max_iterations=5, no_progress_window=2`

## MinimalStrategy（P1-4）

设计：最小模式用于只读探索：
- `max_iterations=1`（只调一次 LLM）
- ToolExecutionStep 不跑（直接返回 ctx，停止）
- 实际上：把 max_iterations 设 1 让循环跑一次然后停止

实现要点：
```python
class MinimalStrategy(CustomStrategy):
    def __init__(self):
        super().__init__(
            name="minimal",
            base_strategy=ReActStrategyFactory.create(
                LoopConfig(max_iterations=1, parallel_tool_execution=False),
            ),
            tool_execution=NoOpToolExecutionStep(),  # 实际跳过工具
        )
```

## 实施步骤

| 步骤 | 内容 | 验证 | 提交 |
|------|------|------|------|
| 2A | `core/agent/strategy/explorer.py` + factory 注册 `"explorer"` | 5 个测试 | `77e3ab4` |
| 2B | `core/agent/strategy/validator.py` + factory 注册 `"validator"` | 5 个测试 | `77e3ab4` |
| 2C | `core/agent/strategy/minimal.py` + factory 注册 `"minimal"` | 5 个测试 | `77e3ab4` |
| 2D | `__init__.py` re-export + 集成测试 | 现有 21 个测试 + 23 个新增 | `77e3ab4` |
| 合并 | merge commit | ✅ | `a9a3c1c` |

**测试**：23 个 P1-2/3/4 新增 + 21 个 P1-1 回归 = 44 个策略相关测试全绿；总回归 270+ 测试通过。
**ruff**：P1-2/3/4 新增文件 0 错误。

## 风险

| 风险 | 缓解 |
|------|------|
| 三个策略行为差异不易测 | 每个策略只验关键 Step override + LoopConfig；不测内部循环 |
| ReActStrategyFactory 默认覆盖 | CustomStrategy 接受 `base_strategy`，所以 `config` 走 base |
| `NoOpToolExecutionStep` 不存在 | 新增 helper step 模块 |

## 不在 P1-2/3/4 范围

- 实际 `AgentLoop._run_loop_core` 迁移（L7 后续）
- Profile YAML 集成（P1-5）