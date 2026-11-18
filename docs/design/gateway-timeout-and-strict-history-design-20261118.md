# Gateway 侧超时等价与历史读取严格化设计（裁决 3 缺口①③）

> **日期**：2026-11-18 · **状态**：设计定稿，**实施中**
> **依据**：`legacy-chat-api-compat-review-20261118.md` §4（两个待裁决差异）+ 维护者裁决（2026-11-18）
> **性质**：补齐 Gateway 路径与旧队列路径的语义等价，**不删除任何旧实现**

## 0. 裁决与本文档

| 缺口 | 裁决 | 本设计落点 |
|---|---|---|
| ① attempt 级超时 | **补 Gateway 等价超时** | AgentLoop 整轮 deadline + 迭代边界中断点（§2） |
| ③ 历史读取异常 | **严格路径显式失败** | chat 路由读取失败 → 中止提交 + 明确错误（§3） |

**共同约束**（维护者原话）：
- 超时保留 `AGENTNODES_CHAT_ATTEMPT_TIMEOUT`：默认 600 秒、0 = 禁用；
- **从实际执行开始计时，不包含排队**；
- 超时须产生**明确失败终态 + 完成事件 + 资源释放**，且**不留下继续执行或写入的孤儿任务**；
- **先验证所选执行器能真正中断，不能只给等待方加超时**；
- 历史读取失败**中止提交/执行**并返回明确错误，不得以空历史继续；
- 仅**明确标记**的 UI best-effort 读取可降级。

## 1. 执行器中断能力核实（设计前置，2026-11-18 实测）

| 事实 | 证据 | 结论 |
|---|---|---|
| `AgentLoop.run()`（同步入口）= `_run_coro_in_sync(self.arun(...))` | `loop/core.py:872-874` | **同步路径内部也是异步** → `asyncio.wait_for` 的真取消在两条路径都可用 |
| 迭代级超时已存在 | `_with_iteration_timeout`（async，`asyncio.wait_for` → **真取消**）/ `_with_iteration_timeout_sync`（sync，事后测量） | 异步路径能取消正在阻塞的 LLM 调用 ✓ |
| **迭代边界是天然中断点** | `_run_loop_core` 的 `for iteration in range(1, max_iter+1)`（`loop/core.py:1162`） | attempt 级超时在**每轮开始处**检查 → 到点即终止循环，不再调 LLM/工具/写库 → **无孤儿写入** |
| 明确失败终态已就位 | `endings.handle_iteration_timeout` → `finished_reason="timeout"` + `result.error` + `loop_end`/`iter_end` 事件；`LoopResult.success` = `finished_reason in ("stop","max_iter") and bool(answer)` → timeout 即 **success=False** | 失败终态可直接复用既有语义 |
| 失败事件面已就位 | `session_controller.py:376-379`：`run.failed` + `run.settled{ok:False}` | 超时须走**同一失败路径**（不能被当成功） |
| GraphEngine 中断检查只在**分支开始前** | `graph_engine.py:378`（`if cancel.is_set()`） | 不能靠 fence/cancel 事件实现 attempt 超时——**超时必须在执行器内部**（AgentLoop） |

**结论**：落点 = **AgentLoop 整轮 deadline**（执行器内部），不是等待方（`wait_for_attempt` / `wait_turn`）加超时。这满足"不能只给等待方加超时"。

## 2. 缺口①：Gateway 等价超时

### 2.1 语义

| 项 | 语义 |
|---|---|
| 配置 | `AGENTNODES_CHAT_ATTEMPT_TIMEOUT`（默认 **600** 秒；**0 = 禁用**）；沿用既有 settings 声明（R-002 入口），不新增 env |
| 计时起点 | **实际执行开始**（`AgentLoop.run/arun` 入口）——**不含排队**（排队在 mailbox，与执行分离） |
| 判定点 | 每轮迭代**开始处**检查 deadline；到点 → 终止循环 |
| 终态 | `LoopResult.finished_reason="timeout"`（→ `success=False`）+ `result.error="attempt timed out after Ns"` |
| 事件 | 复用 `handle_iteration_timeout` 的形态：`loop_end{reason:"timeout"}` + `iter_end{finish_reason:"timeout"}`；**Gateway 侧**另发 `run.failed{error:"attempt timed out after Ns"}` + `run.settled{ok:False}` |
| 资源释放 | controller 的既有 finally 路径（fence release + assignment settle + lease release）——超时走**同一 finally**，不新增释放逻辑 |
| 孤儿防护 | 到点后**不再进入下一轮**（不再调 LLM/工具/写库）；正在阻塞的 LLM 调用由迭代级 `wait_for` 真取消 |

