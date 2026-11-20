# 4c B2.4 生产接线 + per-call id 时序 + deadline/lease R-014 方案

> **性质**：R-014 方案（**待审批——未获批不得实施任何代码**） · **日期**：2026-11-20
> **前置**：B2.2 3a–3d 已验收（B2.2 设计 §12 / 路线图 WP-1–WP-3）；B2.3 3a–3c
> 已验收（B2.3 §9）；roadmap WP-5（§3）为验收/回滚要求来源，WP-6 为后续
> 真实工具试点。
> **范围**：只写方案。不改任何现有文件；实施须逐子批单独批准。
> **证据口径**：工作树存在其他会话的未提交改动；关键源码一律以
> `git show HEAD:<file>` 为准（本文取证 HEAD = `b0d62f2b`），不读脏文件中间态。
> 设计文档引用按章节；被改动的两份设计文档以 HEAD 版本为准。

## 0. 本阶段目标与非目标

**目标**（对齐 WP-5 目标行）：把**已验收的隔离 seam**（`kernel/agent/tools/isolation.py`
单入口 + B2.3 codec）**接到生产工具调用路径**，并把 E5 deadline、进程树终止
确认、迟到结果拒收、lease 生命周期按已验证的顺序接合。

**本阶段的实质变化是生产接线**——B2.2/B2.3 全程「零生产接线」；B2.4 是第一次
让普通 AgentLoop 工具调用可能走隔离分支。因此必须明确边界：

**改哪里（实施时）**

1. `kernel/agent/loop/tools.py::_execute_tool_call_core`：唯一接线决策点——按
   `timeout_capability == isolatable` + `check_isolation_admission` + 平台 + 生产开关
   决定是否走隔离分支；sync/async 两模式共用同一分支。
2. 新增一个 **L3 私有 wiring helper**（建议 `kernel/agent/loop/_isolated_exec.py`）：
   单一决策函数 `should_isolate(...)`、执行包装 `run_isolated(...)`（含
   `asyncio.to_thread`、剩余 deadline 预算、结果映射）；保持 loop core 薄。
   新文件须走架构数字同步（R-007）。
3. `kernel/agent/tools/isolation.py` / `_isolation_worker.py`：**仅**为 per-call id
   关联协议做最小改动（任务行信封 `v` + `call_id`，所有回传行回显 `call_id`，
   父侧按 id 过滤）。不重构既有 spawn/审批/kill/毒化逻辑。
4. 新测试：决策函数单测（全平台）、per-call id 契约（全平台 codec 式 + win32
   真 worker）、时序/lease/E5 不退化（win32 真机，POSIX skip-with-reason）。

**不改哪里**

- `BaseTool.ainvoke` / `invoke` / `_enforce_permission` 的**执行语义**不改
  （唯一可能改动是把权限判定等价抽成共享 helper，见 §5 决策 6，须单独裁决）；
  worker 继续调 `invoke`，因此**不会递归进入隔离分支**。
- `ToolContext` 结构、`_isolation_context.py` 的 `CTX_VERSION` / 9 字段白名单不改；
  per-call id 走**任务行信封**，不塞进 ctx（B2.3 §2.6 契约不动）。
- `SessionExecutionLease` / `session_controller.py` / `gateway.py` 不改：复用既有
  会话级租约与其 `finally` 释放，**不新增第二套租约层**。
- 不改 shell、不启用 POSIX、不做 B2.5 真实工具试点（WP-6）、不改 E5 终态语义、
  不改 `check_isolation_admission` 准入门槛。

**非目标**：① 不承诺撤销已发生副作用；② 不修 sync-only xfail；③ 不做 worker
进程 env 脱敏；④ 不把 `abandonable`/`cancellable` 纳入隔离（见 §5 决策 5）；
⑤ 不改任何持久化/registry 行为。

## 1. 现状证据

**E1 · 生产调用路径现状（HEAD 原文）。** `BaseTool.ainvoke`
（`src/agentnodes/kernel/agent/tools/core.py:668-686`）依次做：权限闸
`_enforce_permission`（:681-682）→ `ctx.before_execute()`（:683-685）→
`self.invoke(kwargs)`（:686）。`invoke`（:616-647）**再次**调用
`before_execute`（:624-626），然后 `_coerce_params` → `_dispatch` →
`ToolResult` 包 wire envelope。`_enforce_permission`（:688-735）的 ASK 分支
用 `ctx.tool_call_id`（:733）与 `PermissionGateway.request` 配对，并把
`__session_id__` 塞进 args（:717-718）。AgentLoop 侧
`_execute_tool_call_core`（`src/agentnodes/kernel/agent/loop/tools.py:96-258`）：
`before_execute` 被设为 `_check_attempt_timeout` 的 partial（:146-149）；
async 走 `await tool.ainvoke(...)`（:177），sync 走
`enforce_permission_sync` + `tool.invoke(...)`（:186-190）；transient 错误重试
最多 2/3 次（:165-172）；成功后发 `tool_result`（:249-258，`call_id=tc.id`）。
worker 侧**只调 `invoke`**（`_isolation_worker.py:102`）——这是接线点不能落在
`invoke` 的硬约束（否则 worker 递归自隔离）。

