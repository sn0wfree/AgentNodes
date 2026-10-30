# R2 设计：常驻消费者 + steer 真注入 + 优先级老化（2026-10-30）

> 计划依据：`roadmap-next-20261030.md` R2；前置：R1 fence 挂根（`823c237e`）。
> 三道验收门沿用 `stage-c1-acceptance-20261028.md`。
> **本文档先评审后实施**——E1 是对外契约变化，需拍板。

---

## 0. 勘查事实（本设计的依据）

| # | 事实 | 证据 |
|---|---|---|
| F1 | chat 生产在请求内同步驱动：submit 后 `run_in_executor(run_once)`，HTTP 等整个 turn | `chat.py:737` |
| F2 | WebUI 的 `send` 响应类型是 `unknown`——**前端不依赖 body**；答案靠 SSE `text_delta` 累积渲染 | `webui/src/api/client.ts:593`、`stores/chat.ts`（part_text_accum_delta） |
| F3 | **`/chat/send_async` 已存在**， receipt 型契约（`{message_id, ...}`）有先例 | `client.ts:561-590` |
| F4 | CLI / TUI / MCP 均不读同步 `reply`（TUI 走流式桥） | 全库 grep 仅注释一处 |
| F5 | automation 走 StudyScheduler 自己的 executor，**不经 Gateway** | `schedule.py:202`（`executor.run_once_async`） |
| F6 | steer 现状 = 一律降级 followup（`steer.downgraded` 桩），语义已定稿（6/7 业界默认 steer） | `mailbox.py:107-110`、`queue-mode-research-20261024.md` |
| F7 | mailbox 有优先级插队但无老化（"urgent 插队成功"测试存在，饿死可能） | `test_gateway_behavior.py:193-205` |

---

## 1. E1 决策：chat HTTP 契约（R2.1 的分水岭）

### 选项 A：submit 即回 receipt，答案只走 SSE（推荐）

```
POST /chat/send
  ← 200 {accepted: true, message_id, session_id, queue_mode}   # 立即
答案：SSE text_delta（流式）→ assistant_message（落库全文）
```

- WebUI **已兼容**（F2/F3）：`send` 响应类型本来就是 unknown，渲染走 SSE；
  甚至可切到 `send_async` 同款形状。
- CLI/TUI/MCP 不受影响（F4）。
- 这是常驻消费者的前提：请求不阻塞，turn 由消费者线程跑。
- steer（R2.2）只有在此契约下才有意义——F1 的同步模型里每个请求自带
  run，没有「正在跑的 turn」可注入。
- 代价：HTTP 响应不再携带 `reply`——**对外契约变化**，需要拍板。

### 选项 B：保留同步 reply

常驻消费者只服务后台；chat 仍请求内驱动。R2 缩水为「R2.3 老化 +
automation 预留」，steer 真注入无限期推迟，与架构图 `api_chat=steer`
的定稿语义持续背离。

---

## 2. R2.1 常驻消费者（选项 A 之下）

### 2.1 消费者形态

```python
Gateway.start_consumer(kinds={WorkKind.CHAT}, poll_interval=0.2)   # daemon 线程
Gateway.stop_consumer(grace=5.0)                                   # 收尾等待
```

- 循环体 = 现有 `run_once(kinds)`，零新执行语义；
- 唤醒 = `work.enqueued` 时 set threading.Event（EntryPoint sink 钩子），
  加 200ms 兜底轮询；
- **只领 CHAT**（F5：automation 归 StudyScheduler，R3.2 才收编——本刀
  不碰调度时序，无双重驱动）；
- 由 API 进程 lifespan 启动；`get_gateway()` 不自动启动（测试零影响）。

### 2.2 答案进事件流：新事件 `run.answer`

现状答案从 `RunResult.answer`（内存）拿（chat.py:742/767）。消费者模型
下请求线程看不到 RunResult，答案必须走事件流（「一切事实经事件流」）：

```
validate(token) ok
  → emit run.answer {run_id, content: answer}    # 新，fenced 不发
  → release
  → emit run.settled {ok:True, answer_chars}
```

- bridge（`_install_chat_event_bridge`）监听 `run.answer` → emit
  `assistant_message`（落库全文，与现契约同形）；
- 落库时序从「请求线程 run 后」迁到「消费者线程 settle 后」——**消息
  表写入方仍是事件投影，单一写者不破坏**；
- 事件体积代价：全文比 delta 多存一份——与现状落库行为相同（现在也
  emit 全文），净变化只是多一条事件。

### 2.3 `/chat/send` 新路径时序（选项 A）