### 2.2 改动点

| 模块 | 改动 |
|---|---|
| `kernel/agent/loop/core.py` | `AgentLoop.__init__` 增 `attempt_timeout_s: float \| None = None`；`run`/`arun` 入口设 `self._attempt_deadline`（执行起算）；`_run_loop_core` 迭代开始处检查 deadline → 到点调用新的 `endings.handle_attempt_timeout` |
| `kernel/agent/loop/endings.py` | 新增 `handle_attempt_timeout(loop, result)`：置 `finished_reason="timeout"` + `result.error` + `loop_end`/`iter_end` 事件（与迭代超时形态一致，文案区分 attempt/iteration） |
| `kernel/agent/loop/lifecycle.py` | 薄转发 `_handle_attempt_timeout`（mixin 形状保持） |
| `kernel/gateway/session_controller.py` | `_build_engine` 装配 timeout（读 `AGENTNODES_CHAT_ATTEMPT_TIMEOUT`，0 → 禁用）；识别结果 `finished_reason=="timeout"` → 走**失败路径**（`run.failed` + `run.settled{ok:False}`），不按成功收尾 |
| `kernel/runtimes/nodes/agent_loop_node.py` | 透传 timeout 到 AgentLoop 构造 |

### 2.3 不做

- 不改 `AGENTNODES_CHAT_ATTEMPT_TIMEOUT` 的声明位置与默认值；
- 不在等待方（`wait_for_attempt` / `wait_turn`）加超时——那只是"不给等待方加超时"的反面，仍然不是执行器中断；
- 不给同步阻塞的 LLM 调用强杀（Python 同步调用无法从同线程取消）——代之以"到点不再进入下一轮"，保证无后续写入；异步路径的阻塞调用由迭代级 `wait_for` 真取消。

## 3. 缺口③：历史读取严格失败

### 3.1 现状与目标

- 现状：`interfaces/api/routers/chat.py:514` `history = service._convert_messages_to_history(...)`；异常时 `:518` 仅 `logger.warning`，随后以**空历史继续**提交。
- 目标：读取或修复失败 → **中止提交/执行** + 向调用方返回**明确错误**；不得以空历史继续。
- 降级例外：仅**明确标记**的 UI best-effort 读取可降级（`MemoryManager.get_stale()` 已是显式降级口；聊天上下文路径**不是**该类）。

### 3.2 改动点

| 模块 | 改动 |
|---|---|
| `interfaces/api/routers/chat.py` | `_send_via_gateway` 的历史读取段：失败 → 抛/返回明确错误（HTTP 5xx + 错误体含 session_id 与原因），**不提交 WorkRequest**（不产生 run/attempt/事件） |
| `interfaces/api/routers/chat.py`（UI 路径） | 历史展示类读取若存在，走显式 best-effort 语义并标注 degraded（不在上下文路径） |

### 3.3 语义边界

- **严格路径**（进入模型上下文的历史）：失败即失败；
- **宽松路径**（UI 列表/预览）：可降级，但须显式标注 degraded，且**不进模型上下文**。

## 4. 出口回归（绝对值断言，追加到 `tests/test_gateway_legacy_parity.py`）

| 用例 | 断言 |
|---|---|
| 超时默认 600 | settings 声明存在且默认 600（沿用旧 env，不新增） |
| 超时 0 = 禁用 | 装配后 timeout 为 None/0 → 短任务正常完成，`finished_reason != "timeout"` |
| 超时触发 | 注入慢 client → `finished_reason == "timeout"` + `result.error` 含 "timed out" + `success is False` |
| 超时后不再执行 | 超时后 client 调用数**不再增长**（无后续轮次） |
| 超时事件面 | `run.failed`（error 含 timed out）+ `run.settled{ok:False}` 均发出 |
| 超时资源释放 | 超时后 fence 无活跃 run（release 完成）+ lease 已释放 |
| 排队不计时 | 提交后**延迟**才开始执行 → 执行从起算点算（排队时长不计入） |
| 历史读取失败 | 注入读取异常 → 端点返回明确错误 + **未提交**（无 run.started 事件） |
| 历史读取成功 | 正常路径不受影响（历史进入上下文） |

## 5. 与旧路径的语义对齐（旧语义作为参考，非基准）