**E2 · 四级超时能力声明与准入（B2.1 已提交，零生产消费者）。**
常量与纪律见 `core.py:33-48`：「四级是机制属性，非强弱排序」「声明 ≠ 事实：
事件按每次调用的实际状态（stopped/still_running/returned/unknown）记录，不由
本声明推断」；默认 `unstoppable`（:577）；`_ISOLATION_FORBIDDEN_EFFECTS={db,net}`
（:50-54）。`check_isolation_admission(tool)`（:863-887）返回 `None`=可声明 /
非空字符串=拒绝原因，且**纯声明面校验，不执行工具、不改运行路径**。
`ToolRegistry.timeout_capability(name)`（:907-920）已把准入折叠进返回值：
isolatable 且准入不通过 → 回落 `unstoppable`（:917-919）。
`git grep` 全 `src/` 中 `timeout_capability` 仅 6 处：4 处声明/校验 + loop 在
`:199` 用**裸 `getattr(tool, ...)`** 记录 E5 outcome 的 `declared` 字段——
**当前绕过了准入过滤**，B2.4 应改用 `registry.timeout_capability(...)`。

**E3 · E5 终态契约现状。** `_record_timeout_outcome`（`core.py:970-986`）逐调用
追加 `{kind,name,declared,state}`，四值语义钉在 :975-979；`_check_attempt_timeout`
（:988-1019）在截止时 finalize `_handle_attempt_timeout` 并抛
`AttemptTimeoutError`（:1016）；`loop/tools.py:205-206` 对 `AttemptTimeoutError`
**直接 re-raise**，不重试；`:195-202` 对「截止后工具自然返回」记 `returned`
（结果拒收）。WP-5「E5 终态不退化」即指这条链不能因接线而变形。

**E4 · `execution_lease` 现有语义与 S4 的证明边界。**
`src/agentnodes/kernel/gateway/session_lease.py`：非阻塞 `acquire`（:61-81，
占用返回 `None`、不顶替）、ownership token、`release` 按 token 校验（:83-95）、
`holder()` 仅观测且明确禁止用于互斥判定（TOCTOU，:97-105）；`threading.Lock`
保底跨线程（:52-54）。`gateway.py:102-110` 建共享实例；
`session_controller.py:165-190` 在 `_execute` 前后以嵌套 `finally` 获取/释放
（租约在**整个 run 外层**，不是每个工具）。3c S4（`tests/test_isolation_pilot.py:87-104`）
用 **stub lease** 证明 `acquire < actual_state=="stopped" < release` 事件序，
**未接真实 `SessionExecutionLease`**——B2.4 不新增租约，只保证「kill+确认完成前
不返回给持有 lease 的外层」。

**E5 · 已验收 seam 的时序实现。** `isolation.py`：`WorkerAdapter.kill_and_confirm`
契约「终止并**逐 pid 确认退出**，确认先于任何释放」（:55-61）；POSIX adapter
`supported()==False` 且调用抛 `UnsupportedPlatform`（:64-79，构造/单入口各再
校验 :250-253 / :468-471）；`ACTUAL_STATES` 四值与 4a 词表严格一致（:93-96）；
`_KILL_CONFIRM_TIMEOUT_S=5.0`（:104）；`ApprovalProxy` fail-closed（:150-162）；
channel 用 `_EOF` 区分读超时与 worker 退出（:101, :205-217）；
`IsolationWorker.call`（:265-303）在 `unknown` 时毒化关闭，**检查顺序为毒化先于
已关闭**（:282-288）；`_execute_on_channel`（:318-406）发任务行
`{"tool","args","ctx"}`（:330，无 id），把 `status=ok` 映射 `completed/stopped`
或工具级失败 `failed/returned`（:354-387），无结果行 → `failed/unknown`
（:388-406）；`_confirm_termination`（:409-429）杀并确认**根 pid**，全
`exited/already-gone` 才升级 `terminated/stopped`，口径按 3c D4（树由 Job Object
覆盖，不宣称全树逐 pid 确认）；transient 单入口在 `finally` 释放句柄**之前**
确认（:481-486）。

**E6 · B2.3 codec 契约与 id 现状。** `_isolation_context.py:32-40`：
`CTX_VERSION=1`、`WIRE_FIELDS` 9 字段白名单，**已含 `tool_call_id`**；
`to_wire`/`from_wire` 两端夹击（B2.3 §2 / §9）。B2.3 §2.6 明确：worker 内回调
恒 None（isolatable 工具须容忍无回调 ctx）、worker 不注入 loop 的 kwargs 直注面
（`workspace`/`session_id`/`_progress_callback`/…）、解码失败 ≠ 毒化。
B2.3 §5.7 / §6 裁定 **per-call id 归 B2.4**。关键区分：`ctx.tool_call_id`
是 LLM/UI 审批配对键且**重试时复用 `tc.id`**，不能充当 worker 任务-结果关联 id。

