# Gateway 侧超时等价与历史读取严格化设计（裁决 3 缺口①③）

> **日期**：2026-11-18 · **状态**：设计定稿，**实施中**
> **依据**：`legacy-chat-api-compat-review-20261118.md` §4（两个待裁决差异）+ 维护者裁决（2026-11-18）
> **性质**：补齐 Gateway 路径与旧队列路径的语义等价，**不删除任何旧实现**

> **2026-11-18 复核修正**：§1、§2 与 §8 中关于超时已等价、异步阻塞
> 调用必然取消和无孤儿的结论不成立，保留为实施时记录，不作为当前验收结论。
> 当前状态及后续方案以 §9 为准；历史读取的结论不受此修正影响。

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

## 9. 超时复核与后续方案（2026-11-18）

### 9.1 实测与未闭合边界

- 真实 `AgentLoop.arun`、`max_iterations=1`、attempt 预算 0.05 秒，异步
  LLM 替身等待 0.25 秒后返回：实测约 0.55 秒返回 `finished_reason=stop`、
  `error=None`，答案仍被接受。迭代开始检查无法处理当前轮超时或最后一轮。
- 既有 `_with_iteration_timeout` 使用独立的迭代预算，并未使用 attempt
  剩余预算，不能证明 attempt 截止时能取消正在运行的请求。
- `BaseTool.ainvoke` 最终直接同步调用 `invoke`；以 0.05 秒的
  `asyncio.wait_for` 包住 0.25 秒的同步工具，实测约 0.25 秒正常返回，
  没有触发超时。同步工具可能执行文件、数据库、网络或子进程副作用；
  取消协程或线程不能证明这些副作用停止。sync-only client 兼容壳也
  使用 `asyncio.to_thread`，取消 await 不等于终止底层线程。
- 真实 `AgentLoop` 调用同步工具的探针同样触发工具并在到期后产生副作用；
  原实现到下一轮才记录超时。最后一轮慢模型调用已修复，正在执行的同步
  工具回归仍为严格 xfail。
- 因此**缺口①和 R-015 ③ 未闭合**。不得据既有检查点或模拟抛错测试
  宣称满足墙钟中断、无孤儿副作用或完整出口网。

### 9.2 验收目标与设计约束

1. 在真实 Gateway → AgentLoop 路径，以可取消的慢 LLM 验证截止后取消、
   最后一轮不成功、失败终态及 fence/lease 释放；同时覆盖 0 禁用、
   排队不计时和正常完成。
2. 真实同步工具以及 sync-only client 必须分别验证：到期后实际执行是否
   停止、是否可能继续写入、何时释放 lease。不能仅测替身直接抛异常，
   也不能用取消 `to_thread` 的 await 冒充工作线程终止。
3. 需要先设计能安全终止或隔离工具/客户端副作用的执行边界，再决定超时
   后的终态与资源释放；不得在工作仍可能写入时宣称无孤儿或提前释放
   会话互斥。方案必须覆盖工具上下文、进程/资源清理和跨平台回滚。
4. 影响面包括 `AgentLoop` 两种引擎、工具执行与权限、Gateway 事件桥、
   Attempt 持久化和 lease；实施前按 R-014 明确设计及验收。
   回滚方式为独立回退该实现批次，保留现有兼容 API。
5. 验收及兼容期公告完成前不删除旧队列；不能依据静态审计推断仓库外无
   消费者。

### 9.3 候选隔离方案（待可行性验证及 R-014 确认）

目标分为两项，不能混为一谈：① 截止时停止本地仍在运行的执行实体，确认
退出后才释放 lease；② 外部已接受的网络请求或已提交的写操作无法仅靠杀
进程撤销，需要具体工具及服务端共同提供可验证的截止与取消语义。
当前不能承诺任意工具都满足第二项。

**本机受控验证（2026-11-19）**：本地 HTTP 服务确认已受理请求后暂缓
处理；终止并等待请求客户端进程退出，再允许服务端继续，服务端操作
仍然完成。该实验不访问外部网络，证明客户端进程退出本身不能撤销已
受理操作；它不证明任意远端服务支持取消，也不能证明任意进程树可终止。
要满足「到期后无后续外部副作用」，必须限制工具契约，或依赖服务端
可验证的取消确认与截止后提交禁令；幂等只能避免重复，单靠幂等或
普通事务不能保证到期后不发生首次副作用。通用进程隔离本身不够。

