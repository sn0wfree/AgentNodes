# 批次 13 设计 · B3 阶段 2 S3（mixin 解体）—— 附：默认引擎能力缺口

> **上位设计**：`batch12-b3-stage2-subtraction-20261109.md` §1 S3。
> **性质**：本批是阶段 2 的最后一步，也是风险最高的一步。
> **前置结论变更**：S3 的原始范围（6 个 mixin 搬家）**不足以**保证 S1 翻默认是安全的。
> 本批因此分成「先补能力、再搬代码」两个阶段。

---

## §0 结论摘要（三句话）

1. **S1 翻默认之后，默认引擎（skeleton）已丢失 6 项在产能力**：预算记账、
   `wrap_up_nudge`、用户输入 guardrail、`max_iter` 终答文案、hook 计数偏差，
   以及**最严重的 post-response injectors / goal 续跑**。
   全部有实测证据（§1）。
2. 因此 S3 **不能只做搬家**。先把这 5 个缺口补进 skeleton 路径，再动 mixin——
   否则「搬家」搬的是删掉了功能之后的空壳，全量测试仍会绿（因为这些能力
   没有任何测试在 skeleton 引擎上断言过），这与本项目反复出现的
   「空集合不是通过」是同一个失败模式。
3. 补完之后，`loop.py` 的行数目标（≤400）**单靠 S3 达不到**：实测下限
   约 600 行（§3）。必须同时搬走 `__init__` 的赋值块与 trace 组，
   并把 `_run_loop_core` 整体删除。≤400 需要在阶段 3 删旧构造参数后才现实。

---

## §1 现状实测：默认引擎的能力缺口

测试方法：同一份 mock LLM、同一份 workspace，`arun(..., engine="legacy")`
与 `arun(..., engine="skeleton")` 逐字段对比。探针脚本
（`.tmp_s3/gap_probe4.py`，不进仓库）。

### 1.1 预算记账 —— **完全丢失**

| engine | `finished_reason` | `iterations` | `metrics.usage_tokens` | `loop._budget_tokens` |
|---|---|---|---|---|
| legacy | `budget` | 1 | 150 | 150 |
| skeleton | `stop` | 5 | **0** | **0** |

条件：`strategy={"name":"react","config":{"budget_max_tokens":100}}`，
每次响应 `usage.total_tokens=150`。

**根因链（已逐点验证）**：

- `_budget_tokens` 全仓只有两个写入点：`loop.py:658`（`_prepare_run` 置零）、
  `loop.py:677`（`_record_budget_usage` 累加）。
- `_record_budget_usage` 的唯一调用点是 `loop.py:1052`，位于 `_run_loop_core`。
- `_make_strategy_ctx`（`loop.py:159`）把 `loop._budget_tokens` 塞进
  `LoopContext.tokens_used`；`BudgetDecider` → `BudgetStopStep.evaluate`
  读 `ctx.tokens_used`。
- 骨架路径只调 `_prepare_run`（置零）**不调** `_record_budget_usage`，
  于是 `tokens_used` 恒为 0，`evaluate_budget(0, limit=100)` → `(False, None)`。

**影响**：`budget_max_tokens` / `budget_max_cost_usd` 这两个上限在生产
（`service.py:993` 走 `arun`）**完全不生效**。这是安全相关的倒退：成本失控
不会被拦住。`budget_max_seconds` 仍然生效（它只依赖 `t0`），所以测试里
`budget_exhausted` 场景能通过——**这正是它没被发现的第二个原因**：
等价测试挑的预算维度恰好是唯一还活着的那一维。

`metrics.usage_tokens` 同样恒 0，`evals/run_evals_llm.py:111` 的
成本统计因此静默归零。

### 1.2 `wrap_up_nudge` —— **丢失**

条件：`max_iterations=5, wrap_up_nudge=True`，5 轮不同参数的 tool_call。

| engine | 每轮 system prompt 里含 "Wrap-up" |
|---|---|
| legacy | `[0, 0, 0, 1, 1]` |
| skeleton | `[0, 0, 0, 0, 0]` |

**根因**：注入逻辑内联在 `_run_loop_core:1002-1009`。骨架的
`ReActSkeleton.__init__` 预留了 `on_iteration_start` 钩子，但
`loop._run_via_skeleton`（`loop.py:899-906`）**从未传入**。

### 1.3 用户输入 guardrail 扫描 —— **丢失**（工具结果侧正常）

