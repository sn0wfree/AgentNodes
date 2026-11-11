# Loop 引擎业界调研：实现方式与功能清单（2026-11-11）

> **性质**：调研（为「在 ReActSkeleton 基础上统一双引擎」提供业界证据）
> **方法**：内部存货盘点（`unified-agent-engine-design.md` / `unified-loop-adoption-eval-20261107.md`
> / 库内参考实现 `reference/llmwikify/kernel-agent/`）+ 2026-11 当日 Web 检索
> （OpenAI Agents SDK / Claude Agent SDK / LangGraph / smolagents / pydantic-ai /
> AutoGen / Strands / OpenHands / Vercel AI SDK）。
> **一句话结论**：单循环 + 可插拔检查点（ReActSkeleton 所属形态）是业界生产系统的
> 主流形态（OpenAI / Anthropic / AWS 全部是这个形状）；统一方向成立。业界比我们强的
> 四处：**终止条件可组合、HITL 是循环级原语、双通道流式契约、检查点/持久化**。
> 我们比业界强的两处：权限分层（hard_deny + L0-L4）与上下文压缩。

---

## §0 调研范围

### 0.1 内部存货（先盘再查，避免重复劳动）

| 存货 | 与本次的关系 |
|---|---|
| `reference/llmwikify/kernel-agent/`（loop.py 301 行 + _core_types + hook + spec + steps）| **ReActSkeleton 的直系祖先**——`unified-loop-adoption-eval-20261107.md` 的 B1（协议统一）/ B2（deciders）/ B3（骨架隔离）三批的移植来源 |
| `unified-loop-adoption-eval-20261107.md` | 已做过一次「借鉴评估」：逐概念对照 llmwikify，裁决 = 换底座不换职责 |
| `unified-agent-engine-design.md`（2026-08-18）| 更早的「单一执行引擎」设计（AgentLoop 吸收 SwarmWorker），当时尺度是 kill SwarmWorker，不是统一双 loop 引擎 |
| `industry-survey-knowledge-base-20261106.md` | 主题是 RSI/observability/feature-flag，与 loop 引擎无关（本次未复用）|

### 0.2 外部对象（9 个框架，检索日期 2026-11-11）

OpenAI Agents SDK · Anthropic Claude Agent SDK · LangGraph · smolagents（HF）·
pydantic-ai · AutoGen/AG2（→ Microsoft Agent Framework）· AWS Strands Agents ·
OpenHands SDK · Vercel AI SDK。另加库内 llmwikify 作第 10 个对照。

---

## §1 业界的五种循环架构模式

