# Chat send_turn 用例下沉实施方案

> **性质**：实施设计（R-014）· **状态**：方案已审阅通过，待开工实施  
> **日期**：2026-11-19  
> **所属路线**：三前端一核心 · 批 3a  
> **决策来源**：[三前端一核心设计 §9](./l1-three-frontends-one-core-design-20261119.md)

## 审阅记录（2026-11-19）

方案审阅通过。审阅期核对的关键事实（行号为本方案基线快照）：

- **Attempt 半提交风险属实**：`chat.py` send_async `:316` create_attempt → `:320`
  发 `attempt.created` → `:328` 才进入 `_send_via_gateway`（严格历史读取在
  此之后）→ `:342` finally delete_attempt。历史失败时 `:320` 的事件已发出
  且无法撤销。§1/§5 要求「先验证历史再创建 Attempt/发事件」为**3a.3 必做
  修复项**，不得作为可选项。
- **单例观察器事实核对**：`_install_answer_persister`（`:457-499`）按 gateway
  旗标幂等安装、`seen_run_ids` 去重；在 `_send_via_gateway` `:587` 安装。
  §2.1「维护观察器的幂等安装与业务 session 归属」要求迁移后保持。
- **边界一致性**：§2.3 分层禁区与四层门禁（`check_dangling_imports --layers`
  上行违规 0）一致；§4 子批切分与「每次独立提交、独立回滚」纪律一致。
- **M13 隔离**与 §0 裁决一致（`_active_runners` 不混入 send-turn）。
- **契约网**§6 列举与本仓实际测试文件一致（`test_chat_control_contract.py`
  41 用例、`test_gateway_timeout_and_strict_history.py` 33 测试定义，含
  parametrize 展开后更多用例）。
- **无异议项**：目标 API、行为矩阵、回滚、明确不做均无修改意见。

审阅未发现方案缺陷；实施按 §4 子批顺序推进（3a.0 → 3a.1 → 3a.2 →
3a.3 → 3a.4；3a-M13 独立）。

## 0. 裁决

| 项 | 裁决 |
|---|---|
| 3b 领域归属 | RSI 用例归新建 `apps/evolution`，不并入 `apps/goal` |
| M2 事件词表 | 产品/前端事件契约归 `apps/chat`；kernel 只保留引擎事件 |
| 会话视图命名 | 先归 `apps/chat`，不新建只有单一消费者群的 `apps/session_views` |
| 3a 开工 | 立即进入本方案和契约核对；**本方案不授权直接改代码** |

本方案只覆盖 3a 的 chat send-turn 下沉。legacy `SessionService.send_message`
及其队列、导出兼容层和漂移测试不改。Goal Workflow runner 注册表（M13）
与 chat turn 没有直接执行依赖，作为独立子批次另行核实 owner 和 R-014
方案；不得为了凑进 3a 把 API/Goal 对象直接塞入 kernel。

## 1. 现状证据

- `interfaces/api/routers/chat.py::_send_via_gateway` 承担严格历史读取、
  WorkRequest 准备、Gateway submit、receipt/sync/wait 分支及 HTTP 错误
  形状；`_install_chat_event_bridge` 承担事件关联过滤、Attempt 生命周期、
  SSE 翻译、终态通知和注销。
- `/send_async` 在 router 创建 Attempt、提交后查询实时状态并发出
  `attempt.created`、`message_received`、`queue_state`；它还处理 `/compact`、
  `/clear`、`/help` 这些 API 命令。`/send` 做 session 授权和参数适配，
  默认 receipt，`?wait=true` 保留等待结果。
- **需在实施前修正的顺序风险**：`/send_async` 当前先创建 Attempt 并发出
  `attempt.created`，之后 `_send_via_gateway` 才读取严格历史；若历史读取
  失败，finally 会删除 Attempt 行，但先前的 `attempt.created` 事件不会
  撤销。因此“历史失败不产生任何 Attempt 事件”目前对 `/send_async`
  不成立。下沉时必须先准备并验证历史，再创建 Attempt/发事件，或采用
  等价的原子方案；不得保留这条半提交路径。
- `365f15b5` 已把 `run.answer` 的 assistant 持久化收敛到每个 Gateway
  实例一个观察器。桥只负责各等待方的 SSE/终态通知。观察器必须继续覆盖
  外部启动的 run 和请求桥提前结束的场景，按 `run_id` 仅持久化一次。
