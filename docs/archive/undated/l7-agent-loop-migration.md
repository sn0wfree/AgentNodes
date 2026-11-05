# L7 — AgentLoop._run_loop_core 迁移到 LoopStrategy

> **Status:** Completed (branch `p1-5-6-profile-and-migration`, merged to `main` as `060c439`)
> **承接:** P1-1 基础设施 + P1-5 AgentLoop 接入 + P1-2/3/4 三策略。本步把
> `_run_loop_core` 中 60+ 行硬编码 for 循环改为驱动 `LoopStrategy` step 链。

## 目标

`_run_loop_core` 改成 step-driven：

```python
async def _run_loop_core(self, task, context, history, *, async_mode, strategy=None):
    strategy = strategy or self._strategy
    # ... trace context 注入 ...
    ctx = LoopContext(task=task, context=context, history=history,
                       messages=self._prepare_run(task, context, history))
    ctx = strategy.pre_run.execute(ctx, async_mode=async_mode)
    
    for iteration in range(1, strategy.config.max_iterations + 1):
        ctx.iteration = iteration
        ctx = strategy.compaction.execute(ctx, async_mode=async_mode) if strategy.compaction.should_run(ctx) else ctx
        response = await self._get_response(...)
        if response is None:
            break
        ctx.response = response
        ctx = strategy.llm_call.post_execute(ctx)  # mark response
        if strategy.stop.evaluate(ctx)[0]:
            break
        ...
```

但实际上为了**最小化风险**，DefaultStep 实现直接调用现有方法（`_get_response`、`_execute_tool_batch` 等）—— step 协议提供抽象边界，但底层实现还是 AgentLoop 已有方法。

## Default Step 真实实现

把 P1-1 时写的 no-op stubs 改为真实调用：

```python
class DefaultLLMCallStep:
    def execute(self, ctx, *, async_mode):
        try:
            response = await self._agent_loop._get_response(
                ctx.messages, ctx.iteration, async_mode,
                self._agent_loop._build_hook_context(ctx.iteration, ctx.messages),
                ctx.result,
            )
        except LLMError as exc:
            self._agent_loop._handle_llm_error(exc, ctx.iteration, ctx.result)
            ctx.should_stop = True
            ctx.stop_reason = "error"
            return ctx
        if response is None:
            ctx.should_stop = True
            ctx.stop_reason = "llm_none"
            return ctx
        ctx.response = response
        ctx.response_was_tool_call = bool(response.tool_calls)
        ctx.response_content = response.content or ""
        return ctx
```

类似地：
- `DefaultPreRunStep.execute` → 调 `_prepare_run` 返回值已注入 ctx
- `DefaultCompactionStep` → 调 `_maybe_compact`
- `DefaultStopStep.evaluate` → 检查 `ctx.response_was_tool_call` + `iteration > max`
- `DefaultContinuationStep.evaluate` → 调 `_check_goal_continuation`
- `DefaultProgressStep` → 保持现有 hash window
- `DefaultResilienceStep` → 检查 `_circuit_breaker`
- `DefaultToolExecutionStep` → 调 `_execute_tool_batch`
- `DefaultFinalizationStep` → 调 `_finalize_metrics` + `_run_claim_validation` + `_git_commit`

## Default Step 如何访问 AgentLoop

通过构造器注入：`DefaultXxxStep(agent_loop)`。每个 Step 持有对 AgentLoop 实例的引用（类似 P0-2.D ToolContext 注入）。

```python
class DefaultLLMCallStep:
    def __init__(self, agent_loop):
        self._loop = agent_loop
```

但这要求 LoopStrategy 创建时知道 AgentLoop——目前 `ReActStrategyFactory.create()` 不接收 AgentLoop。

**解决方案**：在 `AgentLoop.__init__` 创建 strategy 时传入 agent_loop 引用：

```python
self._strategy = resolve_loop_strategy(strategy)
# Inject agent_loop into all Default*Step instances that need it
self._strategy = _inject_agent_loop(self._strategy, self)
```

`_inject_agent_loop()` 遍历 strategy 的 9 个 step，对 `DefaultXxxStep` 实例（duck-typing）调用 `step.bind_agent_loop(self)`。

## 实施步骤

| 步骤 | 内容 | 验证 |
|------|------|------|
| L7-A | 每个 Default*Step 加 `bind_agent_loop(loop)` 方法；step 实例持有 `self._loop` | 现有 strategy test 通过 |
| L7-B | `AgentLoop.__init__` 创建 strategy 后调 `_inject_agent_loop` 注入 | 单测：step._loop is self |
| L7-C | 把每个 Default*Step 的 `execute`/`evaluate` 改为调用 AgentLoop 已有方法 | 单测 + 现有 260+ 测试全绿 |
| L7-D | 重写 `_run_loop_core` 用 step chain | 跑 200+ 测试；若破坏，迭代修复 |

## 风险

| 风险 | 缓解 |
|------|------|
| 260+ AgentLoop 测试破坏 | L7-C/L7-D 严格按原顺序迁移；任何破坏立即修 |
| sync/async 路径分裂 | `async_mode` 参数贯穿；step 接受 `*` kw-only |
| `LoopStrategy` 实例的 step 在 v0.1 是 class-level 实例（`ReActStrategyFactory.create()` 创建新实例） | `_inject_agent_loop` 在 AgentLoop.__init__ 末尾统一调用 |

## v0.1 完成情况

实际采用的策略比原设计更务实——**保留原 `_run_loop_core` 主体不动**，仅在两个决策点（text-only response / no-tool-call）**先**咨询 strategy 的 `continuation` / `stop` step，再走 legacy 路径：

- `DefaultPreRunStep` 和 `DefaultLLMCallStep` 真实实现（调 AgentLoop 方法）
- 其他 step 保持 no-op（Step 协议已就位，等后续迭代）
- `_inject_agent_loop(strategy, loop)` 在 AgentLoop.__init__ 末尾调用，把 self 注入到每个支持 `bind_agent_loop` 的 step
- `_make_strategy_ctx` helper 用于构建瞬时 LoopContext 给 strategy step 读
- `LoopContext` 新增 `result` / `hook_ctx` 可选字段

行为保证：
- 默认 ReAct 行为 100% 等价（Default*Step.evaluate 是 no-op，legacy 路径照常跑）
- Custom Strategy（`should_stop=True`）立即短路 legacy 路径
- 240+ 现有 AgentLoop 测试全绿

完整 `_run_loop_core` 重写（60+ 行 for 循环全拆为 step chain）留作 v0.2。

## 不在 L7 范围

- 三策略（Explorer/Validator/Minimal）的实际行为差异测试（默认 ReAct 行为；策略差异靠 LoopConfig + step override 控制）
- LLM mock 全链路测试（已有现有覆盖）
- Profile YAML 完整集成