候选方向是在父进程保留 Gateway、lease、权限审批和事件桥，为支持隔离的
同步工具创建可追踪的独立工作进程；父进程传递经校验的参数及受限上下文，
处理审批，收集结果。到期时停止并确认该进程及其受控子进程退出，拒绝
过期结果，完成状态记录后才释放 lease。Windows 需要验证进程树控制与
句柄回收；不得假定停止父进程就会停止所有后代。

该方向不是已批准的实现，也不保证所有工具可直接迁移：

- `ToolContext` 包含事件回调、权限 gateway、data store 和 sandbox 等
  进程内对象，不能假定可序列化或复制后仍保持同一资源语义；
- 工具可能启动子进程，或调用进程外服务；权限与取消信号必须贯穿实际
  调用链，不能仅隔离最外层 Python 函数；
- 无法隔离的工具必须在方案中明确 fail-closed 或保持运行并继续持有
  lease 等策略；这两者都改变既有行为，不能静默选用；
- sync-only LLM 的线程桥同样需要单独处置，不能将线程取消视为中断。

下一批先做**不接生产路径的可行性验证**：用代表性的只读、文件写入、
子进程和带权限请求的工具，验证 Windows 下启动、终止、回收、上下文及
副作用边界；同时验证原生异步 LLM 的取消和 sync-only 客户端限制。记录
不支持的类别、性能与迁移路径，再就行为策略形成完整方案并确认。
未验证前不发布声称超时已等价的兼容期公告，不删除旧执行路径。

### 9.4 原生异步模型调用的独立修复批

不等待同步工具隔离，先单独修复可协作取消的原生异步模型调用：在现有
`_with_iteration_timeout` 中选择迭代预算与 attempt 剩余预算的较小者，
对 LLM 调用等待施加该预算。attempt 预算到期则取消并等待协程清理，
记录 attempt 超时终态；原迭代预算先到期时保留原迭代超时语义。
预算禁用时保持现状。该改动不承诺取消同步工具、线程或远端已接收请求。

验收：真实单轮慢异步 LLM 测试由 xfail 变为通过；还须验证流式调用、
两种引擎、迭代预算优先、正常完成和 Gateway 失败收尾。同步工具回归
仍保持严格 xfail，缺口①和删除前置仍未闭合。影响仅限 AgentLoop 的 LLM
调用和相关测试；回滚此独立批次即可恢复原等待行为，不删除兼容 API。

**当前工作区结果**：原生异步 LLM 的非流式/流式慢调用在 skeleton 与
legacy 下均按剩余预算取消；已覆盖迭代预算先到期、provider 自身超时
不误报 attempt、真实 AgentLoop 节点经 Gateway 的失败事件与 fence/lease
释放，以及终态事件不重复。组合回归 60 passed / 1 xfailed；
同步工具不可中断的 xfail 仍在，尚未验证远端操作撤销，不能宣称完整
attempt 墙钟超时或无孤儿副作用。

验证：受影响集 60 passed / 1 xfailed；batch_gate 12 步全绿；架构图与
体量数字一致。全量 pytest 在默认 Windows 编码下有 2 条无关的子进程
编码失败；设置 `PYTHONUTF8=1`、`PYTHONIOENCODING=utf-8` 后复跑为
7738 passed / 106 skipped / 1 xfailed。未修改这两条既有测试。

### 9.5 同步工具返回后的有限防护

在真实隔离方案完成前，仅在工具批次返回后检查 attempt 截止时间，若已
超时则以失败终态结束，不把最后一轮误报为成功；收尾仍等待工具返回，
不提前释放 lease。双引擎须验证超时终态、事件及停止后续模型调用。
该检查**不能中断正在执行的工具，也不能撤销已发生的副作用**，因此
同步工具硬中断测试继续保持严格 xfail，裁决 3 删除前置不受影响。
工具批次中顺序执行的写工具还须在每次启动前检查截止时间，避免前一个
工具返回时已超时却继续启动后续写工具；已并发启动的工作和外部副作用
不受此检查保护。
每次进入工具异步入口及其瞬态错误重试前也检查截止时间；并发只读任务
尚未进入入口时同样不会在已到期后启动。入口内还可能等待权限审批，
因此需要另行验证审批跨过截止时间后是否仍会调用同步工具。