- `tests/test_chat_control_contract.py` 覆盖真实 Gateway 的跨会话/turn
  隔离、sync/wait、STEER 合流/提升、失败、摘要和 Attempt 状态。该测试
  文件及 `test_gateway_timeout_and_strict_history.py` 中严格历史读取测试
  是迁移期间不可删除的契约网。
- `apps/chat` 已有 `policy.py`、`attempts_query.py`、`export.py`。本次复用
  显式依赖和纯用例的既有模式，不引入通用 DI 框架。
- 最近的超时能力 4a/4b 已落地；send-turn 迁移只透传现有超时事件，不改变
  执行截止、取消、lease 或四级能力语义。

开工时重新核对上述符号、测试数量和调用点；此处路径/行号为方案基线，
不是免除实施前复查的快照。

## 2. 目标边界

### 2.1 Apps 用例负责

- 读聊天历史；严格读失败时不提交、不产生 run/Attempt/消息事件。
- 将一次发送编排为 Gateway 提交、Attempt 创建/状态更新、用户消息记录、
  指定 turn 的终态观察和结果。
- 维持 `receipt_only`、consumer receipt、同步驱动、`wait=true`、
  queue-full、拒绝、超时、失败、取消、fence、STEER 吸收/提升、
  `work.summarized` 等既有语义。
- 维护 `run.answer` 单例持久化观察器的幂等安装与业务 session 归属；
  Gateway 仍是执行控制面，不把运行队列移入 apps。
- 定义 `apps/chat` 拥有的产品事件词表和规范消息事件数据。

### 2.2 Interfaces 适配器负责

- HTTP 鉴权、session ownership、请求 schema 和查询参数解析。
- 把 API 字段转换成 apps 用例输入，并把应用层结果/错误映射成现有 HTTP
  status、response body 和 SSE 推送。
- API SSE 缓冲、EventStore 接口等传输适配不进入 `apps/chat`。
- TUI 保留自身 widget/render 行为；只复用语义相同的 apps 事件常量或转换
  规则，不强迫 TUI 采用 SSE 专属字段和传输生命周期。

### 2.3 分层禁区

- `apps/chat` 不导入 `interfaces`、FastAPI、`HTTPException`、router schema
  或 SSE 缓冲模块；kernel 不导入 `apps/chat`。
- 不把 `Gateway` 的准入/队列/取消机制复制到 apps；apps 只编排注入的
  Gateway/端口。
- 不因四种 `message_received` 形状不同而盲目填充同一组字段。先区分它们
  的业务语义，规范事件须能表达可选 Attempt、assistant 占位、队列状态等
  差异；HTTP wire payload 的兼容由接口适配器守护。

## 3. 目标 API（设计约束）

新增 `apps/chat/send_turn.py`，以一个明确的 `send_turn(...)` 用例入口覆盖
API chat 的发送编排。输入、结果和失败使用最小的类型化结构，至少表达：

- 输入：业务 `session_id`、文本、Gateway 执行选项、queue mode/cap、消息和
  Attempt 标识、等待/receipt 模式。
- 依赖：Gateway、SessionService/存储与事件总线、事件发布 callback、
  wait timeout 配置。依赖由组合根/接口适配器提供，避免模块级服务单例。
- 结果：receipt 或已完成的 turn；保留既有 assistant/user/attempt/run ID
  区分，不能把 `attempt_id` 与 `run_id` 合并。
- 错误：以应用层错误表达历史不可用、队列满/拒绝、超时、执行失败、
  被摘要等情况；HTTP status 和 `detail` 只在 L1 映射。

名字和字段可在实施前根据类型系统/现有约定微调，但不能用无类型大 dict
隐藏不同终态，也不增加不被消费者使用的可配置层。

事件词表分两面：`apps/chat` 声明稳定的产品语义；API adapter 将其编码为
兼容 SSE/EventStore payload，TUI adapter 只映射双方确实共有的语义。
引擎事件仍属于 kernel，不因建立产品词表而重命名。

## 4. 分批实施

每个子批一个独立 commit；先用真实 Gateway 路径跑通小探针，再迁移完整编排。
各批只触碰列明文件和紧邻测试；不与 3b/3c、timeout 4c、旧队列删除混提。

