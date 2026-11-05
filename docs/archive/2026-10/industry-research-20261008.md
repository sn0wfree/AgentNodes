# 业界调研 — Agent 框架 / 量化 Agent 研究进展与借鉴（2026-10-08）

> 服务重构决策。**调研降级声明**：本会话无 Browser Use 载体（`agent.browsers` 缺失）与 search MCP，arxiv API 网络受限——采用 **raw.githubusercontent/webfetch 直抓已知官方源**（OpenAI Agents SDK / LangGraph / Anthropic / AutoGen 官方 README 与工程博客）+ 内部调研文档交叉。非 SERP 全景，属「关键源精读」；未覆盖之处标注不确定。

## 1. 业界进展速览（关键源精读）

### 1.1 OpenAI Agents SDK（2026 形态）

核心概念：**Agents**（instructions+tools+guardrails+handoffs）· **Sandbox agents**（容器长任务）· **Realtime/Voice** · **Agents-as-tools / Handoffs**（委派）· **Tools（MCP）** · **Guardrails（输入+输出双向）** · **Human-in-the-loop** · **Sessions（自动对话历史管理）** · **Tracing（内置追踪调试）**。

值得注意：**Sandbox agents** 把「长时程 + 工作区保持」做成一等公民（git repo manifest 注入、Docker/local sandbox client）；**Sessions** 把历史管理从用户责任移入框架。

### 1.2 LangGraph（定位：stateful agents 低层编排）

四大支柱：**Durable execution**（失败后从断点恢复、长时运行）· **HITL interrupts**（任意点 inspect **and modify** agent state）· **Comprehensive memory**（短期工作记忆 + 跨会话长期记忆）· **LangSmith**（轨迹可视化/评估/生产观测）。上层新动向：**Deep Agents**（规划 + subagents + 文件系统利用的高级包）。

### 1.3 Anthropic《Building effective agents》（2024-12，2026 更新至 Claude Managed Agents）

- **Workflow vs Agent 的架构区分**：workflow=预定义代码路径编排；agent=LLM 动态决定过程与工具。**找最简方案，复杂度只在被证明有益时增加**
- 五种 workflow 模式：prompt chaining / routing / parallelization / orchestrator-workers / evaluator-optimizer
- **ACI（Agent-Computer Interface）**：工具定义的工程投入应 ≥ HCI；Poka-yoke 工具设计（例：强制绝对路径根治相对路径错误）；工具文档=给初级工程师写 docstring
- 生产建议：**警惕框架抽象层遮蔽 prompt/response**（调试成本）；SWE-bench agent 的时间大头在工具优化而非总 prompt

### 1.4 AutoGen → Microsoft Agent Framework（硬情报）

**AutoGen 已 archived**（微软不再维护），继任者 **Microsoft Agent Framework 1.0**：企业级多 agent 编排、多 provider、**A2A + MCP 跨运行时互操作**、稳定 API + 长期支持。Magentic-One 多 agent 团队（浏览/执行/文件）为参考实现。→ 业界多 agent 编排进入「协议互操作期」（A2A/MCP）。

### 1.5 量化域（内部调研交叉）

- `quantskills-borrow-list.md`：QuantStudio 研究纪律 B1-B8——**primary_score v2 单源防刷分**、回测体检 5 gate（防 look-ahead/假回测）、结论标签枚举、**output_contract 交付契约**、因子衰减模型、任务 CAS、写操作 Plan 生命周期、指标单一真源
- `deepseek-harness-research.md`：Cordis 内核/能力接缝/Context 注入三原语/Surface/请求信封/**Compaction 锁/KV cache 意识/Crash Repair**/事件源会话日志
- `AI因子挖掘研报`（QuantaAlpha 深研）：三框架一条线的因子挖掘方法论映射

## 2. 对照我们的架构（已具备 / 缺口）

| 业界概念 | 我们的现状 | 差距判断 |
|---|---|---|
| workflow vs agent 区分（Anthropic） | **phase_engine（workflow 式）⇄ langgraph（agent 式）双分支**——查表分派 + RoundOutcome 契约 | ✅ 概念对齐；重构方向（契约化/查表）与业界一致 |
| Sessions 自动历史（OpenAI） | event_store 事件源 + projector 物化 + SessionService | ✅ 我们更重更可审计（事件溯源） |
| Durable execution（LangGraph） | checkpoint（langgraph 路径）+ HITL interrupt/resume | ⚠️ phases 路径无 resume；study 级 crash 无自动续跑 |
| HITL inspect+modify（LangGraph） | interrupt→approve/reject/timeout（M1/M2/H2 语义保留） | ⚠️ 「修改 state 后继续」我们是 redo 整轮，非就地修改 |
| Comprehensive memory（LangGraph） | memory_manager + MemoryRetrieverInjector + goal journal | ✅ 结构在；长期记忆的跨 study 聚合弱 |
| Tracing/轨迹评估（LangSmith） | observability trace + golden eval（70 题，工具/违规/预算/内容四维） | ⚠️ **缺轨迹级评估**（单题分数≠轨迹质量；无轨迹 diff） |
| Guardrails 双向（OpenAI） | output 侧 claim validation ✓；input 侧靠 eval 题（injection-*） | ⚠️ **无运行时输入守卫** |
| ACI 工程（Anthropic） | 16 toolsets + tool_help 文档 + 沙箱 | ⚠️ 无系统性 ACI 审计（Poka-yoke 未做） |
| Sandbox agents（OpenAI） | workspace 沙箱 + 权限层 + bg 任务 | ✅ 同代形态 |
| A2A/MCP 协议互操作（MAF） | MCP 接入面有；swarm 是内部闭合 | ℹ️ 远期关注（重构不需要） |
| Deep Agents/subagents（LangGraph） | swarm + A6 轻量子任务 + task 工具 | ✅ 已有对应物 |
| 研究纪律 B1-B8（QuantStudio） | metric_targets/strategy_acceptance 有雏形 | ⚠️ **单源评分/体检 gate/output_contract 未成文铁律** |