验收补充：真实 Gateway 执行被线程事件阻塞的同步工具，超过截止时间后
检查 lease 和 fence 仍被持有；工具返回后才以失败终态收尾并释放。
另外以并发只读工具验证批次收尾和不再进入后续模型调用，并保留 sync-only
client 的严格 xfail，防止把取消线程等待误判为线程终止。

**当前工作区实测**：双引擎慢工具、顺序写工具及并发只读批次均在
返回后标为 timeout；真实 Gateway 阻塞工具期间保持 lease/fence，
释放工具后才失败并释放；未进入工具入口的调用和重试会被截止检查拦截。
真实 `PermissionGateway` 的 ASK 在 deadline 后获批时，现已在
`BaseTool.ainvoke` 执行前检查截止时间，阻止基类同步 `execute`。
直接异步调用 sync-only 客户端时，取消等待后工作线程仍可继续。
同步工具和 sync-only 客户端两项回归保持严格 xfail，不作为等价超时
验收通过。
真实 Gateway 节点使用同步桥时，sync-only 客户端线程仍运行期间
lease/fence 保持持有，线程返回后才完成 Gateway 失败收尾；直接
`AgentLoop.arun` 不具备这一等待线程退出的保证，且这不等于线程被中断。

**Windows 隔离初探（不接生产）**：将一个自建 Python 父进程放入 Job
Object 后，由其创建普通子进程；`TerminateJobObject` 后等待父、子
句柄均确认退出。本机单次启动约 33.5ms、终止约 1.5ms，仅说明该受控
样例可行，不代表性能基准或任意工具、breakaway 子进程均可控制。
实测 `AgentLoop._build_tool_kwargs` 生成的真实 `ToolContext` 和完整
kwargs 无法 pickle，原因是进度回调为局部函数；单独的工具参数可 pickle。
这是当前构造的一例，不代表移除回调后所有依赖均可安全跨进程传递。
真实工具上下文、权限审批、事件桥、远端操作和异常清理尚未验证，
因此不能据此实施隔离或认定缺口闭合。

### 9.6 审批跨截止时间的修复

`BaseTool.ainvoke` 在工具入口检查之后可能等待 ASK，获批后直接执行
`invoke`，故入口检查不能保证执行前仍未到期。候选方案是给 `ToolContext`
增加可选的执行前检查回调，由 AgentLoop 注入当前 attempt 的检查，并在
`BaseTool.ainvoke` 的权限检查完成后、`invoke` 前调用；默认缺省保持
旧行为。超时异常须在工具执行层按类型透传，不能被泛化成普通工具错误；
自定义 `ainvoke` 覆盖实现需审计并明确适配，不能假定基类保护覆盖它们。

验收：真实 PermissionGateway 延迟 ALLOW 的严格 xfail 转绿且工具未
执行；同时覆盖及时 ALLOW、DENY、超时禁用、双引擎和终态事件唯一性。
此方案只防止等待审批后启动，并不强制取消审批、执行中的同步工具、
sync-only 线程或远端副作用。影响 ToolContext 公共契约与工具执行入口，
回滚单独撤回该批并保留原兼容接口。仓库源码中没有覆盖 `ainvoke`
的工具；外部实现仍需遵守该执行前检查契约，不能假定自动生效。

组合工具使用 `invoke` 顺序调用子工具；其每个子工具入口也执行上下文
中的截止检查，防止前一步超时返回后启动下一步。此检查仍不强制停止
正在运行的组合步骤；绕过 `BaseTool` 入口的外部实现不受其保护。
并发工具中单个检查点只抛出超时信号，须等待同批已启动的任务结束后，
由外层统一记录最终事件，避免 `loop_end` 早于仍在运行的工具。

**验证**：双引擎延迟/及时审批、禁用超时、组合步骤和并发事件顺序均
通过；全量 7760 passed / 106 skipped / 2 xfailed，batch_gate 12 步
全绿。两个 xfail 分别是运行中同步工具和 sync-only 客户端线程的
强中断；它们仍是删除旧路径的阻塞项。

**路线状态（2026-11-19，维护者七步路线）**：后续步骤 3（同步执行
隔离的不接生产原型：真实工具上下文、权限、子进程树、清理及
sync-only 客户端，验证 Windows 及项目支持的其他平台）与步骤 4
（超时能力契约：对不能证明安全停止的能力**明确限制或拒绝**，而非
宣称全部工具可强中断；远端已接受的操作须服务端确认取消并禁止
截止后提交——幂等不能代替取消）均未启动。两 xfail 闭合前**不得
宣称超时完全等价**，删除前置保持未满足。

