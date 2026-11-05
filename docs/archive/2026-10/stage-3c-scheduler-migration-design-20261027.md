# 3c 设计 — scheduler / automation 入口迁移 — 2026-10-27

> 前置：B1-a/B1-b（research preset）、B1-a 阶段图、F1-F4（fanout）。
> 目标（replan v2 §4）：study 门翻默认开；提交走统一入口；**study 真正执行**。

---

## 1. 一个必须先堵的洞：kind 无差别消费

`SessionController.run_pending()` 现在遍历所有 mailbox、轮询所有工单。
一旦 scheduler 把 AUTOMATION 工单投进 Gateway mailbox，而 chat API 的
`gw.run_once()` 触发了消费，**研究工单会被当作聊天轮次执行**（送进
llm 节点）。这是迁移后必然出现的串道事故。

修法：**按 kind 消费**。

- `Mailbox.poll_due(kinds=None)`：可选的 kind 过滤；不匹配的工单**留在队列**
  （不丢弃）。interrupt 优先级仍在过滤后的集合内生效。
- `SessionController.run_pending(kinds=None)` / `Gateway.run_once(kinds=None)`
  透传；**默认 None = 全部**（保留既有测试/脚本行为）。
- chat API 显式只领 CHAT；scheduler 显式只领 AUTOMATION。

这条与 v6 契约一致：入口拥有协议、控制器拥有裁决——现在再加一条
「消费者拥有 kind 归属」。

## 2. 提交级接线：scheduler 用 mailbox 做准入，执行仍归 scheduler

```
submit(study)
  ├─ 防重检查（active / queued / terminal）——不变
  ├─ 提交 AUTOMATION 工单到 mailbox（automation:{session_id}）
  └─ 启动 per-session mailbox 消费者：
        poll_due(kinds={AUTOMATION}) → 拿到 study_id → _run_one_study(study_id)
```

**为什么不把 study 塞进 SessionController 执行**：`AutoresearchRunner.run()`
是一个多轮、长时、带 semaphore / watchdog / cancel token / 心跳的异步执行体，
与「一次 turn 一个 GraphEngine 图」的控制器模型不同构。本阶段统一的是
**准入通道**（唯一开 turn 路径），执行体归属仍是 scheduler——这与「1 控制面 +
N 执行面」的架构一致：AUTOMATION 的执行面就是 study runner。

graph 化 research 轮次已经通过 `QNODES_RESEARCH_GRAPH` 在轮次内部生效
（B1-b），两层各司其职。

## 3. 消费循环的可靠性

- 消费者**按 session 一组**（`automation:{session_id}` mailbox），与既有的
  `_session_loop` 同构；
- mailbox 空 → 消费者退出（懒启动，下次 submit 再起），避免常驻空转；
- 提交被拒（回执 accepted=False）→ submit 返回 False，不加 `_queued_study_ids`；
- 消费者取到工单后先 `_queued_study_ids.discard(sid)`（与旧路径一致，
  保证 cancel 语义、防重语义不变）；
- **旧路径保留**：`QNODES_NEW_ENTRY_STUDY=0` 显式关掉即回到 asyncio.Queue
  （灰度回退能力，阶段 4 删除）。

## 4. 双门翻默认

| 门 | 旧默认 | 新默认 | 回退 |
|---|---|---|---|
| `study_entry_enabled()`（`QNODES_NEW_ENTRY_STUDY`） | 关 | **开** | 设 `0` |
| `research_graph_enabled()`（`QNODES_RESEARCH_GRAPH`） | 关 | **开** | 设 `0` |

翻默认的依据：B1-a/B1-b 的等价性测试（RoundOutcome 逐字段相等）+
3c 的全链路测试（提交 → mailbox → 消费者 → 执行）。

**风险与诚实边界**：等价性测试用的是 fake phase 函数；真实 LLM 轮次的
等价只能靠既有 study 测试套件 + 阶段 4 的真实全量验证。因此本阶段
**保留两个回退开关**，不做删除——删除属阶段 4 的审计门②。

## 5. 验收

1. kind 过滤：CHAT 消费不动 AUTOMATION 工单（反之亦然），工单留队；
2. scheduler 新路径：submit → AUTOMATION 进 `automation:{sid}` mailbox →
   消费者取走 → `_run_one_study` 被调用（等价的执行断言，不只「投递成功」
   ——replan v2 §5 方法论）；
3. 双门默认值 = 开；显式 `0` 可回退旧路径；
4. 防重 / terminal 拒绝在新通道下仍成立；
5. 既有 scheduler 全量测试在门默认开后仍绿（必要时用显式 `0` 标注旧路径用例）。

## 6. 明确不做（YAGNI）

- 把 study 执行搬进 SessionController（见 §2）；
- 删除旧 asyncio.Queue 路径（阶段 4）；
- AUTOMATION 的 steer/interrupt 运行时语义（D3 已定：挂起至 join；study 侧
  cancel 仍走 ControlToken）；
- 多机/持久化队列（0.4/E5 范围）。