| engine | 用户输入扫描事件 | `on_guardrail_finding` hook |
|---|---|---|
| legacy | 1 | 1 |
| skeleton | **0** | **0** |

工具结果侧两个引擎都发 1 条——因为它在 `_append_tool_results`
（`loop_progress.py`，两引擎共用）。

**根因**：`scan_user_input` 只在 `_run_loop_core:980-989` 调用，骨架无对应点。

### 1.4 `max_iter` 终答文案 —— **丢失**

| engine | `finished_reason` | `answer` |
|---|---|---|
| legacy | `max_iter` | `"Reached max_iterations=3 without a final answer."` |
| skeleton | `max_iter` | `""` |

**根因**：文案由 `_handle_max_iter`（`loop_lifecycle.py:41-54`）写，骨架
`for-else` 分支（`skeleton.py:263-268`）只设 `finished_reason` 不设 `answer`。

**影响**：`LoopResult.success` 要求 `bool(self.answer)`，所以 max_iter 时
`success` 从 `True` 变 `False`；前端拿到空答案。

### 1.5 hook 调用次数 —— **偏差两处**

条件：3 轮（tool, tool, text）。

| hook | legacy | skeleton | 差异 |
|---|---|---|---|
| `before_run` | 1 | 1 | — |
| `before_iteration` | 3 | 3 | — |
| `after_iteration` | 3 | **2** | 末轮缺一次 |
| `after_run` | 1 | **2** | 多一次 |

**根因**：
- `after_iteration`：legacy 在**每条**退出路径前都发（含文本终答的
  `_handle_stop` 前）；骨架的文本终答走 `Decision.STOP` → `break`，
  该路径不发 `after_iteration`。
- `after_run`：`DefaultFinalizationStep.execute`（`finalization.py:48`）
  发一次，骨架 `run_stream` 末尾（`skeleton.py:284`）再发一次。

### 1.6 post-response injectors / goal 续跑 —— **完全丢失**（最严重）

| engine | `inject_post_response` 调用次数 | iterations | finished_reason |
|---|---|---|---|
| legacy | 4 | 4 | `max_iter` |
| skeleton | **0** | **1** | `stop` |

条件：注入一个 `order=100` 的 injector，其 `inject_post_response` 恒返回
`True`（"goal 还没跑完，继续"）。

**根因（两层，任一层不修都不会生效）**：

1. **没有调用点**：`inject_post_response` 的循环只在
   `_run_loop_core:1077-1084`，骨架的 `LoopReasoner` 只跑了
   `order==0` 的 per-iteration 分支（`skeleton_adapters.py:51-56`）。
2. **闸位顺序错**：即使补上调用点，`deciders_for` 的 `after_reason`
   顺序是 `[StopSignalDecider, TextFinalDecider, GoalContinuationDecider]`。
   `TextFinalDecider` 命中（无 tool_calls）就直接 STOP，后面的
   `GoalContinuationDecider` 是**死代码**。legacy 的顺序相反：
   先问 continuation（`loop.py:1070-1074`），再问 injector
   （`:1077-1086`），最后才 `_handle_stop`（`:1096`）。

**影响**：`GoalContinuationInjector` 是 `build_default_injectors()` 的默认成员
（`context_injector.py:400`）。对 `session_id` + 活跃 goal 的会话
（即"长程研究"这一核心用法），**默认引擎会在模型第一次给出文本时直接结束**，
而 legacy 会继续推进 goal。这是用户明确的定位（"goal 设定是一个长程研究工具"）
被静默破坏。

**这是本批发现的 6 个缺口里唯一影响业务语义的**，其余 5 个是记账/文案/计数。

### 1.7 其他实测（**无差异**，记录以免后人重复验证）

| 能力 | legacy | skeleton |
|---|---|---|
| 迭代超时 `iteration_timeout_s` | `timeout` | `timeout` |
| LLM 错误路径 | `error` + `LLMConfigError: ...` | 同 |
| `metrics` 键集 | elapsed_s/tokens/usage_cost_usd/usage_tokens | 同 |
| 流式事件序列（前 8 个） | loop_start/iter_start/llm_request/thinking_start/text.started/thinking_done/text_delta/thinking_end | 同 |
| 工具结果 guardrail | 1 | 1 |
| 工具执行失败（tool 返回 error） | 等价 | 等价 |

---

## §2 修正后的 S3 范围

### S3-a · 补齐 6 个缺口（**必须先做**）