**E7 · 3c 试点证明的三件事（B2.2 §8.1 D1–D4）。** `tests/test_isolation_pilot.py`：
S1 真实 `write` 写临时工作区成功（:33-47）；S2 deadline → 杀+确认 →
`terminated/stopped` 且 wall-clock < 6s（:50-60）；S3(a) 迟到结果 payload 不含
工具输出、结构性拒收（:63-69），S3(b) 常驻 worker 超时毒化后拒绝复用（:72-84）；
S4 租约顺序（:87-104）。D2 的毒化是**协议级拒收保证**；per-call id 是更精确的
**关联**手段，二者不是替代关系（§2.2）。

**E8 · worker 协议现状（per-call id 的直接缺口）。** `_isolation_worker.py:108-166`：
任务行只有 `tool`/`args`/`ctx`（:122-124）；`permission_response` 在 worker 主循环
里仅 `continue`（:119-121，实际响应由 `probe_permission` 同步读下一行消费）；
所有事件/结果行无 id（:131-165）；`_run_tool` 每次调用重建注册表（:94-96）。
常驻 worker 复用时，旧任务的迟到结果行无 id 可辩——S3(b) 只能靠毒化兜底。

**E9 · 平台承诺。** B2.2 §11：Windows 本机真机覆盖，POSIX CI 只跑无平台依赖
守卫，Windows CI 不存在（明示不冒充）；生产启用前 POSIX 仍显式 unsupported。

**E10 · WP-5 / WP-6 原文要求。** roadmap `docs/design/architecture-refactor-execution-roadmap-20261119.md`
WP-5（§3 :144-151）：禁止「未确认退出就释放 lease / 超时后接纳迟到产物 /
把幂等误当成取消 / 把进程终止描述成可撤销副作用」；验收覆盖 deadline 前完成、
到达、终止成功、终止失败/未知、迟到结果与 lease，E5 终态不退化；**回滚须整体
关闭隔离 deadline adapter，不得保留部分接线**。WP-6（:153-160）才是 B2.5 的
`file_write` 真实工具试点。

## 2. 深模块设计

### 2.1 接线 seam 的单一入口：loop 层决策（推荐），`BaseTool.ainvoke` 不改执行分支

| 候选 | 论证 | 结论 |
|---|---|---|
| A. `BaseTool.ainvoke` 内按 capability + admission 走隔离 | `BaseTool` 没有 `_attempt_deadline` / `_record_timeout_outcome` / `tc.id` / lease / retry / worker 复用；这些都必须经 ctx 隐式属性或新参数灌入 → 浅抽象、隐式耦合。sync 路径直调 `invoke`（`loop/tools.py:190`）会**绕过** `ainvoke` 的隔离分支 → 两模式 E5 行为分叉，违反 WP-5「E5 终态不退化」。worker 自身调 `invoke`（`_isolation_worker.py:102`）虽不递归，但 `invoke` 绝不能作为接线点。 | 不推荐 |
| B. `loop/tools.py::_execute_tool_call_core` 决策（推荐） | 唯一持有 `tc.id`、`_attempt_deadline`、`_timeout_tool_outcomes`、`result` 载体、retry、circuit breaker、`tool_result` 事件与 `registry`；sync/async 共用该 core（`async_mode` 参数），一处接线覆盖两模式。`registry.timeout_capability(name)` 已折叠 B2.1 准入（E2），决策无需新依赖。隔离 seam 仍是深模块 `isolate_tool_call()`，loop 只做**选择与结果适配**。 | **推荐** |

**单一入口形态**：`_execute_tool_call_core` 在拿到 `tool`、guard 通过后调用
`should_isolate(tool, ctx, registry, flag)`（wiring helper 内唯一读取生产开关 +
能力 + 准入 + 平台的函数）；为真时走 `run_isolated(...)`，否则保持现有
`ainvoke`/`invoke` 调用**逐字不变**。所有路径（async / sync / retry）都必须经
同一个 `should_isolate`，测试断言「开关只有这一处读取点」——这是 WP-5
「不得保留部分接线」的可测落点。

**权限不绕过**：隔离分支里 worker 无 permission 对象（B2.3 §2.6），ASK 必须由
父进程 `ApprovalProxy` 裁决且 fail-closed。为避免复制 `_enforce_permission` 的
ALLOW/DENY/ASK 握手，建议把其判定体等价抽成 `tools/core.py` 内共享 helper，
`ainvoke` 与隔离 decider 都调用它（见 §5 决策 6）。`tool_call_id` 继续用
`ctx.tool_call_id`（= `tc.id`）与 UI 配对；**不要**改用新的 per-call id。

**结果适配**：`IsolationResult` → loop 期望的 `output: str` 由 helper 单点转换：
`completed` → `payload["output"]`；工具级 `failed/returned` → 原 output 字符串
（下游 `classify_tool_output` 行为不变）；`denied` → 结构化 error JSON；
`terminated`/`unknown` → 确定性 error JSON（含 `actual_state`、**不含工具输出**，
兑现 3c S3a）。`ToolResult` 的 wire envelope 在 worker 的 `invoke` 内已生成，
helper 不得二次包装。

### 2.2 per-call id 关联协议（本阶段引入；与毒化分层）

**三个 id，职责不得混淆**