| 子批 | 内容 | 验收重点 |
|---|---|---|
| **3a.0 基线与引用图** | 重跑受影响测试；列出 `_send_via_gateway`、事件桥、Attempt 和 `message_received` 的消费者、monkeypatch 字符串锚与动态引用；冻结 API 响应/事件对照表 | 无猜测的迁移清单；确认 `send_message` 兼容测试独立保留 |
| **3a.1 探针** | 在 `apps/chat` 抽取一个最小、完整且真实使用的 chat 消息记录用例/事件构造器；由现有 API 发送路径调用。接入真实 `SessionService.event_bus` 和 fake publisher，暂不改变响应及事件顺序 | 对四种现存 `message_received` 场景逐字段断言；失败仍保持既有 best-effort 语义；层方向门禁通过 |
| **3a.2 产品事件词表** | 建立 `apps/chat` 事件定义与 API wire 转换；迁移 SSE bridge 的名称映射和可共享 payload 构造，不移动 HTTP/SSE 实现 | API wire 字段/事件名绝对值不变；TUI 只接入可共享语义；无 kernel→apps 或 apps→interfaces 导入 |
| **3a.3 send_turn 用例** | 将严格历史读取、Gateway submit、每 turn 关联、桥订阅/注销、等待/receipt、Attempt 状态和单例答案持久化装配收敛到 apps 用例；router 改为鉴权、参数翻译、结果/错误映射。`/send_async` 的 Attempt 及 queue-state 编排一并接入，不迁移其特殊命令处理 | 所有 HTTP 契约保持不变；单例落库、STEER 多等待方通知、桥生命周期与精确 turn 等待均通过真实 Gateway 验收 |
| **3a.4 清理与审计** | 生产调用切换且测试通过后，搜索并处理旧 helper、重复事件构造和测试 monkeypatch；只删除经 R-015 三查确认无消费者的内容，删除作为最后独立工序 | R-016 出口网逐项关闭；删除前后各跑全量 pytest 和 batch_gate，按 R-020 独立提交 |
| **3a-M13 独立子批** | 单独审查 workflow router 的 `_active_runners` 与 kernel `WorkflowRunRegistry` 的职责、生命周期、线程安全和 API owner 过滤；确认 registry 的 owner 后另出方案 | 不与 send-turn 提交耦合；不把 HTTP 授权或 Goal 产品策略放入 kernel |

探针批的代码范围只限消息记录/事件构造的单一用例，不另造第二条发送队列，
也不复制整个 `_send_via_gateway`。若实测该用例不能保持事件序或失败语义，
先停止扩展并修订方案，不以临时双实现绕过差异。

## 5. 必须保持的行为矩阵

| 场景 | 不变量 |
|---|---|
| 历史读取失败 | 显式失败；submit、run、Attempt 行、`attempt.created` 和本轮消息事件均不发生。尤其验证 `/send_async` 不留下“先发 attempt.created、后删除 Attempt 行”的半提交状态 |
| API `/send` receipt | consumer 已运行且未要求等待时立即回 receipt；答案仍由 run 事件落库并发 SSE |
| API `/send?wait=true` | 等待指定 turn；不误取其他会话或同会话前序 turn 结果 |
| 无 consumer 同步路径 | 维持现有按 CHAT kind 驱动的路径和完成结果 |
| `/send_async` | FOLLOWUP、cap=10、429 body、Attempt ID / Run ID 区分、创建/失败清理及 reload 状态不变 |
| STEER absorbed/promoted | absorbed 时多个请求桥按现有契约通知；同一 run 的 assistant 只落库一次；promoted 请求有自己的终态 |
| 满队摘要 | 被摘要的请求显式失败并关闭其桥；保留请求继续执行 |
| run 失败/取消/fence/执行前异常 | Attempt、错误/终态事件、响应和 lease 收尾保持现有契约 |
| 用户消息持久化失败 | 保持当前 best-effort 行为并记录日志；不借迁移改变事件存储的错误策略 |
| 超时 | 只透传已有四级声明/实际状态；不增加强杀承诺、不提前释放仍运行任务所持 lease |
| legacy `send_message` | 不接新生产调用、不删、不改默认值与既有 drift/parity 测试 |

## 6. 测试与门禁

每批先跑针对性用例，再跑本仓规定的全量和 `scripts/batch_gate.cmd`。
至少保留并复核：

- `tests/test_chat_control_contract.py`：跨 session/turn、consumer、sync/wait、
  STEER absorbed/promoted、summarized、Attempt 生命周期。
- `tests/test_gateway_timeout_and_strict_history.py`：历史严格失败、
  Gateway 超时状态与收尾。
- `tests/test_chat_policy.py`、`tests/test_chat_policy_legacy_wiring.py`：
  产品策略和 legacy 路径默认值漂移守护。
