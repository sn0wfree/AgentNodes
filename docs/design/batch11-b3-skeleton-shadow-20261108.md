# 批次 11 设计 · B3 骨架隔离 阶段 1（影子模式）

> **上位设计**：`unified-loop-adoption-eval-20261107.md` §3 B3。
> **分期**：B3 原计划一次完成（骨架 + LoopSpec 收拢 42+ 参数 + mixins
> 解体 + maybe_await 收口）。实施前风险评估后**拆两阶段**：
>
> - **阶段 1（本批）**：`ReActSkeleton` 落地 + AgentLoop 影子开关
>   （默认 legacy，`AGENTNODES_SKELETON=1` 或 `engine="skeleton"` 启用）
>   + **等价证明**（同一 MockLLM 序列跑两条引擎，LoopResult 逐字段一致）
> - **阶段 2（第二阶段内）**：等价置信建立后翻默认 → LoopSpec 收拢 →
>   mixins 解体 → maybe_await 三处收口
>
> **为什么分期**：mixins 解体是 1,300 行在产功能的搬迁，与骨架重写叠加
> 会让回归面不可定位。影子模式让骨架在**不被默认路径依赖**的状态下
> 接受等价检验——这正是批次 7 之前权限闸「有管道没水」的反面教材的解法。

---

## §0 结论摘要

| 动作 | 文件 | 内容 |
|---|---|---|
| 新增 | `kernel/agent/skeleton.py` | `ReActSkeleton.run_stream(spec)`：PRECHECK → [REASON → DEC① → ACT → DEC② → OBSERVE] → FINALIZE，三闸位消费批次 10 的 deciders |
| 新增 | `kernel/agent/skeleton_adapters.py` | `LoopReasoner` / `LoopActor` / `LoopHooks`——绑定 AgentLoop 实例，复用其 strategy steps（沿用 `bind_agent_loop` 既有协议）|
| 改造 | `loop.py`（+~25 行） | `engine` 参数（默认 `"legacy"`）；`arun` 分派；**旧路径零改动** |
| 新增 | `tests/test_skeleton_equivalence.py` | MockLLM 场景 × 双引擎，LoopResult 逐字段一致 |

**行为零变化**：默认路径不经过骨架任何一行。

---

## §1 骨架状态机（对照 `_run_loop_core` 的映射）

```
PRECHECK（每轮开头）
  ├─ MaxIterationsDecider        （现 for-else :997）
  ├─ BudgetDecider               （现 :884-896 内联）
  └─ StopSignalDecider           （现 cancel/pause/inbox 语义，B3-2 补全）
REASON（= 现 compaction + llm_call 两个 step 的组合位）
  ├─ strategy.compaction
  ├─ drain inbox + injectors(0)
  ├─ strategy.llm_call → response
  └─ StopSignalDecider           （现 :875-879 should_stop/None）
DECIDE① after_reason
  ├─ TextFinalDecider            （现 :898 has_tool_calls 分支）
  │    └─ STOP → 走 legacy _handle_stop 尾语义（B3-2 收编）
  ├─ GoalContinuationDecider     （现 :902-918）
  │    └─ CONTINUE → 下一轮
  └─ PROCEED → ACT（有 tool_calls）
ACT
  ├─ CircuitBreakerDecider       （现 :936-942，pre_act 闸位）
  │    └─ CONTINUE → breaker messages + 下一轮
  └─ strategy.tool_execution     （现 :948-956）
     └─ StopSignalDecider（tool 失败 :954）
DECIDE② after_act
  └─ NoProgressDecider           （现 :971-996）
       └─ EXIT → return result（自带 after_run）
OBSERVE
  └─ messages/step 记录 + after_iteration hook（现 :963）
FINALIZE
  └─ strategy.finalization + git commit + trace close（现 :1005-1019）
```

**语义保真要点**（等价测试逐一覆盖）：
- budget 记账（`_record_budget_usage`）在 REASON 内、BudgetDecider 前
- 文本终答的 `_handle_stop` 尾语义（finished_reason/stop_reason 设置）
- 熔断开的 breaker messages 注入
- no-progress 的 `_check_no_progress` 交互（拒绝→EXIT / 批准→PROCEED）
- for-else 语义（循环体耗尽 → max_iter）由 PRECHECK 每轮判定等价替换

## §2 影子开关

```python
loop.run(task, engine="legacy")            # 默认：现状零改动
await loop.arun(task, engine="skeleton")   # 显式启用
# env: AGENTNODES_SKELETON=1 → 默认翻转（测试/灰度用）
```

`engine="skeleton"` 时 `arun` 调 `ReActSkeleton(handlers).run_to_completion(spec)`，
handlers 绑定 self。**sync `run` 不提供骨架路径**（骨架 async-first，
与 llmwikify 一致；sync 桥在阶段 2）。

## §3 等价证明（验收核心）

`tests/test_skeleton_equivalence.py`：场景 × 双引擎，断言
`LoopResult` 的 `final_content / stop_reason / finished_reason /
iterations / messages 长度 / tool_calls_made` 逐字段一致：

| 场景 | 覆盖的判定路径 |
|---|---|
| 纯文本一轮 | TextFinal STOP + finalize |
| tool_call → 文本 | PROCEED→ACT→OBSERVE→下一轮→TextFinal |
| 连续 N 轮 tool_call 撞上限 | MaxIterations（PRECHECK）|
| MockLLM 抛错 | StopSignal（LLM 错误）|
| budget_max_seconds=0 | BudgetDecider |

## §4 明确不做（阶段 1）

- mixins 解体 / LoopSpec 收拢 / maybe_await 收口（阶段 2）
- sync 引擎路径（阶段 2）
- `_run_loop_core` 删除（阶段 2 翻默认后）
- no-progress 的用户交互收编（`_check_no_progress` 保持原样调用）