| id | 来源 | 用途 | 重试时 |
|---|---|---|---|
| `tc.id` / `ctx.tool_call_id` | LLM tool_call | SSE 审批配对、前端 `call_id` 事件 | **复用**（同一轮多次 attempt） |
| `call_id`（新增） | wiring helper，每次 attempt 生成（建议 `f"{tc.id}:{attempt}"` 或 uuid） | worker 任务行 ↔ 结果/事件行精确关联 | **每次 attempt 全新** |
| lease token | `SessionExecutionLease.acquire` 返回 | 会话互斥、按 token 释放 | run 级，不在工具层 |

**协议改动（最小）**：任务行信封加 `v`（建议 `TASK_V=1`）与 `call_id`：
`{"v":1, "call_id":..., "tool":..., "args":..., "ctx":...}`；worker 的
`progress` / `permission_request` / `result` **所有回传行回显 `call_id`**；
`v` 缺失/不兼容显式报错（与 B2.3 §5 决策 2「信封级留给 B2.4」一致），
`ctx_v` 与其 9 字段白名单不动。父进程只消费 `call_id == 当前 attempt` 的行；
不匹配行**丢弃并计数**（可观测，不接纳其 payload），不把它当成当前调用的结果。

**与 3c D2 毒化的关系（必须写进实现契约）**：毒化是**拒收保证**——worker 一旦
`unknown` 就杀+确认+关闭，后续 `call()` 抛 `RuntimeError`；per-call id 是
**关联精度**——解决「常驻 worker 复用时迟到结果错配」。二者是纵深防御：
有了 id 也不得取消毒化（被杀 worker 仍可能持有部分副作用/未确认退出）；
有了毒化也不得省掉 id（毒化只在 `unknown` 触发，工具级 `returned` 后 worker
仍健康复用，此时无 id 仍会错配）。

**幂等纪律**：`tc.id` 跨重试复用，因此**绝不能用 `tc.id` 当 `call_id`**
（这正是 E6 指出的缺口）。timeout / `terminated` / `unknown` 之后**禁止自动
重试**——隔离不使工具幂等，重试可能重复副作用（WP-5「把幂等误当成取消」）。
transient 错误的重试每次拿新 `call_id`；若该次隔离以 `unknown` 结束，循环立即
停止（现有 `AttemptTimeoutError` 已在 `:205-206` re-raise，隔离分支沿用）。

### 2.3 deadline / lease 顺序与 actual_state 纪律

**顺序（3c D1/D4 + S4 的生产版）**：

```
外层 acquire(lease) ──> should_isolate ──> run_isolated(remaining_budget)
    ──> deadline ──> adapter.kill_and_confirm([root pid], 5s)
    ──> 全部 exited/already-gone ──> status=terminated, actual_state=stopped
    ──> 毒化/关闭 worker ──> 返回 loop ──> 记录 outcome + E5 finalize
    ──> 外层 finally release(token)
```

- **budget**：`timeout_s = max(0, _attempt_deadline - time.monotonic())`，不得用
  seam 默认 30s；`_KILL_CONFIRM_TIMEOUT_S=5s` 是截止后的清理预算，须在时序测试
  里计入 wall-clock（S2 的 6s 口径）。
- **阻塞隔离**：`isolate_tool_call` 是同步阻塞实现（读线程 + `time.monotonic`），
  必须在 `asyncio.to_thread` 中执行，否则阻塞事件循环、并发工具互饿；上层
  `asyncio.wait_for` 只作返回保护，**不能替代 seam 内的 kill+确认**。
- **确认先于释放**：隔离分支在 kill+确认完成（或明确 `unknown`）之前**不得
  返回给 loop**；外层持有 lease 的 `finally` 因此天然落在确认之后。未确认时
  `actual_state` 保持 `unknown`、**不升级 stopped**、worker 毒化；lease 仍由
  acquisition 层的 `finally` 释放（否则永久占用），但那是生命周期清理，不是
  「已停止」断言——结果/事件必须显式标 `unknown`（见 §5 决策 9 待裁决措辞）。
- **毒化判定必须在确认前捕获**：`_confirm_termination` 会把 `result.actual_state`
  从 `unknown` **原地改写成 `stopped`**（`isolation.py:425-429`）。
  `IsolationWorker.call` 的 `if result.actual_state == "unknown"` 在进入时求值一次，
  所以现有毒化仍成立；接线/后续「简化」时**不得**改成确认后重判，否则会把已
  被杀死的 worker 误当可复用。这条是并发安全红线。

**actual_state 四值声明 ≠ 事实纪律**（E2/E3）：

| 隔离结果 | E5 `declared`（用 `registry.timeout_capability`） | E5 `state`（实际观测） | 是否 E5 finalize |
|---|---|---|---|
| `completed` / `stopped` | isolatable | `stopped` | 否（正常完成） |
| `failed` / `returned`（工具级失败、denied、ctx 解码失败、截止后自然返回） | isolatable | `returned`（结果拒收） | 仅当截止已到 |
| `terminated` / `stopped`（确认成功） | isolatable | `stopped` | **是** |
| `failed` / `unknown`（未确认 / EOF，且 `timed_out`） | isolatable | `unknown` | **是** |