- `tests/test_gateway_legacy_parity.py`：旧队列绝对值/迁移兼容断言。
- API `/send`、`/send_async` 的响应体、状态码、SSE/EventStore payload
  契约测试；针对 apps 用例增加真实 Gateway 的直接调用测试。

必须断言的资源不变量：一个 Gateway 仅安装一个答案持久化观察器；每个 turn
桥只收自身关联事件并在终态注销；无残留 sink；一次 run 的 assistant
持久化恰一次；失败的 history 路径不留下半提交对象。

提交前还须运行层方向/导入门禁、kernel SCC 门禁、框架数字同步和
`git diff --check`。工作区存在其他会话文件时，按路径限定提交，不纳入本批。

## 7. 回滚

- 3a.1/3a.2 任一批失败：回滚该独立 commit，保留原 router 执行路径。
- 3a.3 失败：单独回退路由接线和 apps 用例；暂不执行 3a.4 删除，旧实现
  仍可作为回滚路径。
- 3a.4 仅在主体提交已全绿后执行；回滚删除提交即可恢复旧 helper，不应连带
  撤销新用例或 wire 适配。
- 全程不回滚 `365f15b5` 的单例答案持久化修复；不得恢复每桥重复落库。

## 8. 明确不做

- 不删除/改写 `SessionService.send_message`、旧队列、兼容重导出或漂移测试。
- 不统一 HTTP、SSE、TUI、MCP 协议；只抽共享用例及真实共有的事件语义。
- 不顺带做容器/五套服务定位机制的整体 DI 收敛（批 4/M12）。
- 不把 workflow runner registry 混入 chat send-turn；M13 独立审查。
- 不做同步工具隔离生产接线、超时语义变化、SessionService/AgentLoop 大拆分。
- 不批量删除三前端设计中的候选死代码；按 R-015/R-016/R-020 单项处理。

## 9. 3a.3 决策记录（grilling 两轮 · 2026-11-19）

架构摩擦审计（improve-codebase-architecture skill，报告在 OS 临时目录）
选出候选 1（send-turn 深模块）+ 候选 3（attempt.* 单源），并两轮 grilling
走完决策树。以下 8 项全部采纳推荐答案，shared understanding 达成。

### Round 1（interface / 错误 / 桥归属 / 过渡面）

| # | 决策 | 选定 |
|---|---|---|
| 1 | send_turn interface 形状 | **A**：`send_turn(input, deps)`——`SendTurnInput` dataclass + 依赖容器（gateway/store/emit/publish/timeout），interface 缩到两个参数 |
| 2 | 错误表达 | **A**：应用层异常类型（`HistoryUnavailable`/`QueueFull`/`RunFailed`/`WaitTimeout`/`RequestSummarized`），router 一处 except 映射到现有 status/detail |
| 3 | bridge 与答案落库器归属 | **A**：作为 send_turn 模块的**内部 seam**（同文件私有函数/类），router 不再接触 |
| 4 | 现有 monkeypatch 出口网 | **A**：3a.3 期间保留 router 可 patch 薄转发面，3a.4 逐项迁测试后删除（兑现 R-014 退出路径，非新永久垫片）|

### Round 2（Attempt / 单源 / 测试 / 提交）

| # | 决策 | 选定 |
|---|---|---|
| 5 | Attempt 创建与历史预检位置 | **A**：全部下沉进 send_turn——内部先验历史、再建 Attempt、再发事件、再 submit；router 从 `SendTurnResult` 拿 attempt_id。send_sync 不建 Attempt（用 run_id），由 send_turn 内部分支 |
| 6 | attempt.* 单源范围 | **A**：kernel `EventType` 补三个常量，apps `ChatEvent` 与 kernel session service 发射点都改引用——纯引用替换、wire 零变化；不改 `send_message` 逻辑/默认值/测试（§8 边界）|
| 7 | 测试存活与新增 | **A**：41 用例靠过渡面保持绿；只新增 2-3 个直测 send_turn 用例钉新行为（历史预检失败零 Attempt 事件、成功路径状态迁移、错误映射）；3a.4 再逐项迁 |
| 8 | 提交切分 | **A**：`3a.3a` 新模块（含内部 bridge/persister、异常类型、input/result、attempt.* 单源）纯新增零调用方切换 + 直测用例；`3a.3b` send_async/send_sync 切换 + Attempt 顺序修复 + router 过渡面；`3a.4` 清理（另行裁决）|