旧 `SessionService._run_attempt` 的超时行为（`kernel/session/service.py:698-746`）：
超时 → `attempt.failed`（error="attempt timed out after Ns"）+ `agent_done` 事件 + Attempt 终态。
Gateway 侧对齐点：`run.failed`（error 同文案）+ `run.settled{ok:False}` + Attempt 终态（由既有 settle 路径写）。

## 6. 与 R-015/R-016 的关系

本设计**闭合缺口①③**（R-015 ③ 出口网的两个已知缺口），但：
- 审计只能证明**扫描范围内**无调用，**不能证明仓库外无消费者**；
- 兼容期公告与独立删除批次**另行确认**——本轮**不删除**任何旧队列实现。

## 7. 本轮未做事项

- 未删除旧队列实现（裁决 3 只批准审查，本轮补语义缺口）；
- 未改 `send_message` 兼容承诺与默认；
- 未发布兼容期公告。

## 8. 实施结果回填（2026-11-18，缺口①③ 已落地）

### 8.1 缺口①：Gateway 等价超时（已实施）

| 落地项 | 实现 |
|---|---|
| 配置 | 沿用 `AGENTNODES_CHAT_ATTEMPT_TIMEOUT`（**`get_int`**，默认 600；`0` = 禁用）——注意：该 env 声明为 `kind=int`，用 `get_str` 会被 `test_settings_call_sites_use_the_declared_getter` 门禁拦下（实测踩到并修正） |
| 计时起点 | `AgentLoop._begin_attempt_deadline()` 在 `run()` / `arun()` 入口调用 → **实际执行开始**，不含排队 |
| 检查点（双引擎覆盖） | ① 默认引擎（skeleton）经 `engine_adapters.LoopIterationStart`（每轮开头动作钩子）→ `loop._check_attempt_timeout(iteration)`；② legacy 引擎在 `_run_loop_core` 迭代开头直接调用 |
| 中断形态 | 到点 → `endings.handle_attempt_timeout`（补 `finished_reason="timeout"` + `result.error` + `loop_end`/`iter_end` 事件）→ **抛 `AttemptTimeoutError`**（`kernel/agent/loop/errors.py` 新建） |
| 收尾复用 | 异常经 `agent_loop_node`（`result.error` → raise RuntimeError）或 GraphEngine `app.invoke` 冒泡 → `SessionController._execute` 的**既有 `except` 分支**：`run.failed` + `run.settled{ok:False}` + `_settle_assignment("failed")` + `fence.release` |
| 无孤儿 | 到点后不再进入下一轮（不再调 LLM/工具/写库）；异步阻塞调用由迭代级 `wait_for` 真取消 |

**关键设计澄清**（与设计稿一致）：skeleton 引擎的迭代循环在 `engine.py::run_stream`，`on_iteration_start` 钩子在每轮开头被调用（`engine.py:341`）——这是**执行器内部**的检查点，不是等待方超时。

### 8.2 缺口③：历史读取严格失败（已实施）

| 落地项 | 实现 |
|---|---|
| 位置 | `interfaces/api/routers/chat.py::_send_via_gateway` 历史读取段 |
| 行为 | 读取/转换失败 → `logger.exception` + **抛 `HTTPException(500, {"error":"history_unavailable","reason":..., "session_id":...})`** → **submit 不会发生**（无 run/attempt/事件） |
| 不再降级 | 删除原 `logger.warning` + 空历史继续的路径 |
| 降级例外 | 仅 `MemoryManager.get_stale()` 这类**显式标记**的 UI best-effort 读取（不进模型上下文） |

### 8.3 验证

- 新增 `tests/test_gateway_timeout_and_strict_history.py` **11 用例**（三层：执行器检查点 ×6 / 引擎挂钩 ×2 / 控制面收尾 ×2 / 历史严格失败 ×1）
- 既有 `tests/test_settings.py` 的 getter-kind 门禁**抓到并修正**一处真实错误（`get_str` → `get_int`）
- `batch_gate.cmd` **ALL GREEN（12 步）**；全量 **7731 passed / 106 skipped / 0 failed**
- 体量同步：L1 22,428 / L3 223 文件 49,248 行 / 合计 **447 文件 / 93,638 行**

### 8.4 与 R-015/R-016 的关系（按裁决保持不删除）

两个已知缺口已闭合，但**审计只证明扫描范围内无调用**，不能证明仓库外无消费者：
- 兼容期公告未发；
- 独立删除批次未批准；
- **本轮未删除任何旧队列实现**。