### 9.7 进程隔离原型实测（2026-11-19，路线步骤 3 · 不接生产）

原型：`scripts/prototypes/sync_isolation_probe.py`（ctypes kernel32 +
JSON Lines IPC；生产零依赖、零改动）。五实验全部 PASS，数据如下。

**E1 启动/关停**：worker 启动 ~8-9.4ms（§9.5 初探的 33.5ms 为含
解释器冷启的口径，本口径为 spawn+Assign 后首个回包）；干净关停确认
exited。

**E2 上下文跨界清单**（ToolContext 16 字段静态定性 + 数据字段抽样
验证）：

| 类别 | 字段 | 结论 |
|---|---|---|
| 可序列化直传（已验证） | `workspace` / `session_id` / `strategy_dir` / `results_tsv` / `write_roots` / `read_roots` / `message_id` / `tool_call_id` | 数据字段跨界回传一致（E2） |
| 回调转 IPC（原型已验证通道） | `emit_progress` / `emit_event` / `before_execute` | worker 事件行 → 父进程转发到真回调（E3 的请求/结果行同通道） |
| 进程内对象（需代理或重开） | `permission_evaluator` + `permission_gateway` | **审批 IPC 往返已验证**（E3：ALLOW/DENY 两路，往返 ~0.1-0.2ms） |
| 进程内对象（未验证） | `data_store`（worker 自持连接方案）/ `sandbox`（配置子集可传，对象本体不可） | **未验证**，列入迁移路径 |

**E3 权限审批跨进程**：ASK 事件 → 父进程裁决点 → 回传 ALLOW/DENY →
worker 继续/拒绝，两路均通；往返延迟 ~0.1-0.2ms（JSON Lines 管道）。

**E4 子进程树终止**：worker → child → grandchild 两层后代先确认存活
（`[true, true]`）→ 整树终止 → 逐 pid 确认全部消失。

**E5 截止时间 + lease 序列**：lease 全程持有（终止前断言）→ 终止 →
**确认退出 → 无迟到结果 → 之后才 release**——「确认退出后再释放
lease」的序列成立。

**隔离原语的关键环境发现**：本探针宿主（agent 沙箱）自身处于受限
Job，`AssignProcessToJobObject` 被拒（ERROR_ACCESS_DENIED）→ 本主机
验证的是**降级原语 `taskkill /PID <pid> /T /F` 树终止**（语义等价点：
整树杀 + 确认先于释放）。生产 uvicorn 进程在普通 shell/服务下预期
可用 **Job Object**（无树遍历竞态，KILL_ON_JOB_CLOSE 兜底）——部署时
必须先验证 Job 分配可用，不可用则降级 taskkill 树终止。

**支持/不支持类别**（对照 §9.3）：

- ✅ 可隔离：只读工具、文件写入工具（终态后副作用已发生——不支持
  撤销，仅支持终止执行）、子进程树工具、带权限请求工具；
- ✅ 已验证：启动/终止/回收时延、审批往返、树终止、lease 序列；
- ❌ 仍不支持：运行中同步工具的进程内中断（进程终止是唯一手段，
  代价是丢失工具局部状态）；sync-only LLM 客户端线程（不在进程隔离
  范围内，保持 xfail）；远端已接受操作的撤销（步骤 4 契约）；
- ❌ 未验证：POSIX（`os.setsid`+`killpg` 设计未实现——本主机为
  Windows）；`data_store`/`sandbox` 跨界；真实 BaseTool 工具（原型用
  演示替身）；breakaway 子进程（`CREATE_BREAKAWAY_FROM_JOB`）行为。

**迁移路径**（未批准，不实施）：`ToolContext` 增加 `isolated: bool`
与 worker 工厂注入 → permission gateway 走 E3 的 IPC 桥 → deadline
触发 E5 序列（终止/确认/丢弃迟到结果/后释放）→ 按 §9.3 的
fail-closed 策略声明每工具的隔离类别 → R-014 方案 + 用户确认后分批
实施。本原型不构成缺口①闭合：不接生产、未过出口网。