| 缺口 | 修法 | 落点 |
|---|---|---|
| 预算记账 | `LoopReasoner.handle` 拿到 response 后调 `_record_budget_usage`；累计逻辑改调既有的 `strategy/budget.record_usage`（`loop.py:671-689` 是它的手写重复） | `skeleton_adapters.LoopReasoner` |
| **post-response injectors / goal 续跑** | `LoopReasoner` 补 `order>=100` 分支；**并把 `GoalContinuationDecider` 提到 `TextFinalDecider` 之前**（对齐 legacy 顺序） | `skeleton_adapters.LoopReasoner` + `modes.deciders_for` |
| `wrap_up_nudge` | `loop._run_via_skeleton` 传入 `on_iteration_start`，实现搬进 `skeleton_adapters` | `loop.py` + `skeleton_adapters.py` |
| 用户输入 guardrail | 搬进 `LoopHooks.before_run`（骨架已在 prepare 后立即调用它） | `skeleton_adapters.LoopHooks` |
| `max_iter` 文案 | 搬进 `LoopFinalize`：`finished_reason=="max_iter"` 且 `answer` 空时补文案 | `skeleton_adapters.LoopFinalize` |
| hook 计数 | `after_iteration` 在 STOP 分支补发；`after_run` 去掉骨架末尾那次（由 `LoopFinalize` 统一发） | `skeleton.py` + `skeleton_adapters.py` |

**验收**：新增 `tests/test_engine_parity.py`，把 §1 的 **7 组**对比全部写成
参数化断言（双引擎逐字段比对），**并且**这 7 组在修之前必须先红一次
（证明断言有效，不是空集合）。

### S3-b · mixin 解体（原范围，按耦合度两小步）

### S3-b1（低耦合，先做手法验证）

| mixin | 行数 | 去向 |
|---|---|---|
| `loop_telemetry.py` | 93 | → `agent_telemetry.py`：模块级函数 `emit(loop, ...)` / `trace(loop, ...)` / `fire_hooks_async(loop, ...)` / `trace_llm_request(loop, ...)`；`loop_telemetry.py` 保留为 mixin 薄壳，方法体 1 行转发（阶段 3 删）|
| `loop_lifecycle.py` | 123 | → `loop_endings.py`：`handle_max_iter(loop, result)` / `handle_stop(loop, resp, result, it)` / `finalize_metrics(loop, result, msgs, t0)` / `run_claim_validation(loop, result, msgs)` 全部改成显式传 loop 的纯函数 |

**收益**：这两个 mixin 的方法**已经是纯转发/纯函数形态**，改签名的风险低；
它们验证「loop 显式传递」这一手法，为 S3-b2 的 4 个大文件铺路。

**注意**：`_trace_llm_request` 95 行在 `loop.py` 里（不在 mixin），本步一并搬进
`agent_telemetry.py`——这是 loop.py 唯一能靠 S3-b1 减少的 95 行。

**S3-b2（高耦合）**

| mixin | 行数 | 去向 |
|---|---|---|
| `loop_tools.py` | 389 | → `tool_actor.py`：`ToolActor` 持有 loop 引用，方法签名改 `(tc, result) -> msg`；`_build_tool_kwargs` 拆成 `ToolContextFactory` |
| `loop_compaction.py` | 284 | → 复用既有 `compaction_runtime.make_compactor`（**已存在且语义等价**，见 §4.1）；mixin 只留 `_persist_compaction_event` 转发 |
| `loop_progress.py` | 280 | → `progress_hook.py`：hash 窗口 + approval gate + guardrail 扫描 |
| `loop_stream.py` | 261 | → `streaming_reasoner.py`：`StreamingLLMReasoner(StreamingHandler)` |

**约束**：
- mixin 文件**保留为薄兼容层一批**（阶段 3 删），因为 `test_no_loop_compaction_cycle.py`
  等测试按名字 import。
- 每次只搬一个文件，全量回归后再搬下一个。

---

## §3 行数预算（实测，不是估计）

### 3.0 先纠正一个前提：**mixin 解体不会减少 loop.py 一行**

`loop_tools.py`(389) / `loop_compaction.py`(284) / `loop_progress.py`(280) /
`loop_stream.py`(261) / `loop_lifecycle.py`(123) / `loop_telemetry.py`(93)
**本来就在各自独立文件里**——`loop.py` 不包含它们的任何一行，只做
`from .loop_X import LoopXMixin` 然后写进继承列表。

