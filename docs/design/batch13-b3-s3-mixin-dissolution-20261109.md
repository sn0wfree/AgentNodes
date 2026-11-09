# 批次 13 设计 · B3 阶段 2 S3（mixin 解体）—— 附：默认引擎能力缺口

> **上位设计**：`batch12-b3-stage2-subtraction-20261109.md` §1 S3。
> **性质**：本批是阶段 2 的最后一步，也是风险最高的一步。
> **前置结论变更**：S3 的原始范围（6 个 mixin 搬家）**不足以**保证 S1 翻默认是安全的。
> 本批因此分成「先补能力、再搬代码」两个阶段。

---

## §0 结论摘要（三句话）

1. **S1 翻默认之后，默认引擎（skeleton）已丢失 3 项在产能力**：预算记账、
   `wrap_up_nudge`、用户输入 guardrail、`max_iter` 终答文案，以及 hook 计数偏差。
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

### 1.6 其他实测（**无差异**，记录以免后人重复验证）

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

### S3-a · 补齐 5 个缺口（**必须先做**）

| 缺口 | 修法 | 落点 |
|---|---|---|
| 预算记账 | 把 `_record_budget_usage` 从 `loop.py` 搬到**骨架 REASON 之后**的固定位置；`LoopReasoner.handle` 在拿到 response 后调用 | `skeleton_adapters.LoopReasoner` |
| `wrap_up_nudge` | `loop._run_via_skeleton` 传入 `on_iteration_start`，实现搬进 `skeleton_adapters` | `loop.py` + `skeleton_adapters.py` |
| 用户输入 guardrail | 搬进 `LoopHooks.before_run`（骨架已在 prepare 后立即调用它） | `skeleton_adapters.LoopHooks` |
| `max_iter` 文案 | 搬进 `LoopFinalize`：`finished_reason=="max_iter"` 且 `answer` 空时补文案 | `skeleton_adapters.LoopFinalize` |
| hook 计数 | `after_iteration` 在 STOP 分支补发；`after_run` 去掉骨架末尾那次（由 `LoopFinalize` 统一发） | `skeleton.py` + `skeleton_adapters.py` |

**验收**：新增 `tests/test_engine_parity.py`，把 §1 的 6 组对比全部写成
参数化断言（双引擎逐字段比对），**并且**这 6 组在修之前必须先红一次
（证明断言有效，不是空集合）。

### S3-b · mixin 解体（原范围，按耦合度两小步）

**S3-b1（低耦合，先做手法验证）**

| mixin | 行数 | 去向 |
|---|---|---|
| `loop_telemetry.py` | 93 | → `telemetry.py` 模块级函数 + `AgentLoop` 上的 5 行转发；`_trace_llm_request` 从 `loop.py` 一并搬入 |
| `loop_lifecycle.py` | 123 | → `loop_lifecycle` 保留 `_handle_*` 作为纯函数（入参 result/loop 状态），由 `LoopFinalize` 调用 |

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

`loop.py` 当前 1,451 行，构成：

| 分块 | 行数 | S3 后 |
|---|---|---|
| 类外头部（docstring/imports/全局/模块函数/`LoopResult`） | 272 | 保留（可减 ~40：`_merge_spec`、`_inject_agent_loop`、`_get_goal_store` 可搬） |
| `__init__`（签名 73 + fold 49 + 赋值 119） | 241 | 签名/fold 保留（阶段 3 删），**赋值块 119 可搬** |
| `_run_loop_core` | 246 | **删除**（阶段 3 删 legacy 引擎时）；S3 保留 |
| `_record_budget_usage` + `_check_budget` | 36 | 搬走（S3-a） |
| 公共入口/状态机入口（`run`/`arun`/`_run_via_skeleton`/`inject`/`_drain_inbox`/`_call_step`/`_prepare_run`/超时包装等） | 261 | 保留 |
| 上下文/工具装配（`_build_data_store`/`_build_sandbox`/`_build_hook_context`） | 59 | 搬走 |
| trace/遥测（`_trace`/`_trace_llm_request`/`_emit_compaction`/`_emit_iter_start`） | 122 | 搬走 |
| goal/hypothesis（`_maybe_auto_create_hypothesis`/`_get_goal_snapshot`） | 56 | 搬走 |
| 输出转换/收尾（`_response_to_assistant_msg`/`_get_response`/`_git_commit`） | 88 | 部分搬走 |
| 类内非方法行 | 67 | 保留 |

**S3 后的可达下限**：`272 + 241 + 261 + 67 = 841` 行。
再搬 `__init__` 赋值块（119）与 trace 组（122）→ **600 行**。

**结论：≤400 行在 S3 内不可达。** 必须等阶段 3 删掉 `__init__` 的 73 行
旧签名 + `_run_loop_core` 的 246 行才现实（241+261+67+272-73-246 ≈ 522，
再把模块头部拆出去可到 ~400）。**本批把目标改为「≤650 行且能力零倒退」**，
并在设计文档里明确记录这次修正（避免下批重复「怎么还没到 400」的疑问）。

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
| **S3-a1** | 写 `tests/test_engine_parity.py`，6 组断言**先红** | 无需回滚（只加测试） |
| **S3-a2** | 补 5 个缺口 → 该测试转绿 | revert 单 commit |
| **S3-b1** | telemetry + lifecycle 搬家 | revert 单 commit |
| **S3-b2-1** | tools 搬家 | 同上 |
| **S3-b2-2** | compaction 改调 `make_compactor` | 同上 |
| **S3-b2-3** | progress 搬家 | 同上 |
| **S3-b2-4** | stream 搬家 | 同上 |

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
