# HITL resume 机制：方案对比与业界调研 — 2026-10-27

> 状态：**已按方案 A 落地（`8623d42d`，2026-10-27）**——payload 增强 /
> `resume_round_via_graph`（重建 + `start=claims`）/ runner 按 engine 分流
> 全部完成；§5 清单逐项兑现，顺带修复 `get_interrupt_for_round` 平局排序
> 缺陷（Windows 时钟粒度下返回首个 interrupt 而非最新）。
> 本文保留为决策记录。
> 前置：HITL 审批门暂停段已落地（`71d5cf34` + 自查修复 `44c4a355`/`f639ee2d`），
> 设计见 `stage-hitl-gate-graph-design-20261027.md`；resume 是最后一块，
> 也是 P5-2（删 langgraph 分支）的收尾前置。

---

## 1. 决策点

HITL 暂停后跨进程/跨时间的恢复机制，二选一：

- **方案 A（倾向）**：持久化重建——从业务工件（interrupt payload + run_dir +
  工作区文件）重建 state，`engine.run(state, start=<门后继>)` 跳过已完成节点。
- **方案 B**：吸收旧引擎的 SqliteSaver 检查点——每节点边界全量 state 快照，
  恢复时同 thread_id 重编译 + `Command(resume=审批结果)`。

## 2. 本质区别与优劣

**根差异**：A 恢复「业务工件里本来就有的东西」；B 恢复「引擎自己存下来的
内存快照」。

| | A：重建 + `start=` | B：SqliteSaver |
|---|---|---|
| 恢复依据 | 持久化工件确定性重算 state | 每节点边界全量内存快照灌回 |
| 恢复粒度 | 只重建「后继节点读的键」 | 字节级节点边界快照 |
| 恢复入口 | `engine.run(state, start="claims")`，显式 | checkpointer + `Command(resume=...)`，运行时接管 |
| 中断节点命运 | gate 只抛中断；resume 从后继开始，审批决策在图外施加 | gate 节点重新执行，`interrupt()` 返回值=审批结果（节点内双相位逻辑） |
| 新引擎改动 | 零（`start=` 是 B1-a 既有设计） | 需给新引擎补 langgraph 原生 interrupt()/checkpointer 全套管线 |

**A 优势**：顺新引擎设计（不并存两套恢复机制）；版本漂移免疫（旧快照灌新
代码是检查点机制经典病，A 不匹配即大声失败而非静默错跑）；可运维（一条
SELECT 能看见恢复时会拿到什么，B 的 blob 要工具才能检视）；零新增存储；
P5-2 前提（langgraph_engine 全家可退役）。

**B 优势**：崩溃恢复（每节点有快照，进程死掉可从上个节点续跑）——但对本
场景价值低：研究轮是迭代式设计，崩一轮=丢一轮=跑新一轮，旧引擎没检查点时
就是这么兜底的；理论保真度上限高（节点依赖「不可从工件重建的内存态」时 B
能救）——但我们的 state 是域内小 dict，无此约束。

## 3. 业界调研（2026-10，五大系统）

三大流派：

| 流派 | 代表 | 暂停时持久化什么 | 怎么恢复 |
|---|---|---|---|
| ① 全量自动快照 | **LangGraph checkpointer**（唯一主流代表） | 每节点边界自动序列化全部 state | 同 thread_id 重入 + `Command(resume=...)` |
| ② 显式状态序列化 | **OpenAI Agents SDK** | 开发者在暂停点显式序列化 RunState 成 JSON（存 DB/队列，有存 MongoDB 实践） | 反序列化后传回 `Runner.run` |
| ③ 逐步骤结果落盘 + 跳过已完成 | **Temporal / Inngest / n8n / Argo** | 每步骤的**输出**（Temporal 事件史 / Inngest memoization / n8n execution data / Argo artifact） | 重放或跳过已完成步骤；等审批 = 等外部事件（可隔天到达） |

**逐派要点**：

- **LangGraph**：官方语义 `interrupt()` **必须配 checkpointer**（否则抛错）——
  旧引擎正是被这个绑定拖着走了 SqliteSaver。它这么做的原因：引擎面对任意
  用户自定义 state，只能全存。已知代价：`streamEvents` 不支持中断恢复、
  工具状态要手工塞 graph state、**恢复时拓扑必须与暂停时一致**
  （旧引擎注释原话即此坑，`langgraph_engine.py:739-740`）。
- **OpenAI Agents SDK**：暂停 `result.to_state()` 序列化、恢复反序列化——
  可检视的显式 JSON，开发者管持久化。与方案 A 已很接近，差别只在它序列化
  运行时对象、我们序列化域内业务工件。