所以「mixin 解体」（MRO 继承 → 组合）是**结构性改动**：
- 收益：`AgentLoop` 不再是一个 6 基类的上帝对象；各职责可独立测试/注入。
- 收益：`compaction` 能复用既有 `compaction_runtime.make_compactor`，减 ~190 行。
- **不收益**：`loop.py` 行数一个都不减。

`batch12` 设计的「S3 → loop.py ≤400 行」这个因果链是错的，本批明确纠正。

### 3.1 loop.py 的真实构成

| 分块 | 行数 | S3 后 |
|---|---|---|
| 类外头部（docstring/imports/全局/模块函数/`LoopResult`） | 272 | 保留（可减 ~40：`_merge_spec`、`_inject_agent_loop`、`_get_goal_store` 可搬） |
| `__init__`（签名 73 + fold 49 + 赋值 119） | 241 | 签名/fold 保留（阶段 3 删），**赋值块 119 可搬** |
| `_run_loop_core` | 246 | **删除**（阶段 3 删 legacy 引擎时）；S3 保留 |
| `_record_budget_usage` + `_check_budget` | 36 | 搬走（S3-a 已改由骨架调用，方法仍在） |
| 公共入口/状态机入口 | 261 | 保留 |
| 上下文/工具装配（`_build_data_store`/`_build_sandbox`/`_build_hook_context`） | 59 | 搬走 |
| trace/遥测（`_trace`/`_trace_llm_request`/`_emit_compaction`/`_emit_iter_start`） | 122 | 搬走 |
| goal/hypothesis（`_maybe_auto_create_hypothesis`/`_get_goal_snapshot`） | 56 | 搬走 |
| 输出转换/收尾（`_response_to_assistant_msg`/`_get_response`/`_git_commit`） | 88 | 部分搬走 |
| 类内非方法行 | 67 | 保留 |

**S3 后的可达下限**：`272 + 241 + 261 + 67 = 841` 行。
再搬 `__init__` 赋值块（119）与 trace 组（122）→ **600 行**。

**结论：≤400 行在 S3 内不可达。** 必须等阶段 3 删掉 `__init__` 的 73 行
旧签名 + `_run_loop_core` 的 246 行才现实（241+261+67+272−73−246 ≈ 522，
再把模块头部拆出去可到 ~400）。**本批把目标改为「≤650 行且能力零倒退」**，
并在设计文档里明确记录这次修正（避免下批重复「怎么还没到 400」的疑问）。

### 3.2 S3-b 的真实收益（按文件，不是按 loop.py 行数）

| 文件 | 现 | 后 | 减 | 依据 |
|---|---|---|---|---|
| `loop_compaction.py` | 284 | ~90 | **−194** | 复用 `compaction_runtime.make_compactor`（§4.1，已存在且语义等价） |
| `loop_telemetry.py` | 93 | ~50 | −43 | 5 个方法已是纯转发；去 mixin 壳，实现在模块级函数 |
| `loop_lifecycle.py` | 123 | ~95 | −28 | `_handle_*` 已是纯函数形态；去 mixin 壳 |
| `loop_tools.py` | 389 | 389 | 0 | 无重复实现，纯搬位置（→ `tool_actor.py`） |
| `loop_progress.py` | 280 | 280 | 0 | 同上（→ `progress_hook.py`） |
| `loop_stream.py` | 261 | 261 | 0 | 同上（→ `streaming_reasoner.py`） |
| **合计** | **1,430** | **~1,165** | **−265** | |

拓扑上，6 个 mixin 从 `AgentLoop` 的基类变成 `AgentLoop.__init__` 里
组合的 6 个对象（handler/hook）。这使 `AgentLoop` 的 MRO 从 7 层变 2 层。

---

## §4 与既有实现的重叠（避免二次实现）

### 4.1 `compaction_runtime.make_compactor` 已经是 mixin 的等价实现

`compaction_runtime.py:31-101` 的 `make_compactor` 文档写明
「Semantics match `LoopCompactionMixin._maybe_compact_impl` exactly」，
且已被 `gateway/session_controller.py:495` 使用（有 7 个测试
`tests/test_b1c_react_compaction.py`）。

**S3-b2 的 compaction 只需**：让 mixin 的 `_maybe_compact_impl` 改调
`make_compactor`，删掉重复的 40 行；`_persist_compaction_event` 作为
`persist=` 回调传入。这比「重写一个 CompactionHandler」风险低得多。

