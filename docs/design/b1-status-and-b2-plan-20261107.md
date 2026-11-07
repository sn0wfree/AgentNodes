# B1 完成后的引擎收敛状态（2026-11-07）

## 已落地（批次 9，39316e70）

- `kernel/agent/_core_types.py`：StepResult / StepHandler / StreamingHandler / Pipeline / maybe_await
- `kernel/agent/step_adapters.py`：LegacyStepAdapter / FnStreamingHandler
- `strategy/protocol.py`：docstring 指向新底座（零运行时改动）
- 18 个新测试，含互操作证明（真实 legacy step + 新式 handler 同 Pipeline）

## 下一批 · B2 deciders 配置化（第一阶段架构验收 2/2）

1. **先写映射表测试**：枚举现有全部停止路径——
   - 迭代上限（`_handle_max_iter` loop_lifecycle.py:41）
   - 迭代超时（`_handle_iteration_timeout` :30）
   - LLM 空响应/错误（llm_call 步骤置 should_stop）
   - StopStep（无 tool_calls → 停）
   - ContinuationStep（goal 未覆盖 → 续跑注入）
   - ResilienceStep（熔断 open → 停）
   - ProgressStep（重复 hash N 次 → 停）
   - 预算耗尽（`_check_budget`）
   断言：新 decider 组合对同一 ctx 判定一致。
2. `kernel/agent/deciders.py`：`Decider` 协议（handle → StepResult{output=(stop,reason)}）
   + 默认实现 MaxIterations / StopOnNoToolCalls / GoalContinuation /
   RepeatHash / CircuitBreaker / BudgetExhausted
3. `kernel/agent/modes.py`：AgentModeConfig + register_mode + create_agent_loop
   （从 reference/llmwikify registry.py 移植，spec_cls 留 B3）
4. `loop.py::_run_loop_core` 三检查点改造——查 `self._deciders.get(位置)`；
   默认 dict 由 LoopStrategy 槽位映射组装（Stop/Continuation→after_reason，
   Resilience/Progress→after_act，预算/上限→after_observe 或 precheck）
5. 验收：映射表测试全过 + 全量 0 failed + capability_smoke + E1-E5

## 再下一批 · B3（第二阶段开头）

skeleton.py ≤350 行（循环体纯化）→ 预算/假设/goal/git/trace 变 hook →
LoopSpec 收拢 42+ 构造参数 → mixins 解体 → loop.py ≤400 行。
maybe_await 三处同型收口（_core_types / sync_bridge / _call_step）。
