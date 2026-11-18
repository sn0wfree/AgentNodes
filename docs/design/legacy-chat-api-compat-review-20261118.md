# 旧聊天队列 API 兼容出口审查（裁决 3 前置）

> **日期**：2026-11-18 · **状态**：审查完成，**删除未获批准**
> **依据**：`project-architecture-review-and-refactor-plan-20261118.md` §8.3 裁决 1（`send_message` 为兼容 API）+ §12.1 顺序 2/3 + §12.2 剩余边界
> **审计工具**：`scripts/arch/audit_legacy_chat_api.py`（只读、可复跑、不调用被审计的旧实现）
> **裁决结论**：**先批准兼容审查，不批准删除**——本文件给出审查证据、两个待裁决差异、删除边界与兼容期策略。
>
> **后续复核**：§2–§8 保留为当时审查记录，当前状态和测试盲区以 §9 为准；
> 特别是超时缺口尚未闭合，不能据当时的“可删”标注实施删除。

## 0. 结论摘要

1. **仓库内生产调用点 = 0**（逐出口实测，见 §2）；但"仓库内无调用 ≠ 外部无消费者"——`send_message` 已被定为**兼容 API**，删除属对该裁决的变更。
2. **R-015 四项判定**：① Gateway 接管方向已定 ✅；② 主路径与 Goal lease 已接线 ✅；③ **完整出口网尚未证明** ❌；④ **独立依赖审计本文件已交付** ✅（`audit_legacy_chat_api.py`）。→ **四项未全部满足，不得删除**。
3. **两个真实差异需裁决**（§4）：超时语义在 Gateway 侧无对等物（缺口①）；Gateway 聊天历史读取异常被吞（缺口③，与裁决 2 严格读契约冲突）。**2026-11-19 校准：两项均已裁决并实施**（`d9945a5c`，§4 各附校准块）；仍未闭合的是**超时等价性**（两个结构性 xfail，路线步骤 3-4）与**完整出口网**（§9/§10）。
4. **缺口②已证伪**：lease 释放后**常驻消费者自动继续消费**（实测通过，非手动 `run_once`）。
5. **删除边界已划清**（§5）：保留会话/历史/Attempt 持久化/压缩；**不得**随旧队列删除 `_convert_messages_to_history`；**不是**删除全局 AgentLoop。

## 1. 审查方法（不依赖旧实现）

| 手段 | 说明 |
|---|---|
| 静态引用审计 | `scripts/arch/audit_legacy_chat_api.py`：逐出口扫描 src/tests/scripts/docs 的定义与调用点，**按接收者归属**判定（`gateway.cancel_session` ≠ `service.cancel_session`；`runtime.cancel` ≠ `svc.cancel`）；排除日志字符串与定义文件内部自调用 |
| 绝对值断言 | `tests/test_gateway_legacy_parity.py` **18 用例**：每个旧出口只读 **Gateway/apps 当前行为**的绝对值，不与旧实现对比（旧实现即将删除，不能作为基准） |
| 缺口核实 | 超时 env 执行引用 grep；常驻消费者唤醒路径读码 + 真实线程测试；历史读取异常处理读码 |

**静态扫描的固有盲区**（必须写进兼容期公告）：动态 import、插件加载（`importlib.metadata.entry_points`）、仓库外消费者、反射调用。

## 2. 逐出口审计表（2026-11-18 实测）