```
① 读历史（不变：先读历史再 emit，防当前轮重复注入）
② emit message_received（用户消息——从「run 后」提前到「submit 前」）
③ receipt = entry.submit(api_chat_request(...))    # 拒绝 → 400
④ 立即返回 {accepted, message_id, session_id, queue_mode}
错误呈现：run.failed / run.fenced 由 bridge 推 error + agent_done{status:error}
```

- `504 no run executed` 语义消失（submit 成功即受理）；
- legacy 分支（`new_entry_enabled()` else）**不动**——它随 P5-1c 一起死。

### 2.4 内部切分（每刀独立过三道门）

| 刀 | 内容 |
|---|---|
| R2.1a | `run.answer` 事件 + bridge 落库迁移（同步路径仍在——RunResult 与事件并存一个月不删） |
| R2.1b | `start_consumer/stop_consumer` + lifespan 启动 |
| R2.1c | `/chat/send` 切 receipt 契约（E1=A 时）+ 移除请求内 run_once |

---

## 3. R2.2 steer 真注入

### 3.1 队列语义（mailbox）

| 场景 | 行为 | 事件 |
|---|---|---|
| 有活跃 run（`fence.active_run(sid) 非空`） | 入 per-session steer 队（cap 5） | `steer.queued` |
| 无活跃 run | 直接入主队作正常 turn | `steer.became_turn` |
| steer 队满 | 溢出项转 followup 入主队 | `steer.overflow_to_queue` |
| 安全点注入 | 合批 drain，`\n\n` join 成一条 user 消息 append（只追加） | `steer.applied {count}` |
| 删除 | `steer.downgraded` 桩及断言（`test_gateway_behavior.py:124` 随之改写——这是「待 A4」临时桩的兑现，不是回归） | — |

### 3.2 注入通道（引擎侧）

- controller 把 `state["steer_inbox"] = <drainable>` 放进 engine state；
- react 节点在**循环边界安全点**（每次 LLM 调用前）`drain()`，内容作
  user 消息追加；
- steer **不新开 run、不碰 fence**——它是当前 turn 的一部分；
- llm 单发节点无循环：steer 到达时无安全点 → 转 followup 入主队
  （事件同 `steer.overflow_to_queue`）。

## 4. R2.3 优先级老化

- `Mailbox.poll_due` 排序时：低优先项等待 ≥ `aging_s`（默认 15s）→
  有效优先级升至压制者同级（FIFO 按 seq 保先后），一次性事件
  `work.aged {request_id, waited_s}`（不静默，INV-6）；
- 参数进 ConfigHub：`MailboxConfig.aging_s`；
- 「领先有界」的可观测验收：urgent 连发也不能让普通消息等待超过
  aging_s + 一个 turn 时长。

---

## 5. 测试计划（`tests/test_r2_consumer.py` 等）

| # | 用例 | 断言 |
|---|---|---|
| 1 | 消费者自驱 | submit 后不等 run_once，turn 在消费者线程完成（事件可达、RunResult 经回调收集） |
| 2 | 优雅停止 | stop_consumer(grace) 后无孤儿线程、in-flight turn 收尾 |
| 3 | send 契约 | POST /chat/send 立即回 receipt；answer 经 run.answer→assistant_message 落库 |
| 4 | run.answer 只在通过 fence 时发出 | 超越场景无 run.answer（承接 R1 测试骨架） |
| 5 | steer 注入 | 活跃 turn 中提交 steer → 出现在下一轮 LLM 调用的 messages 尾部；无 run.fenced |
| 6 | steer 无活跃 run | became_turn：作为正常 turn 执行 |
| 7 | steer 溢出 | cap 5 满后转主队 + 事件 |
| 8 | 老化 | 低优先等待超阈值后被先服务 + work.aged 事件（假时钟注入） |
| 9 | 等价 | WebUI 契约：text_delta 流 + agent_done 序列不变（bridge 行为回归） |

既有测试影响：`test_gateway_behavior.py:124`（steer.downgraded 断言）
按新契约改写；`test_tui_streaming` 全量照跑（F4 的证据）。

## 6. 风险与不做

- **事件流回放兼容**：新增事件类型对旧消费方是未知 kind——bridge 白名单
  直通模式已天然忽略未知事件；`_event_generator_v2` 契约不变；
- 不做：interrupt（R3/R5）、automation 消费（R3.2）、mailbox 加锁
  （单消费者模型下无并发消费者，锁是 R3.2 的事）；
- 若 E1=B：R2.1 全部不做，仅做 R2.3 + steer 桩保留（R2 缩水为 0.5d）。

## 7. 验收对照

| 门 | 判定方式 |
|---|---|
| 行为等价 | WebUI 可见序列（text_delta/tool_*/agent_done + 落库）逐字节对比；steer.downgraded 改写单独记录 |
| 测试不减 | c1_inventory verify：0 removed |
| 失败集不扩大 | c1_gate：失败集 ⊆ 基线 |