`still_running` 只属于 abandonable 同步桥，隔离分支不得产生该值。任何情况下
不得由 `timeout_capability` 推断实际状态；`declared` 字段必须取准入过滤后的
`registry.timeout_capability(name)`，修正 E2 指出的裸 `getattr` 偏差。
隔离 deadline 必须**重注入 E5 终态链**：记录 outcome → `_handle_attempt_timeout`
→ `AttemptTimeoutError`，禁止把 `terminated` 当普通 `tool_result` 返回。

### 2.4 诚实边界：不撤销副作用

kill+确认只证明**进程停止**，不撤销已发出的文件写入；S1 已证明工具在 worker 内
真实落盘。发布说明/事件措辞必须是「停止执行 + 结果拒收」，不得写「回滚 /
无副作用 / 取消已完成」。B2.3 §2.4 已记录的「worker 继承父 env，密钥可经 env
跨界」仍属 adapter 层未知项，本阶段只保留记录、不宣称 schema 解决了它。

### 2.5 并发与幂等纪律（生产接线专项）

1. **单一决策函数**：`should_isolate` 是生产开关唯一读取点；sync/async/retry
   全部经它；测试用 fake adapter 断言「开关关闭时行为逐字同 HEAD」。
2. **worker 复用默认关闭**：B2.4 默认每次调用 transient worker（安全优先，代价
   ~500ms）；常驻 worker 复用另设开关，且必须在 per-call id + 毒化契约验收后
   才可开（§5 决策 7）。
3. **不并发复用同一 worker 句柄**：`IsolationWorker.call` 无锁、串行协议；
   `asyncio.gather` 并行工具若共用 worker 会在通道上错配响应——助手必须让每次
   隔离调用独占 worker（transient 天然满足；复用模式须加 single-flight 约束）。
4. **`before_execute` 恰好一次**：现有 `ainvoke`(:683) 与 `invoke`(:624) 各调
   一次（HEAD 已是两次，本批不顺手改）；隔离分支绕过二者，须在父进程派发前
   显式调用一次 `_check_attempt_timeout`，且**不进 wire**（codec 已排除回调）。
5. **ASK 仍在父进程**：`ApprovalProxy` decider 需闭包捕获本调用的
   `(tool_name, args, ctx)`（worker 事件只带 `id`/`tool`，无 args），用
   `ctx.tool_call_id` 与 gateway 配对；未知裁决 fail-closed。
6. **侧效应重复**：任何 `unknown`/`terminated` 结果都不得触发 loop 的 transient
   重试；结果 JSON 带 `actual_state`，供上层区分「从未执行」与「已执行但被杀」。

## 3. 分批与验收

| 子批 | 内容 | 改哪里 / 不改哪里 | 验收 |
|---|---|---|---|
| **4a** | 接线 seam 探针：新增 L3 私有 helper（`should_isolate` / `run_isolated` / 结果适配 / 开关读取）；生产开关默认关；shadow 模式（只计算决策并发事件，不执行隔离）；fake adapter 驱动 | 只新增 helper + 测试；生产路径仅在 core 插入**一次** `should_isolate` 调用且默认返回 False；不改 seam / worker / codec | 开关关时现有 E5 与工具测试结果逐字不变（全平台）；决策函数单测覆盖 capability、admission 拒绝回落、平台、开关；POSIX 不启用；层方向/dangling import 0；L3 新文件数字同步 |
| **4b** | per-call id 协议：任务行信封 `v` + `call_id`，worker 全回传行回显，父侧按 id 过滤 + 丢弃计数；codec / `ctx_v` 不动；毒化语义不动 | 改 `isolation.py` 的 `_execute_on_channel`、`IsolationWorker.call` 与 worker 回传；不改 `BaseTool` / loop / lease | 全平台契约测试（信封版本拒、id 回显）；win32 真 worker：两次串行调用，第一次迟到结果被第二次丢弃且 payload 不接纳；S3b 毒化仍拒复用；既有隔离 50 用例不退化 |
| **4c** | 生产接线 + deadline/lease 顺序：`run_isolated` 接 `asyncio.to_thread` + 剩余预算 + kill/确认前不返回 + E5 outcome 重注入 + declared 改 `registry.timeout_capability` | 改 `loop/tools.py` 一处分支 + helper；不改 lease/gateway；不改 E5 终态语义 | WP-5 六场景逐条（下表）；win32 真机时序：acquire < 确认 < release；未确认 → `unknown` + 毒化且结果不接纳；`AttemptTimeoutError` 仍抛；E5 既有测试零改动通过 |
| **4d** | 生产开关默认关闭 + 灰度：win32-only 校验；shadow → 工具 allowlist（B2.4 **允许列表为空**，不启用真实工具，留给 WP-6）；回滚演练 | 只改配置/开关读取与文档；不改执行逻辑 | 关开关 → 全量回到 HEAD 行为；开关强制开但 POSIX → 显式 UnsupportedPlatform / 启动期拒绝，绝不静默 fallback；同步与异步路径都由 `should_isolate` 决策；「不得保留部分接线」有测试锚点 |

**WP-5 验收行 → 落点映射（4c 收口逐条核对）**