## 10. 超时能力契约（路线步骤 4 · R-014 方案 + 维护者裁决，2026-11-19）

### 10.1 问题

现状的承诺谱系不均匀且**未被声明**：原生异步 LLM 调用可协作取消
（§9.4）；同步工具运行中不可中断（严格 xfail）；sync-only 客户端
线程不可终止（严格 xfail）。而 attempt 超时终态事件、API 错误体与
文档都没有区分"600 秒到点后到底停了什么"——对使用者构成过度承诺。
路线步骤 4 的裁定方向：**对不能证明安全停止的能力明确限制或拒绝**。

### 10.2 能力四级（维护者裁决：机制属性，**非强弱排序**）

四级描述的是**终止机制的性质**，不是保证强度的高低——
`abandonable` 与 `unstoppable` 都可能继续执行并产生副作用，区别是
**等待方是否可放弃**，不是"前者没有副作用"。

| 级别 | 机制语义 | 截止时行为 |
|---|---|---|
| `cancellable` | 协作取消（协程可取消） | 发起协作取消并等待清理；**清理可能超时或被忽略**——确认退出后才报告"已停止"，否则记 `unknown`。**不承诺墙钟内停止** |
| `abandonable` | 等待可放弃、底层不可终止 | 弃等 + 记录"仍运行（弃等）"——与 `unstoppable` 的区别仅在**等待方可放弃** |
| `isolatable` | 进程隔离终止 | 终止 worker 树 + 确认退出 + 拒收迟到结果（§9.7 E5 序列）；**进程终止 ≠ 远端操作取消** |
| `unstoppable` | 无任何终止手段 | 仅标记超时终态 |

### 10.3 声明方式与「声明 ≠ 事实」

- `BaseTool.timeout_capability: str = "unstoppable"`——类属性，沿
  `effects` 声明惯例；**默认最保守**（未声明 = 不承诺可中断）。
- 只做静态声明，**不做运行时探测**；且**声明不等于事实**：
  - 事件必须区分**声明能力**（declared）与**实际结果**（actual state，
    每次调用一条）；
  - 实际状态只允许四个观测值：`stopped`（确认退出）/
    `still_running`（弃等且机制已知继续）/ `returned`（截止后发现
    已自然返回，结果拒收）/ `unknown`（无观察通道）——未知必须显式
    记 `unknown`，**不得由注册表声明推断实际结果**；
  - 多个并发调用按调用分别记录；
  - **`isolatable` 未接生产前不得报告进程已终止**——4c 前该声明只
    入声明面，不进实际状态。
- `isolatable` 准入门槛：过隔离清单（§9.7 五实验类别 + 真实工具
  适配）才可声明；覆盖 `ainvoke` 的自定义工具不自动继承基类保护，
  其声明必须审计（§9.6 约定延伸）。

### 10.4 本地与远端**分开承诺**（契约正文）

- **进程终止 ≠ 远端操作取消**：隔离终止只保证本地进程边界内的
  停止；已发出的远端操作不受其影响。
- 既有入口检查只能阻止**尚未发起**的调用；**已受理的远端请求仍可能
  在截止后提交**——本地检查不构成远端提交约束。
- 远端停止需要**服务端确认取消**及提交约束（对提供取消 API 的远端：
  截止时调用并等确认；不提供 → 该工具如实归 `unstoppable`）。
- **幂等不能代替取消**：操作幂等 ≠ 可取消，不得以幂等为由声明更
  高级别。

### 10.5 实际状态事件面（向后兼容的增量字段）

attempt 超时终态（`loop_end{reason:"attempt_timeout"}` 及随路事件）
新增 `timeout_tool_outcomes: list`——**按调用逐条**记录：

```json
{"kind": "llm|tool", "name": "...", "declared": "<四级声明>",
 "state": "stopped|still_running|returned|unknown"}
```

- `stopped`：协作取消已交付且等待完成；或隔离进程确认退出（4c 后）；
- `still_running`：弃等且机制已知底层继续（sync-only 线程桥）；
- `returned`：截止后发现工具已自然返回（结果拒收）；
- `unknown`：无观察通道（取消清理超时、无法观测等）。
- 状态判定**以实际观察为准**：同步工具常阻塞到自然返回后才被发现
  超时——此时记 `returned`，不得记"仍在运行"。

### 10.6 分批实施（4a + 4b 本轮；4c 单独审批）

