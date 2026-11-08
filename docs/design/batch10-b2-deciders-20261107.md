# 批次 10 设计 · B2 deciders 配置化（第一阶段架构验收 2/2）

> **上位设计**：`unified-loop-adoption-eval-20261107.md` §3 B2。
> **与评估文档的一处修正**（实施时发现，见 §1）：deciders **包装**
> 现有 strategy steps（组合），不**重编码**其逻辑（评估原文暗示重编码）。
> `_run_loop_core` 三检查点接线**推迟到 B3**——理由见 §4。

---

## §0 结论摘要

| 动作 | 文件 | 内容 |
|---|---|---|
| 新增 | `kernel/agent/deciders.py` | `Decision` 四值枚举 + `Decider` 基类 + 6 个默认实现（**包装**现有 steps）|
| 新增 | `kernel/agent/modes.py` | `AgentModeConfig` + register/get/list（**不含工厂**，理由 §4）|
| 新增 | `tests/test_deciders.py` | **映射表测试**：8 条停止路径逐一断言 decider 与现有 step 同判定 |

**行为零变化**由构造保证：不触碰 `loop.py` / steps / mixins 任何一行。

---

## §1 核心裁决 · 包装而非重编码

评估文档原设想：deciders 把 StopStep/ContinuationStep/ProgressStep/
ResilienceStep 的逻辑**重编码**为新实现，映射表测试防漂移。

实施时发现三层问题：

1. **漂移风险是结构性的**：4 个 step 各有 loop 绑定降级路径
   （`DefaultProgressStep` 无 loop 时回退 `ctx.recent_hashes`；
   `DefaultResilienceStep` 无 loop 时恒 False）——重编码要复刻全部
   分支，映射表测不全就会静默漂移
2. **语义已有分歧**：`ResilienceStep.is_open` 的结论是「跳过工具执行、
   进下一轮」**不是 stop**；`ProgressStep` 触发是「退出整个 run」
   （return result）**也不是 stop**——二值 (stop, reason) 装不下
3. **组合优于复制**：包装则等价性由构造保证（同一函数被调用），
   映射表测试只需验证**形状适配**，不需验证**逻辑复刻**

**因此**：`Decider` 持有一个 step 引用，`handle` 调用其
`evaluate`/`is_open`/`is_no_progress` 并把结论翻译为四值 `Decision`。

## §2 Decision 四值枚举（二值装不下的实测证据）

| 值 | 语义 | 现有循环中的出处 |
|---|---|---|
| `PROCEED` | 进入下一步骤（ACT 或 OBSERVE） | tool-call 响应正常流转（loop.py:898 else 分支）|
| `CONTINUE` | 跳过剩余步骤，直接下一轮迭代 | goal 续跑注入（:917 continue）、熔断开（:942 continue）|
| `STOP` | 结束循环，走 finalize | 文本终答（:930）、预算耗尽（:896）、tool 失败（:956）、上限（:998）|
| `EXIT` | 结束整个 run（**跳过 finalize 主路径**，自带 after_run）| no-progress 用户拒绝/超时拒绝（:996 return result）|

## §3 六个默认 decider（全部包装）

| Decider | 包装 | 检查点 | 映射 |
|---|---|---|---|
| `StopSignalDecider` | `ctx.should_stop`（LLM 错误/tool 失败置位）| after_reason | True→STOP(reason) |
| `BudgetDecider` | `BudgetStopStep.evaluate` | after_reason | True→STOP |
| `TextFinalDecider` | `not response.has_tool_calls()`（新实现，5 行——现内联在 loop.py:898，无 step 可包）| after_reason | True→STOP(final) |
| `GoalContinuationDecider` | `strategy.continuation.evaluate` | after_reason | True→CONTINUE |
| `CircuitBreakerDecider` | `strategy.resilience.is_open` | **pre_act**（现有位置在工具执行**前**，不是 after_act——修正评估文档的检查点命名）| True→CONTINUE(跳过工具) |
| `NoProgressDecider` | `strategy.progress.record_hash + is_no_progress` | after_act（消息已更新后）| True→EXIT |
| `MaxIterationsDecider` | 计数（iteration ≥ max）| precheck | True→STOP |

检查点命名修正：评估文档说 after_reason/after_act/after_observe 三处；
实测现有循环的**闸位**是 precheck（上限/超时/预算）、after_reason
（续跑/终答/熔断闸）、after_act（no-progress）。B3 skeleton 按此设闸。

## §4 工厂推迟到 B3（诚实理由）

评估文档把 `create_agent_loop` 排在 B2。实施时发现：它要创建的对象
（消费 deciders 的骨架）**B3 才存在**——现在出货只能是一个 raise
NotImplementedError 的桩，那正是我们连续清剿的「有 API 无消费」病。

**B2 的 modes.py 只做注册表**（AgentModeConfig + register/get/list +
`deciders_for(strategy, loop)` 组装器）——B3 skeleton 落地时工厂一行接上。
偏差已记录，理由可审计。

## §5 映射表测试（8 条路径逐一）

| # | 路径 | 现有实现 | decider | 断言 |
|---|---|---|---|---|
| 1 | 迭代上限 | `_handle_max_iter`（for-else）| `MaxIterationsDecider` | iter≥max → STOP |
| 2 | LLM 错误 | `llm_ctx.should_stop` | `StopSignalDecider` | 同判定同 reason |
| 3 | 预算耗尽 | `BudgetStopStep.evaluate` + 内联 `_check_budget` | `BudgetDecider` | 超限 → STOP |
| 4 | 文本终答 | 内联 `not has_tool_calls()` | `TextFinalDecider` | 同判定 |
| 5 | goal 续跑 | `DefaultContinuationStep` | `GoalContinuationDecider` | False→PROCEED |
| 6 | 熔断 | `DefaultResilienceStep.is_open` | `CircuitBreakerDecider` | True→CONTINUE |
| 7 | 重复 hash | `DefaultProgressStep` | `NoProgressDecider` | 窗口全同 → EXIT |
| 8 | 外部 stop | `ctx.should_stop` | `StopSignalDecider` | 透传 reason |

迭代**超时**不入表：它是 per-step 包装器（`_with_iteration_timeout`），
不是检查点判定——B3 骨架的 precheck 接管，文档记录。

## §6 执行顺序

1. deciders.py（Decision + 6 实现）
2. modes.py（注册表 + deciders_for 组装器）
3. tests/test_deciders.py（映射表 + 四值枚举 + 注册表）
4. 全量 + batch_gate + 提交

**不做**：`_run_loop_core` 接线（B3 skeleton 消费，旧循环体上接线是
一次性代码）、create_agent_loop 工厂（B3）、迭代超时 decider 化（B3 precheck）。