| WP-5 验收 | 落点 |
|---|---|
| deadline 前完成 | 4a shadow + 4c `completed/stopped` 正向用例（E5 不 finalize） |
| deadline 到达 | 4c `timeout_s = 剩余预算` 用例；wall-clock < deadline+5s 余量（S2 口径） |
| 终止成功 | `_confirm_termination` 全 `exited/already-gone` → `terminated/stopped`，E5 `state=stopped` + finalize |
| 终止失败/未知 | 未确认 → 保持 `unknown`、毒化、结果拒收、E5 `state=unknown` + finalize |
| 迟到结果 | 4b per-call id 丢弃 + 3c S3a 结构性拒收 + S3b 毒化，三重保证 |
| lease 不误放 | 确认前不返回 + 外层 `finally` 按 token 释放；真 lease stub 事件序断言；不用 `holder()` 做判定 |
| E5 终态不退化 | 开关关时 E5 测试零改动；开关开时 `AttemptTimeoutError` / `finished_reason="timeout"` / `_ending_emitted` 仍成立 |

门禁沿用 R-007：先受影响聚焦测试 → 全量 pytest → `batch_gate` → 架构数字同步；
每子批独立提交、独立回滚。POSIX 行为类 win32-gated skip-with-reason（B2.2 §11
口径不变），全平台守卫测试不得被整文件 gate 误伤。

## 4. 风险与回滚

- **生产接线的回滚开关**：单一开关（建议 `AGENTNODES_ISOLATION_EXECUTION`，默认
  `0`），关闭即回到既有 in-process 执行；wiring 代码保留但不可达。**不得保留
  部分接线**：sync/async/retry 共用 `should_isolate`，禁止某条路径硬编码隔离或
  硬编码绕过；用测试钉住「开关是唯一决策输入」。回滚演练（4d）须在关状态下
  跑全量 + gate 全绿。
- **平台限制**：POSIX 仍**禁启用**。`supported()==False` 的 adapter 与
  `isolate_tool_call` 的显式拒绝（`isolation.py:64-79,250-253,468-471`）保持不变；
  开关求值先按 `sys.platform == "win32"` 短路，非 win32 永远走原路径；若有人强制
  开启，须在配置校验期显式报错，不得静默降级为 in-process 并声称「隔离成功」。
- **租约顺序风险**：确认前返回会让外层 `finally` 提前释放 lease；未确认时不得
  把 `unknown` 写成 `stopped`；`holder()` 只能观测不能判定（E4）。
- **并发风险**：阻塞 seam 直接跑在事件循环会饿死并行工具；worker 句柄不可多
  路复用；`_confirm_termination` 原地改 `actual_state` 会诱使后续误判复用（§2.3）。
- **幂等风险**：`tc.id` 跨 attempt 复用；timeout 后重试会重复副作用；B2.4 禁止
  在 `terminated/unknown` 后重试，且必须把「已执行但被杀」显式写进结果。
- **副作用诚实边界**：进程终止不撤销已写文件；发布说明只写「停止执行/结果拒收」。
- **权限风险**：ASK 必须父进程裁决且 fail-closed；worker 内不得自行 permissive；
  `tool_call_id` 配对键不得被 per-call id 替换。
- **已知未处理（记录不扩大）**：worker 继承父 env（密钥跨界）；工具依赖 kwargs
  直注或回调的 ctx 分叉（B2.3 §2.6，属 B2.5 准入复核）；sync-only xfail。

## 5. 待确认决策点（含推荐默认）

| # | 决策点 | 推荐默认 | 备选与理由 |
|---|---|---|---|
| 1 | 接线点 | **loop 层 `_execute_tool_call_core` + 私有 helper**，`BaseTool.ainvoke` 不改执行分支 | `ainvoke`：无 deadline/lease/id，sync 路径绕过（`loop/tools.py:190`）→ E5 分叉，违反 WP-5 |
| 2 | per-call id 是否本阶段引入 | **引入**（任务行信封 `v` + `call_id`，每次 attempt 新 id），毒化保留 | 不引入：重试复用 `tc.id` + 常驻 worker 时迟到结果必错配（E6/E8）；B2.3 §5.7 已裁定归 B2.4 |
| 3 | 生产开关默认值 | **默认关闭**；win32-only；B2.4 允许列表为空，不启用真实工具 | 默认开：B2.4 无真实工具试点（WP-6），且零回滚缓冲；不允许 |
| 4 | 灰度策略 | **shadow（只记决策）→ 工具 allowlist**；B2.4 不填 allowlist，B2.5 才放 `file_write` | 直接全量：无法隔离副作用边界；按工具名灰度可逐项回滚 |
| 5 | `abandonable`/`cancellable` 是否接隔离 | **不接**；仅 `isolatable` + 准入通过 + win32 + 开关才隔离，其他维持现状 E5 记录 | 接：四级是机制属性非强弱排序（`core.py:33-38`），扩容会改变既有终态语义，超出 WP-5 |
| 6 | 权限判定共享 helper | **从 `_enforce_permission` 等价抽取判定体**，`ainvoke` 与隔离 decider 共用 | 复制 ASK 握手：两处逻辑易漂移，fail-closed 风险；纯注入 decider：需重写 SSE 配对 |
| 7 | worker 复用 | **默认 transient per call**；复用另设开关，须在 4b id + 毒化验收后才开 | 默认常驻：~500ms 节省但引入单入口并发/毒化后残留状态，收益不抵风险 |
| 8 | 任务行信封版本 | **新增 `v=1`，缺失/不兼容显式拒**；`ctx_v` 不动 | 不版本化：B2.3 §5 决策 2 已把信封级留给 B2.4；结构演进需显式锚 |
| 9 | 未确认时的 lease 措辞 | **保持 `unknown`、不报 stopped、毒化、结果拒收；lease 仍由 acquisition 层 `finally` 释放并显式记 unknown** | 「未确认就一直持有 lease」需定义升级/人工介入与超时上限，否则永久占用；若维护者要求此语义，须补独立裁决 |
| 10 | 隔离后重试 | **终止/未知后禁止自动重试**；每次 transient attempt 新 `call_id` | 允许重试：等于把超时当幂等取消，违反 WP-5 且可能重复副作用 |