| 旧出口（`kernel/session/service.py`） | 定义 | **src 生产调用** | 测试调用 | 文档提及 | Gateway 对应物 | 判定 |
|---|---:|---:|---:|---:|---|---|
| `send_message` | :126 | **0** | 30 | 42 | `api_chat_request` + mailbox + `run_once` | 兼容 API（裁决 1）——**不删** |
| `cancel` | :315 | **0** | 16 | 133 | `cancel_session` | 可删（无生产调用） |
| `cancel_session` | :328 | **0** | 9 | 11 | `Gateway.cancel_session` | 可删（同名者是 Gateway 的，非本类） |
| `resume_queue` | :352 | **0** | 3 | 3 | `Gateway.resume_session` | 可删 |
| `wait_for_attempt` | :474 | **0** | 3 | 2 | `Gateway.wait_turn` | 可删 |
| `is_session_processing` | :363 | **0** | 4 | 16 | `execution_lease.holder` | 可删（B4 已标注退役候选） |
| `mark_session_processing` | :397 | **0** | 5 | 19 | `execution_lease.acquire/release` | 可删（B4 已标注退役候选） |
| `list_active_attempts` | :416 | **0** | 8 | 5 | `apps/chat.attempts_query.query_live_attempts` | 可删（已迁，30c40467） |
| `get_available_actions` | :380 | **0** | 4 | 4 | `Gateway.chat_session_state` | 可删 |

**重导出链**（外部消费者可能从这里 import，兼容期必须保留）：

| 文件 | 符号 | 状态 |
|---|---|---|
| `src/agentnodes/kernel/session/__init__.py` | `_CHAT_ATTEMPT_TIMEOUT` | 存在且重导出 |
| `src/agentnodes/interfaces/api/session/service.py` | `_CHAT_ATTEMPT_TIMEOUT` | 存在且重导出 |

**与旧队列绑定的 env**：

| env | src 命中 | 执行读取点 | 结论 |
|---|---:|---|---|
| `AGENTNODES_CHAT_ATTEMPT_TIMEOUT` | 3（settings 声明 + 旧 service 读取 + 旧 service 使用） | **仅** `kernel/session/service.py:43`（2026-11-19 校准：Gateway 侧已有等价物——session_controller 读同一 env 同语义，缺口① 已闭合 `d9945a5c`） |
| `AGENTNODES_EVENT_LOG_READ` | 4 | 仅 settings 声明 + 诊断层 | 读取层已按 C4 移除（顺序①），无旧实现依赖 |

## 3. 逐出口的 Gateway 绝对值断言（`tests/test_gateway_legacy_parity.py`，18 用例）

| 出口 | 断言（绝对值，不依赖旧实现） |
|---|---|
| send_message | 提交 → `run_once` 产出 answer="ok"；回执 `accepted=True`/`reason=None`/`session_id` 非空；空队列 → `run_once() == []` |
| cancel / cancel_session | 无活跃 run → `False`；有活跃 run → `True` 且 `chat_session_state.paused is True`；重复取消幂等 `True` |
| resume_queue | 未暂停 → `False`；取消后 → `True` 且 `paused is False` |
| wait_for_attempt | 无 turn 完成 → 超时 `None`；有结果 → `answer == "ok"`（代际语义，不返回上一轮） |
| list_active_attempts | 存储有记录但 Gateway 无实时状态 → **丢弃**（僵尸行）；查询用例 = 存储快照 × Gateway 实时状态 |
| get_available_actions | 空闲 → `active/pending/paused` 全 False + `lease_holder is None`；有 pending → `pending is True`；goal 持有 → `lease_holder == "goal:g1"` |
| is_session_processing | 空闲 → `holder is None`；持有 → `holder == "goal:g1"`；释放 → 回到 `None` |
| **缺口② 常驻消费者** | **真实 `start_consumer` 线程**：goal 持有期间 `client.calls == []`（不执行）；lease 释放后**无需手动 `run_once`**，消费者自动继续（轮询兜底生效） |

## 4. 两个待裁决差异（旧路径有缺陷，不机械照搬）

### 缺口①：超时语义在 Gateway 侧无对等物