- **Temporal**（durable execution 金标准）：事件溯源——每个活动结果作为事件
  持久化，恢复=重放，已完成活动从历史读回不重执行；审批 = Signal，可隔天
  到达。**「检查点」不是内存快照，是逐步骤显式结果记录**。
- **Inngest**：`step.run()` 天然 memoization（跑过一次永不重跑）+ 
  `step.waitForEvent("await-approval", {timeout})` 等审批事件。
- **n8n / Argo**：n8n Wait 节点发 `$execution.resumeUrl` 给审批人、webhook
  恢复、按节点保存执行数据；Argo suspend 不保存容器内存、节点产物在
  artifact store。已知高频坑：**恢复时节点拿到旧参数而非恢复调用带来的
  新数据**（状态与外部输入的合并是经典坑——我们的「审批决策注入 state」
  契约测试必须钉死这一点）。

**归纳**：没有任何一个主流生产系统靠「灌回全量内存快照」做长时审批恢复；
全量快照派只有 LangGraph 自己，且它背着一串已知坑。生产 durable execution
的共识方向全部落在流派③——**方案 A 是流派③的微缩版**。

## 4. 「检查点保留吸收」的正确落位

业界对应物**不是**内存快照，而是流派③自身的演进：**phase 渐进落盘**——
把 round manifest 从「轮末一次性写」（`phase_engine.py:179` 现状）改成每个
phase 结束就写。这相当于我们自己的 memoization：

- 拿到 B 的唯一真实优势（崩溃恢复）且粒度更细（任意 phase 后继可续）；
- 重建函数从「门专用」升级为通用能力；
- 零新依赖、无版本漂移/拓扑耦合问题；
- langgraph_checkpoints 表保留 schema 停止写入（存量无害），
  langgraph_engine 照常在 P5-2 退役。**SqliteSaver 正式出局**。

排期建议：可与 P5-4（ContextBuilder 并入 ContextHub）并行，优先级待定。

## 5. 方案 A 实施要点（开工清单）

1. **payload 增强**（暂停契约闭合）：门节点中断时把 `researcher_output`
   整个塞进 payload——门后继 claims 节点读 `state["researcher_output"]`
   （存证要 `predictions` 字段），当前 payload 只带 hypothesis/
   predicted_affected 会缺。契约：**暂停点持久化后继节点要读的每一个键**。
2. **重建函数**：从 interrupt payload（researcher_output + hypothesis +
   predicted_affected）+ `read_current_state`（工作区）+ run_dir 重建
   state dict；`runner.py:452-489` 的重建逻辑可直接复用。
3. **决策注入**：approve → state 带审批结果 + `engine.run(state,
   start="claims")`（gate 不重跑，节点单相位）；reject/超时 → 现有跳轮
   兜底已正确（M1 过期 + 预算入账 + H2 防幽灵轮），不动。
4. **resume 分流**：`_handle_hitl_pause` 现在无条件调 `_resume_round_langgraph`
   ——改为按 `study.engine` 分流（`phases` → 新 resume；`langgraph` → 旧
   resume 保留到 P5-2 删除为止）。engine 是 study 创建时固定的，分流可靠。
5. **测试**：恢复契约（state 键完整性/决策注入/跨进程重建）+ n8n 坑
   （恢复时审批结果必须可达，不得被旧值遮蔽）+ reject 路径不变性。

工量估计：~1-1.5d（payload 增强 0.5d + 重建/接线/测试）。

## 6. 来源

- [OpenAI Agents SDK — Human-in-the-loop（RunState 序列化/恢复）](https://openai.github.io/openai-agents-python/human_in_the_loop)
- [OpenAI Agents JS — human-in-the-loop guide](https://openai.github.io/openai-agents-js/guides/human-in-the-loop)
- [LangGraph HITL with interrupt()（checkpointer 硬依赖）](https://medium.com/@areebaayub2908/human-in-the-loop-in-langgraph-with-interrupt-langgraph-a3a9774b847e)
- [LangGraph 论坛 — resume after interrupt 的坑](https://forum.langchain.com/t/how-to-resume-the-agent-workflow-after-interrupt/434)
- [Temporal blog — 长时交互工具与人工审批 Signal](https://temporal.io/blog/building-long-running-interactive-mcp-tools-temporal)
- [Inngest — step.waitForEvent（审批暂停/恢复）](https://www.inngest.com/docs/reference/functions/step-wait-for-event)
- [Inngest durable functions — step memoization](https://www.skillsdirectory.com/skills/asymmetric-al-inngest-durable-functions)
- [n8n Wait node docs — resumeUrl 审批模式](https://n8n-docs.teamlab.info/integrations/builtin/core-nodes/n8n-nodes-base.wait)
- [n8n 社区 — 恢复数据陈旧坑](https://community.n8n.io/t/wait-node-not-receiving-updated-data-from-resume-webhook-getting-original-parameters-instead/140692)