### 落地约束（由决策派生）

- send_turn 的内部 seam 不对外暴露：bridge/persister 不是 apps/chat 平级 module（候选 6「别让 apps 变成浅模块集合」）。
- interface 即测试面：新增直测用例穿过 `send_turn(input, deps)`，不戳内部同步原语。
- 错误类型与 wire 绝对值冻结表（3a.0 基线 §4）并行守护：异常类型只在 router 映射层转成现有 status/detail。
- 回滚粒度按决策 8：3a.3a 回退 = 恢复零新增；3a.3b 回退 = 恢复旧 router 路径；单例观察器不回滚。

实施按此决策记录执行；如实施中发现与新决策冲突的现实约束，先回到本文档修订决策再动代码。

## 10. 执行记录（2026-11-19 · 3a.0 ~ 3a.3b 全部完成）

### 10.1 子批与提交

| 子批 | 提交 | 内容 |
|---|---|---|
| 3a.0 基线 | `52cbee62` | 契约网 179 passed / 2 xfailed 冻结；消费者/monkeypatch 锚图（排除 docstring 误报）；API wire 冻结表；5 项风险登记 |
| 3a.1 探针 | `d33f7b0b` | `apps/chat/messages.py`（build_message_received + record_user_message + UserMessageRecord）；chat.py ②-c 接入；7 用例逐字段断言四种形状 |
| 3a.2 事件词表 | `dde0fd8d` | `apps/chat/events.py`（ChatEvent 从 EventType 取值 + ENGINE_TO_PRODUCT + STREAM_PASSTHROUGH_KINDS + translate_engine_event）；bridge mapping/直通收归词表；6 用例 |
| 方案审阅 + 决策 | `fd1594e8` / `484092e8` | 审阅通过（核实 Attempt 半提交属实、单例观察器位置）；improve-codebase-architecture 审计报告 + 两轮 grilling 8 项决策 |
| 3a.3a 纯新增 | `8afb0640` | `apps/chat/send_turn.py` 深模块（SendTurnInput/Deps/Result + 9 应用异常 + 内部 bridge/persister/precheck）；attempt.* 单源（kernel EventType 补 3 常量，apps/kernel 发射点改引用）；4 直测用例。**零调用方切换** |
| 3a.3b 接线 | `8bd0561f` | `_send_via_gateway` 280 行 → ~60 行薄 wrapper；send_async 历史预检前置（半提交修复生效）；bridge persist 分支补齐（3a.3a 搬迁遗漏）；契约网过渡适配；wire 零变化 |

### 10.2 决策落地核对

| 决策（§9） | 落地位置 | 状态 |
|---|---|---|
| 1 两参数 interface | `send_turn.py:SendTurnInput/SendTurnDeps` | ✅ |
| 2 应用异常 + router 一处映射 | `send_turn.py:SendTurnError` 子类 + wrapper `error_map` | ✅ |
| 3 bridge/persister 内部 seam | `send_turn.py:_event_bridge/_install_answer_persister`（不导出） | ✅ |
| 4 过渡面 | `_send_via_gateway` 签名保留；3a.0 锚 17+16+17 全绿 | ✅ 待 3a.4 关闭 |
| 5 历史预检先于 Attempt | `send_turn.precheck_history()` 在 send_async:create_attempt 之前 | ✅ |
| 6 attempt.* 单源 | kernel EventType 三常量 + apps ChatEvent + 11 处引用替换 | ✅ |
| 7 41 用例保绿 + 新增直测 | `test_chat_control_contract.py` 41 passed；`test_chat_send_turn.py` 4 用例；`test_chat_messages.py` 7；`test_chat_events.py` 6 | ✅ |
| 8 提交切分 | 3a.3a 纯新增 / 3a.3b 接线，各自独立提交 | ✅ |

### 10.3 实施期发现与修正