- 现状：`AGENTNODES_CHAT_ATTEMPT_TIMEOUT`（默认 600s）的**唯一执行读取点**是 `kernel/session/service.py:43`，只作用于**旧队列**的 attempt 执行（`:698/:714/:719/:722/:740/:746`）。
- Gateway 路径：`_send_via_gateway` → `SessionController` → `AgentLoop`，**没有同等的 attempt 级超时**。
- **差异影响**：删除旧队列后，"单次 chat 超过 600s 自动失败"这一语义**在 Gateway 路径上没有承载点**（长任务可能无限跑）。
- **建议裁决（二选一，需维护者定）**：
  - (a) **在 Gateway 侧补等价超时**（推荐）：`SessionController` 执行 turn 时按同一 env 设墙钟上限，超时 → attempt 失败 + 事件 + Attempt 终态，与旧语义对齐；
  - (b) **明确宣布该语义退役**：把 env 移入诊断层（legacy），文档注明"Gateway 路径不再有 attempt 级超时"，并接受长任务风险。
- **不做**：不静默删除 env 声明（会触发 phantom 门禁 + 破坏外部配置）。

> **2026-11-19 校准**：已按 **(a) 裁决并实施**（`d9945a5c`）：session_controller
> 读同一 env（get_int，默认 600、0=禁用），计时从实际执行开始、不含排队；
> attempt 到点 → 失败终态 + 事件 + fence/lease 释放。**尚未闭合**：同步工具
> 强中断与 sync-only 线程两个结构性 xfail（超时设计 §9.1/9.5-9.6）；进程隔离
> 原型（路线步骤 3）与超时能力契约（路线步骤 4：限制/拒绝而非宣称全部可
> 中断）未启动。**不得宣称超时完全等价。**

### 缺口③：Gateway 聊天历史读取异常被吞

- 现状：`interfaces/api/routers/chat.py:514` `history = service._convert_messages_to_history(...)`，异常时 `:518` 仅 `logger.warning("send_via_gateway: history load failed: %s", exc)`，随后以**空历史继续执行**。
- **与裁决 2 冲突**：裁决 2 规定"聊天上下文等严格读路径应显式报错……不能静默喂给模型"。静默空历史 = 模型"忘记"历史，比报错更危险（且不产生可观测错误）。
- **建议裁决**：改为**显式失败**（HTTP 5xx + 明确错误体 + 事件），与 `MemoryManager.get` 的 `HistoryReadError` 语义对齐。
- **不做**：不静默保留（那正是缺口本身）；不改则须在删除清单里注明"历史读取降级仍在"，不得宣称"严格读契约已全量落地"。

> **2026-11-19 校准**：已按建议裁决实施（`d9945a5c`）：`_send_via_gateway`
> 历史读取失败 → `HTTPException(500, {"error": "history_unavailable"})`，
> submit 不发生（无 run/attempt/事件）。仅显式标记的 UI best-effort 读取
> （`get_stale`）可降级，不进模型上下文。

## 5. 删除边界（划清，避免误删）

**保留（不在删除范围）**：

- `SessionService` 的会话生命周期（`create_session` 等）与 Attempt 持久化；
- `SessionStore` 历史读取（`get_messages`）、`Projector` / `ProjectionStore` 读模型；
- `compact_history`（压缩）；
- **`_convert_messages_to_history`（聊天路由仍在调用，`chat.py:514`）**——随旧队列一起删会直接打断 /send；
- 全局 `AgentLoop`（kernel/agent/loop）——本裁决**不涉及**删除 AgentLoop，只是不再由旧 SessionService 调度它。

**候选删除（须先关闭缺口①③ + 兼容期公告）**：

`kernel/session/service.py`：`send_message` / `cancel` / `cancel_session` / `resume_queue` / `wait_for_attempt` / `is_session_processing` / `mark_session_processing` / `list_active_attempts` / `get_available_actions` / `_process_session_queue` / `_run_attempt` / `_signal_attempt_done` + 相关队列字段（`_session_queues` / `_paused_sessions` / `_active_loops` / `_queue_consumers` / `_processing_sessions`）。

**不做**：不制造第二套执行队列；不删除 `SessionService` 类本身（其余职责仍在）。

## 6. 兼容期与破坏性变更策略（需维护者确认）