## 6. 明确不做

1. **shell 工具接入**——B2.5 裁决延后（B2.2 §12 #4），不因接线顺手扩大。
2. **POSIX 生产启用**——禁启用保护不变；生产开关在 POSIX 永远短路为原路径。
3. **B2.5 真实工具试点**——`file_write` 临时工作区试点属 WP-6；B2.4 允许列表
   为空，只用探针/fake adapter 验证接线。
4. **无界副作用承诺**——不宣称终止可撤销已发生写入；不修 env 密钥跨界。
5. **`abandonable`/`cancellable` 接隔离**——见 §5 决策 5。
6. **`ToolContext` / codec schema 变更**——per-call id 属任务行信封，不入 ctx；
   `CTX_VERSION` / 9 字段白名单不动。
7. **`SessionExecutionLease` / gateway / session_controller 改动**——复用既有
   租约与 `finally` 释放；不新增第二套租约或改 404/409 等终态契约。
8. **sync-only xfail、prune/registry、历史文档回写**——本批不碰。

> 实施后按子批追加执行记录（§7+，风格同 B2.3 §7–§9）；本文当前不含实施授权。

## 7. 子批 4a 执行记录（2026-11-20 · 已实施 · 零生产影响）

- 新 helper `kernel/agent/loop/_isolated_exec.py`（L3）：`should_isolate`
  决策探针（开关→平台→capability→准入，顺序先硬后软）+ `IsolationDecision`
  + `isolation_execution_enabled`（`AGENTNODES_ISOLATION_EXECUTION` 默认
  关、大小写不敏感）+ `shadow_event`。生产路径必须传 registry——
  `registry.timeout_capability(name)` 已折叠 B2.1 准入回落；合成工具
  场景才降级裸 getattr。
- 生产路径仅一处改动：`loop/tools.py::_execute_tool_call_core` 拿到 tool
  后调用一次 `should_isolate(tool, self.registry)` 并发
  `isolation.shadow_decision` 事件（**只发事件、不执行隔离**）。执行分支
  逐字不变；sync/async/retry 共用该决策点（单一输入，无旁路）。
- 新测试 `tests/test_isolated_exec_decision.py`：开关值（默认关/6 关值/
  4 开值）、决策短路顺序（关→任何工具不隔离；非 win32 永不隔离；
  capability 门；isolatable 放行；registry 准入回落→reason=admission）、
  shadow 事件形状。

验证：决策测试 23 passed（0.35s）；settings 面 65 passed；loop 核心
+ E5 timeout 面 142 passed / 2 xfailed（开关关时逐字不变）；层方向 0
违规；数字同步 462 文件 / 96,341 行（L3 +1 helper +142 行，L4
settings 登记开关 +3 行）。实施中门禁抓到两处纪律缺口并已修：
`check_env_access`（开关须经 settings 登记，不能裸读 os.environ）、
`test_no_phantom_declarations`（常量形态读取需 RESERVED 说明）——
两次均按门禁反馈修正而非绕过。仍零实际隔离执行（4c 才接）。

下一子批 4b（per-call id 协议）需单独批准。

## 8. 子批 4b 执行记录（2026-11-20 · 已实施 · 仍零生产接线）

- **任务行信封**（`isolation.py`）：新增 `TASK_V=1`（与 ctx_v 正交——
  信封管任务关联，ctx_v 管上下文 schema）；`_execute_on_channel` 每次
  调用生成**全新** `call_id = f"{tool}:{uuid12}"`（不复用 `tc.id`，
  方案 §2.2 幂等纪律），任务行/审批响应行带 `v` + `call_id`；
  `IsolationResult` 增 `call_id` + `discarded_rows`（丢弃计数可观测）。
- **父侧过滤**：只消费 `call_id == 当前` 的回传行；不匹配行**丢弃并
  计数**、绝不接纳其 payload（fake channel 测试精确验证：注入 stale
  + current 两行 → 接纳 NEW、discarded_rows==1）。
- **worker 侧**（`_isolation_worker.py`）：模块镜像 `TASK_V=1`（worker
  子进程不 import isolation.py 以免拉整个 seam；契约测试钉住同值）；
  主循环校验信封 `v`（缺失/不兼容 → 结构化错误行）与 call_id 非空；
  `_CURRENT_CALL_ID` 单任务状态 + `_emit` 自动给**所有**回传行回显
  call_id（progress/permission/result 全覆盖）。