## 3. 重构期可借鉴清单（优先级）

| 优先 | 借鉴 | 来源 | 落地形态 | 与重构的关系 |
|---|---|---|---|---|
| **1** | **轨迹级评估**：在 golden score 外加轨迹指标（步骤数/工具序列/回退率） | LangSmith 轨迹评估 | eval scorecard 扩字段 + nightly diff | **重构护栏**——重构前后轨迹对比比分数更敏感 |
| **2** | **output_contract / 单源评分铁律** | QuantStudio B1/B4/B8 | `factor_validate.primary_score` 单函数 + round 交付 schema | 研究质量门；重构 study 链时顺势落 |
| **3** | **回测体检 gate（防 look-ahead/假回测）** | QuantStudio B2 | `strategy_acceptance` 5 项 health_check | study finalization 加 gate，拦截重构引入的回归 |
| **4** | **输入侧 guardrails** | OpenAI Guardrails | 注入/越权输入的运行时检测（eval 已有题面） | 安全面重构时并入 |
| **5** | **ACI 审计（Poka-yoke）** | Anthropic | 16 toolsets 参数/文档/防呆审查 | 工具面重构（切入点 D/E）时顺手 |
| **6** | **Durable/crash repair** | LangGraph + deepseek-harness §3.8 | study 长任务崩溃续跑 | 长研究可靠性；非紧急 |
| 7 | HITL 就地修改 state | LangGraph interrupts | approve 时带 patch 而非整轮 redo | 产品演进，非本轮重构 |

**不借鉴**（明确排除）：AutoGen 停留路线（已归档）；LangGraph Deep Agents 高层包（与我们自研 loop 重叠，引抽象层违背 Anthropic 的「警惕框架遮蔽」建议）；A2A 协议（内部 swarm 已满足）。

## 4. 对重构方案的三条印证

1. **契约化方向被业界印证**：Anthropic 的 workflow/agent 区分 + OpenAI 的清晰概念面（Agent/Sessions/Tools/Guardrails）都强调**明确的边界与接口**——我们 RoundOutcome/BranchContext/查表分派正是此路线，重构继续走「显式契约」不走「魔法抽象」。
2. **简单优先原则**：Anthropic「最简方案起步、复杂度需被证明」直接支持我们「巨文件不紧急/不做 StudyLifecycle 状态机类」的判断——**方法抽取已达成目标，不追架构纯洁性**。
3. **评估是 harness 的护城河**：LangSmith 生态把 eval/trace 做成产品面；我们 golden eval + A/B 归因 + 跟幅门禁是同一方向——重构期**轨迹评估（借鉴 #1）是最值得加的一块**，它让重构安全网从「过/不过」升级为「行为是否漂移」。

## 5. 不确定性与后续

- 本次为**关键源精读**非全景 SERP（网络/工具降级所致）：2026 年 arXiv 量化 agent 最新论文面（如 QuantaAlpha 原始论文细节、FinRobot 等）未直接覆盖，内部研报（AI因子挖掘）是其代偿
- LangSmith/Managed Agents 的**实现细节**（轨迹 schema、diff 工具）未读源码级——借鉴 #1 落地时需二次调研
- 建议：网络恢复后用 Browser Use 补一轮「2026 quant agent papers」SERP 扫描

## 6. 源清单

| 源 | 获取方式 | 可信度 |
|---|---|---|
| OpenAI Agents SDK README（github.com/openai/openai-agents-python） | webfetch raw | 官方一手 |
| LangGraph README（github.com/langchain-ai/langgraph） | webfetch raw | 官方一手 |
| Anthropic《Building effective agents》（anthropic.com/engineering/…） | webfetch | 官方一手 |
| AutoGen README（github.com/microsoft/autogen）——归档声明 | webfetch raw | 官方一手 |
| `quantskills-borrow-list.md` / `deepseek-harness-research.md` / `AI因子挖掘研报` / `eval-case-sources.md` | 内部文档 | 既有调研 |