1. **兼容承诺期**：`send_message` 已在裁决 1 定为兼容 API → 保留至兼容期结束，期内行为不变（含 `max_iterations=50` / `allow_shell_tools=False` 默认与 policy 注入能力）。
2. **删除前置条件**（全部满足才进入删除批次）：
   - 缺口①按裁决落地（补超时 或 明确退役）；
   - 缺口③按裁决落地（显式失败 或 明确保留并记录）；
   - 兼容期公告已发（仓库 + CHANGELOG + 迁移说明：外部消费者如何改用 Gateway 入口）；
   - 本审查的 18 条绝对值断言全绿（删除前后各跑一次全量 + batch_gate）。
3. **删除批次形式**：R-014 方案先行 + R-015 四项 + R-016 出口网 + R-020 独立最后批次；**独立提交**，可单独回滚。
4. **迁移方式**：外部消费者改用 Gateway 入口（`api_chat_request` + `cancel_session` / `resume_session` / `wait_turn`）；旧方法在兼容期内标注 deprecated（docstring + 公告），期满后随删除批次移除。

## 7. R-015 四项现状判定

| 项 | 状态 | 证据 |
|---|---|---|
| ① Gateway 接管方向已定 | ✅ | §8.3 裁决 2/3 已接受并实施（594fe63a 等） |
| ② 主路径 + Goal lease 已接线 | ✅ | `/send_async`/`/cancel`/`/queue/resume`/`/attempts` 已切 Gateway（f01555d5）；Goal 用 lease（594fe63a） |
| ③ 完整出口网 | ❌ | 两层重导出仍在（§2）；缺口①③ 已实施（2026-11-19 校准）但**未闭合项仍在**：出口网复核未完结（§9：wait/attempt 真实核验、旧 attempt 消失/配置缺失/队列交接映射）、STEER 单次通知兼容语义未决（§9.3）、两个结构性 xfail |
| ④ 独立依赖审计 | ✅ | `scripts/arch/audit_legacy_chat_api.py`（只读、可复跑、不调用旧实现） |

→ **③④ 未闭合前不进入删除批次。**

## 8. 本轮未做事项

- 未删除任何旧实现（裁决 3 只批准审查）；
- 未修改旧队列行为（仅新增审计与断言测试）；
- 未裁决缺口①③（待维护者决定）；**2026-11-19 校准：两项已裁决并实施**（`d9945a5c`，见 §4 校准块）；删除仍未做
- 未提交删除相关改动。

## 9. 出口网复核（2026-11-18，进行中）

复核发现 §3 中两条用例的名称和实际断言不一致：
`test_wait_turn_returns_result_after_completion` 在驱动之前同步等待，
接受 `None`，并未证明完成后可获得结果；
`test_attempts_query_returns_live_running` 实际断言空列表，并未证明
活跃 attempt 可查询。须用真实 Gateway 驱动及正向前提补齐，不将旧测试
的名称当作证据。

| 旧执行出口 | 新路径当前证据 | 尚需确认 |
|---|---|---|
| 入队成功/满队 | `test_gateway_legacy_parity.py` 提交回执；`test_gateway_behavior.py` cap/合并 | 新旧返回形状与上限语义不同，迁移契约须明确 |
| 排队、暂停、恢复 | `test_gateway_behavior.py` FIFO 和取消后恢复 | 仓库外 `resume_queue` 调用方仍受兼容期保护 |
| 执行成功/模型或工具失败 | `test_gateway_behavior.py`、`test_loop_exit_net.py`、`test_r2_consumer.py` | 必须逐出口核对持久化、事件、fence/lease，不以总测试数代替 |
| 构造引擎及执行前准备失败 | 真实 Gateway 回归覆盖构造失败和上下文准备失败 | 已验证失败事件、assignment、fence/lease 收尾及后续 turn |
| 可协作模型超时 | `test_gateway_timeout_and_strict_history.py` 双引擎与 Gateway 收尾 | 非协作同步工具和 sync-only 客户端仍有两个 xfail |
| 用户取消 | `test_gateway_behavior.py` 活跃 turn 取消、暂停及恢复 | 被阻塞同步工具无法按时强制停止 |
| 历史读失败 | `test_gateway_timeout_and_strict_history.py` 严格失败且不提交 | UI 降级不等同模型上下文路径 |
| wait/尝试列表 reload | `test_r7_sink_and_wait.py`、`test_chat_attempts_query.py` | 用真实 Gateway 核验等待结果及 queued/running 身份 |
| 旧 attempt 消失、配置缺失、队列交接 | 旧 `_process_session_queue` / `_run_attempt` 的独立出口 | 新路径是否存在同类场景及收尾尚待逐项映射 |

