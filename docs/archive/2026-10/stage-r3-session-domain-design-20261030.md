# R3 设计：同 session 串行域（勘查后重定界）（2026-10-30）

> 计划依据：`roadmap-next-20261030.md` R3。**勘查推翻了 R3 的原始估计**——
> 三条线的「入口覆盖」大半已在阶段 3c/4 完成；本刀的真实范围收敛为一个
> 正确性要害。三道门沿用。

---

## 1. 勘查结论（原始 R3 vs 实际）

| 原 R3 项 | 勘查事实 | 结论 |
|---|---|---|
| R3.1 CLI/MCP 接适配器 | **CLI 已走 Gateway**：`ChatSession._run_via_gateway`（`QNODES_NEW_ENTRY` 默认开，`test_cli_gateway_path.py` 三测）；`cli_request` 适配器确实无调用方——TUI 用自己的直连路径，属**等价实现**而非缺口。MCP 无 chat 出口（纯工具协议，`server.py` 无 send/chat 方法）——`mcp_request` 无调用方是**合理的** | ✅ 已完成（图更正）；MCP 标注 N/A |
| R3.2 study 收编 | **已完成**（阶段 3c/4）：`StudyScheduler.submit` → `scheduler_request`(AUTOMATION) → `automation:{session}` mailbox → per-session `_mailbox_loop` 消费者 → `_run_one_study`（`scheduler.py:112-184, 726-765`）。并发已有 per-user + global semaphore | 剩缺口见 §2 |
| R3.3 容量组闸 | chat 消费者单线程（天然串行）＝chat 闸；study per-user/global semaphore＝study 闸（窄）；cron 隔离无 cron 系统 | ✅ 已有等价物（图更正）；无需新码 |

## 2. 真实缺口：同 session 跨 kind 并发（正确性 bug）

**现状**：同一物理 session 有两条独立队列——`chat:{sid}` 与
`automation:{sid}`，两个独立消费者（chat：Gateway 常驻线程；study：
scheduler 的 per-session asyncio task）。二者**互不感知**：

```
chat turn  (chat consumer thread)  ──┐
                                     ├── 同一物理 session 可同时执行
study round (scheduler task)       ──┘
```

后果（R1 fence 使其显性化）：chat turn 与 study round 同 session 并发时
**没有互斥**——R1 的 fence 只覆盖 controller 的 chat turn（study round
不经 controller、不 claim fence），所以现在连「后到 fence 掉先到」都
不会发生，是**裸并发**：两条执行线同时读写同一 session 的上下文/记忆/
todos。这正是架构图「session=串行域：turn 不分类型同一条队」没有兑现
的部分，也是 home_session_key 语义的实质。

## 3. 方案：fence 升级为跨 kind 的同 session 互斥

**原则（less-is-more）**：不新建锁、不合并队列——复用 R1 的
`SessionFence`（它已经是 per-session 的活跃 run 登记），把 study 纳入。

### 3.1 chat 侧：准入等待（不再超越）

`SessionController.run_pending` 逐 session 消费前检查
`self._fence.active_run(session_id)`：已被占（正在跑 study round 或
其他 run）→ **跳过该 session**（工单留在队里），消费者下轮 poll 再来。
单线程消费者 + 200ms 轮询 = 天然的等待队列，不加锁。

> fence 语义保持「claim 即超越」不变——但经过 3.1/3.2 后，正常路径
> 不再出现同 session 的竞争性 claim；超越语义保留给未来的 interrupt。

### 3.2 study 侧：执行占栏

`StudyScheduler._mailbox_loop` 派发 `_run_one_study` 前检查
`fence.active_run(session_id)`；非空 → 不取单（留队，watchdog/下次
轮询再来）。取单执行时 `fence.claim(session_id, run_id="study:{sid}")`
占栏，`finally: release`。效果：

- study round 跑着 → chat turn 在 mailbox 里等（chat 消费者跳过）；
- chat turn 跑着 → study 消费者不取单（round 等）；
- **同实体永不并行**由此对所有 kind 成立——这就是 home_session_key
  的运行时语义，无需新键（study.session_id 已是 home 锚）。

### 3.3 异步/线程边界

- fence 是 `threading.Lock` 实现（`fencing.py:50`），asyncio 事件循环
  里调用其同步方法安全（无 await 段、微秒级）；
- study 消费者等待 = 不取单 + asyncio sleep 重试（`_mailbox_loop` 已有
  轮询退出语义，改为带退避的等待循环）；
- 死锁不可能：两侧都是「取单前检查、取单后占栏」，无嵌套等待。

### 3.4 chat 长轮占用的公平性

chat turn 可能跑数分钟，study round 在此期间持续等待——这正是
home_session 语义（同 session 的事按序来），且 watchdog 仍在。若未来
需要「study 优先级高于聊天插播」，那是容量组闸的 priority 参数（记录
进 roadmap，不在本刀）。

## 4. 明确不做（本刀）

- 合并两条 mailbox 为一条物理队列（收益为零——两个消费者已按 kind
  分道，互斥由 fence 完成）；
- 容量组闸新代码（等价物已存在，见 §1）；
- cron lane（无 cron 系统）；
- interrupt 级联取消（R5 后）。

## 5. 测试计划（`tests/test_r3_session_domain.py`）

| # | 用例 | 断言 |
|---|---|---|
| 1 | session 被占时 chat 消费者跳过 | fence 占栏后 run_pending 不消费该 session 工单（留队）；释放后正常消费 |
| 2 | chat turn 在跑时 study 不取单 | fence 占栏下 _mailbox_loop 不派发（用假 runner 探针） |
| 3 | study 执行期间 fence 占栏 | claim("study:{sid}") 生效；chat run_pending 跳过；结束释放 |
| 4 | 交替执行 | chat→study→chat 全序串行（事件时间戳排序断言） |
| 5 | 崩溃释放 | study 执行抛异常 → fence 释放 → chat 恢复消费 |
| 6 | 既有套件 | scheduler/entry/gateway 全量照跑 |

## 6. 验收对照

| 门 | 判定 |
|---|---|
| 行为等价 | 无并发场景逐字节不变（单 kind 使用者不可感知） |
| 测试不减 | inventory verify 0 removed |
| 失败集不扩大 | c1_gate 对冻结基线 |