| 批 | 内容 | 约束 |
|---|---|---|
| **4a** | BaseTool 声明字段 + 四级常量 + registry 聚合面 + attempt 超时终态携带 `timeout_tool_outcomes`（实际状态逐调用） | **保持现有执行与 lease 语义不变**；字段向后兼容（纯增量） |
| **4b** | 事件词表透传 + 工具作者指南（四级语义、远端三条规则、isolatable 准入清单、声明≠事实） | 错误体/文档措辞不得宣称墙钟停止或进程终止（4c 前） |
| **4c** | `isolatable` 隔离接线（E5 序列接 deadline 路径） | **单独审批**；不必然解决 sync-only 客户端 xfail；隔离原型 ≠ 生产能力（演示替身、真实工具/部分上下文/POSIX 未验证） |

**验收（4a/4b）**：真实 Gateway 分别验证四种实际状态——`stopped`
（cancellable 异步取消确认）、`still_running`（sync-only 弃等）、
`returned`（同步工具晚返回被拒收）、`unknown`（无观察通道）；既有
执行/lease 语义不变；两个 xfail 与旧队列删除前置**继续保留**。

### 10.7 裁决记录（2026-11-19，维护者）

1. **保留四级**，但四级不是强弱排序：`abandonable` 与 `unstoppable`
   都可能继续执行并产生副作用；区别是是否支持放弃等待。
2. 默认 `unstoppable`：未声明时不承诺可中断。
3. 接受静态声明，但**声明不等于事实**：事件区分声明能力与实际结果；
   `isolatable` 未接生产前不得报告进程已终止。
4. 范围 = 4a + 4b；4c 单独审批。实施前修订契约表述（本节即修订后
   版本）：cancellable 不承诺墙钟内停止；按实际状态发事件（未知显式
   为 unknown、不由声明推断、并发按调用记录）；本地与远端分开承诺；
   隔离原型不等于生产能力。

### 10.8 实施结果（4a + 4b，2026-11-19）

**4a 声明面 + 实际状态面**：

- `BaseTool.timeout_capability`（默认 `unstoppable`）+ 四级常量 +
  `ToolRegistry.timeout_capability(name)` 聚合面（未注册/未声明/非法
  值 → `unstoppable`）；
- `AgentLoop._timeout_tool_outcomes`：逐调用的**实际**结果记录
  （`kind`/`name`/`declared`/`state`），随 `loop_end{reason:
  "attempt_timeout"}` 事件透出（`timeout_tool_outcomes` 字段，纯
  增量、向后兼容）；
- 记录点：async LLM（取消已交付并等待 → `stopped`；sync-only 桥弃等
  → `still_running`；响应晚于截止返回 → `returned`）、sync LLM 事后
  测量（→ `returned`）、工具完成点（晚返回 → `returned`）；
- `_AsyncCapableClient.is_sync_only_bridge = True` 机制标记（等待可
  放弃、底层线程继续）。

**4b 表现面 + 作者指南**：工具参考手册新增「超时能力声明」章节（四级
语义、远端三条规则、作者速查）；**未**把 `isolatable` 报告为"进程已
终止"（4c 前无生产隔离），**未**宣称墙钟内停止。

**验收（§10.6 要求）**：真实 loop 分别产出四种实际状态——
`stopped`（原生异步取消确认）、`still_running`（sync-only 弃等）、
`returned`（同步工具晚返回被拒收）、`unknown`（显式一等状态）；
既有执行与 lease 语义未变；两个 xfail 与旧队列删除前置**继续保留**。

**验证**：受影响 182 passed（+4 状态验收用例）；串行敏感集 204
passed / 2 xfailed；全量 `-n 4` **7819 passed / 106 skipped / 2
xfailed / 0 failed**；batch_gate 12 步 ALL GREEN；体量数字同步
（448 文件 / 94,059 行）。

**负载抖动说明**：`-n 12` 下偶见 1-3 条时序敏感用例失败（超时预算
0.05s 类）。已用 `git worktree` 在 4a 前的 HEAD 建基线同负载对比：
基线 11 failed + 10 errors（更差，且含与超时无关的 security/legacy
用例），`--lf` 复跑全过——判定为**既有并发负载抖动**，非本批回归。
建议：此类预算敏感用例后续单独加固（放宽预算或改用确定性等待），
不在本批处理。