1. **3a.3a 搬迁遗漏**：`_event_bridge` 漏搬 `if persist:` 整块（run.answer→agent_done completed / 失败→error+agent_done），3a.3b 接线时 23 个契约用例红——从 `git show 8afb0640^:...chat.py` 取回原文补齐，wire 零变化。**教训**：跨文件搬迁后必须在接线前对「被搬函数的完整分支集合」做静态核对，不能只靠新增直测（它们没覆盖 persist 分支）。
2. **wrapper 的 emit 延迟取值**：初版 `emit=service.event_bus.emit` 导致严格历史失败用例的 fake service（无 event_bus）在 deps 构造时就 AttributeError——改 lambda 延迟取，历史失败路径不触达 emit，与旧实现语义一致。
3. **源序测试迁移**：`test_w3_sse_seed.py::test_bridge_installed_before_submit` 原用 `inspect.getsource(chat_router._send_via_gateway)` 文本断言，接线后 router 源码不再含 bridge 调用——改为断言 `apps/send_turn.py` 的 `_event_bridge` 先于 `gw.entry.submit`，语义不变。
4. **两个 send_async 契约测试的 fake 补面**：precheck 新增后 fake store 缺 `get_messages` / service 缺 `_convert_messages_to_history`——补最小空实现，保持它们对提交部分的隔离意图（不弱化断言）。
5. **负载抖动**：全量 `-n 12` 偶见 goal_panel/timeout 预算类用例失败，`--lf` 复跑过；已由 `7b635161` 的时间加固覆盖大部分，剩余为既有并发抖动，非本批回归。

### 10.4 验证矩阵（每批固定）

- 契约网（15 文件）：**205 passed / 2 xfailed**（两 xfail = 同步工具强中断、sync-only 线程桥——既有结构边界）
- 核心 `test_chat_control_contract.py`：**41 passed**（wire/事件/Attempt 状态/STEER 全绿）
- 层方向门禁：L2>L1 / L3>L1 / L3>L2 / L4>L* 全 **0**
- 全量 `-n 12`：**7836 passed / 106 skipped / 2 xfailed / 0 failed**
- batch_gate：**12 步 ALL GREEN**
- 体量数字：**451 文件 / 94,622 行**（L1 从 22,600 降至 22,402 = wrapper 收敛净效果；L2 10,551 = send_turn + events + messages 深模块）

### 10.5 3a.4 清理审计（2026-11-19 · 裁决：本版本只标记，下版本删除）

维护者裁决：**批准 3a.4，但删除延后到下一版本；本版本只做 DEPRECATED 标记**
（与批 2 死代码同款处理）。

**本版本已执行**（零行为改动）：

- **R-015 三查**：过渡面符号按「定义处 + 全仓引用 + 框架注册面」复核。
  关键发现：`_get_session_service` / `_get_gateway` / `_build_llm_config`
  有真实生产跨路由消费者（goal / slash_common / workflow），**不是过渡面**，
  不标 DEPRECATED；`_push_sse` / `_chat_wait_timeout` 是 wrapper 的 deps
  来源与 patch 锚，不是纯过渡面，不标。
- **DEPRECATED 标记**（3 处，均在 docstring 追加、零行为改动）：
  `chat.py::_send_via_gateway`（wrapper 过渡面，生产调用已全迁 send_turn）、
  `_install_chat_event_bridge`（桥副本已下沉为 send_turn 内部 seam）、
  `_install_answer_persister`（观察器副本已下沉；**双实现并存有漂移风险**，
  下版本删除以消除双源）。三处均注明「下一版本删除、本版本保留兼容」。
- **R-016 出口网覆盖核对**（验证性、不改代码）：逐出口核对
  14 类场景（receipt/wait/429/历史失败/wait 超时/run 失败/summarized/
  取消/STEER/Attempt/单例落库/跨会话隔离/legacy 兼容）——12 类已覆盖；
  `entry_rejected` 400 与 legacy send_message 专项断言未直接覆盖，
  **列入下版本删除批的补测项**（届时随测试迁移一并按新 seam 补）。

**本版本明确不做**：不删任何代码、不迁 58 个 router patch 锚（41 契约用例
依赖真实 Gateway + threading，迁移需 fake adapter 重写，风险高——与删除
同批做才有收益；本版本迁移只会制造两套测试并存）。DEPRECATED 标记 +
测试锚保持 = 下版本删除的前置条件已就绪。

### 10.6 剩余工作（下版本 / 独立批）

- **3a.4 删除批（下版本，待再批准）**：关闭 router 过渡面、58 patch 锚逐项
  迁到 send_turn seam、删 3 个 DEPRECATED 旧实现（R-015 三查重核 +
  R-020 独立提交）、补 entry_rejected/legacy 专项断言、R-016 出口网逐项关闭。
- **3a-M13 独立子批**：workflow runner 注册表（`_active_runners`）职责/线程安全/owner 过滤另出方案，不与 send-turn 混提。
- legacy `send_message` / 旧队列 / 兼容重导出 / 漂移测试：全程未动（§8）。