该表不是完整出口网；旧 API 重导出、兼容期公告和独立删除审批仍需保留。
本轮不删除旧实现。

### 9.1 执行前准备失败的收尾方案

验收前先以真实 `Gateway.run_once` 注入首轮 engine factory 失败、同会话
第二轮正常的测试；要求首轮返回明确失败、`run.failed` 和
`run.settled(ok=False)` 各一次、assignment 失败、fence/lease 释放，
且第二轮可继续。以当前实现验证该断言是否从红变绿。

若复现，将从每轮参数装配、构造引擎、上下文与取消作用域准备到
`engine.run` 的代码一并纳入现有失败收尾分支；预先初始化 mailbox 和
scope，保持正常路径、取消注册以及异常结果形状不变。影响仅限
SessionController 在已认领 turn 的准备阶段失败时的终态；回滚可单独
撤回控制器与相关测试，不触碰旧队列或持久化职责。

### 9.2 活跃 attempt 身份映射

`api_chat_request("s1", ...)` 的业务 session key 为 `chat:s1`，EntryPoint
回执可能生成另一个 session id；`run.started` 的活跃 attempt 以回执 id
为键，而 `/attempts` 按业务 session id 查询。补真实 Gateway 的 queued
和运行中查询，分别使用业务 id 和回执 id，确认身份映射及非本会话
隔离。若业务 id 查不到运行项，在 `Gateway.attempt_state` 中优先经
`chat:<session_id>` 解析回执 id 后查询活跃记录，保留直接按回执 id
查询与原有排队分支；回滚单独撤回该映射和对应回归。

实测：修复后的真实 Gateway 回归覆盖 queued/running、业务 ID、回执 ID
及跨会话隔离。`wait_turn` 仍是按会话代际等待，不能作为指定 attempt
或指定排队 turn 的等待接口。

### 9.3 事件桥跨 turn 隔离

聊天桥注册在 Gateway 的广播 sink 上，需验证并发会话及同会话排队
turn 的事件是否只到各自桥、其他 turn 终态是否会错误注销桥。当前
loop 事件未统一携带 turn 身份，不能仅凭桥绑定了 `session_id` 就认定
隔离成立。真实 `_send_via_gateway` 两会话回归复现：第一个会话的答案
被写入第二个会话，两个桥均在首个 turn 终态注销，第二个答案丢失。

方案：每次聊天提交生成独立事件关联 ID，桥在提交前按此 ID 注册；该 ID
随 WorkRequest 的 payload 进入控制器，并附着在 run 生命周期事件及
该 turn 的模型/工具流式事件上。桥只处理匹配 ID 的事件，并且只因匹配
的终态注销。使用按执行上下文隔离的装配期信息构造事件 sink，
不能用控制器共享可变字段串联不同线程。不得将未标识的事件广播到
已绑定 turn 的桥；自定义引擎发流式事件时须保留关联 ID。

验收：真实 Gateway 的不同会话、同会话连续 turn 和流式事件分别断言
答案、终态与事件只到对应桥；正常及失败出口均收尾。无关联 ID 的既有
直接桥构造测试保持其原有行为；此兼容分支不用于聊天提交入口。影响
入口 adapter、SessionController 的事件 sink 和聊天桥，回滚此批即可
恢复原路由，不删除旧队列。