| 模式 | 形状 | 代表 | 对 AgentNodes 的意义 |
|---|---|---|---|
| **A · 单循环 + 可插拔检查点** | 一个显式 while/for 循环；终止、审批、hook 是循环上的挂点 | **OpenAI Agents SDK（Runner）**、**Claude Agent SDK**、**Strands**、llmwikify/**ReActSkeleton** | **我们所属的家族，也是业界生产主流**——统一方向被三个一线厂商背书 |
| **B · 状态图/状态机** | loop = 图；节点 = 步骤；checkpoint 每超步持久化；interrupt/resume 是图原语 | **LangGraph**、Google ADK（Sequential/Loop/ParallelAgent）、pydantic-ai（内部也是图，可出 mermaid）| 持久化/恢复最强；代价是把「循环」升格成「图」——AgentNodes 现有 workflow 层已承担编排，kernel 循环不需要图 |
| **C · 终止条件作为一等可组合原语** | 循环控制 = 条件对象列表，支持 AND/OR 组合 | **AutoGen**（`MaxMessageTermination & TextMentionTermination`）、**Vercel AI SDK**（`stopWhen: [stepCountIs(n), …]`）| 最值得抄的一点：**deciders 应支持组合**（见 §4.1）|
| **D · 事件溯源循环** | 事件流**就是**状态：Action/Observation 都是可回放事件，状态是事件流的投影 | **OpenHands SDK**（closed action vocabulary + LLMConvertibleEvent）| 远期方向。我们的 event_vocab + trace.jsonl 已有雏形，但事件是**副作用发射**，不是状态事实源 |
| **E · 代码即动作** | 动作空间 = 可执行代码（Python）而非 tool-call JSON | smolagents `CodeAgent`、OpenHands `CodeActAgent` | 与 loop 引擎正交（是 reasoner/actor 的形态选择），不影响统一 |

**关键判断**：ReActSkeleton 的 PRECHECK→REASON→DEC①→ACT→DEC②→OBSERVE→FINALIZE
结构与 OpenAI Runner / Strands loop 同构（模式 A），且比它们多了**显式闸位分位**
（precheck/after_reason/after_act 三闸，llmwikify 原版还有 DEC③/after_observe，
移植时被裁掉——见 §4.1 缺口 1）。

---

## §2 逐框架速览

| 框架 | 循环形态 | 循环控制 | HITL/审批 | 流式 | 持久化/恢复 | 子代理/移交 | 上下文管理 |
|---|---|---|---|---|---|---|---|
| **Claude Agent SDK** | 模式 A：gather context → act → verify → repeat | max turns + stop hooks | **permission modes** + hooks（PreToolUse 等切面）| token + 事件双通道 | 会话恢复（resume）+ 跨窗口 harness（长任务初始化 agent）| subagents（隔离上下文）| **compaction / context editing**（业界最强）|
| **OpenAI Agents SDK** | 模式 A：Runner.run/run_sync/run_streamed | max_turns | approvals（runtime 集成）| run_streamed（token+事件）| DBOS 集成（durable long-running）| **handoffs**（一等原语）| sessions/memory |
| **LangGraph** | 模式 B：图 + checkpointer | 条件边 + 递归上限 | **interrupt() + Command(resume)**（图原语，checkpoint 持久）| 5 种流模式（values/updates/messages/custom/debug）| **checkpointer**（每超步）| subgraphs | 状态裁剪 |
| **smolagents** | 模式 A/E：MultiStepAgent while 循环 | max_steps | 无内建 | 流式生成 | 无（E2B 是沙箱不是持久化）| 多 agent 由外层编排 | memory 列表 |
| **pydantic-ai** | 模式 B：图（可出 mermaid）| end strategy | 工具级 HITL | 流式 | **durable execution**（Temporal/DBOS/Prefect/Restate，模型请求和工具调用都是 activity）| 多 agent 编排 | 依赖注入式 |
| **AutoGen/AG2** | 对话驱动 | **终止条件可组合**（`&`/`|`：MaxMessage/TextMention/TokenUsage/Timeout/External/StopMessage）| ExternalTermination | 流式 | 无内建 | team/handoff | 无内建压缩 |
| **Strands（AWS）** | 模式 A：model-driven loop（reason→act→reflect）| max rounds | **lifecycle hooks 即审批闸**（audit/计量/审批都挂 hook）| 事件流 | session 管理 | 多 agent 模式 | memory/session |
| **OpenHands SDK** | 模式 D：事件溯源（action → observation 追加，直到 finish）| finish 动作 + 上限 | 审批是事件类型 | 事件流 | **事件流即状态**（可回放）| agent 抽象分层 | 事件历史裁剪 |
| **Vercel AI SDK** | 模式 A：tool-calling 循环 | **stopWhen 数组**（stepCountIs/hasToolCall/自定义）+ prepareStep 每步钩子 | 工具级 confirm | full-stream（token+工具调用+结果同流）| 无 | 无 | 无 |
| **llmwikify（库内参考）** | 模式 A：UnifiedAgentLoop.run_stream 301 行 | deciders 三检查点（after_reason/after_act/**after_observe**）| 无 | StreamingHandler 双接口 | 无 | 无 | 无 |

---

## §3 功能矩阵（业界共识 vs AgentNodes 现状）

图例：✅ 有 · ⚠️ 部分 · ❌ 无。AgentNodes 列 = 统一前的实测现状。

| 功能 | 业界做法 | 业界普遍度 | AgentNodes | 差距定性 |
|---|---|---|---|---|
| **循环体唯一** | 全部 10 家都是单一循环体 | 10/10 | ⚠️ 双引擎并存（本次要统一的正是它）| **统一本身** |
| 迭代上限 | max_turns/max_steps/MaxMessage | 10/10 | ✅ MaxIterationsDecider（precheck）+ for-else 兜底 | 齐 |
| **终止条件可组合** | AutoGen `&`/`|`；Vercel stopWhen 数组 | 2/10 但被公认为正确形状 | ⚠️ deciders 按闸位列表，**无组合算子**、无 ExternalTermination | **建议 1** |
| 闸位数量 | A 型 3-5 个检查点 | — | ⚠️ 三闸（precheck/after_reason/after_act）；llmwikify 原版第四闸 **after_observe 被裁** | **建议 2** |
| **HITL 是循环原语** | LangGraph interrupt/resume（checkpoint 持久）；pydantic-ai durable 暂停（可停数月）| 4/10 | ⚠️ 审批埋在 no-progress decider 内（`_check_no_progress` 闭包），**不是循环级 pause/resume**；无恢复语义 | **建议 3** |
| 权限分层 | Claude SDK permission modes / OpenAI approvals | — | ✅ **hard_deny 独立层 + L0-L4 + fail-closed**（强于多数框架）| 领先 |
| **双通道流式**（token + 事件同一契约）| OpenAI run_streamed / Vercel full-stream / Claude SDK | 5/10 | ⚠️ legacy 有 token 流；skeleton `run_stream` 只有 error/done 两类事件，**无 token 增量** | **建议 4** |
| 事件词表 | 全部有（tracing 是标配）| 10/10 | ✅ event_vocab + trace.jsonl | 齐 |
| **检查点/持久化** | LangGraph checkpointer；pydantic-ai durable（Temporal 系）| 3/10 | ❌ 只有 trace 文件（不可恢复执行）| **明确不做**（见 §5）|
| 子代理/移交 | OpenAI handoffs 一等原语；Claude subagents | 8/10 | ✅ `task` 工具（DELEGATE_TO_AGENT）+ no-nesting | 齐（形态不同：工具 vs 原语）|
| 上下文压缩 | Claude compaction/context editing 最强 | 5/10 | ✅ compaction step + keep_all 开关 + trace 分载 | 齐 |
| Guardrails（输入/输出扫描）| OpenAI Agents SDK 并行 guardrail | 4/10 | ✅ `scan_user_input`（parity 钉住）+ permission 层 | 齐 |
| 熔断/韧性 | 少数内建（多靠外层）| <2/10 | ✅ CircuitBreakerDecider（precheck 闸）| 领先 |
| **planning step** | smolagents `planning_interval`（动作步间插纯规划步）| 2/10 | ❌ 只有 wrap_up_nudge（收尾提示，不是规划步）| 可选，暂缓 |
| 并行只读工具 | OpenHands/多家 | — | ✅ `asyncio.gather`（共享 I/O 层）| 齐 |
| 沙箱 | E2B / OpenHands runtime | 5/10 | ⚠️ PathWhitelist + sandbox 模块（P4 未做，schema.py 自认「真正的强制靠 OS 沙箱」）| 已知边界 |
| MCP | 主流标配 | 8/10 | ⚠️ 有 `.mcp.json` hard_deny（禁 agent 改配置），运行时支持未查证 | 待查证 |

---

## §4 对「在 ReActSkeleton 上统一双引擎」的证据型建议

### 建议 1 · deciders 升级为可组合原语（抄 AutoGen/Vercel）

现状：deciders 是「闸位 → 列表，按序判定，首个非 PROCEED 生效」。
业界正确形状：条件是对象，支持 `&`/`|` 组合 + 数组 stopWhen。

具体（各一条，不动循环体）：
- `Decider.and_(other)` / `.or_(other)` 组合子；
- `ExternalTermination` 等价物——把现有全局 halt 接进闸位（业界共识：
  **max 计数条件永远与语义条件组合使用当安全网**，AutoGen 官方文档原话）；
- 恢复 llmwikify 被裁掉的 **after_observe 闸**（OBSERVE 后判 goal 续跑/停止——
  现在这类判定挤在 after_act，闸位语义混淆）。

### 建议 2 · 全部出口走 FINALIZE（抄 LangGraph 的 END 漏斗）

业界循环都漏斗到单一终态（LangGraph 的 END 节点 / Runner 的 final output /
OpenHands 的 finish 动作）。skeleton 的两个提前 `return`（EXIT 出口）绕过
LoopFinalize 是**反模式**——inventory 文档 §3.3 实测：trace 不关、`trace_path=None`。
统一时把 EXIT 改为 `break`（或给 return 前插 finalize），这是 legacy 强于
skeleton 的唯一结构性优点，必须吸收。

### 建议 3 · HITL 升格为循环级原语（对标 LangGraph interrupt / Claude permission modes）

现状：审批埋在 no-progress 检测里（`_check_no_progress` 闭包），权限 ASK 是工具级。
业界的 interrupt 是**循环级 pause/resume**，且 LangGraph 的教训（检索到的 double-execution
坑：interrupt 前的副作用在恢复时重放）提示：**恢复语义必须显式设计**，不能靠重跑。
统一引擎应提供 `pause(reason) / resume(input)` 语义挂点；现有 permission gateway
的 ask/respond 通道可作为第一个消费者。

### 建议 4 · 双通道流式契约（对标 OpenAI run_streamed / Vercel full-stream）

统一后 `run_stream` 必须同时暴露 token 增量与生命周期事件——现在 skeleton 只有
error/done。这是 legacy（loop_stream.py 261 行）强于 skeleton 的点，必须吸收。

### 建议 5 · sync 语义要么补齐要么退役（业界无先例的「双引擎分域」）

10 家框架没有任何一家维持「sync 走 A 引擎、async 走 B 引擎」的分域——
OpenAI 的 `run_sync` 是**同一个 Runner 的同步包装**。统一后 `run()` 应是
`_run_coro_in_sync(arun())`（同一引擎），而不是第二个循环体。

### 建议 6 · 事件词表向「可回放」演进（远期，OpenHands 方向）

OpenHands 证明事件流可以**就是**状态。我们的 event_vocab 已有闭环词表；
统一时保持事件类型化（不发自由 dict），为将来「trace 即可恢复状态」留门。

### 建议 7 · 明确不做（业界做了但我们不跟的）

| 不做 | 业界对应 | 理由 |
|---|---|---|
| 循环内建检查点/持久化 | LangGraph / pydantic-ai | ① workflow 层已有 checkpoint_store；② durable execution 需要 Temporal 级运行时，是基础设施决策不是引擎决策；③ 保持 LoopContext/StepResult 纯数据即可序列化，将来可加 |
| 状态图化 kernel 循环 | LangGraph | 编排已有 GraphEngine/workflow 层；kernel 循环保持模式 A |
| CodeAct 动作空间 | smolagents/OpenHands | reasoner 形态选择，与统一正交；现有 tool-call JSON 够用 |

---

## §5 与库内参考实现的关系（必须写清，防止「重新发明」）

ReActSkeleton ← llmwikify UnifiedAgentLoop 的移植（adoption-eval §3 B3）。
本次调研对照发现的两处**移植时丢失**的东西，应在统一时捡回：

| llmwikify 有 | 现状 | 处置 |
|---|---|---|
| `after_observe` 第三决策闸（loop.py:217）| 被裁 | 建议 1 恢复 |
| `StreamingHandler` 双接口（token 流作为 reasoner 的自然形态）| 被裁（skeleton 只有事件流）| 建议 4 恢复 |

---

## §6 一页总结（给决策用）

| 问题 | 业界证据给出的答案 |
|---|---|
| 统一到 skeleton 这个方向对吗？ | 对——模式 A 是 OpenAI/Anthropic/AWS 共同的生产形态 |
| skeleton 缺什么必须补？ | 出口漏斗（建议 2）、token 流（建议 4）、budget_exhausted 事件（legacy 有）、sync 同引擎（建议 5）|
| skeleton 强在哪保留？ | 闸位分位 + decider 可插拔 + 状态机显式 |
| 业界有而我们没有的最重要一块？ | **可组合终止条件 + 循环级 HITL**（建议 1/3）|
| 不要跟的风潮？ | kernel 循环图化、内建 durable、CodeAct |

---

## 附：来源清单（2026-11-11 检索）

内部：`reference/llmwikify/kernel-agent/` · `docs/design/unified-loop-adoption-eval-20261107.md` ·
`docs/design/unified-agent-engine-design.md` · `docs/design/loop-engine-inventory-20261111.md`

外部（主要）：
- OpenAI Agents SDK: [官方文档](https://openai.github.io/openai-agents-python) · [Running agents](https://openai.github.io/openai-agents-python/running_agents)
- Claude Agent SDK: [Agent SDK overview](https://code.claude.com/docs/en/agent-sdk/overview) · [Effective harnesses for long-running agents](https://www.anthropic.com/research/effective-harnesses-for-long-running-agents) · [Building effective agents](https://www.anthropic.com/research/building-effective-agents) · [Effective context engineering](https://www.anthropic.com/research/effective-context-engineering-for-ai-agents)
- LangGraph: [interrupt 双执行坑](https://forum.langchain.com/t/interrupts-on-langgraph-platform/151) · [Chainlit #1998（interrupt+Command 用法）](https://github.com/Chainlit/chainlit/issues/1998)
- smolagents: [Agents reference](https://huggingface.co/docs/smolagents/en/reference/agents) · [Building good Smolagents（planning step）](https://smolagents.org/docs/building-good-smolagents) · [GitHub](https://github.com/huggingface/smolagents)
- pydantic-ai: [Durable execution](https://pydantic.dev/docs/ai/capabilities/durable_execution/overview) · [Temporal 集成](https://pydantic.dev/docs/ai/capabilities/durable_execution/temporal) · [Restate HITL](https://pydantic.dev/articles/restate-durable-execution-pydanticai)
- AutoGen: [Termination 条件](https://microsoft.github.io/autogen/stable//user-guide/agentchat-user-guide/tutorial/termination.html) · [无限循环 pitfall #5869](https://github.com/microsoft/autogen/discussions/5869)
- Strands: [Agent Loop](https://strandsagents.com/docs/user-guide/sdk/agents/agent-loop) · [Hooks](https://strandsagents.com/docs/learning/control-your-agent-with-hooks) · [AWS 深度解析](https://aws.amazon.com/blogs/machine-learning/strands-agents-sdk-a-technical-deep-dive-into-agent-architectures-and-observability)
- OpenHands: [Events 架构](https://docs.openhands.dev/sdk/arch/events) · [SDK 论文（arXiv:2511.03690）](https://arxiv.org/html/2511.03690v2)
- Vercel AI SDK: [Loop Control（stopWhen/prepareStep）](https://ai-sdk.dev/docs/agents/loop-control)
- 横向对比: [Langfuse 2026 框架对比](https://langfuse.com/blog/2025-03-19-ai-agent-comparison) · [Splunk 2026](https://www.splunk.com/en_us/blog/learn/agent-frameworks.html)
