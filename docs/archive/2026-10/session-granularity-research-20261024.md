# Session 粒度业界调研 — 2026-10-24

> 问题：验证 session 粒度草案的六个断言（session=串行域 / turn 类型无关 / home_session / 容量组闸门 / 账本解耦 / 不自动恢复）。
> 对象：OpenClaw（queue/lane 文档）· LangGraph（double-texting）· Temporal（WorkflowId 唯一性）· Codex Cloud · ChatGPT Tasks · Jupyter。
> 结论先行：**六断言五确证一存疑；另收获四条可直接吸收的细化（mailbox 深度上限 / 优先级老化 / 子代理 lane 键 / cron 隔离）。**

---

## 1. 各家证据

### OpenClaw（`docs/concepts/queue`）——与我们的设计几乎逐条对应
- **先序后闸**：mailbox 先「给物理 session 授一个 turn」，然后才进全局容量——session 串行优先于全局并发闸。＝ 我们「闸不是队」的原文级印证。
- **lane 感知 FIFO**：同 session key 的请求进同一 lane，严格串行；**群聊 = 每群一个 session**（session 不按工件类型分）。
- **满则排队不拒绝**：容量满 → FIFO 等待；**运行中的 turn 永不被抢占**；拒绝只发生在 **per-session 队列深度上限**（默认 cap 20，溢出策略 summarize/old/new）。
- **容量组**：`main`（交互，maxConcurrent≥max(8, CPU×4)）/ `cron` 与 `cron-nested` 隔离（后台任务不堵交互；外层任务占 cron 槽，内部执行占 cron-nested）/ `subagent:<session>`（子代理独立 lane，默认每 session 8 并发）。
- **优先级老化**：foreground 可领先 normal ≤15s、领先 background ≤30s，之后不得继续插队——防饥饿。
- **排队项不计为活跃 run**、不进 sessions.list、不继承超时——与「QUEUED 不表现为 executing」一致。

### LangGraph（double-texting / multitask_strategy）
- thread 上已有活跃 run 时新 run 的四种处置：`reject`（拒绝）/ `rollback`（终止当前、丢弃进度、新 run 重来）/ `enqueue`（排队，**默认**）/ `interrupt`（暂停当前、先处理新输入）。
- **默认 enqueue（=followup）**而非 steer——因为调用方是代码不是人；与编码 agent 默认 steer（见 queue-mode 调研）并不矛盾，反而支持我们「按来源定默认」的五行表。
- LangGraph 无 mid-run 注入（steer）——我们的 queue_mode 是它的超集。

### Temporal（WorkflowId 唯一性）
- **同一 WorkflowId 至多一个 open 执行**；已在跑再启动 → "already started" 错误；冲突策略 `Fail / UseExisting / TerminateExisting`，复用策略 `AllowDuplicate / RejectDuplicate`。
- WorkflowId（实体）≠ RunId（一次执行）——我们 B4 身份分离的先例；**study 实体 = WorkflowId，round = RunId**，home_session 是我们的实现载体。

### Codex Cloud
- 卖点原文「**Parallel threads across projects（长任务不阻塞你的日常）**」+ Automations；云任务 = 各自独立 session，共享持久环境但任务/上下文状态独立。
- 形态印证：**异步长活默认独立线程；交互活在你的线程里干**（后者对应 Claude Code 在会话内干活）——恰是我们的分法：交互研究占本会话、automation 走自己的 home session。

### ChatGPT Tasks（反面教材）
- 独立 scheduled task **每次运行新开一个会话**（by design），用户抱怨侧栏被任务会话淹没；在既有会话里创建的任务则把结果倒回该会话（用户更满意）。
- **印证 home_session 方向**：automation 研究固定一个 home session、round 依次累积，避免「每次跑散落一个新会话」的失败模式。

### Jupyter（概念同构，无需链接）
- kernel（执行/时序）与 notebook 文档（资产）分离；关掉浏览器内核还在，重启内核文档不丢——session 管时序、账本管事实的同构。

## 2. 六断言判定

| # | 我们的断言 | 证据 | 判定 |
|---|---|---|---|
| 1 | session=turn 串行域，先序后闸 | OpenClaw mailbox 先授 turn 再全局容量；LangGraph 默认 enqueue | ✅ |
| 2 | turn 类型无关 | OpenClaw 每群一 session 不分工件；LangGraph thread 任意 run | ✅ |
| 3 | home_session：同实体永不并行 | Temporal 单 open 执行/WorkflowId + 冲突策略；ChatGPT Tasks 反例 | ✅ |
| 4 | 容量组=闸不是队，满则排队 | OpenClaw「满→等待，running 不抢占；拒绝只在 session cap」 | ✅ 需补深度上限 |
| 5 | 账本解耦 | Jupyter kernel/document | ✅ |
| 6 | 中断 run 默认不自动恢复 | **Temporal 把自动恢复当核心卖点** | ⚠ 唯一分歧 |

**分歧说明**：Temporal 能自动恢复因为其工作流是**确定性重放**；我们的 run 含 steer 注入与模型调用，天然非确定性，默认不恢复、重试靠重提是诚实的取舍——已做成可配置（`gateway.recovery.auto_resume=false`），若未来 round 内部细分出确定性段（如纯回测段），可对该段做断点续跑，属 B1/C 阶段优化。

## 3. 吸收进方案的四条细化

1. **mailbox 深度上限 + 溢出策略**：默认 cap 20；溢出策略三选（`summarize` 旧消息摘要化（默认）/ `old` 丢旧 / `new` 拒新）——防 followup 无限堆积，拒绝点收窄到唯一一处。
2. **优先级老化**：高优先级领先有时限（OpenClaw 15s/30s 量级），超时不得继续插队——防 Director/automation 提交饿死用户消息。
3. **子代理 lane 键**：`subagent:<session>` 独立 lane（默认并发 8）——DagRuntime 子代理的容量记账方案，落 B1 时直接用。
4. **cron 隔离 + 嵌套占位**：automation 与交互分闸（study/chat 分组的同思想）；嵌套执行占内层槽（外层 cron + 内层 cron-nested）——防「任务等任务」双重占位。

## 4. 出处

- OpenClaw queue：<https://docs.openclaw.ai/concepts/queue> · parallel lanes：<https://docs.openclaw.ai/concepts/parallel-specialist-lanes>
- LangGraph double-texting：<https://docs.langchain.com/langsmith/double-texting>（reject/rollback/enqueue 分页同站）
- Temporal WorkflowId/RunId：<https://docs.temporal.io/workflow-execution/workflowid-runid>
- Codex Cloud：<https://developers.openai.com/blog/run-long-horizon-tasks-with-codex>
- ChatGPT Tasks：<https://help.openai.com/en/articles/10291617-scheduled-tasks-in-chatgpt> + 用户反馈（MemoryLake 博客）