同步出口另有实际复现：无常驻消费者时，`run_once()` 可一次清空多个
会话，取 `runs[-1]` 会将其他会话答案返回并写入当前会话；常驻消费者
的按会话代际等待也无法区分同一会话中排队的多个 turn。同步聊天改为
在提交前注册关联 ID 对应的终态观察器，等到该 turn 的 `run.settled`，
从其自己的 `run.answer` / 失败事件读取结果；拒绝、超时及异常均注销
观察器，不缓存全局历史结果。没有匹配终态时显式失败，不采用其他 turn
的结果。现有 `Gateway.wait_turn` 对其他调用者的公共契约不变。验收包含
跨会话同时排队、同会话前序 turn、先完成再等待、失败和超时。

默认 `/send` 使用 STEER：活跃 turn 可以在安全点吸收请求而不创建新
turn。关联方案不能把该请求永久留在独立桥上：`steer.applied` 须携带
被吸收请求的关联 ID 和正在执行的 run 身份，只有实际被吸收的桥改为
监听该 run；未吸收而提升为独立 turn 的请求继续使用自身关联 ID。
先以真实 AgentLoop 安全点测试此分支，避免把 STEER 隐式改成 FOLLOWUP。

实测：真实 Gateway 覆盖同/跨会话、无关联事件、Attempt 归属、模型和
AgentLoop 工具流、失败及桥注销；同步路径覆盖先前排队、完成先于等待、
失败、超时；STEER 覆盖自定义引擎和真实 AgentLoop 安全点、外部启动
的 run 与提升为独立 turn。当前仍不代表完整出口网已闭合。
STEER 被吸收时，两个同会话桥仍沿用既有行为分别通知同一 run；是否
改为单次推送和落库属于另一个兼容语义决策，本轮不将其视为已优化。

队列 cap 摘要还有一个关联出口需覆盖：`_coalesce_if_full` 保留第二个
请求并移除第一个；被移除请求不会产生 run，故不能等其他 run 的终态，
也不能让其桥永久挂起。方案是在既有 `work.summarized` 事件附带被移除
请求的关联与 Attempt 身份，仅对应桥将它显式标记为失败、通知并注销；
同步等待也应返回明确失败。队列摘要/顺序本身不变。验收要求真实满队、
逐请求 Attempt/事件归属、保留项继续执行及桥全部注销；回滚单独撤回
该事件元数据和桥处理。

实测：cap=2 的真实 Gateway 中，首个被摘要请求的 Attempt 标为失败、
收到对应错误并注销桥，其余两条继续执行；常驻消费者下同步等待被
摘要的请求返回 `request_summarized`，不落入其他请求答案。

### 9.4 STEER 单次通知：落库收归进程单例观察器（2026-11-19，已批准 A1+B2）

§9.3 实测遗留的兼容语义决策：STEER 被吸收时，两个同会话桥分别通知
同一 run——同一 `run.answer` 被落库两次（A 的 receipt 桥 + B 的跟随桥
各 `event_bus.emit("assistant_message")` 一次），transcript 出现重复
assistant 行。维护者已裁决：**A1**（持久化恰好一次；每个等待方连接
各自保留通知/流式转发——多等待方各自得到结果）+ **B2**（落库收归
controller 侧单点，不依赖任何 HTTP 桥存活）。

**落地形态**（B2 的实现取「gateway 广播出口上的进程单例终态观察器」，
避免 kernel gateway→session 的新边与 SessionService 双实例陷阱）：

- `run.answer` 事件补带 `business_session_id`（controller 一行；事件此前
  只有 run_meta 里的 EntryPoint 回执 id——UUID，不能用作持久化归属；
  观察器按剥掉 chat:/automation: kind 前缀后的物理 id 归属）；
- chat.py 新增 `_install_answer_persister(gw)`：按 gateway 实例一次性
  注册（属性旗标防重），观察 `run.answer` → 按 run_id 去重 → 解析
  session service → `event_bus.emit("assistant_message")` 恰一次；
- **三个 HTTP 落库点全部停用**：receipt 桥 persist 分支、sync 分支的
  assistant emit、STEER 跟随桥（同 receipt 分支）。落库失败 → 单例
  观察器记 warning（不阻断事件流），不影响各等待方 SSE；
