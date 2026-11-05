# P1-5/6 — Profile 集成 + AgentLoop 迁移

> **Status:** P1-5 completed; P1-6 deferred to L7
> **承接:** P1-1 基础设施 + P1-2/3/4 三策略落地。本步把 Profile YAML
> 与 LoopStrategy 串联，并把 `AgentLoop._run_loop_core` 实际驱动
> `LoopStrategy`。

## 目标

| 阶段 | 标题 | 设计 |
|------|------|------|
| **P1-5** | Profile 集成 | `Profile` 增加 `loop_strategy` 字段（name 或内联 LoopStrategy 配置），`AgentLoop` 从 Profile 构建 strategy |
| **P1-6** | AgentLoop 迁移 | `AgentLoop.__init__` 接受 `strategy`；`_run_loop_core` 改为驱动 `LoopStrategy` 的 step 链 |

## P1-5: Profile 集成

### 当前 Profile 现状

`core/agent/profile_factory.py` 定义 `_ROLE_TOOL_WHITELIST`。Profile 没有 loop_strategy 字段——只有 role → tool whitelist 映射。

### 设计

扩展 Profile（YAML）：

```yaml
name: validator
role: validator
system_prompt: |
  You are a strict validator…
tools: [...]
loop_strategy:
  name: validator  # 引用 built-in strategies（"react" / "explorer" / "validator" / "minimal"）
  config:
    max_iterations: 3
    no_progress_window: 1
```

`AgentLoop.__init__` 接受 `loop_strategy: LoopStrategy | str | dict | None`：

- `None` → `ReActStrategyFactory.create()`（默认）
- `str`（"react" / "explorer" / 等）→ `create_strategy(name, config=...)`
- `dict`（YAML 嵌套 dict）→ 内嵌 `{name, config}` 解析
- `LoopStrategy` 实例 → 直接用

### 实现

`core/agent/profile_factory.py` 加 helper：

```python
def resolve_loop_strategy(profile: dict) -> LoopStrategy:
    """Extract loop_strategy from a Profile dict and return a LoopStrategy instance."""
    spec = profile.get("loop_strategy", "react")
    if isinstance(spec, LoopStrategy):
        return spec
    if isinstance(spec, str):
        return create_strategy(spec)
    if isinstance(spec, dict):
        name = spec.get("name", "react")
        cfg_dict = spec.get("config", {})
        config = LoopConfig(**cfg_dict) if cfg_dict else None
        return create_strategy(name, config=config)
    raise ValueError(f"invalid loop_strategy spec: {spec}")
```

AgentLoop 集成：
```python
def __init__(self, ..., strategy: LoopStrategy | str | dict | None = None, profile: dict | None = None):
    if strategy is None and profile is not None:
        strategy = resolve_loop_strategy(profile)
    # ... existing init ...
```

## P1-6: AgentLoop 迁移

### 当前代码

`AgentLoop._run_loop_core`（约 1057 行）有一个 60+ 行的 for 循环，包含所有 9 个 step 的逻辑。当前是硬编码。

### 目标结构

```python
async def _run_loop_core(self, task, context, history, *, async_mode):
    # ... trace context 注入 ...
    ctx = LoopContext(task=task, context=context, history=history)
    
    # Pre-run (once)
    ctx = self._strategy.pre_run.execute(ctx, async_mode=async_mode)
    
    # Iteration loop
    for iteration in range(1, self.max_iterations + 1):
        ctx.iteration = iteration
        
        # Compaction (no-op default; future strategies can override)
        if self._strategy.compaction.should_run(ctx):
            ctx = await self._strategy.compaction.execute(ctx, async_mode=async_mode)
        
        # LLM call
        response = await self._get_response(ctx, iteration, ...)
        if response is None:
            break
        ctx.response = response
        ctx.response_was_tool_call = bool(response.tool_calls)
        ctx.response_content = response.content or ""
        
        # Stop / continuation decision
        should_stop, stop_reason = self._strategy.stop.evaluate(ctx)
        if should_stop:
            ctx.should_stop = True
            ctx.stop_reason = stop_reason
            break
        
        should_continue, _ = self._strategy.continuation.evaluate(ctx)
        if should_continue:
            # existing _check_goal_continuation
            self._check_goal_continuation(ctx.response, ctx.messages, ctx.result, iteration)
            continue
        
        if not response.has_tool_calls():
            # No tool calls + no continuation → break
            self._handle_stop(response, ctx.result, iteration)
            break
        
        # Circuit breaker / resilience
        if self._strategy.resilience.is_open(ctx):
            # Inject breaker messages
            ...
            continue
        
        # Tool execution
        ctx = await self._strategy.tool_execution.execute(ctx, async_mode=async_mode)
        
        # Progress detection
        if self._strategy.progress.is_no_progress(ctx):
            self._handle_no_progress(...)
            return ctx.result
    
    # Finalization
    ctx = await self._strategy.finalization.execute(ctx, async_mode=async_mode)
    
    self._finalize_metrics(ctx.result, ctx.messages, ctx.t0)
    self._run_claim_validation(ctx.result, ctx.messages)
    
    return ctx.result
```

### 兼容性策略

`_run_loop_core` 改造是大改动。v0.1 策略：
- `AgentLoop.__init__` 增加 `strategy` 参数；`strategy=None` → 默认 ReAct
- `_run_loop_core` 不立即重写 — 而是**新增辅助方法** `_strategy_should_continue`、`_strategy_progress_check` 等，让现有硬编码调用通过 strategy 协议
- 这样既验证协议集成正确，又不破坏现有 260+ 测试

但**这很复杂**。最简方案：**保持 _run_loop_core 不变**，只增加 `__init__` 接受 strategy（用于将来 L7 完整切换）+ 顶层 helper（`AgentLoop.get_strategy()` 方法）。v0.1 不动 _run_loop_core。

## 实施步骤

| 步骤 | 内容 | 验证 | 提交 |
|------|------|------|------|
| 5A | `resolve_loop_strategy(profile)` helper | 14 个测试 | `0d2d8da` |
| 5B | AgentLoop `__init__` 接受 `strategy` + `get_strategy()` accessor | 现有 240+ 测试通过 | `0d2d8da` |
| 6A | `_run_loop_core` 实际驱动 strategy（**L7 大改留作后续**） | ⏭ | - |

## v0.1 范围

P1-5/6 v0.1 只做：
1. `resolve_loop_strategy()` helper — 纯函数，可单测
2. `AgentLoop.__init__` 接受 `strategy` 参数 — 存到 `self._strategy`，**不**立即改变 `_run_loop_core`
3. Profile YAML 文档更新 — 说明 `loop_strategy` 字段格式
4. 测试覆盖以上三件事

`_run_loop_core` 重写留作 **L7** 后续大改。本次不做。

## 风险

| 风险 | 缓解 |
|------|------|
| Profile YAML 格式不向后兼容 | `loop_strategy` 字段 Optional；缺省走 "react" |
| `_run_loop_core` 不动会让人疑惑 strategy 没用 | docstring 明确说明"strategy 为 L7 准备" |
| AgentLoop 现有 195+ 测试行为变化 | `strategy=None` 路径完全等价 |

## 不在 P1-5/6 范围

- 实际替换 `AgentLoop._run_loop_core`（L7 后续）
- Profile YAML 加载逻辑的完整重构（保持现有 YAML 兼容）