- **与毒化分层**：per-call id 是关联精度，3c D2 毒化是拒收保证——
  二者互不替代（工具级 returned 后 worker 健康复用时仍需 id 防错配）。
  codec / ctx_v / 9 字段白名单 / 毒化判定全部未动。

验证：全平台 codec+守卫 33 passed；platform 新增信封同值 + 迟到
丢弃 2 例（11 passed）；win32 真机 19 passed（含每次调用全新
call_id、审批往返不退化）；全量 + gate 见提交前记录；数字同步
462 文件 / 96,397 行（L3 +56 行）。仍零实际隔离执行（4c 才接）。

下一子批 4c（生产接线 + deadline/lease 顺序）需单独批准。

## 9. 子批 4c 执行记录（2026-11-20 · 已实施 · 开关默认关）

- **helper**（`_isolated_exec.py`）：`run_isolated`（同步阻塞核心）+ `arun_isolated`
  （`asyncio.to_thread` 包装，不阻塞事件循环）+ `IsolatedOutcome`（结构化
  结果：payload/actual_state/status/call_id/discarded_rows/timed_out +
  `result_rejected`）。剩余预算 `max(0, deadline-now)` 透传（不用 seam
  默认 30s）；每调用独占 transient worker（不复用句柄→无并发错配）；
  seam 内 kill_and_confirm 完成后才返回（确认先于释放，lease 外层 finally
  天然落在确认之后）；本函数不自动重试（unknown/terminated 不得重试）。
- **loop 接线**（`loop/tools.py`）：retry 循环执行点一处分支——仅
  `should_isolate=True`（开关开 + win32 + isolatable + 准入通过）可达；
  执行前 `tool.enforce_permission_sync` 规则判定（**实现调整**：worker
  内无 gateway，ASK 无法在 blocking 线程 await——故隔离模式 ASK 走
  fail-closed，与 sync 路径同语义；方案 §2.5.5 的 gateway 配对属 4d+
  异步握手另议）；E5 outcome 用 `_emit_isolation_outcome` 重注入
  （declared 取 `registry.timeout_capability`，修正裸 getattr 绕过 B2.1
  回落；state 取实际观测；terminal → `_check_attempt_timeout(finalize)`
  + `isolation_terminal` 事件；正常完成不重注入）；截止后自然返回的
  declared 也改 registry。**开关关时所有分支逐字不变**。
- 新测试 `tests/test_isolated_exec_wiring.py` 12 用例：run_isolated 六
  场景（deadline 前完成/到达/终止成功/终止失败未知/迟到丢弃计数/unknown
  不重试/transient 独占/ctx+approval 透传/无预算用默认）+ E5 重注入
  3 例（declared 取 registry、终态重注入、正常完成不重注入）。

验证：wiring 12 passed；开关关闭回归 118 passed / 2 xfailed（loop+E5
timeout 面逐字不变）；层方向 0 违规；数字同步 462 文件 / 96,577 行
（L3 +180 行）。**生产开关仍默认关、工具 allowlist 为空（4d 灰度）**，
正常生产路径不会走隔离分支。

下一子批 4d（开关默认关 + 灰度 + 回滚演练）需单独批准。

## 10. 子批 4d 执行记录（2026-11-20 · 已实施 · B2.4 收口）

- **灰度 allowlist**：新增 `AGENTNODES_ISOLATION_TOOLS`（逗号分隔，
  经 settings 登记，默认空）——**B2.4 不放行任何真实工具**（留给 B2.5
  试点单独裁决）；`should_isolate` 判定新增第 5 顺位「工具名不在
  allowlist → 不隔离」（reason=not_allowlisted）。
- **启动期平台校验**：`validate_isolation_config()`——开关默认关时永远
  通过（零影响）；强制开但非 win32 → 抛 RuntimeError（不静默降级
  in-process 后声称隔离成功）。
- **测试**：decision 测试补 allowlist 解析/默认值、平台校验、单一决策
  输入（开关关时 allowlist 有无都不隔离）等；settings RESERVED 补新
  env 说明（常量形态读取，扫描器盲区）。
- **回滚演练**：关开关状态下跑全量 + gate 全绿（8003 passed，仅数字
  同步需更新）——证明关闭即回到 HEAD 行为；wiring 代码保留但不可达；
  不得部分接线由「开关是唯一决策输入」测试锚钉住。

验证：decision+settings 71 passed；env 门禁 0 问题；全量 8003 passed
（关开关）；数字同步 462 文件 / 96,622 行（L3 +42 / L4 +3）；gate 全绿。

**B2.4 结论**：4a（决策探针）→ 4b（per-call id）→ 4c（生产接线+
deadline/lease/E5 重注入）→ 4d（灰度 + 回滚演练）全部验收；全程
**生产开关默认关 + allowlist 空**，正常路径零行为变化。四子批独立
提交、独立可回滚；回滚任一子批即恢复前态，普通工具调用路径从未改变。

**后续**：B2.5（WP-6 真实工具试点 + 准入复核）需在 B2.4 验收后单独
批准——注意 4d allowlist 默认空正是 B2.5 试点的受控入口（试点放行
`write` 须经 B2.5 方案裁决与单独审批，不得在此顺手打开）。