- sync 路径的用户 `message_received` 前移到 submit 后（对齐 receipt
  路径的提交即落库语义），保证事件序 user→assistant 不反转；
  **既有「失败不落任何行」sync 不变量随之修订**：失败/超时后用户消息
  在档（提交即事实，transcript 如实记录"问过但失败"），assistant/答案
  不落库——与 receipt 路径行为对齐；
- 桥的 `_update_attempt`（含跟随桥把 B 的 attempt 标 completed）与
  各连接的 agent_done/error 通知**保持不变**（A1）。

**验收矩阵**（对照路线步骤 2 的四个场景）：

| 场景 | 验收 |
|---|---|
| STEER 被吸收（B 注入 A 的 run） | `assistant_message` 恰 emit 一次（run_id 去重）；A/B 两连接各收到 agent_done（既有 expected_done==2 保持）；B 的 attempt 标 completed 带答案摘要 |
| 原桥超时（B 同步等待 504 后 run 仍完成） | 答案仍恰一次落库（观察器不依赖桥存活）；B 的连接不再收到通知（已注销） |
| 外部启动 run（无 HTTP 桥，消费者驱动） | 提交后无人挂桥 → 观察器仍恰一次落库 |
| 多个 STEER（B、C 同注入 A） | 一次 run.answer → 一次落库；B/C 各自通知 |
| 失败 / 取消 / fenced | 无 `run.answer` → 观察器零落库（既有 502/no-persist 断言保持） |
| 兼容面 | 无 HTTP 桥的直接桥构造测试保持原行为（观察器只由 `_send_via_gateway` 安装）；`Gateway.wait_turn` 公共契约不变 |

**回滚**：单独撤回本批（controller 一行 + chat.py 观察器与两处 emit
删除）即恢复三落库点原状；不触碰旧队列与持久化职责归属。
**明确不做**：不把「桥不再落库」扩大为「桥退役」（批 3a 范畴）；
不改 `work.summarized`/STEER 提升/溢出语义（§9.3 已闭合项）。

## 10. 现状校准（2026-11-19 · 维护者路线对齐）

维护者已定七步路线（步骤 1 即本节校准）。三个维度的**当前事实**：

| 维度 | 已闭合 ✅ | **尚未闭合 ❌** |
|---|---|---|
| **出口网**（R-016） | 构造/准备失败收尾、事件桥跨 turn 隔离（关联 ID）、cap 摘要显式失败、活跃 attempt 身份映射、队列/暂停/恢复、历史读失败、取消/暂停恢复 | ① wait/attempt reload 用真实 Gateway 核验（`wait_turn` 是会话代际语义 ≠ 指定 attempt 等待）；② 旧 attempt 消失、配置缺失、队列交接的逐项映射；③ **STEER 被吸收时两个同会话桥分别通知同一 run**——单次 SSE/持久化 + 多等待方各自得到结果的兼容语义未决（§9.3，路线步骤 2） |
| **超时**（裁决 3 缺口①） | 迭代边界双引擎检查点、原生异步 LLM 协作取消、工具入口/重试/批次返回后检查、**审批跨截止执行前检查**（§9.6，`f2f2617b`） | ① 同步工具强中断（严格 xfail）；② sync-only 客户端线程（严格 xfail）；③ 进程隔离原型未做（路线步骤 3，需验证 Windows 及其他平台）；④ 超时能力契约未定（路线步骤 4：推荐对不能证明安全停止的能力**限制或拒绝**；远端已接受操作须服务端确认取消 + 禁止截止后提交，幂等不能代替取消） |
| **删除前置**（R-015 ③ + R-016 + R-020） | ①②④ 满足；缺口①③ 已实施 | ③ 完整出口网（上两行全部 ❌ 项）；兼容期公告未写；外部迁移接口与期限未定；**维护者未批准删除** → **不删除旧队列，不宣称超时完全等价** |

STEER 单次通知语义（路线步骤 2）在实施前需维护者确认契约——确认后按
R-014 出半页方案（含验收与回滚）再独立提交。