### 4.2 两处 `_tool_call_hash` 实现不同（**是 bug，不是重复**）

| 位置 | 算法 | 输出长度 |
|---|---|---|
| `loop.py:255` | `md5(..., sort_keys=True, ensure_ascii=False)` | 12 |
| `loop_progress.py:20` | `sha1(..., default=str)` | 16 |

`_collect_tool_hashes` 用 `loop_progress` 的版本；`_detect_no_progress`
比较的是这个 16 位 sha1。`loop.py` 的 md5 版本**只被测试用**
（`tests/test_agent_loop.py:390-404` 断言长度 12）。

**处置**：保留 `loop.py` 的 md5 版本作为公共 API（向后兼容测试），
但把 `loop_progress` 的 sha1 版本统一为同一个函数——两套哈希同时存在时，
「同一个 tool_call 在两处是否相同」的结论可能不一致。**这是一个待修 bug，
不属 S3 范围**，记录到 `docs/design/` 的遗留清单。

---

## §5 执行顺序（每步独立 commit + 全量回归）

| 步骤 | 内容 | 回滚 |
|---|---|---|
| **S3-a1** | 写 `tests/test_engine_parity.py`，7 组断言**先红** | 无需回滚（只加测试） |
| **S3-a2** | 补 6 个缺口 → 该测试转绿 | revert 单 commit |
| **S3-b1** | telemetry + lifecycle 搬家 | revert 单 commit |
| **S3-b2-1** | tools 搬家 | 同上 |
| **S3-b2-2** | compaction 改调 `make_compactor` | 同上 |
| **S3-b2-3** | progress 搬家 | 同上 |
| **S3-b2-4** | stream 搬家 | 同上 |

### S3-a2 实际改动（已完成，commit 见 git log）

| 缺口 | 改动 |
|---|---|
| 预算记账 | `LoopReasoner` 在 REASON 后调 `_record_budget_usage`，并把结果同步进 `ctx.tokens_used` / `ctx.cost_usd`；`after_reason` 闸位加入 `BudgetDecider`（同轮生效，对齐 legacy `loop.py:1056`）；`skeleton.py` STOP 分支把 `budget*` 映射到 `finished_reason="budget"` |
| post-response injectors | `LoopReasoner` 补 `order>=100` 分支（仅在无 tool_calls 时，对齐 legacy）；新增 `InjectorContinuationDecider` 消费 `ctx.metadata["injector_continued"]`；`after_reason` 顺序改为 **Budget → GoalContinuation → InjectorContinuation → StopSignal → TextFinal** |
| `wrap_up_nudge` | 新增 `LoopIterationStart`，由 `loop._run_via_skeleton` 传给 `ReActSkeleton(on_iteration_start=...)` |
| 用户输入 guardrail | 搬进 `LoopHooks.before_run`；`LoopPrepare` 把原始 task 写进 `ctx.metadata["raw_task"]`（`ctx.task` 已含 pre-run 前缀） |
| `max_iter` 文案 | `LoopFinalize` 在发事件前补 `"Reached max_iterations=N without a final answer."` |
| hook 计数 | `skeleton.py` 的 STOP 分支补发 `after_iteration`；`LoopHooks.after_run` 用 `ctx._after_run_fired` 标记去重，`LoopFinalize` 在 finalization step 之后置位（EXIT 路径不走 step，仍能正常发） |

**验证**：`tests/test_engine_parity.py` 7 passed；相关 8 个测试文件 251 passed；
全量见 §5 末尾。

每步验证：
1. `pytest tests -q` 0 failed（基线 6977 passed / 71 skipped / 5 collect errors）
2. `scripts\batch_gate.cmd` ALL GREEN
3. `tests/test_skeleton_equivalence.py`（5 场景）
4. `tests/test_engine_parity.py`（6 组，S3-a 新增）
5. `tests/test_capability_smoke.py` + `tests/test_kernel_standalone.py`（E1–E6）

---

## §6 明确不做

- 删旧构造参数（阶段 3）
- 删 mixin 文件（阶段 3；本批保留薄兼容层）
- 删 `_run_loop_core`（阶段 3；**它是回滚保险**）
- 统一两处 `_tool_call_hash`（§4.2，单独立项）
- 骨架的 sync 路径（`run()` 仍走 legacy；阶段 3）
- llmwikify 反向依赖接入
